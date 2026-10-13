import json
import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from ai.training.retrain import (BuildStats, Registry, build_dataset, gate, is_val, label_lines, run_job)

W = H = 100


def D(cid=1, conf=None, bbox=(10, 10, 50, 50)):
    return {"class_id": cid, "class_name": "x", "bbox": list(bbox), "confidence": 0.9, "confirmed": conf}


def S(part="P", status="NG", defects=(), iid=1):
    return {"inspection_id": iid, "part_id": part, "image_key": f"{iid}.png", "status": status, "defects": list(defects)}


# ---------------------------------------------------------------- labelling rules

def test_confirmed_defect_becomes_box():
    out = label_lines(S(defects=[D(1, True)]), W, H)
    assert out == ["0 0.300000 0.300000 0.400000 0.400000"]  # class 1 -> index 0


def test_false_positive_dropped_but_other_boxes_kept():
    out = label_lines(S(defects=[D(1, False), D(2, True, (60, 60, 80, 90))]), W, H)
    assert len(out) == 1 and out[0].startswith("1 ")


def test_ok_verdict_with_unreviewed_defects_is_a_background_image():
    assert label_lines(S(status="OK", defects=[D(1, None), D(2, None)]), W, H) == []
    assert label_lines(S(status="OK", defects=[D(1, False)]), W, H) == []


def test_ng_without_saying_which_boxes_is_ambiguous():
    assert label_lines(S(status="NG", defects=[D(1, None)]), W, H) is None


def test_ng_with_nothing_usable_is_skipped():
    assert label_lines(S(status="NG", defects=[D(1, False)]), W, H) is None
    assert label_lines(S(status="NG", defects=[]), W, H) is None


def test_boxes_are_clipped_and_tiny_ones_dropped():
    out = label_lines(S(defects=[D(1, True, (-20, -20, 40, 40)), D(2, True, (50, 50, 51, 51))]), W, H)
    assert len(out) == 1 and out[0].split()[1:3] == ["0.200000", "0.200000"]


# ---------------------------------------------------------------- split / dataset

def _png(color=80):
    ok, buf = cv2.imencode(".png", np.full((H, W, 3), color, np.uint8))
    return buf.tobytes()


def _base(tmp, n=6):
    for sp in ("train", "val"):
        os.makedirs(tmp / "base" / "images" / sp, exist_ok=True)
        os.makedirs(tmp / "base" / "labels" / sp, exist_ok=True)
        for i in range(n):
            cv2.imwrite(str(tmp / "base" / "images" / sp / f"b{i}.png"), np.zeros((H, W, 3), np.uint8))
            (tmp / "base" / "labels" / sp / f"b{i}.txt").write_text("")
    return str(tmp / "base")


def _samples(n=40):
    return [S(part=f"part{i}", status="NG" if i % 2 == 0 else "OK", iid=i,
              defects=[D(1, True)] if i % 2 == 0 else [D(1, None)]) for i in range(n)]


def test_split_is_deterministic_and_part_level():
    assert [is_val(f"p{i}", 0.2) for i in range(50)] == [is_val(f"p{i}", 0.2) for i in range(50)]
    frac = np.mean([is_val(f"p{i}", 0.2) for i in range(2000)])
    assert 0.15 < frac < 0.25


def test_build_dataset_counts_exclusions_and_replay(tmp_path):
    samples = _samples()
    samples.append(S(part="amb", status="NG", defects=[D(1, None)], iid=900))
    samples.append(S(part="blind1", status="NG", defects=[D(1, True)], iid=901))
    samples.append(S(part="noimg", status="NG", defects=[D(1, True)], iid=902))
    images = lambda key: None if key == "902.png" else _png()  # noqa: E731
    st = build_dataset(samples, images, str(tmp_path / "ds"), exclude={"blind1"}, base_dir=_base(tmp_path))
    assert st.skipped_ambiguous == 1 and st.skipped_excluded == 1 and st.skipped_image == 1
    assert st.train + st.val == 40 and st.positives == 20 and st.negatives == 20
    assert st.replay_train > 0 and st.replay_val > 0
    n_tr = len(os.listdir(tmp_path / "ds" / "images" / "train"))
    assert n_tr == st.train + st.replay_train
    assert (tmp_path / "ds" / "data.yaml").exists()
    # OK-verdict samples became empty label files (hard negatives)
    empties = [f for f in os.listdir(tmp_path / "ds" / "labels" / "train")
               if f.startswith("rev_") and not (tmp_path / "ds" / "labels" / "train" / f).read_text()]
    assert empties


def test_no_part_in_both_splits_and_manifest_is_stable(tmp_path):
    a = build_dataset(_samples(), lambda k: _png(), str(tmp_path / "a"))
    b = build_dataset(_samples(), lambda k: _png(), str(tmp_path / "b"))
    assert a.summary()["manifest_sha256"] == b.summary()["manifest_sha256"]
    tr = {f for f in os.listdir(tmp_path / "a" / "images" / "train")}
    va = {f for f in os.listdir(tmp_path / "a" / "images" / "val")}
    assert not tr & va


# ---------------------------------------------------------------- gate

def M(crit=0.99, fn=0.01, prec=0.98, rec=0.93):
    return {"critical_recall": crit, "image_level": {"false_negative_rate": fn},
            "object_level": {"precision": prec, "recall": rec}, "frames": 100}


def test_gate_passes_equal_or_better():
    assert gate(M(), M())[0]
    assert gate(M(), M(crit=0.995, fn=0.005, prec=0.99))[0]


@pytest.mark.parametrize("cand,word", [
    (M(crit=0.97), "critical_recall"), (M(fn=0.03), "false_negative_rate"), (M(prec=0.90), "precision")])
def test_gate_blocks_regressions(cand, word):
    ok, why = gate(M(), cand)
    assert not ok and any(word in r for r in why)


def test_gate_treats_missing_metric_as_failure():
    assert not gate(M(), M(prec=None))[0]


# ---------------------------------------------------------------- end to end (fake training)

class FakeApi:
    def __init__(self, samples):
        self.samples = samples

    def reviewed(self):
        return self.samples

    def image(self, key):
        return _png()


def _args(tmp, **kw):
    cur = tmp / "models"
    cur.mkdir(exist_ok=True)
    (cur / "best.onnx").write_bytes(b"current-onnx")
    (cur / "best.pt").write_bytes(b"current-pt")
    base = dict(work_dir=str(tmp / "work"), registry=str(cur / "registry.json"), current_onnx=str(cur / "best.onnx"),
                current_weights=str(cur / "best.pt"), gate_images="g", gate_labels="g", template=None,
                min_new=10, val_frac=0.2, base_dataset=_base(tmp), replay_ratio=1.0, epochs=1, batch=1, device=None,
                promote=False, dry_run=False, exclude_manifest=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _train(data_yaml, weights, out, epochs, batch, device):
    os.makedirs(os.path.join(out, "w"), exist_ok=True)
    onnx, pt = os.path.join(out, "w", "m.onnx"), os.path.join(out, "w", "best.pt")
    open(onnx, "wb").write(b"candidate-onnx")
    open(pt, "wb").write(b"candidate-pt")
    return pt, onnx


def _eval_factory(cand_metrics, cur_metrics=None):
    def ev(onnx, images, labels, template):
        return cand_metrics if open(onnx, "rb").read() == b"candidate-onnx" else (cur_metrics or M())
    return ev


def test_not_enough_data_is_recorded_and_nothing_trains(tmp_path):
    called = []
    e = run_job(_args(tmp_path, min_new=100), FakeApi(_samples(40)),
                train_fn=lambda *a: called.append(1), eval_fn=_eval_factory(M()))
    assert e["status"] == "skipped_not_enough_data" and not called
    assert json.load(open(tmp_path / "models" / "registry.json"))["versions"][0]["status"] == "skipped_not_enough_data"


def test_dry_run_builds_but_does_not_train(tmp_path):
    e = run_job(_args(tmp_path, dry_run=True), FakeApi(_samples(40)),
                train_fn=lambda *a: pytest.fail("trained"), eval_fn=_eval_factory(M()))
    assert e["status"] == "dry_run" and e["dataset"]["positives"] == 20


def test_good_candidate_is_not_promoted_without_flag(tmp_path):
    e = run_job(_args(tmp_path), FakeApi(_samples(40)), train_fn=_train, eval_fn=_eval_factory(M(crit=0.995)))
    assert e["gate_passed"] and e["status"] == "candidate_gate_passed"
    assert (tmp_path / "models" / "best.onnx").read_bytes() == b"current-onnx"


def test_promotion_swaps_models_and_archives_previous(tmp_path):
    e = run_job(_args(tmp_path, promote=True), FakeApi(_samples(40)), train_fn=_train,
                eval_fn=_eval_factory(M(crit=0.995)))
    assert e["status"] == "promoted"
    m = tmp_path / "models"
    assert (m / "best.onnx").read_bytes() == b"candidate-onnx" and (m / "best.pt").read_bytes() == b"candidate-pt"
    archived = list((m / "archive").iterdir())
    assert {p.read_bytes() for p in archived} == {b"current-onnx", b"current-pt"}
    assert e["parent_onnx_sha256"] != e["candidate_onnx_sha256"]


def test_regressing_candidate_is_rejected_even_with_promote(tmp_path):
    e = run_job(_args(tmp_path, promote=True), FakeApi(_samples(40)), train_fn=_train,
                eval_fn=_eval_factory(M(crit=0.90, fn=0.05)))
    assert e["status"] == "rejected" and not e["gate_passed"] and e["gate_reasons"]
    assert (tmp_path / "models" / "best.onnx").read_bytes() == b"current-onnx"
    assert not (tmp_path / "models" / "archive").exists()


def test_registry_accumulates_runs(tmp_path):
    for _ in range(2):
        run_job(_args(tmp_path, dry_run=True), FakeApi(_samples(40)), train_fn=_train, eval_fn=_eval_factory(M()))
    assert len(Registry(str(tmp_path / "models" / "registry.json")).data["versions"]) == 2


def test_blind_manifest_is_never_trained_on(tmp_path):
    (tmp_path / "blind.txt").write_text("\n".join(f"part{i}" for i in range(0, 40, 2)) + "\n")  # every NG part
    e = run_job(_args(tmp_path, exclude_manifest=str(tmp_path / "blind.txt"), min_new=5, dry_run=True),
                FakeApi(_samples(40)), train_fn=_train, eval_fn=_eval_factory(M()))
    assert e["dataset"]["skipped_excluded"] == 20 and e["dataset"]["positives"] == 0
