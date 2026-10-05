# Iran Yasa Rubber — Intelligent Defect Inspection System

2D/3D imaging + deep learning inspection of rubber products. Full requirements: [docs/SRS.md](docs/SRS.md) (Persian).

## Layout
| Path | Purpose (SRS ref) |
|---|---|
| `backend/` | FastAPI service: inspections, defects, KPIs, WebSocket feed (FR-3, FR-4) |
| `ai/` | Synthetic data, training, inference/detector abstraction (FR-2) |
| `acquisition/` | Camera / 3D sensor / PLC trigger abstractions (FR-1) |
| `frontend/` | React + Vite dashboard, fa/en with RTL (NFR-8) |
| `deploy/` | docker-compose, Mosquitto, Prometheus (NFR-7, NFR-9) |

## Quick start
```bash
# backend (SQLite dev DB)
cd backend && python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload     # http://localhost:8000/docs
pytest

# frontend
cd frontend && npm install && npm run dev

# full stack
docker compose -f deploy/docker-compose.yml up --build
```

## Status
Scaffold only. Detector is a stub (`ai/inference/detector.py`), camera is simulated, auth is not yet enforced.
