import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("PLANT_SIM_ENABLED", "false")

from app.plant.anomaly import AnomalyDetector, feature_ids  # noqa: E402
from app.plant.catalog import build_plant  # noqa: E402
from app.plant.engine import PlantEngine  # noqa: E402

PRESS = "L1.press1.plate_temp"
LEARN = 900


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(seed=1, **kw):
    c = Clock()
    e = PlantEngine(lines=1, seed=seed, fault_rate=0.0, clock=c, anomaly_min_samples=LEARN, **kw)
    return e, c


def run(e, c, seconds):
    for _ in range(int(seconds)):
        c.t += 1
        e.step(c.t)


def anomaly_alarms(e):
    return [a for a in list(e.alarms.active.values()) + list(e.alarms.history) if a.sensor_id.startswith("anomaly:")]


def test_learning_progress_then_active():
    e, c = make()
    assert e.anomaly.status()["state"] == "learning"
    run(e, c, LEARN // 2)
    assert 0.3 < e.anomaly.progress() < 0.8
    run(e, c, LEARN)
    s = e.anomaly.status()
    assert s["state"] == "active" and s["models"] == s["equipment"]


def test_no_false_anomalies_on_long_normal_operation():
    e, c = make()
    run(e, c, LEARN + 3600)  # 1 h of normal operation after learning
    assert not anomaly_alarms(e), [(a.message, a.raised_at) for a in anomaly_alarms(e)]


def test_drift_is_detected_before_the_fixed_limit_alarm():
    e, c = make()
    run(e, c, LEARN + 60)
    e.sim.inject(PRESS, "drift", 1.0, 3000, c.t)  # slow ramp to +7 C over 30 min; the fixed warning limit is +7 C
    detected_pv = None
    for _ in range(1800):
        run(e, c, 1)
        if any(a.sensor_id == "anomaly:L1.press1" for a in e.alarms.active.values()):
            detected_pv = e.sim.states[PRESS].value
            break
    assert detected_pv is not None, "drift was never flagged"
    assert detected_pv < 182.0  # flagged while the value is still inside the fixed limits (> 1 C margin)
    assert PRESS not in e.alarms.active  # the classic limit alarm has not fired yet
    a = next(a for a in e.alarms.active.values() if a.sensor_id == "anomaly:L1.press1")
    assert "دمای صفحه قالب" in a.message and a.severity == "warning"


def test_anomaly_alarm_clears_after_recovery():
    e, c = make()
    run(e, c, LEARN + 60)
    e.sim.inject(PRESS, "drift", 0.8, 300, c.t)
    run(e, c, 300)
    assert any(a.sensor_id == "anomaly:L1.press1" for a in anomaly_alarms(e))
    run(e, c, 400)  # fault over, value back to normal
    assert "anomaly:L1.press1" not in e.alarms.active


def test_setpoint_moves_are_not_anomalies():
    """Controlled variables are scored on control error, so an autopilot/operator setpoint change must not alarm."""
    e, c = make()
    run(e, c, LEARN + 60)
    e.sim.set_setpoint(PRESS, 172.0)
    run(e, c, 600)
    assert not [a for a in anomaly_alarms(e) if a.sensor_id == "anomaly:L1.press1"]


def test_unhealthy_samples_do_not_pollute_learning():
    e, c = make()
    run(e, c, 60)
    e.sim.inject(PRESS, "drift", 2.0, 100, c.t)  # large excursion during learning (limit alarm active -> sample excluded)
    run(e, c, LEARN + 150)
    m = e.anomaly.models["L1.press1"]
    i = m.ids.index(PRESS)
    assert m.std[i] < 2.5  # roughly the sensor's own noise, not inflated by the excursion
    assert abs(m.mean[i]) < 1.0  # error feature (pv - setpoint) is centred


def test_model_save_and_load_roundtrip(tmp_path):
    e, c = make()
    run(e, c, LEARN + 5)
    p = str(tmp_path / "m.json")
    e.anomaly.save(p)
    fresh = AnomalyDetector(e.equipment, min_samples=LEARN)
    assert fresh.load(p) == len(e.anomaly.models) and not fresh.learning
    a, b = e.anomaly.models["L1.press1"], fresh.models["L1.press1"]
    assert np.allclose(a.mean, b.mean) and np.allclose(a.cov_inv, b.cov_inv) and a.pattern_thr == b.pattern_thr


def test_speed_dependent_measurements_are_left_to_fixed_limits():
    eq = {x.id: x for x in build_plant(1)}
    ids = feature_ids(eq["L1.conveyor"])
    assert "L1.conveyor.speed" in ids and "L1.conveyor.motor_current" not in ids
    assert "L1.inspection.throughput" not in feature_ids(eq["L1.inspection"])


def _fake_states(eq, rng, shift=None):
    st = {}
    for s in eq.sensors:
        v = s.spec.nominal + rng.normal(0, s.spec.noise)
        sp = s.spec.nominal if s.spec.controlled else None
        if shift and s.id in shift:
            v += shift[s.id]
        st[s.id] = SimpleNamespace(value=v, quality="good", setpoint=sp)
    return st


def test_pattern_detector_catches_a_combination_no_single_sensor_shows():
    eq = next(x for x in build_plant(1) if x.id == "L1.press1")
    det = AnomalyDetector([eq], min_samples=400)
    rng = np.random.default_rng(0)
    for _ in range(400):
        det.learn(eq.id, _fake_states(eq, rng), True)
    assert not det.learning
    m = det.models[eq.id]
    sig = {s.id: m.std[m.ids.index(s.id)] for s in eq.sensors if s.id in m.ids}
    keys = [k for k in m.ids][:4]
    shift = {k: 3.2 * sig[k] for k in keys}  # each below the 3.5 sigma single-sensor threshold, together unusual
    out = None
    for t in range(60):
        out = det.update(eq.id, _fake_states(eq, rng, shift), 1000.0 + t, 1.0)
    assert out and out["kind"] == "pattern"
    # and the same detector is quiet on normal data
    quiet = AnomalyDetector([eq], min_samples=400)
    rng2 = np.random.default_rng(1)
    for _ in range(400):
        quiet.learn(eq.id, _fake_states(eq, rng2), True)
    res = [quiet.update(eq.id, _fake_states(eq, rng2), 2000.0 + t, 1.0) for t in range(600)]
    assert not any(res)


def test_status_lists_active_anomalies_in_the_snapshot():
    e, c = make()
    run(e, c, LEARN + 60)
    e.sim.inject(PRESS, "drift", 0.9, 600, c.t)
    run(e, c, 450)
    snap = e.snapshot(spark=False)
    assert snap["anomaly"]["state"] == "active"
    assert any(x["equipment_id"] == "L1.press1" for x in snap["anomaly"]["active"])
