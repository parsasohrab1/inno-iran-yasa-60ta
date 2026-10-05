"""Detector abstraction (FR-2-2, FR-2-7, FR-2-8). ONNX Runtime backend runs on CPU, CUDA or TensorRT."""
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

IMGSZ = 512  # keep in sync with ai.training.train_yolo
CLASS_MAP = {
    0: "healthy", 1: "crack", 2: "bubble", 3: "deformation",
    4: "foreign_particle", 5: "discoloration", 6: "flash", 7: "incomplete_fill",
}
# Criticality weight per class; severity_score = weight * (0.5 + 0.5 * size factor), clipped to [0, 1]
CRITICALITY = {1: 1.0, 2: 0.6, 3: 0.7, 4: 0.9, 5: 0.3, 6: 0.5, 7: 0.9}


@dataclass
class Detection:
    class_id: int  # 1..7 (0 = healthy is never emitted)
    class_name: str
    confidence: float
    severity_score: float
    bbox: list[float]  # x1, y1, x2, y2 in original image pixels


class Detector(Protocol):
    def predict(self, image: np.ndarray, depth: np.ndarray | None = None) -> list[Detection]: ...


class StubDetector:
    """Returns no defects; used when no model is available."""

    def predict(self, image: np.ndarray, depth: np.ndarray | None = None) -> list[Detection]:
        return []


def severity(class_id: int, bbox: list[float], shape: tuple[int, int]) -> float:
    area_frac = max(0.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / (shape[0] * shape[1])
    size = min(1.0, area_frac * 50)
    return float(min(1.0, CRITICALITY.get(class_id, 0.5) * (0.5 + 0.5 * size)))


def postprocess(output: np.ndarray, orig_shape: tuple[int, int], conf: float, iou: float = 0.5) -> list[Detection]:
    """Decode raw YOLOv8 ONNX output of shape (1, 4+nc, N) with xywh boxes in IMGSZ space."""
    pred = output[0].T  # (N, 4+nc)
    scores = pred[:, 4:]
    cls = scores.argmax(1)
    confs = scores.max(1)
    keep = confs >= conf
    pred, cls, confs = pred[keep], cls[keep], confs[keep]
    if not len(pred):
        return []
    h, w = orig_shape
    sx, sy = w / IMGSZ, h / IMGSZ
    boxes = np.stack([
        (pred[:, 0] - pred[:, 2] / 2) * sx, (pred[:, 1] - pred[:, 3] / 2) * sy,
        (pred[:, 0] + pred[:, 2] / 2) * sx, (pred[:, 1] + pred[:, 3] / 2) * sy,
    ], axis=1)
    xywh = [[b[0], b[1], b[2] - b[0], b[3] - b[1]] for b in boxes.tolist()]
    idx = cv2.dnn.NMSBoxes(xywh, confs.tolist(), conf, iou)
    out = []
    for i in np.array(idx).reshape(-1):
        class_id = int(cls[i]) + 1  # model class k -> dataset class k+1
        bbox = [float(v) for v in boxes[i]]
        out.append(Detection(class_id, CLASS_MAP[class_id], float(confs[i]),
                             severity(class_id, bbox, orig_shape), bbox))
    return out


class OnnxDetector:
    def __init__(self, model_path: str, conf_threshold: float = 0.7):
        import onnxruntime as ort  # lazy: only needed on inference nodes

        avail = ort.get_available_providers()
        prefs = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
        self.session = ort.InferenceSession(model_path, providers=[p for p in prefs if p in avail])
        self.input_name = self.session.get_inputs()[0].name
        self.conf_threshold = conf_threshold

    def predict(self, image: np.ndarray, depth: np.ndarray | None = None) -> list[Detection]:
        """image: RGB uint8 HxWx3. Depth is unused by the 2D detector (reserved for fusion)."""
        h, w = image.shape[:2]
        x = cv2.resize(image, (IMGSZ, IMGSZ)).astype(np.float32) / 255.0
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
        out = self.session.run(None, {self.input_name: x})[0]
        return postprocess(out, (h, w), self.conf_threshold)
