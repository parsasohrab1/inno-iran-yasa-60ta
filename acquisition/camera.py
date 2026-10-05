"""Acquisition abstractions (FR-1). Real drivers: GigE Vision / GenICam (e.g. harvesters, pypylon)."""
from dataclasses import dataclass, field
from time import time
from typing import Protocol

import numpy as np


@dataclass
class Frame:
    rgb: np.ndarray
    depth: np.ndarray | None = None
    timestamp: float = field(default_factory=time)


class Camera(Protocol):
    def grab(self) -> Frame: ...


class SimulatedCamera:
    """Emits random frames so the pipeline runs without hardware."""

    def __init__(self, size: int = 512):
        self.size = size

    def grab(self) -> Frame:
        rgb = np.random.randint(20, 60, (self.size, self.size, 3), dtype=np.uint8)
        return Frame(rgb=rgb, depth=np.zeros((self.size, self.size), np.float32))
