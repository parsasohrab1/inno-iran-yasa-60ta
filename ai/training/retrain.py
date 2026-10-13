"""Continuous-learning job: retrain from QC-reviewed inspections (FR-2-5).

Pipeline
  1. fetch  reviewed inspections + stored images from the backend (QC/admin login)
  2. label  turn reviewer verdicts into YOLO labels (rules in `label_lines`)
  3. build  dataset = reviewed samples (+ replay of the base dataset to limit forgetting)
  4. train  fine-tune from the current production weights
  5. gate   candidate must not regress vs. the current model on a FIXED gate set and on reviewed validation parts
  6. record every run in a registry (hashes, metrics, dataset manifest, git commit)
  7. promote only when the gate passed AND --promote was given (never automatic, protocol T6)

python -m ai.training.retrain --min-new 50                 # candidate only
python -m ai.training.retrain --min-new 50 --promote       # swap best.onnx if the gate passes
python -m ai.training.retrain --dry-run                    # just build the dataset and report

Env: API_URL, API_USER, API_PASSWORD (a QC/admin account)
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

# ---------------------------------------------------------------- labelling rules


def label_lines(sample: dict, w: int, h: int) -> list[str] | None:
    """YOLO label lines for one reviewed sample, or None when the verdict is ambiguous (skip it).

    * confirmed True             -> positive box (includes defects the reviewer added)
    * confirmed False            -> dropped: a false positive; the part becomes a hard negative for that region
    * confirmed None, verdict OK -> every detection was wrong -> no boxes (background image)
    * confirmed None, verdict NG -> the reviewer did not say which boxes are real -> ambiguous
    """
    lines = []
    for d in sample["defects"]:
        c = d["confirmed"]
        if c is None:
            if sample["status"] == "NG":
                return None
            continue
        if not c:
            continue
        x1, y1, x2, y2 = (max(0.0, d["bbox"][0]), max(0.0, d["bbox"][1]),
                          min(float(w), d["bbox"][2]), min(float(h), d["bbox"][3]))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        lines.append(f"{d['class_id'] - 1} {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} "
                     f"{(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}")
    if sample["status"] == "NG" and not lines:
        return None  # NG with no usable box: nothing to learn from
    return lines


def is_val(part_id: str, val_frac: float) -> bool:
    """Deterministic split by part: the same part never appears in both train and val, and the split is stable."""
    return int(hashlib.sha1(part_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < val_frac


# ---------------------------------------------------------------- dataset


@dataclass
class BuildStats:
    train: int = 0
    val: int = 0
    positives: int = 0
    negatives: int = 0
    skipped_ambiguous: int = 0
    skipped_excluded: int = 0
    skipped_image: int = 0
    replay_train: int = 0
    replay_val: int = 0
    manifest: list = field(default_factory=list)

    def summary(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "manifest"}
        d["manifest_sha256"] = hashlib.sha256("\n".join(sorted(self.manifest)).encode()).hexdigest()
        return d


def _replay(base_dir: str, out_dir: str, split: str, n: int, rng: np.random.Generator) -> int:
    src_i, src_l = os.path.join(base_dir, "images", split), os.path.join(base_dir, "labels", split)
    if not os.path.isdir(src_i) or n <= 0:
        return 0
    files = sorted(os.listdir(src_i))
    pick = rng.choice(len(files), size=min(n, len(files)), replace=False)
    for i in pick:
        f = files[i]
        shutil.copy(os.path.join(src_i, f), os.path.join(out_dir, "images", split, "base_" + f))
        shutil.copy(os.path.join(src_l, os.path.splitext(f)[0] + ".txt"),
                    os.path.join(out_dir, "labels", split, "base_" + os.path.splitext(f)[0] + ".txt"))
    return len(pick)


def build_dataset(samples: list[dict], fetch_image, out_dir: str, *, exclude: set[str] = frozenset(),
                  val_frac: float = 0.2, base_dir: str | None = None, replay_ratio: float = 1.0,
                  seed: int = 0) -> BuildStats:
    """Writes YOLO train/val under out_dir (+ data.yaml) from reviewed samples and a replay of base data."""
    st = BuildStats()
    for split in ("train", "val"):
        os.makedirs(os.path.join(out_dir, "images", split), exist_ok=True)
        os.makedirs(os.path.join(out_dir, "labels", split), exist_ok=True)
    # The reviewed-only validation set is kept separate so the gate can measure it on its own.
    for sub in ("images", "labels"):
        os.makedirs(os.path.join(out_dir, "reviewed_val", sub), exist_ok=True)

    for s in samples:
        if s["part_id"] in exclude:
            st.skipped_excluded += 1
            continue
        data = fetch_image(s["image_key"])
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
        if img is None:
            st.skipped_image += 1
            continue
        h, w = img.shape[:2]
        lines = label_lines(s, w, h)
        if lines is None:
            st.skipped_ambiguous += 1
            continue
        split = "val" if is_val(s["part_id"], val_frac) else "train"
        name = f"rev_{s['inspection_id']}"
        cv2.imwrite(os.path.join(out_dir, "images", split, name + ".png"), img)
        with open(os.path.join(out_dir, "labels", split, name + ".txt"), "w") as fh:
            fh.write("\n".join(lines))
        if split == "val":
            cv2.imwrite(os.path.join(out_dir, "reviewed_val", "images", name + ".png"), img)
            with open(os.path.join(out_dir, "reviewed_val", "labels", name + ".txt"), "w") as fh:
                fh.write("\n".join(lines))
        st.train += split == "train"
        st.val += split == "val"
        st.positives += bool(lines)
        st.negatives += not lines
        st.manifest.append(f"{split}:{s['part_id']}:{len(lines)}")

    if base_dir:
        rng = np.random.default_rng(seed)
        st.replay_train = _replay(base_dir, out_dir, "train", int(max(st.train, 1) * replay_ratio), rng)
        st.replay_val = _replay(base_dir, out_dir, "val", int(max(st.val, 1) * replay_ratio), rng)
    names = {0: "crack", 1: "bubble", 2: "deformation", 3: "foreign_particle", 4: "discoloration",
             5: "flash", 6: "incomplete_fill"}
    import yaml

    with open(os.path.join(out_dir, "data.yaml"), "w") as fh:
        yaml.safe_dump({"path": os.path.abspath(out_dir), "train": "images/train", "val": "images/val",
                        "names": names}, fh)
    return st


# ---------------------------------------------------------------- gate


def gate(current: dict, candidate: dict, *, recall_tol: float = 0.005, fn_tol: float = 0.005,
         precision_tol: float = 0.02) -> tuple[bool, list[str]]:
    """Candidate must not be worse than the current model beyond small tolerances. Returns (passed, reasons)."""
    reasons = []

    def need(name, cur, cand, ok):
        if cur is None or cand is None:
            reasons.append(f"{name}: not comparable (current={cur}, candidate={cand})")
        elif not ok:
            reasons.append(f"{name} regressed: current={cur:.4f} candidate={cand:.4f}")

    cc, kc = current["critical_recall"], candidate["critical_recall"]
    need("critical_recall", cc, kc, kc is not None and cc is not None and kc >= cc - recall_tol)
    cf, kf = current["image_level"]["false_negative_rate"], candidate["image_level"]["false_negative_rate"]
    need("false_negative_rate", cf, kf, kf is not None and cf is not None and kf <= cf + fn_tol)
    cp, kp = current["object_level"]["precision"], candidate["object_level"]["precision"]
    need("precision", cp, kp, kp is not None and cp is not None and kp >= cp - precision_tol)
    return (not reasons), reasons


# ---------------------------------------------------------------- registry


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


class Registry:
    def __init__(self, path: str):
        self.path = path
        self.data = json.load(open(path)) if os.path.exists(path) else {"versions": []}

    def add(self, entry: dict):
        self.data["versions"].append(entry)
        self.save()

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump(self.data, fh, indent=2)


# ---------------------------------------------------------------- api / training / evaluation


class Api:
    def __init__(self, base: str, user: str, password: str):
        import httpx

        self.c = httpx.Client(base_url=base, timeout=30)
        tok = self.c.post("/api/auth/login", json={"username": user, "password": password}).raise_for_status().json()
        self.c.headers["Authorization"] = f"Bearer {tok['access_token']}"

    def reviewed(self) -> list[dict]:
        out, since = [], None
        while True:  # page by review time
            r = self.c.get("/api/training/reviewed", params={"limit": 1000, **({"since": since} if since else {})})
            r.raise_for_status()
            page = r.json()
            out += page
            if len(page) < 1000:
                return out
            since = page[-1]["reviewed_at"]

    def image(self, key: str) -> bytes | None:
        r = self.c.get(f"/api/images/{key}")
        return r.content if r.is_success else None


def train_candidate(data_yaml: str, weights: str, out_dir: str, epochs: int, batch: int, device) -> tuple[str, str]:
    """Fine-tune and export. Returns (best.pt path, onnx path). Full precision (FP16 validation breaks on TU117)."""
    from ultralytics import YOLO

    from ai.training.train_yolo import IMGSZ

    model = YOLO(weights)
    model.train(data=data_yaml, epochs=epochs, imgsz=IMGSZ, batch=batch, device=device, workers=2, amp=False,
                lr0=0.002, lrf=0.1, warmup_epochs=0, project=os.path.abspath(out_dir), name="train", exist_ok=True)
    best_pt = os.path.join(out_dir, "train", "weights", "best.pt")
    onnx = YOLO(best_pt).export(format="onnx", imgsz=IMGSZ, simplify=True, dynamic=False, device="cpu")
    return best_pt, onnx


def evaluate(onnx_path: str, images: str, labels: str, template: str | None = None) -> dict:
    from ai.inference.detector import OnnxDetector, ShapeAwareDetector
    from ai.validation.hil_replay import run

    det = OnnxDetector(onnx_path, conf_threshold=0.5)
    if template:
        from ai.inference.shape_check import ShapeTemplate

        det = ShapeAwareDetector(det, ShapeTemplate.load(template))
    return run(images, labels, det, rate=0)


# ---------------------------------------------------------------- orchestration


def run_job(args, api, *, train_fn=train_candidate, eval_fn=evaluate) -> dict:
    ts = time.strftime("%Y%m%d-%H%M%S")
    work, n = os.path.join(args.work_dir, ts), 1
    while os.path.exists(work):  # never reuse a directory: stale files would leak into the dataset
        n += 1
        work = os.path.join(args.work_dir, f"{ts}-{n}")
    ts = os.path.basename(work)
    os.makedirs(work)
    reg = Registry(args.registry)
    entry = {"id": ts, "created": ts, "git_commit": _git_commit(), "status": "started",
             "parent_onnx_sha256": sha256_file(args.current_onnx) if os.path.exists(args.current_onnx) else None}

    exclude = set()
    if args.exclude_manifest:
        exclude = {ln.strip() for ln in open(args.exclude_manifest) if ln.strip()}

    samples = api.reviewed()
    usable = [s for s in samples if s["part_id"] not in exclude]
    entry["fetched"] = len(samples)
    if len(usable) < args.min_new:
        entry.update(status="skipped_not_enough_data", reason=f"{len(usable)} usable reviewed samples < --min-new {args.min_new}")
        reg.add(entry)
        return entry

    ds = os.path.join(work, "dataset")
    st = build_dataset(samples, api.image, ds, exclude=exclude, val_frac=args.val_frac,
                       base_dir=args.base_dataset, replay_ratio=args.replay_ratio)
    entry["dataset"] = st.summary()
    if st.train < args.min_new // 2 or st.val < 5:
        entry.update(status="skipped_not_enough_labelled",
                     reason=f"train={st.train} val={st.val} after skipping ambiguous/excluded samples")
        reg.add(entry)
        return entry
    if args.dry_run:
        entry["status"] = "dry_run"
        reg.add(entry)
        return entry

    best_pt, onnx = train_fn(os.path.join(ds, "data.yaml"), args.current_weights, work, args.epochs, args.batch, args.device)
    cand_onnx = os.path.join(work, "candidate.onnx")
    shutil.copy(onnx, cand_onnx)
    entry["candidate_onnx"], entry["candidate_onnx_sha256"] = cand_onnx, sha256_file(cand_onnx)

    cur_g = eval_fn(args.current_onnx, args.gate_images, args.gate_labels, args.template)
    new_g = eval_fn(cand_onnx, args.gate_images, args.gate_labels, args.template)
    ok_g, why = gate(cur_g, new_g)
    entry["gate_set"] = {"current": _slim(cur_g), "candidate": _slim(new_g)}
    rv_i, rv_l = os.path.join(ds, "reviewed_val", "images"), os.path.join(ds, "reviewed_val", "labels")
    ok_v = True
    if st.val >= 10:
        cur_v, new_v = (eval_fn(m, rv_i, rv_l, args.template) for m in (args.current_onnx, cand_onnx))
        ok_v, why_v = gate(cur_v, new_v)
        entry["reviewed_val"] = {"current": _slim(cur_v), "candidate": _slim(new_v)}
        why += [f"reviewed-val: {r}" for r in why_v]
    entry["gate_passed"], entry["gate_reasons"] = ok_g and ok_v, why

    if entry["gate_passed"] and args.promote:
        archive = os.path.join(os.path.dirname(args.current_onnx), "archive")
        os.makedirs(archive, exist_ok=True)
        if os.path.exists(args.current_onnx):
            shutil.copy(args.current_onnx, os.path.join(archive, f"{entry['parent_onnx_sha256'][:12]}.onnx"))
            if os.path.exists(args.current_weights):
                shutil.copy(args.current_weights, os.path.join(archive, f"{entry['parent_onnx_sha256'][:12]}.pt"))
        shutil.copy(cand_onnx, args.current_onnx)
        shutil.copy(best_pt, args.current_weights)
        entry["status"] = "promoted"
    else:
        entry["status"] = "candidate_gate_passed" if entry["gate_passed"] else "rejected"
    reg.add(entry)
    return entry


def _slim(m: dict) -> dict:
    return {"critical_recall": m["critical_recall"], "false_negative_rate": m["image_level"]["false_negative_rate"],
            "precision": m["object_level"]["precision"], "recall": m["object_level"]["recall"],
            "frames": m["frames"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api-url", default=os.getenv("API_URL", "http://localhost:8000"))
    ap.add_argument("--min-new", type=int, default=50, help="minimum usable reviewed samples before training")
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--replay-ratio", type=float, default=1.0, help="base-dataset images per reviewed image")
    ap.add_argument("--base-dataset", default="data/yolo_v2")
    ap.add_argument("--gate-images", default="data/yolo_v2/images/test")
    ap.add_argument("--gate-labels", default="data/yolo_v2/labels/test")
    ap.add_argument("--template", default=None, help="shape template used in gate evaluation (match production)")
    ap.add_argument("--exclude-manifest", help="file of part_ids (one per line) that must never be trained on, "
                                               "e.g. the frozen blind test set")
    ap.add_argument("--current-weights", default="data/models/best.pt")
    ap.add_argument("--current-onnx", default="data/models/best.onnx")
    ap.add_argument("--registry", default="data/models/registry.json")
    ap.add_argument("--work-dir", default="data/retrain")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--device", default=None)
    ap.add_argument("--promote", action="store_true", help="swap in the candidate if the gate passes")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    api = Api(a.api_url, os.getenv("API_USER", "admin"), os.getenv("API_PASSWORD", "admin"))
    e = run_job(a, api)
    print(json.dumps({k: e[k] for k in e if k in ("id", "status", "reason", "gate_passed", "gate_reasons", "dataset")}, indent=2))


if __name__ == "__main__":
    main()
