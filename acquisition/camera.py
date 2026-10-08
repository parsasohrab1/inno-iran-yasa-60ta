"""Acquisition abstractions (FR-1). Real drivers: GigE Vision / GenICam (e.g. harvesters, pypylon)."""
import os
from dataclasses import dataclass, field
from time import time
from typing import Iterator, Protocol

import cv2
import numpy as np


@dataclass
class Frame:
    rgb: np.ndarray
    depth: np.ndarray | None = None
    timestamp: float = field(default_factory=time)
    name: str = ""


class Camera(Protocol):
    def grab(self) -> Frame: ...


class SimulatedCamera:
    """Emits random frames so the pipeline runs without hardware."""

    def __init__(self, size: int = 512):
        self.size = size

    def grab(self) -> Frame:
        rgb = np.random.randint(20, 60, (self.size, self.size, 3), dtype=np.uint8)
        return Frame(rgb=rgb, depth=np.zeros((self.size, self.size), np.float32))


class ReplayCamera:
    """Plays recorded images (e.g. a held-out real/synthetic test set) as if from a line camera.

    Used for hardware-in-the-loop replay: the full detect -> decide -> report path runs on stored frames.
    """

    def __init__(self, image_dir: str, loop: bool = False, limit: int | None = None):
        self.files = sorted(f for f in os.listdir(image_dir) if f.lower().endswith((".png", ".jpg", ".jpeg")))
        if limit:
            self.files = self.files[:limit]
        if not self.files:
            raise FileNotFoundError(f"no images in {image_dir}")
        self.dir, self.loop = image_dir, loop
        self._it = self._iterate()

    def __len__(self) -> int:
        return len(self.files)

    def _iterate(self) -> Iterator[str]:
        while True:
            yield from self.files
            if not self.loop:
                return

    def grab(self) -> Frame:
        try:
            name = next(self._it)
        except StopIteration:
            raise EOFError("replay finished")
        bgr = cv2.imread(os.path.join(self.dir, name))
        if bgr is None:
            raise IOError(f"unreadable image {name}")
        return Frame(rgb=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), name=name)
