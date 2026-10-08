"""Hardware-in-the-loop replay validation.

Replays a labelled image set through the real pipeline (camera interface -> detector -> OK/NG/Review
decision) at line pace and checks the SRS acceptance criteria that can be measured without a live line.

python -m ai.validation.hil_replay --images data/yolo/images/test --labels data/yolo/labels/test \
    --model data/models/best.onnx --rate 0 --report data/models/hil_report.json

--rate 0 replays as fast as possible (stress / throughput); --rate 30 paces like the real line.
Exit code 1 if any criterion fails. mAP@0.5 is measured separately with `yolo val` (see docs/VALIDATION_PROTOCOL.md).
"""
import argparse
import json
import os
import time
from dataclasses import dataclass

import numpy as np

from acquisition.camera import ReplayCamera

CRITICAL_IDS = {1, 4, 7}  # crack, foreign_particle, incomplete_fill (dataset ids; model idx = id - 1)
THRESHOLD, SURE = 0.7, 0.85  # keep in sync with backend settings: confidence_threshold, +review_margin

CRITERIA = {  # SRS §2-3, §5, §8
    "critical_recall_min": 0.95,
    "false_negative_rate_max": 0.01,
    "precision_min": 0.92,
    "recall_min": 0.90,
    "latency_p95_ms_max": 100.0,
    "throughput_per_min_min": 30.0,
    "uptime_min": 0.99,
}


@dataclass
class GT:
    boxes: list  # (class_id 1..7, x1, y1, x2, y2) in pixels


def load_gt(label_path: str, w: int, h: int) -> GT:
    boxes = []
    if os.path.exists(label_path):
        for line in open(label_path).read().split("\n"):
            if line.strip():
                c, cx, cy, bw, bh = line.split()
                cx, cy, bw, bh = float(cx) * w, float(cy) * h, float(bw) * w, float(bh) * h
                boxes.append((int(c) + 1, cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2))
    return GT(boxes)


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def decide(dets) -> str:
    if any(d.confidence >= SURE for d in dets):
        return "NG"
    if any(d.confidence >= THRESHOLD for d in dets):
        return "Review"
    return "OK"


def run(image_dir: str, label_dir: str, detector, rate: float = 0, limit: int | None = None) -> dict:
    cam = ReplayCamera(image_dir, limit=limit)
    period = 60.0 / rate if rate else 0.0
    lat, errors, frames = [], 0, 0
    img_tp = img_fn = img_fp = img_tn = 0
    crit_pos = crit_hit = 0
    obj_tp = obj_fp = obj_fn = 0
    t_start = time.perf_counter()

    while True:
        t0 = time.perf_counter()
        try:
            frame = cam.grab()
        except EOFError:
            break
        except IOError:
            errors += 1
            frames += 1
            continue
        frames += 1
        h, w = frame.rgb.shape[:2]
        gt = load_gt(os.path.join(label_dir, os.path.splitext(frame.name)[0] + ".txt"), w, h)
        try:
            ts = time.perf_counter()
            dets = detector.predict(frame.rgb, frame.depth)
            lat.append((time.perf_counter() - ts) * 1000)
        except Exception:
            errors += 1
            continue

        flagged = decide(dets) != "OK"  # NG or Review both stop the part for a human
        pos = bool(gt.boxes)
        img_tp += pos and flagged
        img_fn += pos and not flagged
        img_fp += (not pos) and flagged
        img_tn += (not pos) and not flagged
        if any(b[0] in CRITICAL_IDS for b in gt.boxes):
            crit_pos += 1
            crit_hit += flagged

        used = set()
        for d in (x for x in dets if x.confidence >= THRESHOLD):
            best, bi = 0.0, None
            for i, b in enumerate(gt.boxes):
                if i not in used and b[0] == d.class_id and iou(d.bbox, b[1:]) > best:
                    best, bi = iou(d.bbox, b[1:]), i
            if best >= 0.5:
                used.add(bi)
                obj_tp += 1
            else:
                obj_fp += 1
        obj_fn += len(gt.boxes) - len(used)

        if period:
            time.sleep(max(0.0, period - (time.perf_counter() - t0)))

    wall = time.perf_counter() - t_start
    n_pos = img_tp + img_fn
    div = lambda a, b: a / b if b else None  # noqa: E731
    m = {
        "frames": frames,
        "errors": errors,
        "uptime": div(frames - errors, frames),
        "throughput_per_min": div(frames, wall / 60),
        "latency_ms": {"p50": float(np.percentile(lat, 50)), "p95": float(np.percentile(lat, 95)),
                       "p99": float(np.percentile(lat, 99)), "max": float(max(lat))} if lat else None,
        "image_level": {"tp": img_tp, "fn": img_fn, "fp": img_fp, "tn": img_tn,
                        "false_negative_rate": div(img_fn, n_pos), "false_positive_rate": div(img_fp, img_fp + img_tn)},
        "critical_recall": div(crit_hit, crit_pos),
        "critical_positives": crit_pos,
        "object_level": {"precision": div(obj_tp, obj_tp + obj_fp), "recall": div(obj_tp, obj_tp + obj_fn),
                         "tp": obj_tp, "fp": obj_fp, "fn": obj_fn},
        "defective_images": n_pos,
    }
    m["checks"] = evaluate(m)
    return m


def evaluate(m: dict) -> dict:
    """Each check is True/False, or None when it could not be computed (counts as not passed)."""
    c = CRITERIA
    lat = m["latency_ms"]
    vals = {
        "critical_recall": (m["critical_recall"], ">=", c["critical_recall_min"]),
        "false_negative_rate": (m["image_level"]["false_negative_rate"], "<=", c["false_negative_rate_max"]),
        "precision": (m["object_level"]["precision"], ">=", c["precision_min"]),
        "recall": (m["object_level"]["recall"], ">=", c["recall_min"]),
        "latency_p95_ms": (lat["p95"] if lat else None, "<=", c["latency_p95_ms_max"]),
        "throughput_per_min": (m["throughput_per_min"], ">=", c["throughput_per_min_min"]),
        "uptime": (m["uptime"], ">=", c["uptime_min"]),
    }
    out = {}
    for k, (v, op, t) in vals.items():
        ok = None if v is None else (v >= t if op == ">=" else v <= t)
        out[k] = {"value": v, "target": f"{op} {t}", "pass": ok}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--model", help="ONNX model; omit to run the stub (will fail recall criteria)")
    ap.add_argument("--rate", type=float, default=0, help="parts/min pacing; 0 = max speed")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--report", default="hil_report.json")
    a = ap.parse_args()

    if a.model:
        from ai.inference.detector import OnnxDetector
        det = OnnxDetector(a.model, conf_threshold=0.5)
    else:
        from ai.inference.detector import StubDetector
        det = StubDetector()

    m = run(a.images, a.labels, det, a.rate, a.limit)
    with open(a.report, "w") as f:
        json.dump(m, f, indent=2)
    for k, v in m["checks"].items():
        print(f"{'PASS' if v['pass'] else 'FAIL' if v['pass'] is False else 'N/A '} {k}: {v['value']} (target {v['target']})")
    raise SystemExit(0 if all(v["pass"] for v in m["checks"].values()) else 1)


if __name__ == "__main__":
    main()
