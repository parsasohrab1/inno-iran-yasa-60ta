import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("PLANT_SIM_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import seed_admin  # noqa: E402
from app.db import Base, SessionLocal, engine as db_engine  # noqa: E402
from app.main import _audit_to_db, _downtime_to_db, app  # noqa: E402
from app.plant.autopilot import AutopilotConfig  # noqa: E402
from app.plant.downtime import DowntimeTracker, pareto  # noqa: E402
from app.plant.engine import PlantEngine  # noqa: E402

PRESS = "L1.press1.plate_temp"
H = {}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def run(e, c, n):
    for _ in range(int(n)):
        c.t += 1
        e.step(c.t)


# ---------------------------------------------------------------- pure logic

def test_tracker_opens_and_closes_stops_once():
    log = []
    tr = DowntimeTracker(lambda kind, st: log.append((kind, st.line, st.reason)) or (len(log) if kind == "open" else None))
    tr.observe(1, True, 0)
    tr.observe(1, False, 10)
    tr.observe(1, False, 20)  # still stopped: no second event
    assert [k for k, *_ in log] == ["open"] and 1 in tr.open and tr.open[1].db_id == 1
    tr.observe(1, True, 100)
    assert [k for k, *_ in log] == ["open", "close"] and not tr.open
    assert tr.recent[0].end == 100 and tr.recent[0].start == 10


def test_assign_changes_only_the_open_stop():
    tr = DowntimeTracker()
    assert tr.assign(1, "mechanical", "manual", None, "u") is None
    tr.observe(1, False, 0)
    st = tr.assign(1, "mechanical", "manual", "bearing", "ali")
    assert (st.reason, st.source, st.set_by) == ("mechanical", "manual", "ali")


def test_pareto_sums_minutes_sorts_clips_to_window_and_reports_unassigned():
    ev = [{"start": 0, "end": 600, "reason_code": "mechanical"},      # 10 min
          {"start": 1000, "end": 1300, "reason_code": "changeover"},  # 5 min, planned
          {"start": 2000, "end": 2060, "reason_code": "unknown"},     # 1 min unassigned
          {"start": 3000, "end": None, "reason_code": "mechanical"},  # still open: runs to "now"
          {"start": -5000, "end": 100, "reason_code": "electrical"}]  # starts before the window: only 100 s count
    p = pareto(ev, now_ts=3300, window_start_ts=0)
    rows = {r["reason_code"]: r for r in p["rows"]}
    assert rows["mechanical"]["minutes"] == 15.0 and rows["mechanical"]["count"] == 2
    assert rows["electrical"]["minutes"] == round(100 / 60, 1)
    assert p["rows"][0]["reason_code"] == "mechanical"
    assert p["unassigned_minutes"] == 1.0 and p["planned_minutes"] == 5.0
    assert abs(p["rows"][-1]["cumulative_pct"] - 1.0) < 1e-3
    assert abs(sum(r["pct"] for r in p["rows"]) - 1.0) < 1e-2


def test_pareto_empty():
    assert pareto([], 100, 0)["rows"] == [] and pareto([], 100, 0)["total_minutes"] == 0


def test_engine_labels_a_safe_stop_automatically_and_manual_stops_with_their_reason():
    c = Clock()
    e = PlantEngine(lines=2, seed=1, fault_rate=0.0, clock=c)
    e.autopilots[1].set_mode("autonomous", "m", c.t)
    e.sim_operator_present[1] = False
    run(e, c, 150)
    e.sim.inject(PRESS, "drift", 2.6, 300, c.t)
    run(e, c, 400)
    st = e.downtime.open[1]
    assert st.reason == "safe_stop" and st.source == "auto" and "حد بحرانی" in (st.note or "")
    e.stop_line(2, "changeover", "قالب جدید", "ali")
    run(e, c, 3)
    s2 = e.downtime.open[2]
    assert (s2.reason, s2.source, s2.set_by, s2.note) == ("changeover", "manual", "ali", "قالب جدید")
    with pytest.raises(ValueError):
        e.stop_line(2, "nonsense", None, "ali")


def test_unexplained_stop_is_unknown_until_a_human_explains_it():
    c = Clock()
    e = PlantEngine(lines=1, seed=1, fault_rate=0.0, clock=c)
    run(e, c, 5)
    e.sim.stop_line(1)  # e.g. a stop observed from the PLC with no reason attached
    run(e, c, 3)
    assert e.downtime.open[1].reason == "unknown"


# ---------------------------------------------------------------- API

@pytest.fixture
def api():
    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    with SessionLocal() as db:
        seed_admin(db)
    c = Clock()
    e = PlantEngine(lines=2, seed=1, fault_rate=0.0, clock=c, audit=_audit_to_db, downtime_sink=_downtime_to_db)
    app.state.plant = e

    def client(user="admin", pw="admin", role=None):
        cl = TestClient(app)
        if role:
            adm = TestClient(app)
            t = adm.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["access_token"]
            pw = "Passw0rd-test"
            adm.post("/api/users", json={"username": user, "password": pw, "role": role}, headers={"Authorization": f"Bearer {t}"})
        t = cl.post("/api/auth/login", json={"username": user, "password": pw}).json()["access_token"]
        cl.headers["Authorization"] = f"Bearer {t}"
        return cl

    yield SimpleNamespace(e=e, c=c, client=client, admin=client())
    app.state.plant = None


def test_stop_assign_restart_and_pareto_over_the_api(api):
    op = api.client("op1", role="operator")
    run(api.e, api.c, 5)
    assert op.post("/api/plant/lines/1/stop", json={"reason_code": "bogus"}).status_code == 422
    r = op.post("/api/plant/lines/1/stop", json={"reason_code": "changeover", "note": "تعویض قالب"})
    assert r.status_code == 200 and r.json()["state"] == "stopped"
    assert op.post("/api/plant/lines/1/stop", json={"reason_code": "changeover"}).status_code == 409
    run(api.e, api.c, 300)  # 5 minutes of stop

    rows = op.get("/api/plant/downtime?open_only=true").json()
    assert len(rows) == 1 and rows[0]["reason_code"] == "changeover" and rows[0]["source"] == "manual"
    assert 4.5 < rows[0]["minutes"] < 6

    # a human corrects the reason while the stop is still open
    eid = rows[0]["id"]
    assert op.patch(f"/api/plant/downtime/{eid}", json={"reason_code": "maintenance", "note": "سرویس"}).status_code == 200
    assert op.patch(f"/api/plant/downtime/{eid}", json={"reason_code": "bogus"}).status_code == 422
    assert op.patch("/api/plant/downtime/9999", json={"reason_code": "maintenance"}).status_code == 404
    assert api.e.downtime.open[1].reason == "maintenance"

    mgr = api.client("mgr1", role="manager")
    assert mgr.post("/api/plant/lines/1/restart").json()["state"] == "running"
    run(api.e, api.c, 3)
    done = op.get("/api/plant/downtime").json()
    assert len(done) == 1 and done[0]["end"] is not None and done[0]["reason_code"] == "maintenance"

    p = op.get("/api/plant/downtime/pareto?days=1").json()
    assert p["rows"][0]["reason_code"] == "maintenance" and p["planned_minutes"] > 4 and p["unassigned_minutes"] == 0
    assert op.get("/api/plant/downtime/reasons").json()[0]["code"]


def test_safe_stop_appears_in_the_pareto_as_a_distinct_reason(api):
    e, c = api.e, api.c
    e.autopilots[2].set_mode("autonomous", "m", c.t)
    e.sim_operator_present[2] = False
    run(e, c, 150)
    e.sim.inject("L2.press1.plate_temp", "drift", 2.6, 300, c.t)
    run(e, c, 400)
    p = api.admin.get("/api/plant/downtime/pareto?days=1&line=2").json()
    assert p["rows"][0]["reason_code"] == "safe_stop" and p["rows"][0]["minutes"] > 0


# ---------------------------------------------------------------- corrective actions

def _raise_critical(api):
    e, c = api.e, api.c
    run(e, c, 5)
    e.sim.inject(PRESS, "dropout", 1.0, 400, c.t)  # safety sensor loses its signal -> critical quality alarm
    run(e, c, 3)
    return e.alarms.active[PRESS]


def test_recording_an_action_acknowledges_the_alarm_and_is_stored(api):
    a = _raise_critical(api)
    op = api.client("op2", role="operator")
    assert op.post(f"/api/plant/alarms/{a.id}/action", json={"cause_code": "x", "action_text": "check"}).status_code == 422
    assert op.post(f"/api/plant/alarms/{a.id}/action", json={"cause_code": "other", "action_text": "ab"}).status_code == 422
    assert op.post("/api/plant/alarms/9999/action", json={"cause_code": "other", "action_text": "check it"}).status_code == 404
    r = op.post(f"/api/plant/alarms/{a.id}/action", json={"cause_code": "sensor_fault", "action_text": "کابل سنسور تعویض شد"})
    assert r.status_code == 201
    row = r.json()
    assert row["status"] == "open" and row["created_by"] == "op2" and row["line"] == 1 and row["severity"] == "critical"
    assert a.acked_by == "op2"  # no more escalation after somebody took responsibility


def test_action_can_be_closed_and_listed_with_filters(api):
    a = _raise_critical(api)
    op = api.client("op3", role="operator")
    r = op.post(f"/api/plant/alarms/{a.id}/action", json={"cause_code": "equipment_wear", "action_text": "در حال بررسی"}).json()
    assert [x["id"] for x in op.get("/api/plant/actions?status=open").json()] == [r["id"]]
    assert op.get("/api/plant/actions?status=done").json() == []
    closed = op.patch(f"/api/plant/actions/{r['id']}", json={"status": "done", "action_text": "یاتاقان تعویض شد"}).json()
    assert closed["status"] == "done" and closed["closed_by"] == "op3" and "یاتاقان" in closed["action_text"]
    assert op.get("/api/plant/actions?line=2").json() == []
    assert op.patch("/api/plant/actions/999", json={"status": "done"}).status_code == 404


def test_coverage_counts_critical_alarms_without_action(api):
    a = _raise_critical(api)
    adm = api.admin
    cov = adm.get("/api/plant/actions/coverage").json()
    assert cov["critical_total"] == 1 and cov["with_action"] == 0 and cov["coverage"] == 0.0
    assert len(cov["without_action"]) == 1 and cov["without_action"][0]["line"] == 1
    adm.post(f"/api/plant/alarms/{a.id}/action", json={"cause_code": "sensor_fault", "action_text": "cable replaced"})
    cov = adm.get("/api/plant/actions/coverage").json()
    assert cov["with_action"] == 1 and cov["coverage"] == 1.0 and cov["without_action"] == []


def test_actions_can_also_be_recorded_for_recently_cleared_alarms(api):
    a = _raise_critical(api)
    run(api.e, api.c, 500)  # dropout ends, alarm clears and moves to history
    assert PRESS not in api.e.alarms.active
    r = api.admin.post(f"/api/plant/alarms/{a.id}/action", json={"cause_code": "other", "action_text": "retrospective note"})
    assert r.status_code == 201
