import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import seed_admin  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


def _login(user="admin", pw="admin"):
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"username": user, "password": pw}).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {tok}"
    return c


@pytest.fixture
def adm():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_admin(db)
    return _login()


def _mat(c, code="M1", **kw):
    body = {"code": code, "name_fa": "ماده", **kw}
    r = c.post("/api/business/materials", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_inventory_status_and_days_of_cover(adm):
    mid = _mat(adm, reorder_point=500, safety_stock=100, lead_time_days=10)
    adm.post("/api/business/stock", json={"material_id": mid, "qty": 1000, "kind": "receipt"})
    inv = adm.get("/api/business/inventory").json()[0]
    assert inv["on_hand"] == 1000 and inv["status"] == "ok" and inv["days_of_cover"] is None

    adm.post("/api/business/stock", json={"material_id": mid, "qty": 600, "kind": "issue"})  # issue stored as negative
    inv = adm.get("/api/business/inventory").json()[0]
    assert inv["on_hand"] == 400 and inv["status"] == "reorder"  # below the reorder point
    assert inv["daily_use"] == round(600 / 14, 1)

    adm.post("/api/business/stock", json={"material_id": mid, "qty": 350, "kind": "issue"})
    assert adm.get("/api/business/inventory").json()[0]["status"] == "critical"  # at/below safety stock


def test_cover_shorter_than_lead_time_triggers_reorder(adm):
    mid = _mat(adm, reorder_point=10, safety_stock=1, lead_time_days=30)
    adm.post("/api/business/stock", json={"material_id": mid, "qty": 1000, "kind": "receipt"})
    adm.post("/api/business/stock", json={"material_id": mid, "qty": 280, "kind": "issue"})  # 20/day -> 36 days cover
    assert adm.get("/api/business/inventory").json()[0]["status"] == "ok"
    adm.post("/api/business/stock", json={"material_id": mid, "qty": 280, "kind": "issue"})  # 40/day, 440 left -> 11 days
    assert adm.get("/api/business/inventory").json()[0]["status"] == "reorder"


def test_prices_keep_source_and_date_and_reject_the_demo_source(adm):
    mid = _mat(adm)
    ok = adm.post("/api/business/prices", json={"material_id": mid, "price": 123.5, "source": "ime", "as_of": "2026-10-01"})
    assert ok.status_code == 201
    assert adm.post("/api/business/prices", json={"material_id": mid, "price": 1, "source": "demo",
                                                   "as_of": "2026-10-01"}).status_code == 422
    assert adm.post("/api/business/prices", json={"material_id": mid, "price": -5, "as_of": "2026-10-01"}).status_code == 422
    hist = adm.get(f"/api/business/prices/{mid}").json()
    assert len(hist) == 1 and hist[0]["source"] == "ime"
    assert adm.get("/api/business/inventory").json()[0]["last_price"]["source"] == "ime"


def test_duplicate_material_code_conflicts(adm):
    _mat(adm, "DUP")
    assert adm.post("/api/business/materials", json={"code": "DUP", "name_fa": "x"}).status_code == 409


def test_customers_by_kind(adm):
    adm.post("/api/business/customers", json={"name": "الف", "kind": "domestic"})
    adm.post("/api/business/customers", json={"name": "B", "kind": "international", "country": "TR"})
    assert [c["name"] for c in adm.get("/api/business/customers?kind=international").json()] == ["B"]
    assert adm.post("/api/business/customers", json={"name": "C", "kind": "other"}).status_code == 422


def test_write_access_is_restricted(adm):
    adm.post("/api/users", json={"username": "op77", "password": "Passw0rd-test", "role": "operator"})
    op = _login("op77", "Passw0rd-test")
    assert op.get("/api/business/inventory").status_code == 200  # read: any signed-in user
    assert op.post("/api/business/materials", json={"code": "X", "name_fa": "x"}).status_code == 403
    assert op.post("/api/business/demo-seed").status_code == 403


def test_demo_seed_is_flagged_and_removable(adm):
    assert adm.post("/api/business/demo-seed").json()["materials"] == 8
    assert adm.post("/api/business/demo-seed").status_code == 409
    mats = adm.get("/api/business/materials").json()
    assert all(m["is_demo"] for m in mats)
    inv = adm.get("/api/business/inventory").json()
    assert all(i["last_price"]["source"] == "demo" and "نمونه" in i["last_price"]["note"] for i in inv)
    assert all(c["is_demo"] for c in adm.get("/api/business/customers").json())
    assert {i["status"] for i in inv} <= {"ok", "reorder", "critical"}
    assert adm.delete("/api/business/demo-seed").status_code == 200
    assert adm.get("/api/business/materials").json() == []
