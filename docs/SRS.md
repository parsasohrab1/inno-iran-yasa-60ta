# inno-iran-yasa-60ta# سند نیازمندی‌های نرم‌افزار (SRS)
## سامانه هوشمند بازرسی و تشخیص عیوب محصولات لاستیکی
### مبتنی بر تصویربرداری دوبعدی/سه‌بعدی و یادگیری عمیق – نسخه ۱.۰

---

## ۱. مقدمه

### ۱-۱. هدف (Purpose)
هدف این سند، تعریف دقیق نیازمندی‌های عملکردی و غیرعملکردی سامانه‌ای است که با ترکیب تصویربرداری دوبعدی/سه‌بعدی و الگوریتم‌های یادگیری عمیق، عیوب محصولات لاستیکی شرکت ایران یاسا ورابر را به‌صورت خودکار شناسایی، طبقه‌بندی، ثبت و تحلیل نماید.

### ۱-۲. دامنه (Scope)
سامانه شامل سه ماژول یکپارچه است:
- **ماژول ۱:** تصویربرداری و پایش هوشمند محصول
- **ماژول ۲:** تشخیص و طبقه‌بندی عیوب با هوش مصنوعی
- **ماژول ۳:** ارزیابی کیفیت، تصمیم‌یار و داشبورد مدیریتی

سامانه قابلیت اتصال به خط تولید، PLC، MES و سامانه‌های کنترل کیفیت را دارد.

### ۱-۳. تعاریف و اصطلاحات
| اصطلاح | تعریف |
|---|---|
| ROI | Region of Interest – ناحیه مورد بازرسی |
| CNN | Convolutional Neural Network |
| mAP | mean Average Precision |
| IoU | Intersection over Union |
| OK/NG | محصول سالم / محصول معیوب |
| Digital Twin | دوقلوی دیجیتال محصول/خط |
| Synthetic Data | داده مصنوعی تولیدشده برای آموزش |

### ۱-۴. مراجع
- IEEE 830-1998 (Recommended Practice for SRS)
- ISO/IEC 25010 (Quality Model)
- استانداردهای داخلی کنترل کیفیت ایران یاسا ورابر

---

## ۲. توصیف کلی سیستم

### ۲-۱. دیدگاه محصول
سامانه یک پلتفرم نرم‌افزاری-سخت‌افزاری است که روی خط تولید نصب شده و به‌صورت Real-Time محصولات لاستیکی را از نظر ظاهری و هندسی بازرسی می‌کند.

### ۲-۲. کاربران اصلی
| کاربر | نقش |
|---|---|
| اپراتور خط | پایش و تأیید نتایج |
| کارشناس کنترل کیفیت | بررسی عیوب و برچسب‌گذاری |
| مدیر تولید | مشاهده داشبورد و KPI |
| مهندس نگهداری | تحلیل روند عیوب |
| مدیر سیستم | مدیریت کاربران و تنظیمات |

### ۲-۳. محدودیت‌ها
- حداقل سرعت خط تولید قابل پشتیبانی: ۳۰ قطعه در دقیقه
- حداقل دقت تشخیص: mAP ≥ ۰٫۹۰ برای عیوب بحرانی
- نرخ False Negative ≤ ۱٪ برای عیوب بحرانی
- زمان استنتاج هر قطعه ≤ ۱۰۰ میلی‌ثانیه

### ۲-۴. فرضیات
- وجود روشنایی کنترل‌شده در ایستگاه تصویربرداری
- دسترسی به برق پایدار و شبکه صنعتی
- وجود نمونه‌های آموزشی اولیه برای Fine-tune

---

## ۳. نیازمندی‌های عملکردی

### ۳-۱. ماژول اول: تصویربرداری و پایش هوشمند

| کد | نیازمندی |
|---|---|
| FR-1-1 | سامانه باید از حداقل ۲ دوربین ۲D و ۱ سنسور ۳D (لیزر پروفایلومتر یا Structured Light) پشتیبانی کند. |
| FR-1-2 | نرخ فریم هر دوربین ≥ ۶۰ FPS در رزولوشن ≥ ۵ MP. |
| FR-1-3 | همگام‌سازی (Sync) زمانی بین دوربین‌ها و سنسور ۳D با خطای ≤ ۱ms. |
| FR-1-4 | کالیبراسیون خودکار (Intrinsic/Extrinsic) با چک‌لیست تصویری. |
| FR-1-5 | ساخت **تصویر مرجع (Reference Template)** از محصول سالم به‌صورت خودکار. |
| FR-1-6 | پشتیبانی از Trigger از طریق PLC (Digital I/O) و Encoder. |
| FR-1-7 | ذخیره‌سازی خام تصاویر و Point Cloud در بافر حلقوی (≥ ۱۰ دقیقه). |
| FR-1-8 | تشخیص عدم تطابق موقعیت (Misalignment) و اعلام هشدار. |

### ۳-۲. ماژول دوم: تشخیص و طبقه‌بندی عیوب با AI

| کد | نیازمندی |
|---|---|
| FR-2-1 | سامانه باید حداقل ۸ کلاس عیب را پشتیبانی کند: ترک، حباب/تخلخل، تغییرشکل، ذرات خارجی، تغییررنگ، پلیسه (Flash)، پرنشدگی، و سایر. |
| FR-2-2 | معماری: CNN-based Detector (YOLOv8 / Faster R-CNN) + Segmentation (U-Net / Mask R-CNN). |
| FR-2-3 | پشتیبانی از **Few-Shot Learning** برای کلاس‌های جدید با ≤ ۵۰ نمونه. |
| FR-2-4 | خروجی: نوع عیب، مختصات Bounding Box، ماسک پیکسلی، شدت (۰–۱)، سطح اهمیت (Low/Med/High). |
| FR-2-5 | قابلیت یادگیری مستمر (Continuous Learning) از داده‌های تأییدشده. |
| FR-2-6 | ارائه Explainability (Grad-CAM) برای عیوب تشخیص‌داده‌شده. |
| FR-2-7 | پشتیبانی از مدل‌های Edge (TensorRT / ONNX Runtime) برای استنتاج سریع. |
| FR-2-8 | آستانه اطمینان قابل تنظیم توسط کاربر (پیش‌فرض ۰٫۷). |

### ۳-۳. ماژول سوم: ارزیابی کیفیت، تصمیم‌یار و داشبورد

| کد | نیازمندی |
|---|---|
| FR-3-1 | ثبت هر بازرسی با مهر زمانی، شناسه قطعه، خط تولید، شیفت. |
| FR-3-2 | تعیین خودکار وضعیت نهایی: OK / NG / Review. |
| FR-3-3 | بانک اطلاعاتی کیفیت (SQL/NoSQL) با قابلیت Query. |
| FR-3-4 | تحلیل روند (Trend) عیوب در بازه‌های روز/هفته/ماه. |
| FR-3-5 | شناسایی الگوهای تکرارشونده و هشدار (SPC / Pareto). |
| FR-3-6 | داشبورد مدیریتی با KPI: نرخ عیب، FPY، OEE کیفیت، Top Defects. |
| FR-3-7 | خروجی گزارش PDF/Excel و API برای MES/ERP. |
| FR-3-8 | مدیریت کاربران و سطوح دسترسی (RBAC). |
| FR-3-9 | قابلیت اتصال به Digital Twin خط تولید. |

### ۳-۴. یکپارچگی و رابط‌ها
| کد | نیازمندی |
|---|---|
| FR-4-1 | پروتکل‌های ارتباطی: OPC-UA, Modbus TCP, MQTT, REST API. |
| FR-4-2 | اتصال به دوربین‌ها: GigE Vision, USB3 Vision, GenICam. |
| FR-4-3 | ذخیره‌سازی: PostgreSQL + MinIO/S3 برای تصاویر. |
| FR-4-4 | احراز هویت: LDAP/AD + JWT. |

---

## ۴. نیازمندی‌های داده

### ۴-۱. حجم داده مورد نیاز برای اتکاپذیری
| سطح | تعداد نمونه هر کلاس | توضیح |
|---|---|---|
| حداقل قابل قبول | ۵۰۰ | آموزش اولیه، دقت پایین |
| پیشنهادی | ۲٬۵۰۰ | دقت متعادل، عمومی‌سازی خوب |
| بهینه | ۵٬۰۰۰+ | دقت بالا، مقاوم به تغییر شرایط |
| پس از Augmentation | ۲۰٬۰۰۰+ | افزایش تنوع و Robustness |

> **جمع‌بندی:** تولید **۲٬۵۰۰ نمونه برای هر کلاس × ۸ کلاس = ۲۰٬۰۰۰ نمونه** به‌صورت پایه، و با Augmentation به **۱۰۰٬۰۰۰+ نمونه مؤثر** می‌رسد که برای آموزش مدل قابل اتکا کافی است.

### ۴-۲. انواع داده
- تصویر RGB (۲D)
- نقشه عمق / Point Cloud (۳D)
- ماسک Segmentation
- متادیتا (JSON)

### ۴-۳. استراتژی داده
- ۷۰٪ آموزش، ۱۵٪ اعتبارسنجی، ۱۵٪ تست
- افزودن نویز، تغییر روشنایی، چرخش، تغییر مقیاس
- Domain Randomization برای شرایط محیطی مختلف

---

## ۵. نیازمندی‌های غیرعملکردی

| کد | دسته | نیازمندی |
|---|---|---|
| NFR-1 | عملکرد | Latency استنتاج ≤ ۱۰۰ms/قطعه |
| NFR-2 | عملکرد | Throughput ≥ ۳۰ قطعه/دقیقه |
| NFR-3 | اطمینان | Uptime ≥ ۹۹٪ |
| NFR-4 | دقت | Precision ≥ ۰٫۹۲ ، Recall ≥ ۰٫۹۰ |
| NFR-5 | امنیت | رمزنگاری TLS 1.3 و ذخیره‌سازی رمزنگاری‌شده |
| NFR-6 | مقیاس‌پذیری | پشتیبانی از ۴ خط تولید همزمان |
| NFR-7 | نگهداشت | Log کامل، Monitoring با Prometheus/Grafana |
| NFR-8 | قابلیت استفاده | UI فارسی/انگلیسی، RTL |
| NFR-9 | سازگاری | Windows/Linux، Docker/K8s |

---

## ۶. معماری پیشنهادی سامانه

```
┌─────────────────────────────────────────────────────────┐
│                    لایه نمایش (UI/Dashboard)            │
│  React + FastAPI + WebSocket + Grafana                  │
├─────────────────────────────────────────────────────────┤
│                    لایه سرویس (Backend)                 │
│  FastAPI + PostgreSQL + MinIO + Redis + MQTT            │
├─────────────────────────────────────────────────────────┤
│                    لایه هوش مصنوعی                      │
│  YOLOv8 (Detection) + U-Net (Segmentation) + ONNX      │
├─────────────────────────────────────────────────────────┤
│                    لایه اکتساب داده                     │
│  GigE Cameras + 3D Sensor + PLC Trigger + Encoder      │
└─────────────────────────────────────────────────────────┘
```

---

## ۷. سخت‌افزار پیشنهادی

| جزء | مشخصات |
|---|---|
| دوربین ۲D | Basler acA5472-17um یا مشابه، ۵MP، GigE |
| سنسور ۳D | Cognex 3D-A5000 یا Zivid One+ |
| نورپردازی | LED Dome + Backlight کنترل‌شده |
| Edge GPU | NVIDIA Jetson AGX Orin یا RTX 4090 |
| سرور | Xeon + ۶۴GB RAM + NVMe 2TB |
| شبکه | Industrial Gigabit Switch + VLAN |

---

## ۸. معیارهای پذیرش (Acceptance Criteria)

1. mAP@0.5 ≥ ۰٫۹۰ روی مجموعه تست
2. Recall عیوب بحرانی ≥ ۰٫۹۵
3. False Negative ≤ ۱٪
4. Latency ≤ ۱۰۰ms
5. Uptime در تست ۷۲ ساعته ≥ ۹۹٪
6. داشبورد بدون خطا و با بارگذاری ≤ ۲ ثانیه
7. مستندات فنی کامل (SRS, SDS, User Manual, API Docs)

---

# ۹. کد تولید داده سنتتیک (Synthetic Data Generator)

در ادامه، کد کامل پایتون برای تولید **۲۰٬۰۰۰+ نمونه سنتتیک** (۸ کلاس × ۲٬۵۰۰ نمونه) شامل تصاویر ۲D، نقشه عمق ۳D، ماسک Segmentation و متادیتا ارائه می‌شود.

### ۹-۱. نصب پیش‌نیازها
```bash
pip install numpy opencv-python pillow tqdm scipy scikit-image matplotlib
```

### ۹-۲. کد کامل `synthetic_data_generator.py`

```python
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
                depth[dx, dy] -= 0.15
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
        cv2.circle(img, (x, y), radius, (255,), -1)  # placeholder
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
    dx = gaussian_filter(np.random.normal(0, 6, (h, w)), sigma=25)
    dy = gaussian_filter(np.random.normal(0, 6, (h, w)), sigma=25)
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
                depth[dx, dy] += 0.2
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
        "bboxes": bbox_list,
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
    ensure_dirs()
    total = SAMPLES_PER_CLASS * NUM_CLASSES
    print(f"[INFO] Generating {total} base samples "
          f"({SAMPLES_PER_CLASS} per class × {NUM_CLASSES} classes)")
    print(f"[INFO] With augmentation ×{AUGMENT_FACTOR} → "
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

            for (vi, vd, vm, vmd, vlbl) in variants:
                # Split assignment
                r = np.random.rand()
                if r < 0.70:
                    split = "train"
                elif r < 0.85:
                    split = "val"
                else:
                    split = "test"

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
```

### ۹-۳. خروجی مورد انتظار
```
synthetic_rubber_dataset/
├── train/    (~70%)
│   ├── images/   *.png
│   ├── depth/    *.png
│   ├── masks/    *_prod.png, *_def.png
│   └── labels/   *.json
├── val/      (~15%)
└── test/     (~15%)
```

### ۹-۴. نمونه کد بارگذاری برای آموزش (PyTorch)

```python
import os, json
import numpy as np
import cv2
import torch
from torch.utils.data import Dataset, DataLoader

CLASS_MAP = {0:"healthy",1:"crack",2:"bubble",3:"deformation",
             4:"foreign_particle",5:"discoloration",6:"flash",7:"incomplete_fill"}

class RubberDefectDataset(Dataset):
    def __init__(self, root, split="train", transform=None):
        self.root = os.path.join(root, split)
        self.files = [f for f in os.listdir(os.path.join(self.root, "images"))
                      if f.endswith(".png")]
        self.transform = transform

    def __len__(self):
        return len(self.files)

    def __getitem__(self, idx):
        fname = self.files[idx]
        base = fname.replace(".png", "")
        img = cv2.cvtColor(cv2.imread(os.path.join(self.root, "images", fname)),
                           cv2.COLOR_BGR2RGB)
        depth = cv2.imread(os.path.join(self.root, "depth", fname),
                           cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535.0
        mask_def = cv2.imread(os.path.join(self.root, "masks", base + "_def.png"),
                              cv2.IMREAD_GRAYSCALE)
        with open(os.path.join(self.root, "labels", base + ".json"), encoding="utf-8") as f:
            label = json.load(f)

        img_t = torch.from_numpy(img).permute(2,0,1).float() / 255.0
        depth_t = torch.from_numpy(depth).unsqueeze(0)
        mask_t = torch.from_numpy(mask_def).unsqueeze(0).float() / 255.0

        return {
            "image": img_t,
            "depth": depth_t,
            "mask": mask_t,
            "class_id": torch.tensor(label["class_id"], dtype=torch.long),
            "bboxes": torch.tensor(label["bboxes"], dtype=torch.float32)
                      if label["bboxes"] else torch.zeros((0,4)),
        }

# Usage
ds = RubberDefectDataset("./synthetic_rubber_dataset", split="train")
dl = DataLoader(ds, batch_size=16, shuffle=True, num_workers=4)
print(f"Train samples: {len(ds)}")
```

---

## ۱۰. جمع‌بندی و مسیر پیشنهادی پیاده‌سازی

| مرحله | مدت | خروجی |
|---|---|---|
| ۱. تولید داده سنتتیک | ۱ هفته | ۲۰٬۰۰۰+ نمونه |
| ۲. آموزش مدل پایه | ۲ هفته | mAP ≥ ۰٫۸۵ |
| ۳. جمع‌آوری داده واقعی | ۳ هفته | ۱٬۰۰۰+ نمونه واقعی |
| ۴. Fine-tune | ۱ هفته | mAP ≥ ۰٫۹۰ |
| ۵. استقرار Edge | ۲ هفته | سامانه عملیاتی |
| ۶. پایلوت روی خط | ۴ هفته | اعتبارسنجی میدانی |
| ۷. توسعه کامل + داشبورد | ۶ هفته | محصول نهایی |

---

**آماده‌ام در صورت نیاز، موارد زیر را نیز ارائه دهم:**
- کد آموزش کامل (Training Script) با YOLOv8 + U-Net
- کد استقرار Edge با TensorRT
- API Specification کامل (OpenAPI)
- طرح UI/UX داشبورد
- مستند SDS (Software Design Specification)
