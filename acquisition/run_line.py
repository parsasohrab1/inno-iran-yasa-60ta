"""Simulated line loop: grab frame -> detect -> POST inspection to the backend."""
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acquisition.camera import SimulatedCamera  # noqa: E402
from ai.inference.detector import StubDetector  # noqa: E402

API = "http://localhost:8000/api/inspections"


def main(line_id: int = 1, rate_per_min: int = 30):
    cam, det = SimulatedCamera(), StubDetector()
    n = 0
    while True:
        t0 = time.perf_counter()
        frame = cam.grab()
        dets = det.predict(frame.rgb, frame.depth)
        latency = (time.perf_counter() - t0) * 1000
        n += 1
        httpx.post(API, json={
            "part_id": f"SIM-{line_id}-{n:06d}", "line_id": line_id, "shift": "A",
            "latency_ms": latency,
            "defects": [d.__dict__ for d in dets],
        })
        time.sleep(60 / rate_per_min)


if __name__ == "__main__":
    main()
