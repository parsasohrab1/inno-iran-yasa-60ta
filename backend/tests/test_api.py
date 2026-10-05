import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import seed_admin  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def c():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_admin(db)
    client = TestClient(app)
    tok = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {tok}"
    return client


def _defect(conf, sev, name="crack", cid=1):
    return {"class_id": cid, "class_name": name, "confidence": conf,
            "severity_score": sev, "bbox": [1, 2, 3, 4]}


def _post(c, part, defects=(), line=1):
    return c.post("/api/inspections", json={"part_id": part, "line_id": line, "shift": "A", "defects": list(defects)})


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_requires_auth(c):
    anon = TestClient(app)
    assert anon.get("/api/kpi").status_code == 401
    assert anon.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code == 401


def test_ok_and_ng_flow(c):
    assert _post(c, "P1").json()["status"] == "OK"
    r = _post(c, "P2", [_defect(0.95, 0.8)])
    assert r.json()["status"] == "NG"
    assert r.json()["defects"][0]["severity"] == "High"

    k = c.get("/api/kpi").json()
    assert k["total"] == 2 and k["fpy"] == 0.5
    assert k["top_defects"][0]["class_name"] == "crack"


def test_low_confidence_goes_to_review(c):
    assert _post(c, "P3", [_defect(0.75, 0.2, "bubble", 2)], line=2).json()["status"] == "Review"


def test_below_threshold_ignored(c):
    assert _post(c, "P4", [_defect(0.5, 0.9)]).json()["status"] == "OK"


def test_review_workflow(c):
    r = _post(c, "P5", [_defect(0.75, 0.2), _defect(0.72, 0.3, "bubble", 2)]).json()
    keep = r["defects"][0]["id"]
    out = c.patch(f"/api/inspections/{r['id']}/review",
                  json={"status": "NG", "note": "real crack", "confirmed_defect_ids": [keep]}).json()
    assert out["status"] == "NG" and out["reviewed_by"] == "admin"
    flags = {d["id"]: d["confirmed"] for d in out["defects"]}
    assert flags[keep] is True and list(flags.values()).count(False) == 1
    assert c.patch(f"/api/inspections/{r['id']}/review", json={"status": "Review"}).status_code == 422


def test_rbac(c):
    c.post("/api/users", json={"username": "op1", "password": "password1", "role": "operator"})
    tok = TestClient(app).post("/api/auth/login", json={"username": "op1", "password": "password1"}).json()
    op = TestClient(app)
    op.headers["Authorization"] = f"Bearer {tok['access_token']}"
    assert op.get("/api/inspections").status_code == 200
    assert op.get("/api/analytics/pareto").status_code == 403
    assert op.get("/api/users").status_code == 403


def test_analytics_and_reports(c):
    for i in range(3):
        _post(c, f"A{i}", [_defect(0.95, 0.8)])
    _post(c, "B", [_defect(0.95, 0.5, "bubble", 2)])
    _post(c, "C")
    par = c.get("/api/analytics/pareto").json()
    assert par[0]["class_name"] == "crack" and par[-1]["cumulative_pct"] == 1.0
    tr = c.get("/api/analytics/trend?period=week").json()
    assert tr[0]["total"] == 5 and tr[0]["ng"] == 4
    spc = c.get("/api/analytics/spc").json()
    assert 0.79 < spc["p_bar"] < 0.81 and len(spc["points"]) == 1
    csv_text = c.get("/api/reports/inspections.csv").text
    assert csv_text.splitlines()[0].startswith("id,part_id") and len(csv_text.splitlines()) == 6
    assert c.get("/api/reports/inspections.xlsx").content[:2] == b"PK"


def test_websocket_requires_token(c):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect("/ws/inspections?token=bad"):
            pass
    tok = c.headers["Authorization"].split()[1]
    with TestClient(app).websocket_connect(f"/ws/inspections?token={tok}"):
        pass
