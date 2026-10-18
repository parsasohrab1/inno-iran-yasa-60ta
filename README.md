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
Shape check against a reference template (catches deformation, which box detectors miss):
```bash
python -m ai.inference.shape_check fit --images data/yolo_v2/images/train --labels data/yolo_v2/labels/train     --out data/models/ring_template.json            # learn the nominal silhouette from healthy parts (FR-1-5)
python -m ai.validation.hil_replay ... --template data/models/ring_template.json
python -m acquisition.run_line --template data/models/ring_template.json
```
Assumes a controlled station with a part that contrasts with a uniform background; unsegmentable frames are counted, not passed silently.

## Continuous learning from QC reviews
QC reviewers confirm/reject detections and can add defects the model missed (`PATCH /api/inspections/{id}/review`
with `added_defects`). `acquisition.run_line` stores images for flagged parts plus a 2 % audit sample of passed parts
(`--keep-ok-rate`) so misses can be found. The retraining job turns those reviews into a fine-tuning set:
```bash
python -m ai.training.retrain --dry-run                       # fetch + build dataset, report only
python -m ai.training.retrain --min-new 50 --exclude-manifest data/blind_parts.txt   # train a candidate
python -m ai.training.retrain --min-new 50 --promote          # swap best.onnx only if the gate passes
```
Safeguards: part-level train/val split (no leakage), blind-set exclusion list, ambiguous verdicts skipped, replay of the
base dataset against forgetting, a fixed gate set (candidate must not regress on critical recall, false negatives,
precision) plus reviewed-validation parts, a registry with hashes/metrics/dataset manifest (`data/models/registry.json`),
the previous model archived on promotion. Promotion is never automatic and does **not** replace the protocol's
re-validation on the blind set (T6) before a deployment.

Validation plan toward TRL 5: [docs/VALIDATION_PROTOCOL.md](docs/VALIDATION_PROTOCOL.md).

## Smart plant (simulated) — live dashboard, alarms, shifts, autopilot, warehouse
The backend runs a **simulated** plant (4 lines + shared hall, 34 typical equipment items, 132 sensors) and streams it to the
dashboard ("Plant (live)" tab). Alarms escalate operator -> production manager -> plant manager; shifts come from
`data/plant_shifts.json` (default is an *assumed* 3x8 schedule, flagged in the UI); each line has an autopilot
(off / advisory / autonomous) with hard safety limits; the "Warehouse, procurement & customers" tab holds stock, prices and
customers (empty until real data is entered; a flagged demo seed exists).
Nothing here is real plant data and no real PLC is connected. Details: [docs/PLANT_AND_BENCHMARK_FA.md](docs/PLANT_AND_BENCHMARK_FA.md),
[docs/PLANT_SENSORS_FA.md](docs/PLANT_SENSORS_FA.md), [docs/SITE_VISIT_CHECKLIST_FA.md](docs/SITE_VISIT_CHECKLIST_FA.md).
```bash
PLANT_SIM_ENABLED=false uvicorn ...        # turn the simulator off when a real adapter is attached
python scripts/demo_safe_stop.py           # autopilot takeover + injected fault + latched safe stop
```

## Market development, OEE, PLC and SMS
- **Market development tab:** quality-improvement analytics from real inspections, competitor benchmarking (scores need evidence), export-readiness
  checklist, demand forecast per product and per customer (backtested, with honest intervals) and five production scenarios with material needs.
  See [docs/MARKET_DEVELOPMENT_FA.md](docs/MARKET_DEVELOPMENT_FA.md). With no real data it stays empty and says so.
- **Plant tab additions:** anomaly detection learned from normal operation, downtime reason codes + Pareto, corrective actions on alarms, and OEE
  (availability x performance x quality with a loss tree) computed only over time the monitoring system actually observed.
- **PLC / SMS:** `PLANT_SOURCE=opcua` uses the OPC-UA adapter (read-only unless `write_enabled`), tested against a real OPC-UA server; SMS goes through
  an Iranian gateway (tested with a mock; verify with a real test message). See [docs/PLC_AND_SMS_FA.md](docs/PLC_AND_SMS_FA.md).

## Roles
`operator` (view, post inspections) · `qc` (review, analytics, reports) · `manager` (analytics, reports) · `maintenance` (analytics) · `admin` (everything, user management).

## Not done yet (honest status)
- **No trained model exists.** The pipeline is written and unit-tested on tiny data, but no training run has happened, so mAP/recall targets (SRS §8) are unverified. Synthetic data must be followed by real-data fine-tuning.
- Real GigE/GenICam drivers, 3D sensor, Modbus/MQTT triggers (FR-1-x, FR-4-1), time sync and calibration: only interfaces/simulation. OPC-UA exists but is not connected to a real PLC.
- Few-shot learning, Grad-CAM, a reviewer UI for drawing boxes (the API supports `added_defects`), a scheduler for the retraining job, Digital Twin, LDAP/AD, PDF reports, Alembic migrations, TLS.
- Latency/throughput/uptime NFRs have not been measured.
