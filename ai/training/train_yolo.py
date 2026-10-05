"""Train YOLOv8 detector and export ONNX (FR-2-2, FR-2-7).

python -m ai.training.train_yolo --data yolo_dataset/data.yaml --epochs 50
Fine-tune on real data: --weights runs/detect/train/weights/best.pt --data real/data.yaml
"""
import argparse

IMGSZ = 512  # must match ai.inference.detector.IMGSZ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--weights", default="yolov8s.pt")
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=None)
    a = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(a.weights)
    model.train(data=a.data, epochs=a.epochs, imgsz=IMGSZ, batch=a.batch, device=a.device)
    metrics = model.val(data=a.data, split="test", imgsz=IMGSZ)
    print(f"mAP50={metrics.box.map50:.3f} recall={metrics.box.mr:.3f} precision={metrics.box.mp:.3f}")  # SRS §8
    print("exported:", model.export(format="onnx", imgsz=IMGSZ, simplify=True, dynamic=False))


if __name__ == "__main__":
    main()
