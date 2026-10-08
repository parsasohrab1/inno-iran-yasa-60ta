"""Train YOLOv8 detector and export ONNX (FR-2-2, FR-2-7).

python -m ai.training.train_yolo --data data/yolo/data.yaml --epochs 50
Fine-tune on real data: --weights runs/detect/train/weights/best.pt --data real/data.yaml
"""
import argparse
import os
import shutil

IMGSZ = 512  # must match ai.inference.detector.IMGSZ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--weights", default="yolov8s.pt")
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--amp", action="store_true",
                    help="mixed precision; leave off on GTX16xx/MX450-class (TU117) GPUs, where FP16 validation returns NaN")
    a = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(a.weights)
    model.train(data=a.data, epochs=a.epochs, imgsz=IMGSZ, batch=a.batch, device=a.device, workers=a.workers, amp=a.amp)
    metrics = model.val(data=a.data, split="test", imgsz=IMGSZ, half=False, device=a.device)
    print(f"mAP50={metrics.box.map50:.3f} recall={metrics.box.mr:.3f} precision={metrics.box.mp:.3f}")  # SRS §8
    onnx_path = model.export(format="onnx", imgsz=IMGSZ, simplify=True, dynamic=False)
    os.makedirs("data/models", exist_ok=True)
    shutil.copy(onnx_path, "data/models/best.onnx")  # picked up by acquisition.run_line
    print("exported:", onnx_path, "-> data/models/best.onnx")


if __name__ == "__main__":
    main()
