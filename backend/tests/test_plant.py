import os
from collections import deque
from datetime import datetime, timedelta

import pytest

os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import seed_admin  # noqa: E402
from app.db import Base, SessionLocal, engine as db_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.plant.autopilot import AutopilotConfig  # noqa: E402
from app.plant.catalog import build_plant  # noqa: E402
from app.plant.engine import PlantEngine  # noqa: E402
from app.plant.notify import Contact, Notifier, WebhookChannel  # noqa: E402
from app.plant.shifts import IRAN_TZ, ShiftSchedule  # noqa: E402

PRESS = "L1.press1.plate_temp"
OVEN = "L1.oven.air_temp"
SPEED = "L1.conveyor.speed"


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(clock=None, **kw):
    clock = clock or Clock()
    kw.setdefault("lines", 2)
    kw.setdefault("seed", 1)
    kw.setdefault("fault_rate", 0.0)
    return PlantEngine(clock=clock, **kw), clock


def run(e, clock, seconds):
    for _ in range(int(seconds)):
        clock.t += 1
        e.step(clock.t)


def sent(e, role=None, contains=None):
    return [n for n in e.notifier.history if (role is None or n.role == role) and n.channel == "log"
            and (contains is None or contains in n.body or contains in n.subject)]


# ---------------------------------------------------------------- catalog / simulator

def test_catalog_is_complete_and_ids_unique():
    eq = build_plant(4)
    ids = [s.id for e in eq for s in e.sensors]
    assert len(ids) == len(set(ids)) and len(ids) > 100
    assert {e.line for e in eq} == {0, 1, 2, 3, 4}
    assert PRESS in ids and OVEN in ids and SPEED in ids
    assert all(not e.verified for e in eq)  # nothing is claimed to be confirmed plant data


def test_simulated_values_stay_in_normal_range_without_faults():
    e, c = make()
    run(e, c, 300)
    bad = [(sid, st.value) for sid, st in e.sim.states.items() if e.sensor_state(st) != "ok"]
    assert not bad, bad
    assert not e.alarms.active


def test_simulation_is_deterministic_for_a_seed():
    a, ca = make(seed=7)
    b, cb = make(seed=7)
    run(a, ca, 50)
    run(b, cb, 50)
    assert {k: s.value for k, s in a.sim.states.items()} == {k: s.value for k, s in b.sim.states.items()}


# ---------------------------------------------------------------- shifts

def _friday():
    d = datetime(2026, 10, 1, 12, 0, tzinfo=IRAN_TZ)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def test_shift_boundaries_and_overnight_date():
    s = ShiftSchedule()
    fri = _friday()
    mon = fri + timedelta(days=3)
    assert s.at(mon.replace(hour=6, minute=0)).key == "morning"
    assert s.at(mon.replace(hour=13, minute=59)).key == "morning"
    assert s.at(mon.replace(hour=14, minute=0)).key == "afternoon"
    assert s.at(mon.replace(hour=22, minute=0)).key == "night"
    # 02:00 belongs to the night shift that started the previous day
    night_tail = s.at(mon.replace(hour=2))
    assert night_tail.key == "night" and night_tail.shift_date == (mon - timedelta(days=1)).date().isoformat()
    assert night_tail.end.hour == 6 and night_tail.start.hour == 22


def test_friday_is_off_and_saturday_night_tail_inherits_it():
    s = ShiftSchedule()
    fri = _friday()
    assert not s.at(fri.replace(hour=10)).working
    assert not s.at((fri + timedelta(days=1)).replace(hour=2)).working  # tail of Friday's night shift
    assert s.at((fri + timedelta(days=1)).replace(hour=10)).working


def test_default_schedule_is_flagged_as_assumed():
    assert ShiftSchedule().describe()["assumed"] is True


# ---------------------------------------------------------------- notifications

def test_recipients_by_role_and_line():
    n = Notifier([Contact("a", "op1", "operator", 1), Contact("b", "op2", "operator", 2),
                  Contact("c", "pm", "production_manager"), Contact("d", "plm", "plant_manager")])
    assert [r.id for r in n.recipients("operator", 2)] == ["b"]
    assert [r.id for r in n.recipients("production_manager", 2)] == ["c"]


def test_failing_webhook_never_raises_and_is_recorded():
    n = Notifier([Contact("a", "op1", "operator", 1, {"webhook": "http://127.0.0.1:9/none"})],
                 channels=[__import__("app.plant.notify", fromlist=["LogChannel"]).LogChannel(), WebhookChannel(0.2)])
    out = n.send("operator", 1, "s", "b", "critical")
    assert {x.channel for x in out} == {"log", "webhook"}
    assert [x.ok for x in out if x.channel == "webhook"] == [False]


# ---------------------------------------------------------------- alarms & escalation

def _drive_press_hot(e):
    e.sim.set_setpoint(PRESS, 200.0)  # simulate a runaway heater: PV heads for 200 °C


def test_critical_alarm_escalates_operator_manager_plantmanager():
    e, c = make()
    run(e, c, 5)
    _drive_press_hot(e)
    run(e, c, 70)  # warning (182) then critical (190) are crossed
    a = e.alarms.active[PRESS]
    assert a.severity == "critical" and a.limit == "hi_hi"
    assert sent(e, "operator", "بحرانی")
    assert not sent(e, "production_manager", "بحرانی")  # not yet: 120 s unacknowledged required
    run(e, c, 125)
    assert sent(e, "production_manager", "بدون تأیید")
    assert not sent(e, "plant_manager", "بدون تأیید")
    run(e, c, 200)
    assert sent(e, "plant_manager", "بدون تأیید")


def test_ack_stops_escalation():
    e, c = make()
    run(e, c, 5)
    _drive_press_hot(e)
    run(e, c, 70)
    a = e.alarms.active[PRESS]
    assert e.alarms.ack(a.id, "op_ali", c.t).acked_by == "op_ali"
    run(e, c, 400)
    assert not sent(e, "production_manager", "بدون تأیید") and not sent(e, "plant_manager", "بدون تأیید")


def test_alarm_clears_with_hysteresis_and_is_kept_in_history():
    e, c = make()
    run(e, c, 5)
    _drive_press_hot(e)
    run(e, c, 70)
    e.sim.set_setpoint(PRESS, 175.0)
    run(e, c, 200)
    assert PRESS not in e.alarms.active
    assert any(a.sensor_id == PRESS and a.cleared_at for a in e.alarms.history)


def test_sensor_dropout_raises_quality_alarm_critical_for_safety_sensors():
    e, c = make()
    run(e, c, 5)
    e.sim.inject(PRESS, "dropout", 1.0, 100, c.t)
    run(e, c, 3)
    a = e.alarms.active[PRESS]
    assert a.limit == "quality" and a.severity == "critical"


# ---------------------------------------------------------------- autopilot: control

def _autonomous(e, c, line=1, absent=True, **cfg):
    e.autopilots[line].cfg = AutopilotConfig(**cfg) if cfg else e.autopilots[line].cfg
    e.autopilots[line].set_mode("autonomous", "mgr", c.t)
    e.sim_operator_present[line] = not absent


def test_off_mode_never_writes():
    e, c = make()
    e.sim_operator_present[1] = False
    e.sim.inject(PRESS, "drift", 0.6, 500, c.t)
    run(e, c, 450)
    assert e.plc.read_setpoint(PRESS) == 175.0
    assert not e.autopilots[1].actions


def test_autonomous_waits_for_operator_absence_then_acts_within_band():
    e, c = make()
    _autonomous(e, c, absent=True)
    e.sim.inject(PRESS, "drift", 0.7, 700, c.t)  # ramps over 420 s to +4.9 °C above recipe, then holds
    run(e, c, 60)
    assert not e.autopilots[1].engaged and e.plc.read_setpoint(PRESS) == 175.0  # operator was present < 120 s ago
    run(e, c, 600)
    ap = e.autopilots[1]
    assert ap.engaged
    sp = e.plc.read_setpoint(PRESS)
    assert 172.0 <= sp <= 173.5  # lowered step by step (at least two 1 °C steps), inside the recipe - 3 °C band
    assert any(a.kind == "action" for a in ap.actions)
    assert sent(e, "production_manager", "اقدام")


def test_setpoint_never_leaves_the_band_even_if_deviation_is_permanent():
    e, c = make()
    _autonomous(e, c, absent=True)
    e.sim.inject(PRESS, "drift", 0.9, 600, c.t)  # ramps to +6.3 °C, more than the controller may cancel (+-3)
    run(e, c, 520)
    assert abs(e.plc.read_setpoint(PRESS) - 172.0) < 1e-9  # pinned at the band floor, never below


def test_operator_return_hands_control_back():
    e, c = make()
    _autonomous(e, c, absent=True)
    run(e, c, 200)
    assert e.autopilots[1].engaged
    e.sim_operator_present[1] = True
    run(e, c, 3)
    assert not e.autopilots[1].engaged
    assert any(a.kind == "handover" for a in e.autopilots[1].actions)
    before = e.plc.read_setpoint(PRESS)
    e.sim.inject(PRESS, "drift", 0.6, 700, c.t)
    run(e, c, 400)
    assert e.plc.read_setpoint(PRESS) == before  # operator present: advisory only


def test_autonomous_mode_expires():
    e, c = make()
    _autonomous(e, c, absent=True, max_autonomous_s=100)
    run(e, c, 140)
    assert e.autopilots[1].mode == "off" and not e.autopilots[1].engaged
    assert sent(e, "production_manager", "منقضی")


def test_quality_drop_reduces_speed_stepwise_down_to_the_floor_then_recovers():
    state = {"q": (60, 0.40)}
    e, c = make(quality=lambda line: state["q"])
    _autonomous(e, c, absent=True)
    run(e, c, 1200)
    sp = e.plc.read_setpoint(SPEED)
    assert abs(sp - 0.7 * 12.0) < 1e-6  # floor, never below 70 %
    state["q"] = (60, 0.02)
    run(e, c, 2000)
    assert e.plc.read_setpoint(SPEED) > sp


def test_advisory_mode_recommends_but_does_not_write():
    state = {"q": (60, 0.40)}
    e, c = make(quality=lambda line: state["q"])
    e.autopilots[1].set_mode("advisory", "mgr", c.t)
    e.sim_operator_present[1] = False
    run(e, c, 600)
    ap = e.autopilots[1]
    assert e.plc.read_setpoint(SPEED) == 12.0
    assert any(a.kind == "recommendation" for a in ap.actions) and not any(a.kind == "action" for a in ap.actions)


# ---------------------------------------------------------------- autopilot: fail-safe

def test_critical_limit_while_engaged_requests_latched_safe_stop():
    e, c = make()
    _autonomous(e, c, absent=True)
    run(e, c, 150)
    e.sim.inject(PRESS, "drift", 2.6, 300, c.t)  # heads past hi_hi
    run(e, c, 400)
    ap = e.autopilots[1]
    assert ap.safe_stop and e.plc.stopped.get(1)
    assert not e.sim.line_running[1] and e.sim.states[SPEED].setpoint == 0.0
    for role in ("operator", "production_manager", "plant_manager"):
        assert sent(e, role, "توقف ایمن"), role
    # latched: no further actions and no automatic restart
    n_actions = len(ap.actions)
    run(e, c, 300)
    assert len(ap.actions) == n_actions and not e.sim.line_running[1]


def test_safety_sensor_signal_loss_triggers_safe_stop():
    e, c = make()
    _autonomous(e, c, absent=True)
    run(e, c, 150)
    e.sim.inject("L1.press1.hydraulic_pressure", "dropout", 1.0, 500, c.t)
    run(e, c, 60)
    assert e.autopilots[1].safe_stop and "هیدرولیک" in e.autopilots[1].safe_stop


def test_failsafe_in_advisory_only_recommends():
    e, c = make()
    e.autopilots[1].set_mode("advisory", "mgr", c.t)
    e.sim_operator_present[1] = False
    run(e, c, 150)
    e.sim.inject(PRESS, "drift", 2.6, 300, c.t)
    run(e, c, 400)
    assert e.sim.line_running[1] and not e.autopilots[1].safe_stop
    assert any("توقف ایمن" in a.summary for a in e.autopilots[1].actions)


def test_too_many_actions_in_an_hour_stops_the_line_instead_of_trying_harder():
    e, c = make()
    _autonomous(e, c, absent=True, max_actions_per_hour=3)
    run(e, c, 150)
    e.autopilots[1]._hour = deque([c.t - 10, c.t - 20, c.t - 30])
    run(e, c, 5)
    assert e.autopilots[1].safe_stop and "همگرا" in e.autopilots[1].safe_stop


def test_plc_clamps_writes_to_hard_envelope():
    e, c = make()
    applied = e.plc.write_setpoint(PRESS, 400.0)
    assert applied == 190.0  # hi_hi


# ---------------------------------------------------------------- API

@pytest.fixture
def api():
    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    with SessionLocal() as db:
        seed_admin(db)
    e, c = make()
    app.state.plant = e

    def client_for(user="admin", pw="admin", role=None):
        cl = TestClient(app)
        if role:
            adm = TestClient(app)
            tok = adm.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["access_token"]
            pw = "Passw0rd-test"  # backend requires >= 8 characters
            r = adm.post("/api/users", json={"username": user, "password": pw, "role": role},
                         headers={"Authorization": f"Bearer {tok}"})
            assert r.status_code in (201, 409), r.text
        tok = cl.post("/api/auth/login", json={"username": user, "password": pw}).json()["access_token"]
        cl.headers["Authorization"] = f"Bearer {tok}"
        return cl

    yield SimpleNamespace(e=e, c=c, client_for=client_for, admin=client_for())
    app.state.plant = None


from types import SimpleNamespace  # noqa: E402


def test_plant_endpoints_require_auth(api):
    assert TestClient(app).get("/api/plant/snapshot").status_code == 401


def test_snapshot_shape_and_simulated_flag(api):
    run(api.e, api.c, 3)
    s = api.admin.get("/api/plant/snapshot").json()
    assert s["simulated"] is True and len(s["lines"]) == 2
    assert s["shift"]["assumed"] is True
    assert all(not eq["verified"] for eq in s["equipment"])
    assert any(sen["spark"] for eq in s["equipment"] for sen in eq["sensors"])
    cat = api.admin.get("/api/plant/catalog").json()
    assert cat["verified"] is False and {t["type"] for t in cat["equipment_types"]} >= {"press", "oven", "mixer"}


def test_shifts_endpoint(api):
    j = api.admin.get("/api/plant/shifts").json()
    assert [s["key"] for s in j["shifts"]] == ["morning", "afternoon", "night"] and j["assumed"]


def test_autopilot_rbac_and_confirmation(api):
    op = api.client_for("operator1", "pw", role="operator")
    mgr = api.client_for("mgr1", "pw", role="manager")
    body = {"mode": "autonomous", "confirm": True}
    assert op.put("/api/plant/lines/1/autopilot", json=body).status_code == 403
    assert mgr.put("/api/plant/lines/1/autopilot", json={"mode": "autonomous"}).status_code == 422
    r = mgr.put("/api/plant/lines/1/autopilot", json=body)
    assert r.status_code == 200 and r.json()["mode"] == "autonomous" and r.json()["enabled_by"] == "mgr1"
    assert api.admin.put("/api/plant/lines/9/autopilot", json=body).status_code == 404
    assert op.get("/api/plant/lines/1/autopilot").status_code == 200  # everyone may look


def test_safe_stop_blocks_reenabling_until_manual_restart(api):
    e, c = api.e, api.c
    mgr = api.client_for("mgr2", "pw", role="manager")
    mgr.put("/api/plant/lines/1/autopilot", json={"mode": "autonomous", "confirm": True})
    mgr.post("/api/plant/lines/1/operator-presence", json={"present": False})
    run(e, c, 150)
    e.sim.inject(PRESS, "drift", 2.6, 300, c.t)
    run(e, c, 400)
    assert e.autopilots[1].safe_stop
    assert mgr.put("/api/plant/lines/1/autopilot", json={"mode": "autonomous", "confirm": True}).status_code == 409
    assert mgr.post("/api/plant/lines/1/restart").json()["state"] == "running"
    assert e.autopilots[1].safe_stop is None
    assert mgr.post("/api/plant/lines/1/restart").status_code == 409  # already running


def test_ack_and_fault_injection_api(api):
    e, c = api.e, api.c
    assert api.client_for("op9", "pw", role="operator").post(
        "/api/plant/faults", json={"sensor_id": PRESS, "kind": "dropout"}).status_code == 403
    assert api.admin.post("/api/plant/faults", json={"sensor_id": PRESS, "kind": "dropout", "duration_s": 100}).status_code == 200
    run(e, c, 3)
    aid = e.alarms.active[PRESS].id
    op = api.client_for("op10", "pw", role="operator")
    assert op.post(f"/api/plant/alarms/{aid}/ack").json()["acked_by"] == "op10"
    assert op.post("/api/plant/alarms/99999/ack").status_code == 404


def test_plant_503_when_engine_not_running():
    app.state.plant = None
    Base.metadata.create_all(db_engine)
    with SessionLocal() as db:
        seed_admin(db)
    cl = TestClient(app)
    tok = cl.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["access_token"]
    assert cl.get("/api/plant/snapshot", headers={"Authorization": f"Bearer {tok}"}).status_code == 503


def test_events_are_persisted_when_an_audit_sink_is_attached(api):
    from app.main import _audit_to_db

    e = PlantEngine(lines=1, seed=1, fault_rate=0, clock=api.c, audit=_audit_to_db)
    app.state.plant = e
    run(e, api.c, 5)
    e.sim.inject(PRESS.replace("L1", "L1"), "dropout", 1.0, 100, api.c.t)
    run(e, api.c, 3)
    kinds = {x["kind"] for x in api.admin.get("/api/plant/events").json()}
    assert "alarm_raised" in kinds and "notification" in kinds


def test_controller_does_not_hunt():
    """A correction must stay in place while the deviation persists (no walk-back that re-creates the error)."""
    e, c = make()
    _autonomous(e, c, absent=True)
    e.sim.inject(PRESS, "drift", 0.5, 900, c.t)
    run(e, c, 450)
    sps = []
    for _ in range(300):
        run(e, c, 1)
        sps.append(e.plc.read_setpoint(PRESS))
    assert sps == sorted(sps, reverse=True)  # never increases while engaged


def test_handover_and_mode_off_restore_recipe_setpoints():
    e, c = make()
    _autonomous(e, c, absent=True)
    e.sim.inject(PRESS, "drift", 0.9, 800, c.t)
    run(e, c, 500)
    assert e.plc.read_setpoint(PRESS) < 175.0
    e.sim_operator_present[1] = True
    run(e, c, 3)
    assert e.plc.read_setpoint(PRESS) == 175.0  # handover leaves the recipe, not a silent override
    # same for switching the mode off while engaged
    e.sim_operator_present[1] = False
    e.autopilots[1].heartbeat(c.t - 1000)
    run(e, c, 400)
    assert e.autopilots[1].engaged and e.plc.read_setpoint(PRESS) < 175.0
    e.autopilots[1].set_mode("off", "mgr", c.t)
    run(e, c, 2)
    assert e.plc.read_setpoint(PRESS) == 175.0


def test_expiry_restores_recipe():
    e, c = make()
    _autonomous(e, c, absent=True, max_autonomous_s=500)
    e.sim.inject(PRESS, "drift", 0.9, 800, c.t)
    run(e, c, 520)
    assert e.autopilots[1].mode == "off" and e.plc.read_setpoint(PRESS) == 175.0


def test_loop_stall_is_reported_and_resets_autopilot_timers():
    e, c = make()
    _autonomous(e, c, absent=True)
    e.sim.inject(PRESS, "drift", 0.7, 3000, c.t)
    run(e, c, 300)
    ap = e.autopilots[1]
    assert ap._deviation_since or ap._ema  # timers are running
    c.t += 1000  # laptop sleeps / loop freezes for ~17 minutes
    e.step(c.t)
    assert any(ev["kind"] == "engine_stall" for ev in e.events)
    assert sent(e, "production_manager", "وقفه")
    assert all(v >= c.t for v in ap._deviation_since.values())  # timers restarted at the stall, not carried over
    sp_before = e.plc.read_setpoint(PRESS)
    run(e, c, 30)
    assert e.plc.read_setpoint(PRESS) == sp_before  # no immediate action on pre-stall evidence


def test_simulated_time_does_not_leap_over_a_stall():
    e, c = make()
    run(e, c, 20)
    before = {k: s.value for k, s in e.sim.states.items()}
    c.t += 5000
    e.step(c.t)
    moved = max(abs(e.sim.states[k].value - v) for k, v in before.items() if e.sim.states[k].value is not None and v)
    assert moved < 50  # no explosion after a long gap


def test_shift_schedule_from_json_replaces_the_assumed_default(tmp_path):
    f = tmp_path / "shifts.json"
    f.write_text('{"off_weekdays": [4, 3], "shifts": [{"key": "day", "name_fa": "روز", "start": "07:30", "end": "16:00"},'
                 ' {"key": "eve", "name_fa": "عصر", "start": "16:00", "end": "00:30"}]}', encoding="utf-8")
    s = ShiftSchedule.from_json(str(f))
    mon = _friday() + timedelta(days=3)
    assert s.assumed is False and s.describe()["assumed"] is False
    assert s.at(mon.replace(hour=7, minute=29)).key == "none"  # gap between shifts is reported, not hidden
    assert s.at(mon.replace(hour=7, minute=30)).key == "day"
    tail = s.at(mon.replace(hour=0, minute=15))
    assert tail.key == "eve" and tail.shift_date == (mon - timedelta(days=1)).date().isoformat()
    assert not s.at((_friday() - timedelta(days=1)).replace(hour=10)).working  # Thursday off in this file
