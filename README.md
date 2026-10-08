# Iran Yasa Rubber — Intelligent Defect Inspection System

2D/3D imaging + deep learning inspection of rubber products. Full requirements: [docs/SRS.md](docs/SRS.md) (Persian).

## Layout
| Path | Purpose (SRS ref) |
|---|---|
| `backend/` | FastAPI: JWT auth + RBAC, inspections, QC review, KPIs, trend/Pareto/SPC, CSV/XLSX export, image storage, WebSocket feed (FR-3, FR-4) |
| `ai/` | Synthetic data, YOLO conversion/training, U-Net, ONNX detector (FR-2) |
| `acquisition/` | Camera abstraction (simulated) and line loop client (FR-1) |
| `frontend/` | React + Vite dashboard, fa/en with RTL (NFR-8) |
| `deploy/` | docker-compose, Mosquitto, Prometheus (NFR-7, NFR-9) |

## Run
```bash
# backend  (default login admin/admin in dev — set ADMIN_PASSWORD and JWT_SECRET for anything real)
cd backend && python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt && uvicorn app.main:app --reload   # /docs for OpenAPI
pytest

# frontend
cd frontend && npm install && npm run dev      # http://localhost:5173

# simulated production line feeding the backend
python -m acquisition.run_line --line 1 --rate 30

# full stack
docker compose -f deploy/docker-compose.yml up --build
```

## Model pipeline
```bash
pip install -r ai/requirements.txt torch ultralytics onnxruntime
python -m ai.datagen.synthetic_data_generator --samples 2500 --augment 4 --out data/synthetic
python -m ai.training.convert_to_yolo --src data/synthetic --dst data/yolo
python -m ai.training.train_yolo --data data/yolo/data.yaml --epochs 50     # exports ONNX
python -m ai.training.unet --root data/synthetic                     # segmentation
# train_yolo copies the export to data/models/best.onnx; run_line uses it automatically
python -m acquisition.run_line --source data/yolo/images/test --loop     # replay images through the live pipeline
python -m ai.validation.hil_replay --images data/yolo/images/test --labels data/yolo/labels/test     --model data/models/best.onnx --report data/models/hil_report.json    # SRS acceptance checks
```
Validation plan toward TRL 5: [docs/VALIDATION_PROTOCOL.md](docs/VALIDATION_PROTOCOL.md).

## Roles
`operator` (view, post inspections) · `qc` (review, analytics, reports) · `manager` (analytics, reports) · `maintenance` (analytics) · `admin` (everything, user management).

## Not done yet (honest status)
- **No trained model exists.** The pipeline is written and unit-tested on tiny data, but no training run has happened, so mAP/recall targets (SRS §8) are unverified. Synthetic data must be followed by real-data fine-tuning.
- Real GigE/GenICam drivers, 3D sensor, PLC/OPC-UA/Modbus/MQTT triggers (FR-1-x, FR-4-1), time sync and calibration: only interfaces/simulation.
- Few-shot learning, Grad-CAM, continuous-learning retrain job (confirmed labels are stored, not yet consumed), Digital Twin, LDAP/AD, PDF reports, Alembic migrations, TLS.
- Latency/throughput/uptime NFRs have not been measured.
