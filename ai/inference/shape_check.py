"""Shape check against a reference template (FR-1-5, FR-2-1 deformation).

Training-free geometric inspection for ring-shaped parts (gaskets, O-rings):
  1. segment the part from the background,
  2. describe its silhouette as outer/inner radial profiles (size-normalised, mean-removed),
  3. learn per-angle mean/std from healthy parts -> the reference template,
  4. flag parts whose profile leaves the template's tolerance band.

Complements the CNN detector: a global shape change (dent, bulge, ovalisation) has no local texture
signature, which is why box detectors miss it.

Assumes a controlled imaging station (SRS 2-4: controlled lighting, backlight): the part must contrast
with a uniform background. When segmentation is implausible the check *abstains* ("unsegmentable") instead of
guessing, so callers can route those parts to a human.

CLI:  python -m ai.inference.shape_check fit --images data/yolo_v2/images/train \
          --labels data/yolo_v2/labels/train --out data/models/ring_template.json
"""
import argparse
import json
import os
from dataclasses import dataclass, field

import cv2
import numpy as np

N_BINS = 90
MIN_FILL, MAX_FILL = 0.05, 0.60  # plausible foreground fraction of the frame
MIN_HOLE = 0.01  # hole must exceed this fraction of the part area


@dataclass
class ShapeResult:
    status: str  # "ok" | "anomaly" | "unsegmentable"
    score: float | None = None  # >= 1.0 means outside tolerance
    bbox: list[float] | None = None  # whole part, x1 y1 x2 y2 in pixels
    worst_angle_deg: float | None = None
    details: dict = field(default_factory=dict)


def segment(rgb: np.ndarray) -> np.ndarray | None:
    """Foreground mask (uint8 0/1) by colour distance from the border-median background, or None if implausible."""
    h, w = rgb.shape[:2]
    b = 8
    border = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3),
                             rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3)])
    bg = np.median(border, 0)
    d = np.linalg.norm(cv2.GaussianBlur(rgb, (7, 7), 0).astype(np.float32) - bg, axis=2)
    # Clip at the 90th percentile: the part covers well over 10 % of the frame, so small bright/dark
    # defects (bubbles, particles) must not stretch the contrast scale and hijack Otsu.
    cap = float(np.percentile(d, 90))
    if cap < 4:  # no contrast at all
        return None
    d8 = np.clip(np.minimum(d, cap) * (255.0 / cap), 0, 255).astype(np.uint8)
    d8 = cv2.medianBlur(d8, 5)  # suppress speckle on low-contrast, noisy frames
    t, _ = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    fg = (d8 > t).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fg = cv2.morphologyEx(cv2.morphologyEx(fg, cv2.MORPH_OPEN, k), cv2.MORPH_CLOSE, k)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(fg)
    if n < 2:
        return None
    fg = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    if not (MIN_FILL <= fg.mean() <= MAX_FILL):
        return None
    return fg


SMOOTH_BINS = 5  # 20 deg at 90 bins; real dents/ovalisation span >= ~40 deg, edge noise does not


def _smooth(x: np.ndarray, w: int = SMOOTH_BINS) -> np.ndarray:
    return sum(np.roll(x, i) for i in range(-(w // 2), w // 2 + 1)) / w


def features(rgb: np.ndarray, n_bins: int = N_BINS) -> tuple[dict, list[float]] | None:
    """Returns ({outer_dev, inner_dev, ratio, concentricity}, part bbox) or None when unsegmentable."""
    fg = segment(rgb)
    if fg is None:
        return None
    h, w = fg.shape
    cnts, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    outer_c = max(cnts, key=cv2.contourArea)
    filled = np.zeros_like(fg)
    cv2.drawContours(filled, [outer_c], -1, 1, -1)
    hole = (filled & (1 - fg)).astype(np.uint8)
    n, lab, stats, cents = cv2.connectedComponentsWithStats(hole)
    if n < 2:
        return None
    hi = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[hi, cv2.CC_STAT_AREA] < MIN_HOLE * filled.sum():
        return None  # not an annulus
    m = cv2.moments(filled, binaryImage=True)
    c = (m["m10"] / m["m00"], m["m01"] / m["m00"])
    r_eq = float(np.sqrt(filled.sum() / np.pi))
    r_max = int(min(h, w) // 2)
    # WARP_FILL_OUTLIERS: without it, samples falling outside the frame are left undefined (non-deterministic)
    pol = cv2.warpPolar(fg, (r_max, n_bins), c, r_max,
                        cv2.INTER_NEAREST + cv2.WARP_POLAR_LINEAR + cv2.WARP_FILL_OUTLIERS)
    outer = np.zeros(n_bins)
    inner = np.zeros(n_bins)
    for i in range(n_bins):
        nz = np.flatnonzero(pol[i])
        if nz.size == 0:
            return None
        outer[i], inner[i] = nz[-1], nz[0]
    if np.any(outer < 2) or np.any(inner < 1):
        return None
    f = {
        "outer_dev": _smooth(outer / outer.mean() - 1.0),
        "inner_dev": _smooth(inner / inner.mean() - 1.0),
        "ratio": float(inner.mean() / outer.mean()),
        "concentricity": float(np.hypot(cents[hi][0] - c[0], cents[hi][1] - c[1]) / r_eq),
    }
    x, y, bw, bh = cv2.boundingRect(outer_c)
    return f, [float(x), float(y), float(x + bw), float(y + bh)]


class ShapeTemplate:
    """Per-angle mean/std of healthy silhouettes plus tolerance rules."""

    def __init__(self, n_bins: int = N_BINS, k: float = 4.0, tol: float = 0.04,
                 tol_ratio: float = 0.08, tol_conc: float = 0.03):
        self.n_bins, self.k, self.tol, self.tol_ratio, self.tol_conc = n_bins, k, tol, tol_ratio, tol_conc
        self.mean: dict | None = None
        self.std: dict | None = None
        self.n_fit = 0

    def fit(self, images, min_samples: int = 20) -> "ShapeTemplate":
        feats = [r[0] for r in (features(im, self.n_bins) for im in images) if r]
        if len(feats) < min_samples:
            raise ValueError(f"need >= {min_samples} segmentable healthy parts, got {len(feats)}")
        self.n_fit = len(feats)
        self.mean, self.std = {}, {}
        for key in ("outer_dev", "inner_dev"):
            a = np.stack([f[key] for f in feats])
            self.mean[key], self.std[key] = a.mean(0), a.std(0)
        for key in ("ratio", "concentricity"):
            a = np.array([f[key] for f in feats])
            self.mean[key], self.std[key] = float(a.mean()), float(a.std())
        return self

    def check(self, rgb: np.ndarray) -> ShapeResult:
        if self.mean is None:
            raise RuntimeError("template not fitted")
        r = features(rgb, self.n_bins)
        if r is None:
            return ShapeResult("unsegmentable")
        f, bbox = r
        zs, worst = {}, {}
        for key in ("outer_dev", "inner_dev"):
            z = np.abs(f[key] - self.mean[key]) / np.maximum(self.k * self.std[key], self.tol)
            zs[key], worst[key] = float(z.max()), int(z.argmax())
        zs["ratio"] = abs(f["ratio"] - self.mean["ratio"]) / max(self.k * self.std["ratio"], self.tol_ratio)
        zs["concentricity"] = abs(f["concentricity"] - self.mean["concentricity"]) / max(
            self.k * self.std["concentricity"], self.tol_conc)
        score = max(zs.values())
        dom = max(zs, key=zs.get)
        angle = worst[dom] * 360.0 / self.n_bins if dom in worst else None
        return ShapeResult("anomaly" if score >= 1.0 else "ok", float(score), bbox, angle,
                           {"z": zs, "dominant": dom})

    def save(self, path: str):
        d = {"n_bins": self.n_bins, "k": self.k, "tol": self.tol, "tol_ratio": self.tol_ratio,
             "tol_conc": self.tol_conc, "n_fit": self.n_fit,
             "mean": {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in self.mean.items()},
             "std": {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in self.std.items()}}
        with open(path, "w") as fh:
            json.dump(d, fh)

    @classmethod
    def load(cls, path: str) -> "ShapeTemplate":
        d = json.load(open(path))
        t = cls(d["n_bins"], d["k"], d["tol"], d["tol_ratio"], d["tol_conc"])
        t.n_fit = d["n_fit"]
        conv = lambda m: {k: (np.array(v) if isinstance(v, list) else v) for k, v in m.items()}  # noqa: E731
        t.mean, t.std = conv(d["mean"]), conv(d["std"])
        return t


def _healthy_images(img_dir: str, label_dir: str, limit: int):
    """Healthy = image whose YOLO label file is empty."""
    n = 0
    for f in sorted(os.listdir(img_dir)):
        lp = os.path.join(label_dir, os.path.splitext(f)[0] + ".txt")
        if os.path.exists(lp) and not open(lp).read().strip():
            yield cv2.cvtColor(cv2.imread(os.path.join(img_dir, f)), cv2.COLOR_BGR2RGB)
            n += 1
            if n >= limit:
                return


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fit", help="build the reference template from healthy parts")
    p.add_argument("--images", required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--limit", type=int, default=400)
    a = ap.parse_args()
    t = ShapeTemplate().fit(_healthy_images(a.images, a.labels, a.limit))
    t.save(a.out)
    print(f"template fitted on {t.n_fit} healthy parts -> {a.out}")


if __name__ == "__main__":
    main()
