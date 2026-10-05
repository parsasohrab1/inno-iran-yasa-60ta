import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _defect(conf, sev, name="crack", cid=1):
    return {"class_id": cid, "class_name": name, "confidence": conf,
            "severity_score": sev, "bbox": [1, 2, 3, 4]}


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_ok_and_ng_flow():
    c = TestClient(app)
    r = c.post("/api/inspections", json={"part_id": "P1", "line_id": 1, "shift": "A"})
    assert r.status_code == 201 and r.json()["status"] == "OK"

    r = c.post("/api/inspections", json={
        "part_id": "P2", "line_id": 1, "shift": "A", "defects": [_defect(0.95, 0.8)]})
    assert r.json()["status"] == "NG"
    assert r.json()["defects"][0]["severity"] == "High"

    k = c.get("/api/kpi").json()
    assert k["total"] == 2 and k["fpy"] == 0.5
    assert k["top_defects"][0]["class_name"] == "crack"


def test_low_confidence_goes_to_review():
    c = TestClient(app)
    r = c.post("/api/inspections", json={
        "part_id": "P3", "line_id": 2, "shift": "B",
        "defects": [_defect(0.75, 0.2, "bubble", 2)]})
    assert r.json()["status"] == "Review"


def test_below_threshold_ignored():
    c = TestClient(app)
    r = c.post("/api/inspections", json={
        "part_id": "P4", "line_id": 1, "shift": "A", "defects": [_defect(0.5, 0.9)]})
    assert r.json()["status"] == "OK"
