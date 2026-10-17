"""Acquisition abstractions (FR-1). Real drivers: GigE Vision / GenICam (e.g. harvesters, pypylon)."""
import os
import random
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

    def __init__(self, image_dir: str, loop: bool = False, limit: int | None = None, shuffle: bool = False,
                 healthy_share: float | None = None, seed: int = 0):
        """shuffle: random order instead of alphabetical (alphabetical = one defect class at a time).
        healthy_share: demo aid for synthetic sets named `healthy_*`; repeat healthy images so they make up this
        fraction of the stream (a real line is mostly good parts, while test sets are defect-heavy)."""
        self.files = sorted(f for f in os.listdir(image_dir) if f.lower().endswith((".png", ".jpg", ".jpeg")))
        if limit:
            self.files = self.files[:limit]
        if not self.files:
            raise FileNotFoundError(f"no images in {image_dir}")
        if healthy_share is not None:
            good = [f for f in self.files if f.startswith("healthy")]
            bad = [f for f in self.files if not f.startswith("healthy")]
            if good and bad and 0 < healthy_share < 1:
                n_good = round(len(bad) * healthy_share / (1 - healthy_share))
                self.files = bad + [good[i % len(good)] for i in range(n_good)]
        self._rng = random.Random(seed)
        self.dir, self.loop, self.shuffle = image_dir, loop, shuffle
        self._it = self._iterate()

    def __len__(self) -> int:
        return len(self.files)

    def _iterate(self) -> Iterator[str]:
        while True:
            order = list(self.files)
            if self.shuffle:
                self._rng.shuffle(order)
            yield from order
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
