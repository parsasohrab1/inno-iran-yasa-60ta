import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("PLANT_SIM_ENABLED", "false")

from app.plant.oee import Window, by_shift, oee_breakdown, scheduled_windows  # noqa: E402
from app.plant.shifts import IRAN_TZ, ShiftSchedule  # noqa: E402


def monday(h=0):
    d = datetime(2026, 10, 5, h, 0, tzinfo=IRAN_TZ)  # check: Monday
    assert d.weekday() == 0
    return d


def win(hours=8, start=None, shift="morning"):
    s = start or monday(6)
    return Window(s, s + timedelta(hours=hours), shift)


def parts(n, ng, start, minutes):
    return [{"ts": start + timedelta(minutes=i * minutes / n), "status": "NG" if i < ng else "OK"} for i in range(n)]


def test_windows_cover_three_shifts_skip_friday_and_clip_to_the_range():
    s = ShiftSchedule()
    start, end = monday(0), monday(0) + timedelta(days=7)  # Mon .. next Mon, includes one Friday
    ws = scheduled_windows(s, start, end)
    total = sum(w.minutes for w in ws) / 60
    assert total == pytest.approx(6 * 24, abs=1)  # 6 working days x 24 h (Friday off, its night tail removed with it)
    assert {w.shift for w in ws} == {"morning", "afternoon", "night"}
    assert all(start <= w.start < w.end <= end for w in ws)
    half = scheduled_windows(s, monday(6), monday(10))
    assert sum(w.minutes for w in half) == 240 and [w.shift for w in half] == ["morning"]


def test_perfect_run_is_100_percent():
    w = [win()]
    r = oee_breakdown(w, [], parts(480 * 30, 0, w[0].start, 480), ideal_rate=30)
    assert r["oee"] == 1.0 and r["availability"] == 1.0 and r["performance"] == 1.0 and r["quality"] == 1.0
    assert r["minutes"]["fully_productive"] == 480 and r["stop_reasons"] == []


def test_the_three_losses_multiply_and_the_loss_tree_adds_up():
    w = [win()]  # 480 min
    stops = [{"start": w[0].start + timedelta(hours=1), "end": w[0].start + timedelta(hours=1, minutes=48), "reason_code": "mechanical"}]  # 48 min down
    run = 480 - 48
    n = int(run * 30 * 0.8)  # 80 % of ideal speed
    r = oee_breakdown(w, stops, parts(n, int(n * 0.05), w[0].start, 480), ideal_rate=30)
    assert r["availability"] == pytest.approx(0.9, abs=0.001)
    assert r["performance"] == pytest.approx(0.8, abs=0.002)
    assert r["quality"] == pytest.approx(0.95, abs=0.002)
    assert r["oee"] == pytest.approx(0.9 * 0.8 * 0.95, abs=0.003)
    m = r["minutes"]
    assert m["planned"] == 480 and m["unplanned_stops"] == 48 and m["run"] == 432
    assert m["unplanned_stops"] + m["speed_loss"] + m["quality_loss"] + m["fully_productive"] == pytest.approx(m["planned"], abs=0.3)
    assert r["stop_reasons"][0] == {"reason_code": "mechanical", "name_fa": "خرابی مکانیکی", "minutes": 48.0, "pct_of_planned": 0.1}


def test_planned_stops_shrink_planned_time_instead_of_hurting_availability():
    w = [win()]
    stops = [{"start": w[0].start, "end": w[0].start + timedelta(minutes=60), "reason_code": "changeover"}]
    n = int(420 * 30)
    r = oee_breakdown(w, stops, parts(n, 0, w[0].start, 480), ideal_rate=30)
    assert r["minutes"]["planned_stops"] == 60 and r["minutes"]["planned"] == 420 and r["availability"] == 1.0


def test_stops_outside_scheduled_time_are_ignored_and_straddling_stops_are_clipped():
    w = [win()]  # 06:00-14:00
    outside = {"start": monday(15), "end": monday(17), "reason_code": "electrical"}
    straddle = {"start": monday(13), "end": monday(16), "reason_code": "utility"}  # only 13:00-14:00 counts
    r = oee_breakdown(w, [outside, straddle], parts(100, 0, monday(6), 480), ideal_rate=30)
    assert r["minutes"]["unplanned_stops"] == 60
    assert [x["reason_code"] for x in r["stop_reasons"]] == ["utility"]


def test_unknown_reason_stops_count_as_unplanned_availability_loss():
    w = [win()]
    r = oee_breakdown(w, [{"start": monday(7), "end": monday(8), "reason_code": "unknown"}], parts(50, 0, monday(6), 480), 30)
    assert r["stop_reasons"][0]["reason_code"] == "unknown" and r["availability"] == pytest.approx(420 / 480, abs=0.001)


def test_no_inspections_means_unknown_not_zero():
    r = oee_breakdown([win()], [], [], 30)
    assert r["oee"] is None and r["performance"] is None and r["quality"] is None and r["availability"] == 1.0
    assert any("no inspections" in x for x in r["warnings"])


def test_performance_above_100_percent_is_capped_and_flagged():
    w = [win()]
    r = oee_breakdown(w, [], parts(480 * 45, 0, w[0].start, 480), ideal_rate=30)
    assert r["performance"] == 1.0 and any("ideal rate" in x for x in r["warnings"])


def test_empty_window_and_outages_are_reported():
    r = oee_breakdown([], [], [], 30)
    assert r["oee"] is None and r["availability"] is None and any("no scheduled" in x for x in r["warnings"])
    r = oee_breakdown([win()], [], parts(100, 0, monday(6), 480), 30, unobserved_minutes=90)
    assert any("90 min of monitoring outages" in x for x in r["warnings"])


def test_inspections_outside_the_shifts_do_not_inflate_performance():
    w = [win()]
    inside = parts(1000, 0, w[0].start, 480)
    extra = [{"ts": monday(20), "status": "OK"}] * 5000
    r = oee_breakdown(w, [], inside + extra, 30)
    assert r["parts"] == 1000 and any("outside scheduled shifts" in x for x in r["warnings"])


def test_by_shift_splits_the_same_data():
    ws = [win(8, monday(6), "morning"), win(8, monday(14), "afternoon")]
    stops = [{"start": monday(15), "end": monday(16), "reason_code": "mechanical"}]  # only in the afternoon
    ins = parts(8000, 0, monday(6), 480) + parts(7000, 700, monday(14), 480)
    res = {x["shift"]: x for x in by_shift(ws, stops, ins, 30)}
    assert res["morning"]["availability"] == 1.0 and res["afternoon"]["availability"] == pytest.approx(420 / 480, abs=0.001)
    assert res["afternoon"]["quality"] == pytest.approx(0.9, abs=0.001) and res["morning"]["quality"] == 1.0


# ---------------------------------------------------------------- API (database rows, deterministic always-on schedule)
def test_oee_endpoint_combines_downtime_inspections_and_the_schedule():
    import time
    from datetime import time as dtime
    from datetime import timezone

    os.environ["DATABASE_URL"] = "sqlite:///./test.db"
    from fastapi.testclient import TestClient

    from app.auth import seed_admin
    from app.db import Base, SessionLocal, engine as db_engine
    from app.main import _audit_to_db, _downtime_to_db, app
    from app.models import Inspection, Status
    from app.plant.engine import PlantEngine
    from app.plant.shifts import Shift

    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)
    with SessionLocal() as db:
        seed_admin(db)

    class Clock:
        t = time.time() - 3600

        def __call__(self):
            return self.t

    c = Clock()
    always = ShiftSchedule([Shift("all", "همه", dtime(0, 0), dtime(0, 0))], off_weekdays=(), assumed=False)
    e = PlantEngine(lines=1, seed=1, fault_rate=0.0, clock=c, schedule=always, audit=_audit_to_db, downtime_sink=_downtime_to_db)
    app.state.plant = e
    try:
        def run(n):
            for _ in range(n):
                c.t += 1
                e.step(c.t)

        run(300)
        e.stop_line(1, "mechanical", "bearing", "ali")
        run(300)  # 5 minutes stopped
        e.plc.restart(1)
        run(300)
        now_dt = datetime.fromtimestamp(c.t, timezone.utc)
        with SessionLocal() as db:
            for i in range(2000):
                db.add(Inspection(part_id=f"P{i}", line_id=1, shift="A", status=Status.NG if i % 20 == 0 else Status.OK,
                                  created_at=now_dt - timedelta(minutes=1) - timedelta(seconds=i)))
            db.commit()
        cl = TestClient(app)
        tok = cl.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["access_token"]
        r = cl.get("/api/plant/oee?days=1&line=1", headers={"Authorization": f"Bearer {tok}"}).json()
        assert r["schedule_assumed"] is False
        assert r["stop_reasons"][0]["reason_code"] == "mechanical" and 4.5 < r["stop_reasons"][0]["minutes"] < 6.5
        assert r["availability"] == pytest.approx(1 - 5.0 / 1440, abs=0.002)
        assert r["quality"] == pytest.approx(0.95, abs=0.001) and r["parts"] == 2000
        assert r["by_shift"][0]["shift"] == "all"
        allr = cl.get("/api/plant/oee?days=1", headers={"Authorization": f"Bearer {tok}"}).json()
        assert [x["line"] for x in allr["by_line"]] == [1]
    finally:
        app.state.plant = None


def test_observed_intervals_bridge_small_gaps_and_cut_at_outages():
    from app.plant.oee import clip_to_observed, observed_intervals

    t0 = monday(6)
    beats = [t0 + timedelta(minutes=m) for m in (0, 1, 2, 3)] + [t0 + timedelta(minutes=m) for m in (60, 61, 62)]
    ivs = observed_intervals(beats)
    assert len(ivs) == 2 and ivs[0] == (t0, t0 + timedelta(minutes=3)) and ivs[1][0] == t0 + timedelta(minutes=60)
    w = [Window(t0, t0 + timedelta(hours=2), "morning")]
    clipped = clip_to_observed(w, ivs)
    assert sum(x.minutes for x in clipped) == 3 + 2  # only the two observed stretches count
    assert clip_to_observed(w, []) == []


def test_oee_over_observed_time_only_is_not_diluted_by_unwatched_hours():
    """Regression: a 3-hour-old system must not report 3 % OEE for a 7-day window."""
    from app.plant.oee import clip_to_observed, observed_intervals

    sched = [Window(monday(0), monday(0) + timedelta(days=6), "all")]
    t0 = monday(10)
    beats = [t0 + timedelta(minutes=m) for m in range(0, 181)]  # observed for 3 hours
    observed = clip_to_observed(sched, observed_intervals(beats))
    ins = parts(180 * 30, 0, t0, 180)
    diluted = oee_breakdown(sched, [], ins, 30)
    honest = oee_breakdown(observed, [], ins, 30)
    assert diluted["oee"] < 0.05 and honest["oee"] == pytest.approx(1.0, abs=0.01)
    assert honest["minutes"]["planned"] == 180
