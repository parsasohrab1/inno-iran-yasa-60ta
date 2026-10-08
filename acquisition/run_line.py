"""Line loop: grab frame -> detect -> upload image -> POST inspection to the backend.

python -m acquisition.run_line --line 1 --rate 30                       # simulated camera, uses data/models/best.onnx if present
python -m acquisition.run_line --source data/yolo/images/test --loop    # replay recorded images
Env: API_URL, API_USER, API_PASSWORD
"""
import argparse
import os
import time

import cv2
import httpx

from acquisition.camera import ReplayCamera, SimulatedCamera
from ai.inference.detector import StubDetector

DEFAULT_MODEL = "data/models/best.onnx"


def build_detector(model: str | None, conf: float):
    path = model or (DEFAULT_MODEL if os.path.exists(DEFAULT_MODEL) else None)
    if not path:
        print("[run_line] no ONNX model found -> StubDetector (no defects will be reported)")
        return StubDetector()
    from ai.inference.detector import OnnxDetector

    print(f"[run_line] using model {path}")
    return OnnxDetector(path, conf_threshold=conf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", type=int, default=1)
    ap.add_argument("--rate", type=int, default=30, help="parts per minute (SRS NFR-2: >= 30)")
    ap.add_argument("--model", help=f"ONNX model path (default {DEFAULT_MODEL} if it exists)")
    ap.add_argument("--conf", type=float, default=0.5, help="detector floor; the backend applies the 0.7 decision threshold")
    ap.add_argument("--source", help="folder of images to replay instead of the simulated camera")
    ap.add_argument("--loop", action="store_true", help="loop the replay source forever")
    ap.add_argument("--shift", default="A")
    a = ap.parse_args()

    base = os.getenv("API_URL", "http://localhost:8000")
    client = httpx.Client(base_url=base, timeout=10)
    tok = client.post("/api/auth/login", json={
        "username": os.getenv("API_USER", "admin"), "password": os.getenv("API_PASSWORD", "admin"),
    }).raise_for_status().json()["access_token"]
    client.headers["Authorization"] = f"Bearer {tok}"

    cam = ReplayCamera(a.source, loop=a.loop) if a.source else SimulatedCamera()
    det = build_detector(a.model, a.conf)

    n = 0
    while True:
        t0 = time.perf_counter()
        try:
            frame = cam.grab()
        except EOFError:
            break
        dets = det.predict(frame.rgb, frame.depth)
        latency = (time.perf_counter() - t0) * 1000
        n += 1

        image_key = None
        if dets:  # keep evidence only for flagged parts
            ok, buf = cv2.imencode(".png", cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2BGR))
            r = client.post("/api/images", files={"file": ("f.png", buf.tobytes(), "image/png")})
            image_key = r.json()["image_key"] if r.is_success else None

        client.post("/api/inspections", json={
            "part_id": frame.name or f"L{a.line}-{int(time.time())}-{n:06d}", "line_id": a.line, "shift": a.shift,
            "latency_ms": latency, "image_key": image_key,
            "defects": [d.__dict__ for d in dets],
        }).raise_for_status()
        time.sleep(max(0.0, 60 / a.rate - (time.perf_counter() - t0)))


if __name__ == "__main__":
    main()
