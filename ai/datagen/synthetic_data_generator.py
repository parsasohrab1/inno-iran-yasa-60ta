"""
Synthetic Data Generator for Rubber Product Defect Detection
------------------------------------------------------------
Generates:
  - 2D RGB images (512x512)
  - Depth maps (256x256)
  - Segmentation masks
  - JSON metadata
Classes (8):
  0: healthy, 1: crack, 2: bubble, 3: deformation,
  4: foreign_particle, 5: discoloration, 6: flash, 7: incomplete_fill
"""

import os
import json
import random
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFilter
from tqdm import tqdm
from scipy.ndimage import gaussian_filter, map_coordinates
from skimage.draw import polygon, disk, line

# ================== CONFIG ==================
SEED = 42
random.seed(SEED)
np.random.seed(SEED)

IMG_SIZE = 512
DEPTH_SIZE = 256
SAMPLES_PER_CLASS = 2500          # 8 classes -> 20,000 samples
OUTPUT_DIR = "./synthetic_rubber_dataset"
AUGMENT_FACTOR = 4                # offline augmentation multiplier

CLASS_MAP = {
    0: "healthy",
    1: "crack",
    2: "bubble",
    3: "deformation",
    4: "foreign_particle",
    5: "discoloration",
    6: "flash",
    7: "incomplete_fill",
}
NUM_CLASSES = len(CLASS_MAP)

# ================== UTILS ==================
def ensure_dirs():
    for split in ["train", "val", "test"]:
        for sub in ["images", "depth", "masks", "labels"]:
            os.makedirs(os.path.join(OUTPUT_DIR, split, sub), exist_ok=True)

def random_lighting(img):
    """Apply random brightness/contrast to simulate lighting variation."""
    alpha = np.random.uniform(0.85, 1.15)   # contrast
    beta = np.random.uniform(-20, 20)       # brightness
    img = cv2.convertScaleAbs(img, alpha=alpha, beta=beta)
    # Add slight color temperature shift
    b, g, r = cv2.split(img.astype(np.float32))
    b *= np.random.uniform(0.95, 1.05)
    r *= np.random.uniform(0.95, 1.05)
    img = cv2.merge([b, g, r])
    return np.clip(img, 0, 255).astype(np.uint8)

def add_gaussian_noise(img, sigma=5):
    noise = np.random.normal(0, sigma, img.shape).astype(np.float32)
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)

def apply_blur(img, ksize=3):
    return cv2.GaussianBlur(img, (ksize, ksize), 0)

# ================== BASE PRODUCT ==================
def draw_base_gasket(size=IMG_SIZE):
    """
    Draw a synthetic rubber gasket (circular ring) with random variations.
    Returns: rgb image (uint8), base mask (uint8), depth map (float32)
    """
    img = np.zeros((size, size, 3), dtype=np.uint8)

    # Random background
    bg_color = np.random.randint(20, 60, size=3).tolist()
    img[:] = bg_color

    # Gasket geometry
    cx = size // 2 + np.random.randint(-15, 15)
    cy = size // 2 + np.random.randint(-15, 15)
    outer_r = np.random.randint(int(size * 0.35), int(size * 0.45))
    inner_r = int(outer_r * np.random.uniform(0.55, 0.75))
    thickness_jitter = np.random.uniform(0.9, 1.1)

    # Base color of rubber (dark gray / black with slight variation)
    base_color = np.random.randint(25, 55, size=3).tolist()

    # Draw ring using cv2
    cv2.circle(img, (cx, cy), outer_r, base_color, -1)
    cv2.circle(img, (cx, cy), inner_r, bg_color, -1)

    # Soft edges (anti-alias)
    img = cv2.GaussianBlur(img, (5, 5), 0)

    # Add texture (rubber surface noise)
    texture = np.random.normal(0, 8, (size, size, 1)).astype(np.float32)
    img = np.clip(img.astype(np.float32) + texture, 0, 255).astype(np.uint8)

    # Mask (region of gasket)
    mask = np.zeros((size, size), dtype=np.uint8)
    cv2.circle(mask, (cx, cy), outer_r, 255, -1)
    cv2.circle(mask, (cx, cy), inner_r, 0, -1)

    # Depth map: outer ring is higher, inner hole is background
    depth = np.zeros((DEPTH_SIZE, DEPTH_SIZE), dtype=np.float32)
    scale = DEPTH_SIZE / size
    cv2.circle(depth, (int(cx * scale), int(cy * scale)),
               int(outer_r * scale), 1.0, -1)
    cv2.circle(depth, (int(cx * scale), int(cy * scale)),
               int(inner_r * scale), 0.0, -1)
    depth = gaussian_filter(depth, sigma=1.5)
    # Add small height variation on the ring
    depth += gaussian_filter(np.random.normal(0, 0.02, depth.shape).astype(np.float32),
                             sigma=2)
    depth = np.clip(depth, 0, 1)

    meta = {
        "center": [cx, cy],
        "outer_r": outer_r,
        "inner_r": inner_r,
        "base_color": base_color,
    }
    return img, mask, depth, meta

# ================== DEFECT INJECTORS ==================
def inject_crack(img, mask, depth, meta):
    """Add crack defect (dark thin irregular line)."""
    cx, cy = meta["center"]
    outer_r, inner_r = meta["outer_r"], meta["inner_r"]
    num_cracks = np.random.randint(1, 4)
    bbox_list, mask_defect = [], np.zeros(mask.shape, dtype=np.uint8)

    for _ in range(num_cracks):
        # random point on the ring
        ang = np.random.uniform(0, 2 * np.pi)
        r = np.random.uniform(inner_r + 10, outer_r - 10)
        x0 = int(cx + r * np.cos(ang))
        y0 = int(cy + r * np.sin(ang))

        # random crack path
        length = np.random.randint(20, 70)
        angle = np.random.uniform(0, 2 * np.pi)
        pts = []
        cur_ang = angle
        for i in range(length):
            cur_ang += np.random.normal(0, 0.3)
            x = int(x0 + i * np.cos(cur_ang))
            y = int(y0 + i * np.sin(cur_ang))
            if 0 <= x < IMG_SIZE and 0 <= y < IMG_SIZE:
                pts.append((x, y))
        if len(pts) < 2:
            continue
        # Draw crack
        thickness = np.random.randint(1, 3)
        for i in range(len(pts) - 1):
            cv2.line(img, pts[i], pts[i + 1], (5, 5, 5), thickness)
            cv2.line(mask_defect, pts[i], pts[i + 1], 255, thickness)
        # Update depth: crack lowers height
        scale = DEPTH_SIZE / IMG_SIZE
        for (x, y) in pts:
            dx, dy = int(x * scale), int(y * scale)
            if 0 <= dx < DEPTH_SIZE and 0 <= dy < DEPTH_SIZE:
                depth[dy, dx] -= 0.15
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        bbox_list.append([min(xs), min(ys), max(xs), max(ys)])

    depth = np.clip(depth, 0, 1)
    return img, depth, mask_defect, bbox_list

def inject_bubble(img, mask, depth, meta):
    """Add bubbles / porosity (bright or dark circular spots)."""
    cx, cy = meta["center"]
    outer_r, inner_r = meta["outer_r"], meta["inner_r"]
    n = np.random.randint(2, 8)
    bbox_list, mask_defect = [], np.zeros(mask.shape, dtype=np.uint8)

    for _ in range(n):
        ang = np.random.uniform(0, 2 * np.pi)
        r = np.random.uniform(inner_r + 5, outer_r - 5)
        x = int(cx + r * np.cos(ang))
        y = int(cy + r * np.sin(ang))
        radius = np.random.randint(3, 12)
        color_val = np.random.choice([200, 220, 10, 30])
        cv2.circle(img, (x, y), radius, (int(color_val),) * 3, -1)
        cv2.circle(mask_defect, (x, y), radius, 255, -1)
        # Depth: bubble raises or lowers
        scale = DEPTH_SIZE / IMG_SIZE
        dx, dy = int(x * scale), int(y * scale)
        rr = max(1, int(radius * scale))
        yy, xx = np.ogrid[-rr:rr + 1, -rr:rr + 1]
        circle_mask = xx ** 2 + yy ** 2 <= rr ** 2
        y0, y1 = max(0, dy - rr), min(DEPTH_SIZE, dy + rr + 1)
        x0, x1 = max(0, dx - rr), min(DEPTH_SIZE, dx + rr + 1)
        sub = depth[y0:y1, x0:x1]
        cm = circle_mask[:sub.shape[0], :sub.shape[1]]
        depth[y0:y1, x0:x1] += (np.random.choice([-1, 1]) * 0.1) * cm
        bbox_list.append([x - radius, y - radius, x + radius, y + radius])

    depth = np.clip(depth, 0, 1)
    return img, depth, mask_defect, bbox_list

def inject_deformation(img, mask, depth, meta):
    """Add deformation: local geometric warp of the ring."""
    cx, cy = meta["center"]
    outer_r, inner_r = meta["outer_r"], meta["inner_r"]
    # Elastic-like deformation on the whole image
    h, w = img.shape[:2]
    amp = np.random.uniform(6, 15)  # peak displacement in px (smoothed noise alone is ~0.1 px)

    def _field():
        f = gaussian_filter(np.random.normal(0, 1, (h, w)), sigma=25)
        return f / (np.abs(f).max() + 1e-8) * amp

    dx, dy = _field(), _field()
    xx, yy = np.meshgrid(np.arange(w), np.arange(h))
    coords = np.array([yy + dy, xx + dx])
    img = cv2.remap(img, (xx + dx).astype(np.float32),
                    (yy + dy).astype(np.float32),
                    interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    mask_new = cv2.remap(mask, (xx + dx).astype(np.float32),
                         (yy + dy).astype(np.float32),
                         interpolation=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REFLECT)

    # Depth warp
    scale = DEPTH_SIZE / IMG_SIZE
    xs = np.arange(DEPTH_SIZE)
    ys = np.arange(DEPTH_SIZE)
    dxx, dyy = np.meshgrid(xs, ys)
    ddx = cv2.resize(dx, (DEPTH_SIZE, DEPTH_SIZE)) * scale
    ddy = cv2.resize(dy, (DEPTH_SIZE, DEPTH_SIZE)) * scale
    depth = cv2.remap(depth, (dxx + ddx).astype(np.float32),
                      (dyy + ddy).astype(np.float32),
                      interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    depth = np.clip(depth, 0, 1)

    # Mask defect = difference between original and deformed
    mask_defect = cv2.absdiff(mask, mask_new)
    _, mask_defect = cv2.threshold(mask_defect, 30, 255, cv2.THRESH_BINARY)
    bbox_list = []
    cnts, _ = cv2.findContours(mask_defect, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        if cv2.contourArea(c) > 50:
            x, y, ww, hh = cv2.boundingRect(c)
            bbox_list.append([x, y, x + ww, y + hh])
    return img, depth, mask_defect, bbox_list

def inject_foreign_particle(img, mask, depth, meta):
    """Add foreign particles (metal/plastic bits) on the product."""
    cx, cy = meta["center"]
    outer_r, inner_r = meta["outer_r"], meta["inner_r"]
    n = np.random.randint(1, 5)
    bbox_list, mask_defect = [], np.zeros(mask.shape, dtype=np.uint8)

    for _ in range(n):
        ang = np.random.uniform(0, 2 * np.pi)
        r = np.random.uniform(inner_r + 5, outer_r - 5)
        x = int(cx + r * np.cos(ang))
        y = int(cy + r * np.sin(ang))
        # Random polygon shape
        npts = np.random.randint(3, 7)
        pts = []
        for _ in range(npts):
            px = x + np.random.randint(-8, 8)
            py = y + np.random.randint(-8, 8)
            pts.append((px, py))
        pts = np.array(pts, dtype=np.int32)
        color = tuple(np.random.randint(150, 255, size=3).tolist())
        cv2.fillPoly(img, [pts], color)
        cv2.fillPoly(mask_defect, [pts], 255)
        # Depth: particle protrudes
        scale = DEPTH_SIZE / IMG_SIZE
        for (px, py) in pts:
            dx, dy = int(px * scale), int(py * scale)
            if 0 <= dx < DEPTH_SIZE and 0 <= dy < DEPTH_SIZE:
                depth[dy, dx] += 0.2
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        bbox_list.append([min(xs), min(ys), max(xs), max(ys)])

    depth = np.clip(depth, 0, 1)
    return img, depth, mask_defect, bbox_list

def inject_discoloration(img, mask, depth, meta):
    """Add discoloration (patches with different hue/brightness)."""
    cx, cy = meta["center"]
    outer_r, inner_r = meta["outer_r"], meta["inner_r"]
    n = np.random.randint(1, 4)
    bbox_list, mask_defect = [], np.zeros(mask.shape, dtype=np.uint8)

    for _ in range(n):
        ang = np.random.uniform(0, 2 * np.pi)
        r = np.random.uniform(inner_r, outer_r)
        x = int(cx + r * np.cos(ang))
        y = int(cy + r * np.sin(ang))
        axes = (np.random.randint(15, 50), np.random.randint(15, 50))
        angle = np.random.randint(0, 180)
        color = tuple(np.random.randint(60, 150, size=3).tolist())
        cv2.ellipse(img, (x, y), axes, angle, 0, 360, color, -1)
        cv2.ellipse(mask_defect, (x, y), axes, angle, 0, 360, 255, -1)
        bbox_list.append([x - axes[0], y - axes[1], x + axes[0], y + axes[1]])

    # Discoloration does not affect depth
    return img, depth, mask_defect, bbox_list

def inject_flash(img, mask, depth, meta):
    """Add flash (excess rubber at edges)."""
    cx, cy = meta["center"]
    outer_r, inner_r = meta["outer_r"], meta["inner_r"]
    n = np.random.randint(1, 4)
    bbox_list, mask_defect = [], np.zeros(mask.shape, dtype=np.uint8)

    for _ in range(n):
        ang = np.random.uniform(0, 2 * np.pi)
        # place flash on outer edge
        arc_len = np.random.randint(20, 60)
        start_ang = ang
        end_ang = ang + np.random.uniform(0.2, 0.6)
        # Draw arc-shaped blob
        pts = []
        for a in np.linspace(start_ang, end_ang, 20):
            rr = outer_r + np.random.randint(3, 12)
            px = int(cx + rr * np.cos(a))
            py = int(cy + rr * np.sin(a))
            pts.append((px, py))
        for a in np.linspace(end_ang, start_ang, 20):
            rr = outer_r - 2
            px = int(cx + rr * np.cos(a))
            py = int(cy + rr * np.sin(a))
            pts.append((px, py))
        pts = np.array(pts, dtype=np.int32)
        color = tuple(np.random.randint(25, 55, size=3).tolist())
        cv2.fillPoly(img, [pts], color)
        cv2.fillPoly(mask_defect, [pts], 255)
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        bbox_list.append([min(xs), min(ys), max(xs), max(ys)])

    return img, depth, mask_defect, bbox_list

def inject_incomplete_fill(img, mask, depth, meta):
    """Add incomplete fill (missing chunk of rubber)."""
    cx, cy = meta["center"]
    outer_r, inner_r = meta["outer_r"], meta["inner_r"]
    # Select a random sector to remove
    start_ang = np.random.uniform(0, 2 * np.pi)
    end_ang = start_ang + np.random.uniform(0.3, 1.2)
    pts = []
    for a in np.linspace(start_ang, end_ang, 30):
        pts.append((int(cx + (outer_r + 5) * np.cos(a)),
                    int(cy + (outer_r + 5) * np.sin(a))))
    for a in np.linspace(end_ang, start_ang, 30):
        pts.append((int(cx + (inner_r - 5) * np.cos(a)),
                    int(cy + (inner_r - 5) * np.sin(a))))
    pts = np.array(pts, dtype=np.int32)

    # Fill with background color
    bg_color = tuple(np.random.randint(20, 60, size=3).tolist())
    cv2.fillPoly(img, [pts], bg_color)

    mask_defect = np.zeros(mask.shape, dtype=np.uint8)
    cv2.fillPoly(mask_defect, [pts], 255)
    # Remove from product mask
    mask = cv2.bitwise_and(mask, cv2.bitwise_not(mask_defect))

    # Depth: remove region
    scale = DEPTH_SIZE / IMG_SIZE
    pts_d = (pts * scale).astype(np.int32)
    cv2.fillPoly(depth, [pts_d], 0.0)

    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    bbox_list = [[min(xs), min(ys), max(xs), max(ys)]]
    return img, depth, mask_defect, bbox_list, mask

# ================== SAMPLE GENERATOR ==================
def generate_sample(class_id):
    img, mask, depth, meta = draw_base_gasket()
    bbox_list, mask_defect = [], np.zeros(mask.shape, dtype=np.uint8)

    if class_id == 0:  # healthy
        pass
    elif class_id == 1:
        img, depth, mask_defect, bbox_list = inject_crack(img, mask, depth, meta)
    elif class_id == 2:
        img, depth, mask_defect, bbox_list = inject_bubble(img, mask, depth, meta)
    elif class_id == 3:
        img, depth, mask_defect, bbox_list = inject_deformation(img, mask, depth, meta)
    elif class_id == 4:
        img, depth, mask_defect, bbox_list = inject_foreign_particle(img, mask, depth, meta)
    elif class_id == 5:
        img, depth, mask_defect, bbox_list = inject_discoloration(img, mask, depth, meta)
    elif class_id == 6:
        img, depth, mask_defect, bbox_list = inject_flash(img, mask, depth, meta)
    elif class_id == 7:
        img, depth, mask_defect, bbox_list, mask = inject_incomplete_fill(
            img, mask, depth, meta)

    # Post-processing
    img = random_lighting(img)
    img = add_gaussian_noise(img, sigma=np.random.uniform(2, 7))
    if np.random.rand() < 0.4:
        img = apply_blur(img, ksize=3)

    # Ensure depth shape
    depth = cv2.resize(depth, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_LINEAR)

    # Metadata
    label = {
        "class_id": class_id,
        "class_name": CLASS_MAP[class_id],
        # NOTE: boxes describe the un-augmented sample only; derive boxes from *_def.png masks for training
        "bboxes": [[int(v) for v in b] for b in bbox_list],
        "image_size": [IMG_SIZE, IMG_SIZE],
    }
    return img, depth, mask, mask_defect, label

# ================== AUGMENTATION ==================
def augment(img, depth, mask, mask_defect):
    """Apply random augmentation."""
    # Random flip
    if np.random.rand() < 0.5:
        img = cv2.flip(img, 1); depth = cv2.flip(depth, 1)
        mask = cv2.flip(mask, 1); mask_defect = cv2.flip(mask_defect, 1)
    if np.random.rand() < 0.5:
        img = cv2.flip(img, 0); depth = cv2.flip(depth, 0)
        mask = cv2.flip(mask, 0); mask_defect = cv2.flip(mask_defect, 0)
    # Random rotation
    if np.random.rand() < 0.6:
        ang = np.random.uniform(-25, 25)
        M = cv2.getRotationMatrix2D((IMG_SIZE / 2, IMG_SIZE / 2), ang, 1.0)
        img = cv2.warpAffine(img, M, (IMG_SIZE, IMG_SIZE), borderMode=cv2.BORDER_REFLECT)
        depth = cv2.warpAffine(depth, M, (IMG_SIZE, IMG_SIZE), borderMode=cv2.BORDER_REFLECT)
        mask = cv2.warpAffine(mask, M, (IMG_SIZE, IMG_SIZE), borderMode=cv2.BORDER_REFLECT)
        mask_defect = cv2.warpAffine(mask_defect, M, (IMG_SIZE, IMG_SIZE),
                                     borderMode=cv2.BORDER_REFLECT)
    # Random scale
    if np.random.rand() < 0.4:
        s = np.random.uniform(0.9, 1.1)
        M = cv2.getRotationMatrix2D((IMG_SIZE / 2, IMG_SIZE / 2), 0, s)
        img = cv2.warpAffine(img, M, (IMG_SIZE, IMG_SIZE), borderMode=cv2.BORDER_REFLECT)
        depth = cv2.warpAffine(depth, M, (IMG_SIZE, IMG_SIZE), borderMode=cv2.BORDER_REFLECT)
        mask = cv2.warpAffine(mask, M, (IMG_SIZE, IMG_SIZE), borderMode=cv2.BORDER_REFLECT)
        mask_defect = cv2.warpAffine(mask_defect, M, (IMG_SIZE, IMG_SIZE),
                                     borderMode=cv2.BORDER_REFLECT)
    return img, depth, mask, mask_defect

# ================== MAIN ==================
def main():
    global SAMPLES_PER_CLASS, OUTPUT_DIR, AUGMENT_FACTOR
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--samples', type=int, default=SAMPLES_PER_CLASS, help='base samples per class')
    ap.add_argument('--out', default=OUTPUT_DIR)
    ap.add_argument('--augment', type=int, default=AUGMENT_FACTOR, help='variants per base sample')
    a = ap.parse_args()
    SAMPLES_PER_CLASS, OUTPUT_DIR, AUGMENT_FACTOR = a.samples, a.out, a.augment
    ensure_dirs()
    total = SAMPLES_PER_CLASS * NUM_CLASSES
    print(f"[INFO] Generating {total} base samples "
          f"({SAMPLES_PER_CLASS} per class x {NUM_CLASSES} classes)")
    print(f"[INFO] With augmentation x{AUGMENT_FACTOR} -> "
          f"~{total * AUGMENT_FACTOR} effective samples")

    idx_global = 0
    for class_id in range(NUM_CLASSES):
        cname = CLASS_MAP[class_id]
        print(f"\n[CLASS {class_id}] {cname}")
        for i in tqdm(range(SAMPLES_PER_CLASS)):
            img, depth, mask, mask_defect, label = generate_sample(class_id)

            # Build a set of variants: original + augmented
            variants = [(img, depth, mask, mask_defect, label)]
            for _ in range(AUGMENT_FACTOR - 1):
                ai, ad, am, amd = augment(img.copy(), depth.copy(),
                                          mask.copy(), mask_defect.copy())
                variants.append((ai, ad, am, amd, label))

            # One split per base sample, so augmented copies never leak across splits
            r = np.random.rand()
            split = "train" if r < 0.70 else "val" if r < 0.85 else "test"

            for (vi, vd, vm, vmd, vlbl) in variants:
                fname = f"{cname}_{idx_global:07d}"
                # Save RGB
                cv2.imwrite(os.path.join(OUTPUT_DIR, split, "images", fname + ".png"),
                            cv2.cvtColor(vi, cv2.COLOR_RGB2BGR))
                # Save depth (normalized to 16-bit)
                depth_u16 = (vd * 65535).astype(np.uint16)
                cv2.imwrite(os.path.join(OUTPUT_DIR, split, "depth", fname + ".png"),
                            depth_u16)
                # Save product mask
                cv2.imwrite(os.path.join(OUTPUT_DIR, split, "masks", fname + "_prod.png"),
                            vm)
                # Save defect mask
                cv2.imwrite(os.path.join(OUTPUT_DIR, split, "masks", fname + "_def.png"),
                            vmd)
                # Save label JSON
                with open(os.path.join(OUTPUT_DIR, split, "labels", fname + ".json"),
                          "w", encoding="utf-8") as f:
                    json.dump(vlbl, f, ensure_ascii=False, indent=2)
                idx_global += 1

    print(f"\n[DONE] Total samples written: {idx_global}")
    print(f"[DONE] Output directory: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
