import cv2
import numpy as np
import pytest

from ai.inference.detector import Detection, ShapeAwareDetector, StubDetector
from ai.inference.shape_check import ShapeTemplate, segment

SIZE = 256


def ring(rng, outer=90, ratio=0.6, dent=0.0, oval=0.0, noise=3.0):
    """Synthetic annulus on a uniform background, optionally dented/ovalised."""
    bg = rng.integers(20, 40, 3)
    img = np.tile(bg.astype(np.uint8), (SIZE, SIZE, 1))
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    cx = cy = SIZE / 2 + rng.integers(-6, 7)
    th = np.arctan2(yy - cy, xx - cx)
    r = np.hypot(xx - cx, yy - cy)
    shift = dent * outer * np.exp(-np.angle(np.exp(1j * (th - 1.0))) ** 2 / (2 * 0.5 ** 2)) \
        + oval * outer * np.cos(2 * th)
    rr = r - shift
    part = (rr <= outer) & (rr >= outer * ratio)
    img[part] = (bg + 70).clip(0, 255)
    img = img.astype(np.float32) + rng.normal(0, noise, img.shape)
    return img.clip(0, 255).astype(np.uint8)


@pytest.fixture(scope="module")
def template():
    rng = np.random.default_rng(0)
    imgs = [ring(rng, outer=rng.integers(80, 100), ratio=rng.uniform(0.55, 0.65)) for _ in range(60)]
    return ShapeTemplate().fit(imgs)


def test_healthy_rings_pass(template):
    rng = np.random.default_rng(1)
    res = [template.check(ring(rng, outer=rng.integers(80, 100), ratio=rng.uniform(0.55, 0.65))) for _ in range(40)]
    assert sum(r.status == "anomaly" for r in res) <= 2  # <= 5 % false alarms
    assert all(r.status != "unsegmentable" for r in res)


@pytest.mark.parametrize("kw", [{"dent": -0.15}, {"dent": 0.15}, {"oval": 0.12}])
def test_deformed_rings_flagged(template, kw):
    rng = np.random.default_rng(2)
    res = [template.check(ring(rng, **kw)) for _ in range(15)]
    assert all(r.status == "anomaly" for r in res), [(r.status, r.score) for r in res]
    assert all(r.score > 1.5 for r in res)
    assert res[0].bbox is not None and res[0].bbox[2] > res[0].bbox[0]


def test_unsegmentable_abstains(template):
    flat = np.full((SIZE, SIZE, 3), 30, np.uint8)
    assert segment(flat) is None
    assert template.check(flat).status == "unsegmentable"


def test_not_enough_healthy_samples():
    rng = np.random.default_rng(3)
    with pytest.raises(ValueError):
        ShapeTemplate().fit([ring(rng) for _ in range(5)])


def test_save_load_roundtrip(template, tmp_path):
    p = str(tmp_path / "t.json")
    template.save(p)
    loaded = ShapeTemplate.load(p)
    img = ring(np.random.default_rng(4), dent=-0.15)
    a, b = template.check(img), loaded.check(img)
    assert a.status == b.status == "anomaly" and abs(a.score - b.score) < 1e-9


class _Fixed:
    def __init__(self, dets):
        self.dets = dets

    def predict(self, image, depth=None):
        return self.dets


def test_shape_detector_adds_deformation_only_when_cnn_silent(template):
    bad = ring(np.random.default_rng(5), dent=-0.15)
    det = ShapeAwareDetector(StubDetector(), template)
    out = det.predict(bad)
    assert len(out) == 1 and out[0].class_id == 3 and out[0].confidence >= 0.85  # confident NG
    assert det.stats["anomaly"] == 1

    crack = Detection(1, "crack", 0.9, 0.8, [0, 0, 5, 5])
    det2 = ShapeAwareDetector(_Fixed([crack]), template)
    assert det2.predict(bad) == [crack]  # CNN already stopped the part; no duplicate
    assert det2.stats["cnn_flagged"] == 1

    good = ring(np.random.default_rng(6))
    assert ShapeAwareDetector(StubDetector(), template).predict(good) == []


def test_check_is_deterministic(template):
    """Regression: warpPolar without WARP_FILL_OUTLIERS returned undefined pixels off-frame."""
    rng = np.random.default_rng(9)
    for _ in range(10):
        img = ring(rng, outer=95)  # large and off-centre, so the polar radius leaves the frame
        scores = [template.check(img).score for _ in range(4)]
        assert max(scores) == min(scores)
