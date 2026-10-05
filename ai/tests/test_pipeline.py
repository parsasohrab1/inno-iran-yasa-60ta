import json
import os
import subprocess
import sys

import numpy as np

from ai.inference.detector import IMGSZ, postprocess
from ai.training.convert_to_yolo import convert

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_postprocess_decodes_and_filters():
    nc = 7
    out = np.zeros((1, 4 + nc, 3), dtype=np.float32)
    out[0, :4, 0] = [256, 256, 50, 50]  # confident crack (model class 0)
    out[0, 4 + 0, 0] = 0.95
    out[0, :4, 1] = [260, 258, 50, 50]  # overlapping duplicate -> NMS removes
    out[0, 4 + 0, 1] = 0.80
    out[0, :4, 2] = [100, 100, 20, 20]  # below threshold
    out[0, 4 + 3, 2] = 0.4
    dets = postprocess(out, (IMGSZ * 2, IMGSZ * 2), conf=0.7)
    assert len(dets) == 1
    d = dets[0]
    assert d.class_id == 1 and d.class_name == "crack"
    assert d.bbox == [462.0, 462.0, 562.0, 562.0]  # scaled 2x
    assert 0 < d.severity_score <= 1


def test_generate_and_convert(tmp_path):
    src, dst = tmp_path / "ds", tmp_path / "yolo"
    subprocess.run([sys.executable, "-m", "ai.datagen.synthetic_data_generator",
                    "--samples", "6", "--augment", "2", "--out", str(src)],
                   cwd=ROOT, check=True, capture_output=True)
    stats = convert(str(src), str(dst))
    assert sum(stats.values()) == 8 * 6 * 2

    # augmented copies must stay in the same split as their base sample
    seen = {}
    for split in stats:
        for f in os.listdir(src / split / "images"):
            seen.setdefault(f.rsplit("_", 1)[0], set()).add(split)
    # (names are per-variant, so check labels instead) every non-healthy image has >=1 box mostly
    boxed = total = 0
    for split in stats:
        for f in os.listdir(dst / "labels" / split):
            if f.startswith("healthy"):
                assert (dst / "labels" / split / f).read_text() == ""
            else:
                total += 1
                boxed += bool((dst / "labels" / split / f).read_text().strip())
    assert boxed / total > 0.9
    assert (dst / "data.yaml").exists()
