import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("PLANT_SIM_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import seed_admin  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.market import insights as ins  # noqa: E402
from app.market import planning as plan  # noqa: E402

CFG = {**plan.DEFAULTS, "lines": 2, "days_per_month": 30, "hours_per_day": 20, "target_oee": 0.5}


def prod(rate=60.0, fpy=0.9, bom=((1, 0.5),)):
    return plan.Product(1, "P", "p", rate, fpy, 100.0, 0.0, list(bom))


def sc(demand, mats=(), months=3, fpy_gain=0.0, products=None):
    return plan.build_scenario("t", "t", products or [prod()], list(mats), {1: demand}, months, CFG, fpy_gain)


# ---------------------------------------------------------------- planning

def test_machine_hours_utilization_and_lines_needed_follow_the_formula():
    r = sc([27_000, 27_000, 27_000])["months"][0]
    gross = 27_000 / 0.9
    hours = gross / (60.0 * 60.0 * 0.5)  # rate/min * 60 * OEE
    assert r["machine_hours"] == round(hours)
    avail = 30 * 20
    assert r["utilization"] == round(hours / (2 * avail), 3) and r["lines_needed"] == round(hours / avail, 2)
    assert r["unmet_units"] == 0


def test_over_capacity_reports_unmet_demand_revenue_at_risk_and_a_critical_action():
    cap_units = 2 * 30 * 20 * 60 * 60 * 0.5 * 0.9  # good units both lines can make in a month
    s = sc([cap_units * 1.25] * 3)
    m = s["months"][0]
    assert m["utilization"] == pytest.approx(1.25, abs=0.01)
    assert m["unmet_units"] == pytest.approx(cap_units * 1.25 * 0.2, rel=0.01)  # 1 - 1/1.25
    assert m["revenue_at_risk"] == pytest.approx(m["unmet_units"] * 100, rel=0.01)
    rec = plan.recommendations(s, CFG)
    assert rec[0]["severity"] == "critical" and rec[0]["kind"] == "capacity" and rec[0]["extra_lines"] >= 1


def test_high_but_feasible_utilization_is_a_warning_not_a_crisis():
    cap_units = 2 * 30 * 20 * 60 * 60 * 0.5 * 0.9
    s = sc([cap_units * 0.97] * 3)
    rec = plan.recommendations(s, CFG)
    assert rec[0]["severity"] == "warning" and s["months"][0]["unmet_units"] == 0


def test_low_month_is_offered_for_maintenance_and_prebuild():
    cap_units = 2 * 30 * 20 * 60 * 60 * 0.5 * 0.9
    s = sc([cap_units * 0.9, cap_units * 0.3, cap_units * 0.8])
    rec = plan.recommendations(s, CFG)
    maint = [r for r in rec if r["kind"] == "maintenance"]
    assert maint and "ماه 2" in maint[0]["text_fa"]


def test_material_shortage_month_order_deadline_and_quantity():
    cap = 2 * 30 * 20 * 60 * 60 * 0.5 * 0.9
    demand = [cap * 0.5] * 3
    need_per_month = demand[0] / 0.9 * 0.5  # BOM 0.5 per unit
    stock = need_per_month * 1.5  # runs out during month 2
    m = plan.Material(1, "RM", "ماده", stock, 45, 0.0)
    out = sc(demand, [m])["materials"][0]
    assert out["shortfall_month"] == 2 and out["lead_time_days"] == 45
    assert out["order_within_days"] == 0  # 45 days lead time is already longer than the 30 days left
    assert out["order_qty"] == pytest.approx(round(need_per_month * 3 - stock), abs=2)
    rec = plan.recommendations(sc(demand, [m]), CFG)
    assert any(r["kind"] == "material" and r["severity"] == "critical" for r in rec)


def test_enough_stock_means_no_material_alert_and_safety_stock_is_respected():
    demand = [10_000] * 3
    big = plan.Material(1, "RM", "ماده", 1e9, 30, 0.0)
    assert sc(demand, [big])["materials"][0]["shortfall_month"] is None
    need = 10_000 / 0.9 * 0.5
    tight = plan.Material(1, "RM", "ماده", need * 3 + 10, 30, 100.0)  # stock covers need but not need + safety stock
    assert sc(demand, [tight])["materials"][0]["shortfall_month"] == 3


def test_better_first_pass_yield_saves_machine_hours():
    base = sc([30_000] * 3)["months"][0]["machine_hours"]
    better = sc([30_000] * 3, fpy_gain=0.05)["months"][0]["machine_hours"]
    assert better < base


def test_all_five_scenarios_are_produced_with_assumptions_attached():
    q = {1: {"p10": [100.0] * 3, "p50": [200.0] * 3, "p90": [400.0] * 3}}
    res = plan.scenarios([prod()], [], q, 3, {"lines": 2})
    assert [s["name"] for s in res["scenarios"]] == ["base", "growth", "downturn", "export_push", "quality_plus"]
    by = {s["name"]: s["months"][0]["demand_units"] for s in res["scenarios"]}
    assert by["growth"] > by["base"] > by["downturn"] and by["export_push"] == round(200 * 1.2)
    assert res["assumptions"]["lines"] == 2 and "فرضی" in res["assumptions_note"]
    assert res["quality_plus_hours_saved"] >= 0


# ---------------------------------------------------------------- insights

def test_two_proportion_test():
    z, p = ins.z_two_prop(10, 100, 10, 100)
    assert z == 0 and p == pytest.approx(1.0)
    z, p = ins.z_two_prop(30, 100, 10, 100)
    assert z > 3 and p < 0.01
    assert ins.z_two_prop(0, 0, 5, 10) == (0.0, 1.0)


def rows(n, rate, line=1, shift="A", start=None, defect="crack"):
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        bad = (i % 100) < rate * 100
        out.append({"ts": start + timedelta(minutes=i), "line": line, "shift": shift,
                    "status": "NG" if bad else "OK", "defects": [defect] if bad else []})
    return out


def test_quality_summary_pareto_and_group_comparison():
    data = rows(200, 0.05, line=1) + rows(200, 0.20, line=2, defect="bubble")
    s = ins.quality_summary(data, 30)
    assert s["total"] == 400 and s["ng"] == 50 and s["fpy"] == pytest.approx(0.875)
    assert s["pareto"][0]["class_name"] == "bubble" and s["pareto"][-1]["cumulative_pct"] == 1.0
    l2 = next(x for x in s["by_line"] if x["line"] == 2)
    assert l2["p_value"] < 0.001 and l2["vs_rest_z"] > 0
    opp = ins.opportunities(s, [], None)
    assert any("خط 2" in o["text_fa"] for o in opp)
    assert opp[0]["priority"] == 1  # the dominant defect class comes first


def test_small_samples_are_not_over_interpreted():
    s = ins.quality_summary(rows(20, 0.5), 7)
    assert s["warnings"] and all(x["p_value"] is None for x in s["by_line"])
    assert "half_over_half" not in s


def test_alarm_association_needs_enough_data_and_reports_direction():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    quiet = rows(300, 0.04, start=start)                                    # 5 hours, no alarms
    noisy = rows(300, 0.25, start=start + timedelta(hours=6))               # 5 hours with an alarm
    ivs = {1: [(start + timedelta(hours=6), start + timedelta(hours=12))]}
    res = ins.alarm_association(quiet + noisy, ivs)[0]
    assert res["rate_with_alarm"] > res["rate_without_alarm"] and res["p_value"] < 0.01 and "not proof" in res["note"]
    thin = ins.alarm_association(rows(10, 0.5), ivs)[0]
    assert thin["insufficient_data"] is True


def test_competitive_gap_weights_gaps_and_prioritises():
    own = {"quality": 3, "price": 4, "delivery": 2}
    rivals = {"A": {"quality": 5, "price": 3, "delivery": 3}, "B": {"quality": 4, "price": 5}}
    w = {"quality": 0.5, "price": 0.3, "delivery": 0.2, "brand": 0.0}
    r = ins.competitive_gap(own, rivals, w, {"A": {"quality": 10}, "B": {"price": 500}})
    rows_ = {x["criterion"]: x for x in r["rows"]}
    assert rows_["quality"]["gap"] == 2 and rows_["quality"]["best_rival_name"] == "A" and rows_["quality"]["weighted_gap"] == 1.0
    assert rows_["price"]["gap"] == 1 and rows_["price"]["best_rival_name"] == "B"
    assert rows_["quality"]["lever"] and r["priorities"][0]["criterion"] == "quality"
    assert r["own_total"] == pytest.approx((3 * .5 + 4 * .3 + 2 * .2) / 1.0, abs=0.01)
    assert r["rivals"]["B"]["coverage"] == 0.8
    assert any("قدیمی" in x for x in r["warnings"])  # evidence older than a year is called out


def test_gap_reports_missing_own_scores_and_thin_rival_coverage():
    r = ins.competitive_gap({"quality": 3}, {"X": {"quality": 4}}, {"quality": 0.4, "price": 0.6})
    assert any("وارد نشده" in x for x in r["warnings"]) and any("کم‌اعتبار" in x for x in r["warnings"])


# ---------------------------------------------------------------- API

def _client(user="admin", pw="admin", role=None):
    c = TestClient(app)
    if role:
        adm = _client()
        pw = "Passw0rd-test"
        adm.post("/api/users", json={"username": user, "password": pw, "role": role})
    t = c.post("/api/auth/login", json={"username": user, "password": pw}).json()["access_token"]
    c.headers["Authorization"] = f"Bearer {t}"
    return c


@pytest.fixture
def adm():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_admin(db)
    return _client()


def test_demo_data_is_flagged_and_forecast_endpoints_work(adm):
    assert adm.get("/api/market/scenarios").status_code == 409  # nothing defined yet: refuses instead of inventing
    assert adm.post("/api/market/demo-seed").json()["customers"] == 6
    assert adm.post("/api/market/demo-seed").status_code == 409
    prods = adm.get("/api/market/products").json()
    assert all(p["is_demo"] for p in prods) and len(prods[0]["bom"]) == 3
    f = adm.get(f"/api/market/forecast?product_id={prods[0]['id']}&horizon=6").json()
    assert len(f["history"]) == 36 and f["demo"] is True
    fc = f["forecast"]
    assert len(fc["p50"]) == 6 and fc["method"] != "none" and fc["wape"] is not None and fc["confidence"] in ("low", "medium", "high")
    assert all(lo <= m <= hi for lo, m, hi in zip(fc["p10"], fc["p50"], fc["p90"]))
    this_month = datetime.now(timezone.utc).date().replace(day=1).isoformat()
    assert all(h["month"] < this_month for h in f["history"])  # the incomplete current month is never used


def test_customer_consumption_flags_the_fading_and_the_lapsed_customer(adm):
    adm.post("/api/market/demo-seed")
    cust = {c["name"]: c for c in adm.get("/api/market/customers/consumption?horizon=3").json()}
    fading = cust["مشتری داخلی نمونه ج (رو به افول)"]
    assert fading["profile"]["risk"] == "high" and fading["profile"]["trend_pct_per_month"] < 0
    assert cust["مشتری داخلی نمونه الف"]["profile"]["risk"] == "low"
    quarterly = cust["مشتری فصلی نمونه"]["profile"]
    assert quarterly["typical_interval"] == 3 and quarterly["risk"] != "high"
    assert all(c["forecast"]["p10"][0] <= c["forecast"]["p90"][0] for c in cust.values() if c["forecast"])
    assert adm.get("/api/market/customers/consumption").json()[0]["profile"]["risk"] == "high"  # risky customers listed first


def test_scenarios_endpoint_uses_the_forecast_stock_and_bom(adm):
    adm.post("/api/market/demo-seed")
    res = adm.get("/api/market/scenarios?months=6&uplift=0.3").json()
    assert [s["name"] for s in res["scenarios"]] == ["base", "growth", "downturn", "export_push", "quality_plus"]
    assert res["assumptions"]["export_uplift"] == 0.3 and res["measured_fpy"] is None  # no inspections yet: default FPY used
    base, growth = res["scenarios"][0], res["scenarios"][1]
    assert growth["months"][0]["demand_units"] >= base["months"][0]["demand_units"]
    assert 0.2 < base["months"][0]["utilization"] < 1.6  # demo volumes are sized to the plant
    assert base["materials"] and all("shortfall_month" in m for m in base["materials"])


def test_measured_fpy_replaces_the_default_when_enough_inspections_exist(adm):
    adm.post("/api/market/demo-seed")
    for i in range(60):
        defects = [{"class_id": 1, "class_name": "crack", "confidence": 0.95, "severity_score": 0.8, "bbox": [1, 2, 3, 4]}] if i < 6 else []
        adm.post("/api/inspections", json={"part_id": f"P{i}", "line_id": 1, "shift": "A", "defects": defects})
    res = adm.get("/api/market/scenarios?months=3").json()
    assert res["measured_fpy"] == pytest.approx(0.9, abs=0.001)


def test_quality_endpoint_summarises_real_inspections(adm):
    for i in range(80):
        line = 1 + i % 2
        bad = line == 2 and i % 4 == 1
        defects = [{"class_id": 2, "class_name": "bubble", "confidence": 0.95, "severity_score": 0.5, "bbox": [1, 2, 3, 4]}] if bad else []
        adm.post("/api/inspections", json={"part_id": f"Q{i}", "line_id": line, "shift": "A", "defects": defects})
    q = adm.get("/api/market/quality?days=7").json()
    assert q["total"] == 80 and q["ng"] == 20 and q["pareto"][0]["class_name"] == "bubble"
    assert {x["line"] for x in q["by_line"]} == {1, 2}
    assert q["opportunities"] and q["cost_per_ng"] is None


def test_competitor_workflow_requires_evidence_and_computes_gaps(adm):
    assert adm.get("/api/market/competitive?segment=domestic").json()["warnings"]  # no data: says so
    own = adm.post("/api/market/competitors", json={"name": "ما", "kind": "domestic", "is_own": True}).json()
    rival = adm.post("/api/market/competitors", json={"name": "رقیب ۱", "kind": "domestic"}).json()
    assert adm.post("/api/market/competitors", json={"name": "ما۲", "is_own": True}).status_code == 409
    s = {"criterion": "quality", "score": 3, "as_of": "2026-09-01"}
    assert adm.post(f"/api/market/competitors/{rival['id']}/scores", json={**s, "evidence": ""}).status_code == 422
    assert adm.post(f"/api/market/competitors/{rival['id']}/scores", json={**s, "evidence": "x", "criterion": "nope"}).status_code in (422, 404)
    assert adm.post(f"/api/market/competitors/{rival['id']}/scores", json={**s, "score": 6, "evidence": "lab test"}).status_code == 422
    adm.post(f"/api/market/competitors/{rival['id']}/scores", json={**s, "score": 5, "evidence": "نتیجه آزمون مستقل"})
    adm.post(f"/api/market/competitors/{own['id']}/scores", json={**s, "score": 3, "evidence": "FPY داخلی"})
    g = adm.get("/api/market/competitive?segment=domestic").json()
    q = next(r for r in g["rows"] if r["criterion"] == "quality")
    assert q["gap"] == 2 and g["priorities"][0]["criterion"] == "quality" and g["own_defined"]
    assert adm.get("/api/market/competitive?segment=nope").status_code == 422


def test_public_competitor_candidates_have_sources_but_no_scores(adm):
    assert adm.post("/api/market/competitors/seed-public").json()["added"] >= 5
    assert adm.post("/api/market/competitors/seed-public").json()["added"] == 0
    comps = adm.get("/api/market/competitors").json()
    pub = [c for c in comps if c["origin"] == "public"]
    assert pub and all(c["source_url"].startswith("https://") and c["scores"] == [] for c in pub)


def test_export_readiness_counts_only_applicable_certificates(adm):
    assert adm.get("/api/market/export-readiness").json()["readiness"] is None
    assert adm.post("/api/market/certifications/seed-reference").json()["added"] >= 6
    certs = adm.get("/api/market/export-readiness").json()["certifications"]
    iso = next(c for c in certs if c["name"].startswith("ISO 9001"))
    adm.patch(f"/api/market/certifications/{iso['id']}", json={"status": "have"})
    for c in certs:
        if c["scope"] == "tires":
            adm.patch(f"/api/market/certifications/{c['id']}", json={"status": "na"})  # product is not a tire
    r = adm.get("/api/market/export-readiness").json()
    n_applicable = sum(c["status"] != "na" for c in r["certifications"])
    assert r["readiness"] == round(1 / n_applicable, 3) and r["counts"]["have"] == 1


def test_settings_validation_and_rbac(adm):
    ok = adm.put("/api/market/settings", json={"planning": {"lines": 3, "target_oee": 0.7}, "cost_per_ng": 1234})
    assert ok.status_code == 200 and ok.json()["planning"]["lines"] == 3 and ok.json()["cost_per_ng"] == 1234
    assert adm.put("/api/market/settings", json={"planning": {"nonsense": 1}}).status_code == 422
    assert adm.put("/api/market/settings", json={"weights": {"domestic": {"quality": 0.5}}}).status_code == 422
    op = _client("op1", role="operator")
    assert op.get("/api/market/settings").status_code == 200
    assert op.put("/api/market/settings", json={"cost_per_ng": 1}).status_code == 403
    assert op.post("/api/market/products", json={"code": "X", "name_fa": "x"}).status_code == 403
    assert op.post("/api/market/demo-seed").status_code == 403


def test_demo_clear_removes_only_flagged_rows(adm):
    adm.post("/api/market/demo-seed")
    adm.post("/api/market/products", json={"code": "REAL-1", "name_fa": "محصول واقعی"})
    assert adm.delete("/api/market/demo-seed").status_code == 200
    assert [p["code"] for p in adm.get("/api/market/products").json()] == ["REAL-1"]
    assert adm.get("/api/market/sales/summary").json()["rows"] == 0


def test_sales_bulk_import_validates_references(adm):
    c = adm.post("/api/business/customers", json={"name": "ک", "kind": "domestic"}).json()
    p = adm.post("/api/market/products", json={"code": "P1", "name_fa": "p"}).json()
    ok = adm.post("/api/market/sales/bulk", json=[{"customer_id": c["id"], "product_id": p["id"], "period": "2026-01-17", "qty": 5}])
    assert ok.status_code == 201
    assert adm.post("/api/market/sales/bulk", json=[{"customer_id": 999, "product_id": p["id"], "period": "2026-01-01", "qty": 5}]).status_code == 422
    assert adm.get("/api/market/sales/summary").json()["first"] == "2026-01-01"  # normalised to the first of the month
