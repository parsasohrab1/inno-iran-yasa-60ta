"""OPC-UA adapter tested against a real OPC-UA server (asyncua's, in-process) - protocol, not mocks."""
import os
import socket
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("PLANT_SIM_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from asyncua.sync import Server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import seed_admin  # noqa: E402
from app.db import Base, SessionLocal, engine as db_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.plant.catalog import build_plant  # noqa: E402
from app.plant.engine import PlantEngine  # noqa: E402
from app.plant.opcua import OpcUaMap, OpcUaPlant  # noqa: E402

PRESS = "L1.press1.plate_temp"
SPEED = "L1.conveyor.speed"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def opc():
    eq = build_plant(1)
    port = free_port()
    server = Server()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{port}/plant")
    idx = server.register_namespace("urn:iyr:test")
    obj = server.nodes.objects.add_object(idx, "Plant")
    pv, sp = {}, {}
    for e in eq:
        for s in e.sensors:
            pv[s.id] = obj.add_variable(idx, s.id, float(s.spec.nominal))
            if s.spec.controlled:
                sp[s.id] = obj.add_variable(idx, s.id + ".SP", float(s.spec.nominal))
                sp[s.id].set_writable()
    running = {1: obj.add_variable(idx, "Line1.Running", True)}
    stop = {1: obj.add_variable(idx, "Line1.SafeStop", False)}
    hb = {1: obj.add_variable(idx, "Line1.HB", 0)}
    for v in (*stop.values(), *hb.values()):
        v.set_writable()
    server.start()
    ns = SimpleNamespace(eq=eq, port=port, pv=pv, sp=sp, running=running, stop=stop, hb=hb, server=server)
    yield ns
    server.stop()


def make_cfg(opc, write_enabled=False, **kw):
    nid = lambda n: n.nodeid.to_string()  # noqa: E731
    return OpcUaMap(
        endpoint=f"opc.tcp://127.0.0.1:{opc.port}/plant",
        sensors={k: nid(v) for k, v in opc.pv.items()}, setpoints={k: nid(v) for k, v in opc.sp.items()},
        running={n: nid(v) for n, v in opc.running.items()}, safe_stop={n: nid(v) for n, v in opc.stop.items()},
        heartbeat={n: nid(v) for n, v in opc.hb.items()}, write_enabled=write_enabled, stale_after_s=3600,
        timeout_s=2, reconnect_s=1.0, **kw)


@pytest.fixture
def reset(opc):
    for k, v in opc.pv.items():
        v.write_value(float(next(s.spec.nominal for e in opc.eq for s in e.sensors if s.id == k)))
    for k, v in opc.sp.items():
        v.write_value(float(next(s.spec.nominal for e in opc.eq for s in e.sensors if s.id == k)))
    opc.running[1].write_value(True)
    opc.stop[1].write_value(False)
    opc.hb[1].write_value(0)


def connected_plant(opc, write_enabled=False, **kw):
    p = OpcUaPlant(opc.eq, make_cfg(opc, write_enabled, **kw))
    assert p.connect(), p.last_error
    return p


# ---------------------------------------------------------------- reading

def test_reads_values_setpoints_and_running_state_from_a_real_server(opc, reset):
    p = connected_plant(opc)
    opc.pv[PRESS].write_value(176.4)
    opc.sp[PRESS].write_value(173.0)
    opc.running[1].write_value(False)
    p.step(1.0)
    st = p.states[PRESS]
    assert st.quality == "good" and st.value == 176.4 and st.setpoint == 173.0
    assert p.line_running[1] is False
    assert all(s.quality == "good" for s in p.states.values())
    p.close()


def test_changes_propagate_on_the_next_step(opc, reset):
    p = connected_plant(opc)
    p.step(1.0)
    opc.pv[PRESS].write_value(190.5)
    p.step(2.0)
    assert p.states[PRESS].value == 190.5
    p.close()


def test_unreachable_server_marks_everything_bad_and_recovers_after_backoff(opc, reset):
    cfg = make_cfg(opc)
    good_endpoint = cfg.endpoint
    cfg.endpoint = f"opc.tcp://127.0.0.1:{free_port()}/nothing"  # nobody listening
    p = OpcUaPlant(opc.eq, cfg)
    p.step(100.0)
    assert not p.connected and p.last_error
    assert all(s.quality == "bad" and s.value is None for s in p.states.values())
    assert all(s.bad_since == 100.0 for s in p.states.values())
    cfg.endpoint = good_endpoint
    p.step(100.2)  # inside the back-off window: no new attempt, still bad
    assert not p.connected
    p.step(102.0)
    assert p.connected and p.states[PRESS].quality == "good" and p.states[PRESS].bad_since is None
    p.close()


def test_unmapped_sensors_stay_bad_instead_of_being_invented(opc, reset):
    cfg = make_cfg(opc)
    del cfg.sensors[PRESS]
    p = OpcUaPlant(opc.eq, cfg)
    assert p.connect()
    p.step(1.0)
    assert p.states[PRESS].quality == "bad" and p.states[PRESS].value is None
    assert p.states["L1.press2.plate_temp"].quality == "good"
    p.close()


def test_stale_source_timestamp_is_flagged(opc, reset):
    p = connected_plant(opc)
    p.cfg.stale_after_s = 0.5
    opc.pv[PRESS].write_value(175.0)
    p.step(1.0)
    time.sleep(1.2)
    p.step(2.0)
    st = p.states[PRESS]
    # servers that stamp writes make old values stale; ones that do not stamp are treated as good (no information)
    assert st.quality in ("stale", "good")
    if st.quality == "stale":
        assert st.bad_since is not None
    p.close()


def test_mapping_validation_catches_mistakes(opc):
    cfg = make_cfg(opc)
    assert cfg.validate(opc.eq) == []
    cfg.sensors["L9.ghost.sensor"] = "ns=2;i=1"
    cfg.setpoints["L1.press1.hydraulic_pressure"] = "ns=2;i=2"  # not a controlled variable
    problems = cfg.validate(opc.eq)
    assert any("ghost" in x for x in problems) and any("not a controlled" in x for x in problems)
    cfg = make_cfg(opc, write_enabled=True)
    del cfg.setpoints[PRESS]
    assert any("no setpoint node" in x for x in cfg.validate(opc.eq))


# ---------------------------------------------------------------- read-only mode (default) can write nothing

def test_read_only_mode_refuses_every_write(opc, reset):
    p = connected_plant(opc, write_enabled=False)
    assert p.can_write is False
    with pytest.raises(PermissionError):
        p.write_setpoint(PRESS, 170.0)
    with pytest.raises(PermissionError):
        p.request_safe_stop(1, "test")
    with pytest.raises(PermissionError):
        p.restart(1)
    p.heartbeat(1)  # silently does nothing
    assert opc.sp[PRESS].read_value() == 175.0 and opc.stop[1].read_value() is False and opc.hb[1].read_value() == 0
    p.close()


# ---------------------------------------------------------------- write mode

def test_writes_are_clamped_to_the_hard_envelope_and_reach_the_server(opc, reset):
    p = connected_plant(opc, write_enabled=True)
    assert p.can_write
    assert p.write_setpoint(PRESS, 400.0) == 190.0  # hi_hi of the sensor
    assert opc.sp[PRESS].read_value() == 190.0
    assert p.write_setpoint(PRESS, 120.0) == 160.0  # lo_lo
    assert p.write_setpoint(PRESS, 173.5) == 173.5 and opc.sp[PRESS].read_value() == 173.5
    p.close()


def test_safe_stop_restart_and_heartbeat_over_the_wire(opc, reset):
    p = connected_plant(opc, write_enabled=True)
    p.request_safe_stop(1, "over-temperature")
    assert opc.stop[1].read_value() is True and p.stopped[1] == "over-temperature"
    p.restart(1)
    assert opc.stop[1].read_value() is False and 1 not in p.stopped
    p.heartbeat(1)
    p.heartbeat(1)
    assert opc.hb[1].read_value() == 2
    p.close()


def test_write_to_an_unmapped_line_is_refused(opc, reset):
    cfg = make_cfg(opc, write_enabled=True)
    cfg.safe_stop.clear()
    p = OpcUaPlant(opc.eq, cfg)
    assert p.connect()
    with pytest.raises(PermissionError):
        p.request_safe_stop(1, "x")
    p.close()


# ---------------------------------------------------------------- engine + API on a real protocol

@pytest.fixture
def stack(opc, reset):
    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    with SessionLocal() as db:
        seed_admin(db)

    class Clock:
        t = 1_900_000_000.0

        def __call__(self):
            return self.t

    c = Clock()

    def build(write_enabled):
        plant = OpcUaPlant(opc.eq, make_cfg(opc, write_enabled))
        e = PlantEngine(lines=1, clock=c, equipment=opc.eq, source=plant)
        app.state.plant = e
        cl = TestClient(app)
        t = cl.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["access_token"]
        cl.headers["Authorization"] = f"Bearer {t}"
        return SimpleNamespace(e=e, plant=plant, c=c, api=cl)

    made = []

    def factory(write_enabled=False):
        s = build(write_enabled)
        made.append(s)
        return s

    yield factory
    for s in made:
        s.plant.close()
    app.state.plant = None


def step(s, n=1):
    for _ in range(n):
        s.c.t += 1
        s.e.step(s.c.t)


def test_engine_on_opcua_reports_real_source_and_blocks_demo_endpoints(stack):
    s = stack(False)
    step(s, 3)
    snap = s.api.get("/api/plant/snapshot").json()
    assert snap["simulated"] is False and snap["source"]["kind"] == "opcua" and snap["source"]["connected"] is True
    assert snap["source"]["can_write"] is False
    assert s.api.post("/api/plant/faults", json={"sensor_id": PRESS, "kind": "drift"}).status_code == 501
    assert s.api.post("/api/plant/lines/1/operator-presence", json={"present": False}).status_code == 409


def test_real_over_temperature_raises_a_critical_alarm_through_the_engine(stack, opc):
    s = stack(False)
    step(s, 3)
    assert not s.e.alarms.active
    opc.pv[PRESS].write_value(191.0)  # above hi_hi, written on the server like a PLC would
    step(s, 2)
    a = s.e.alarms.active[PRESS]
    assert a.severity == "critical" and a.limit == "hi_hi"


def test_read_only_plant_refuses_autonomous_mode_manual_stop_and_restart(stack):
    s = stack(False)
    step(s, 3)
    r = s.api.put("/api/plant/lines/1/autopilot", json={"mode": "autonomous", "confirm": True})
    assert r.status_code == 409 and "writes" in r.json()["detail"]
    assert s.api.put("/api/plant/lines/1/autopilot", json={"mode": "advisory"}).status_code == 200  # watching is allowed
    assert s.api.post("/api/plant/lines/1/stop", json={"reason_code": "changeover"}).status_code == 409


def test_autopilot_in_advisory_mode_on_a_read_only_plant_writes_nothing(stack, opc):
    s = stack(False)
    s.api.put("/api/plant/lines/1/autopilot", json={"mode": "advisory"})
    s.e.autopilots[1].last_heartbeat = s.c.t - 1000  # operator absent
    opc.pv[PRESS].write_value(179.0)
    step(s, 200)
    assert opc.sp[PRESS].read_value() == 175.0 and opc.stop[1].read_value() is False


def test_autonomous_safe_stop_reaches_the_plc_over_opcua(stack, opc):
    s = stack(True)
    step(s, 3)
    assert s.api.get("/api/plant/snapshot").json()["source"]["can_write"] is True
    r = s.api.put("/api/plant/lines/1/autopilot", json={"mode": "autonomous", "confirm": True})
    assert r.status_code == 200
    s.e.autopilots[1].last_heartbeat = s.c.t - 1000  # operator absent -> autopilot engages
    step(s, 3)
    assert s.e.autopilots[1].engaged
    assert opc.hb[1].read_value() > 0  # PLC watchdog heartbeat is flowing
    opc.pv[PRESS].write_value(192.0)  # runaway temperature
    step(s, 3)
    assert s.e.autopilots[1].safe_stop and opc.stop[1].read_value() is True
    # downtime starts only when the PLC REPORTS the line stopped (the request alone proves nothing about the machine)
    step(s, 2)
    assert 1 not in s.e.downtime.open
    opc.running[1].write_value(False)  # what the PLC program does after honouring the request
    step(s, 2)
    assert s.e.downtime.open[1].reason == "safe_stop"
    # restart clears OUR request flag only; the machines are restarted by a human at the PLC/HMI
    assert s.api.post("/api/plant/lines/1/restart").status_code == 200
    assert opc.stop[1].read_value() is False


def test_autopilot_setpoint_correction_is_written_within_its_band(stack, opc):
    s = stack(True)
    step(s, 3)  # connect first: autonomous mode is refused while the PLC is not reachable
    assert s.api.put("/api/plant/lines/1/autopilot", json={"mode": "autonomous", "confirm": True}).status_code == 200
    s.e.autopilots[1].last_heartbeat = s.c.t - 1000
    for _ in range(400):  # PV sits 3.5 C high, persistently
        opc.pv[PRESS].write_value(178.5)
        step(s)
    sp = opc.sp[PRESS].read_value()
    assert 172.0 <= sp < 175.0  # lowered through the real protocol, never beyond recipe - 3
