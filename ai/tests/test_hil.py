import cv2
import numpy as np

from ai.inference.detector import Detection, StubDetector
from ai.validation.hil_replay import load_gt, run
from acquisition.camera import ReplayCamera

SIZE = 128


class OracleDetector:
    """Test double: reads ground truth from a lookup and reports it with a given confidence."""

    def __init__(self, truth: dict, conf=0.95, miss=()):
        self.truth, self.conf, self.miss, self.i = truth, conf, set(miss), -1

    def predict(self, image, depth=None):
        self.i += 1
        if self.i in self.miss:
            return []
        return [Detection(c, "x", self.conf, 0.5, [x1, y1, x2, y2]) for c, x1, y1, x2, y2 in self.truth.get(self.i, [])]


def _make(tmp_path, n=20):
    """n images; every 2nd is defective (crack, class id 1) with a 20x20 box."""
    img_dir, lab_dir = tmp_path / "images", tmp_path / "labels"
    img_dir.mkdir()
    lab_dir.mkdir()
    truth = {}
    for i in range(n):
        name = f"s_{i:03d}"
        cv2.imwrite(str(img_dir / f"{name}.png"), np.full((SIZE, SIZE, 3), 40, np.uint8))
        lines = ""
        if i % 2 == 0:
            lines = "0 0.5 0.5 0.15625 0.15625"  # model class 0 = crack
            truth[i] = [(1, 54, 54, 74, 74)]
        (lab_dir / f"{name}.txt").write_text(lines)
    return str(img_dir), str(lab_dir), truth


def test_load_gt(tmp_path):
    _, lab, _ = _make(tmp_path, 2)
    gt = load_gt(f"{lab}/s_000.txt", SIZE, SIZE)
    assert gt.boxes[0][0] == 1 and abs(gt.boxes[0][1] - 54) < 0.5


def test_replay_camera_order_and_eof(tmp_path):
    img, _, _ = _make(tmp_path, 3)
    cam = ReplayCamera(img)
    assert [cam.grab().name for _ in range(3)] == ["s_000.png", "s_001.png", "s_002.png"]
    try:
        cam.grab()
        assert False
    except EOFError:
        pass


def test_perfect_detector_passes_everything(tmp_path):
    img, lab, truth = _make(tmp_path)
    m = run(img, lab, OracleDetector(truth))
    assert m["frames"] == 20 and m["errors"] == 0
    assert m["image_level"]["false_negative_rate"] == 0
    assert m["critical_recall"] == 1 and m["object_level"]["precision"] == 1
    assert all(c["pass"] for c in m["checks"].values()), m["checks"]


def test_stub_detector_fails_recall_criteria(tmp_path):
    img, lab, _ = _make(tmp_path)
    m = run(img, lab, StubDetector())
    assert m["image_level"]["false_negative_rate"] == 1.0
    assert m["checks"]["critical_recall"]["pass"] is False
    assert m["checks"]["precision"]["pass"] is None  # nothing predicted -> not computable, not a pass


def test_one_miss_breaks_fn_target(tmp_path):
    img, lab, truth = _make(tmp_path)
    m = run(img, lab, OracleDetector(truth, miss={4}))
    assert m["image_level"]["fn"] == 1 and m["checks"]["false_negative_rate"]["pass"] is False


def test_low_confidence_counts_as_missed(tmp_path):
    img, lab, truth = _make(tmp_path)
    m = run(img, lab, OracleDetector(truth, conf=0.6))
    assert m["image_level"]["fn"] == 10


def test_detector_exception_counts_against_uptime(tmp_path):
    img, lab, _ = _make(tmp_path)

    class Boom:
        def predict(self, *_):
            raise RuntimeError("gpu fault")

    m = run(img, lab, Boom())
    assert m["errors"] == 20 and m["checks"]["uptime"]["pass"] is False
