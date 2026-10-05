"""Detector abstraction (FR-2-2, FR-2-7). Swap StubDetector for an ONNX/TensorRT one."""
from dataclasses import dataclass
from typing import Protocol

import numpy as np

CLASS_MAP = {
    0: "healthy", 1: "crack", 2: "bubble", 3: "deformation",
    4: "foreign_particle", 5: "discoloration", 6: "flash", 7: "incomplete_fill",
}


@dataclass
class Detection:
    class_id: int
    class_name: str
    confidence: float
    severity_score: float
    bbox: list[float]  # x1, y1, x2, y2


class Detector(Protocol):
    def predict(self, image: np.ndarray, depth: np.ndarray | None = None) -> list[Detection]: ...


class StubDetector:
    """Placeholder: returns no defects. Replace with OnnxDetector once a model is trained."""

    def predict(self, image: np.ndarray, depth: np.ndarray | None = None) -> list[Detection]:
        return []


class OnnxDetector:
    def __init__(self, model_path: str, conf_threshold: float = 0.7):
        import onnxruntime as ort  # lazy: only needed on edge nodes

        self.session = ort.InferenceSession(
            model_path, providers=["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        self.conf_threshold = conf_threshold

    def predict(self, image: np.ndarray, depth: np.ndarray | None = None) -> list[Detection]:
        raise NotImplementedError("Implement pre/post-processing for the trained YOLOv8 export")
