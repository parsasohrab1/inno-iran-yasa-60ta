"""Convert the generated dataset to Ultralytics YOLO detection format.

Boxes come from the *defect mask* (not label JSON): the mask is transformed together with the
image during augmentation, whereas JSON boxes are only valid for un-augmented samples.

Usage: python -m ai.training.convert_to_yolo --src synthetic_rubber_dataset --dst yolo_dataset
"""
import argparse
import os
import shutil

import cv2
import yaml

from ai.inference.detector import CLASS_MAP

MIN_AREA = 20  # px; ignore specks


def boxes_from_mask(mask_path: str):
    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    if mask is None:
        return [], (0, 0)
    h, w = mask.shape
    mask = cv2.dilate(mask, None, iterations=2)  # merge fragments of one defect (cracks)
    cnts, _ = cv2.findContours((mask > 127).astype("uint8"), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw * bh >= MIN_AREA:
            out.append(((x + bw / 2) / w, (y + bh / 2) / h, bw / w, bh / h))
    return out, (w, h)


def convert(src: str, dst: str) -> dict:
    import json

    stats = {}
    for split in ("train", "val", "test"):
        img_dir = os.path.join(src, split, "images")
        if not os.path.isdir(img_dir):
            continue
        os.makedirs(os.path.join(dst, "images", split), exist_ok=True)
        os.makedirs(os.path.join(dst, "labels", split), exist_ok=True)
        n = 0
        for f in sorted(os.listdir(img_dir)):
            base = f[:-4]
            with open(os.path.join(src, split, "labels", base + ".json"), encoding="utf-8") as fh:
                class_id = json.load(fh)["class_id"]
            shutil.copy(os.path.join(img_dir, f), os.path.join(dst, "images", split, f))
            lines = []
            if class_id != 0:  # healthy -> empty label file (background)
                boxes, _ = boxes_from_mask(os.path.join(src, split, "masks", base + "_def.png"))
                lines = [f"{class_id - 1} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for cx, cy, w, h in boxes]
            with open(os.path.join(dst, "labels", split, base + ".txt"), "w") as out:
                out.write("\n".join(lines))
            n += 1
        stats[split] = n

    data = {
        "path": os.path.abspath(dst),
        "train": "images/train", "val": "images/val", "test": "images/test",
        "names": {i - 1: name for i, name in CLASS_MAP.items() if i > 0},
    }
    with open(os.path.join(dst, "data.yaml"), "w") as fh:
        yaml.safe_dump(data, fh)
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="synthetic_rubber_dataset")
    ap.add_argument("--dst", default="yolo_dataset")
    a = ap.parse_args()
    print(convert(a.src, a.dst))
