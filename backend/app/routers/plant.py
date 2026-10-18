"""Plant API: live (simulated) equipment/sensor data, alarms, shifts, notifications and the line autopilots."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, require
from ..db import get_db
from ..models import CorrectiveAction, DowntimeEvent, PlantEvent, Role, User
from ..plant.catalog import EQUIPMENT_TYPES
from ..plant.downtime import CAUSES, REASONS, pareto

router = APIRouter(prefix="/api/plant", tags=["plant"])
_manager = require(Role.MANAGER, Role.QC)  # production manager / QC (admin is always allowed)
_admin = require()


def engine(request: Request):
    e = getattr(request.app.state, "plant", None)
    if e is None:
        raise HTTPException(503, "Plant engine is not running (PLANT_SIM_ENABLED=false and no adapter attached)")
    return e


def _line(e, n: int):
    if n not in e.autopilots:
        raise HTTPException(404, f"Unknown line {n}")
    return e.autopilots[n]


@router.get("/snapshot")
def snapshot(e=Depends(engine), _: User = Depends(current_user)):
    return e.snapshot(spark=True)


@router.get("/shifts")
def shifts(e=Depends(engine), _: User = Depends(current_user)):
    from datetime import datetime

    from ..plant.shifts import IRAN_TZ

    cur = e.schedule.at(datetime.now(IRAN_TZ))
    return {**e.schedule.describe(), "current": {"key": cur.key, "name_fa": cur.name_fa, "date": cur.shift_date,
                                                 "working": cur.working, "ends_at": cur.end.isoformat()}}


@router.get("/catalog")
def catalog(_: User = Depends(current_user)):
    """The sensors the smart plant needs, per equipment type (typical, to be confirmed on site)."""
    return {"verified": False, "equipment_types": [
        {"type": k, "name_fa": v["name_fa"], "name_en": v["name_en"],
         "sensors": [{"key": s.key, "name_fa": s.name_fa, "unit": s.unit, "kind": s.kind, "nominal": s.nominal,
                      "lo": s.lo, "hi": s.hi, "lo_lo": s.lo_lo, "hi_hi": s.hi_hi, "controlled": s.controlled,
                      "safety": s.safety} for s in v["sensors"]]} for k, v in EQUIPMENT_TYPES.items()]}


@router.get("/events")
def events(limit: int = 100, kind: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    q = select(PlantEvent).order_by(PlantEvent.ts.desc()).limit(min(limit, 500))
    if kind:
        q = q.where(PlantEvent.kind == kind)
    return [{"id": r.id, "ts": r.ts.isoformat(), "kind": r.kind, "line": r.line, "severity": r.severity,
             "summary": r.summary, "payload": r.payload} for r in db.scalars(q)]


@router.post("/alarms/{alarm_id}/ack")
def ack(alarm_id: int, e=Depends(engine), user: User = Depends(current_user)):
    a = e.alarms.ack(alarm_id, user.username, e.clock())
    if not a:
        raise HTTPException(404, "Alarm not active")
    return a.to_dict()


class AutopilotIn(BaseModel):
    mode: Literal["off", "advisory", "autonomous"]
    confirm: bool = False  # autonomous needs an explicit confirmation
    reason: str | None = Field(default=None, max_length=200)


@router.put("/lines/{n}/autopilot")
def set_autopilot(n: int, body: AutopilotIn, e=Depends(engine), user: User = Depends(_manager)):
    ap = _line(e, n)
    if body.mode == "autonomous":
        if not body.confirm:
            raise HTTPException(422, "autonomous mode requires confirm=true")
        if ap.safe_stop:
            raise HTTPException(409, "line is in latched safe stop; restart it manually first")
        if not getattr(e.plc, "can_write", False):
            raise HTTPException(409, "autonomous mode needs a PLC that accepts writes (connected and write_enabled)")
    ap.set_mode(body.mode, user.username, e.clock())
    e._audit("autopilot_mode", n, "warning" if body.mode == "autonomous" else "info",
             f"اتوپایلوت خط {n}: {body.mode} توسط {user.username}" + (f" – {body.reason}" if body.reason else ""))
    return ap.status(e.clock())


@router.get("/lines/{n}/autopilot")
def get_autopilot(n: int, e=Depends(engine), _: User = Depends(current_user)):
    return _line(e, n).status(e.clock())


class PresenceIn(BaseModel):
    present: bool


@router.post("/lines/{n}/operator-presence")
def presence(n: int, body: PresenceIn, e=Depends(engine), user: User = Depends(_manager)):
    """DEMO control standing in for badge/HMI/camera presence. A real adapter calls operator-heartbeat instead."""
    _line(e, n)
    if not e.simulated:
        raise HTTPException(409, "operator presence comes from the real presence source on a real plant (use operator-heartbeat)")
    e.sim_operator_present[n] = body.present
    e._audit("operator_presence", n, "info", f"حضور اپراتور خط {n}: {'حاضر' if body.present else 'غایب'} ({user.username}, شبیه‌سازی)")
    return {"line": n, "present": body.present}


@router.post("/lines/{n}/operator-heartbeat")
def heartbeat(n: int, e=Depends(engine), user: User = Depends(current_user)):
    ap = _line(e, n)
    ap.heartbeat(e.clock())
    return {"line": n, "ok": True}


@router.post("/lines/{n}/restart")
def restart(n: int, e=Depends(engine), user: User = Depends(_manager)):
    ap = _line(e, n)
    if e.sim.line_running.get(n, True) and not ap.safe_stop:
        raise HTTPException(409, "line is already running")
    if not getattr(e.plc, "can_write", False):
        raise HTTPException(409, "PLC writes are disabled: clear the stop at the machine/HMI")
    e.plc.restart(n)
    ap.reset_safe_stop(user.username, e.clock())
    e._audit("line_restart", n, "warning", f"راه‌اندازی مجدد خط {n} توسط {user.username}")
    return e.line_summary(n)


class FaultIn(BaseModel):
    sensor_id: str
    kind: Literal["drift", "spike", "stuck", "dropout"]
    magnitude: float = 1.5
    duration_s: float = Field(default=120, ge=5, le=3600)


@router.post("/faults")
def inject_fault(body: FaultIn, e=Depends(engine), user: User = Depends(_admin)):
    """DEMO: inject a fault into the simulator to exercise alarms, escalation and the autopilot."""
    if not e.simulated:
        raise HTTPException(501, "fault injection exists only on the simulator")
    if body.sensor_id not in e.sim.states:
        raise HTTPException(404, "unknown sensor")
    e.sim.inject(body.sensor_id, body.kind, body.magnitude, body.duration_s, e.clock())
    e._audit("fault_injected", e.sim.states[body.sensor_id].sensor.line, "warning",
             f"خطای آزمایشی {body.kind} روی {body.sensor_id} ({user.username})")
    return {"ok": True}


# ---------------------------------------------------------------------------------------------- downtime
class StopIn(BaseModel):
    reason_code: str
    note: str | None = Field(default=None, max_length=200)


class ReasonIn(BaseModel):
    reason_code: str
    note: str | None = Field(default=None, max_length=200)


def _ts_dt(dt) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ts(dt) -> float | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


@router.get("/downtime/reasons")
def downtime_reasons(_: User = Depends(current_user)):
    return [{"code": k, "name_fa": v[0], "category": v[1]} for k, v in REASONS.items()]


@router.post("/lines/{n}/stop")
def stop_line(n: int, body: StopIn, e=Depends(engine), user: User = Depends(current_user)):
    ap = _line(e, n)
    if body.reason_code not in REASONS:
        raise HTTPException(422, "unknown reason code")
    if not e.sim.line_running.get(n, True) or ap.safe_stop:
        raise HTTPException(409, "line is already stopped")
    if not getattr(e.plc, "can_write", False):
        raise HTTPException(409, "PLC writes are disabled: stop the line at the machine/HMI, then assign the reason")
    e.stop_line(n, body.reason_code, body.note, user.username)
    return e.line_summary(n)


def _now(request: Request) -> datetime:
    """The engine's clock when it is running (one clock for the whole plant), else the system clock."""
    e = getattr(request.app.state, "plant", None)
    return datetime.fromtimestamp(e.clock(), timezone.utc) if e else datetime.now(timezone.utc)


@router.get("/downtime")
def downtime_list(request: Request, days: int = 7, line: int | None = None, open_only: bool = False,
                  db: Session = Depends(get_db), _: User = Depends(current_user)):
    since = _now(request) - timedelta(days=days)
    q = (select(DowntimeEvent).where((DowntimeEvent.end.is_(None)) | (DowntimeEvent.end >= since))
         .order_by(DowntimeEvent.start.desc()).limit(500))
    if line:
        q = q.where(DowntimeEvent.line == line)
    if open_only:
        q = q.where(DowntimeEvent.end.is_(None))
    now = _now(request).timestamp()
    out = []
    for r in db.scalars(q):
        s, en = _ts(r.start), _ts(r.end)
        out.append({"id": r.id, "line": r.line, "start": s, "end": en, "minutes": round(((en or now) - s) / 60.0, 1),
                    "reason_code": r.reason_code, "reason_fa": REASONS.get(r.reason_code, (r.reason_code,))[0],
                    "source": r.reason_source, "note": r.note, "set_by": r.set_by})
    return out


@router.patch("/downtime/{event_id}")
def downtime_assign(event_id: int, body: ReasonIn, e=Depends(engine), db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    if body.reason_code not in REASONS:
        raise HTTPException(422, "unknown reason code")
    r = db.get(DowntimeEvent, event_id)
    if not r:
        raise HTTPException(404, "stop not found")
    r.reason_code, r.reason_source, r.note, r.set_by = body.reason_code, "manual", body.note, user.username
    db.commit()
    cur = e.downtime.open.get(r.line)
    if cur and cur.db_id == event_id:  # keep the in-memory open stop consistent
        cur.reason, cur.source, cur.note, cur.set_by = body.reason_code, "manual", body.note, user.username
    e._audit("downtime_reason", r.line, "info", f"دلیل توقف خط {r.line}: {REASONS[body.reason_code][0]} ({user.username})")
    return {"id": r.id, "reason_code": r.reason_code}


@router.get("/downtime/pareto")
def downtime_pareto(request: Request, days: int = 7, line: int | None = None, db: Session = Depends(get_db),
                    _: User = Depends(current_user)):
    now = _now(request)
    since = now - timedelta(days=days)
    q = select(DowntimeEvent).where((DowntimeEvent.end.is_(None)) | (DowntimeEvent.end >= since))
    if line:
        q = q.where(DowntimeEvent.line == line)
    ev = [{"start": _ts(r.start), "end": _ts(r.end), "reason_code": r.reason_code} for r in db.scalars(q)]
    return {"days": days, "line": line, **pareto(ev, now.timestamp(), since.timestamp())}


# ---------------------------------------------------------------------------------------------- corrective actions
class ActionIn(BaseModel):
    cause_code: str
    action_text: str = Field(min_length=3, max_length=1000)
    done: bool = False


class ActionPatch(BaseModel):
    status: Literal["open", "done"] = "done"
    action_text: str | None = Field(default=None, min_length=3, max_length=1000)


def _action_row(a: CorrectiveAction) -> dict:
    return {"id": a.id, "created_at": a.created_at.isoformat(), "alarm_id": a.alarm_id, "sensor_key": a.sensor_key,
            "line": a.line, "severity": a.severity, "alarm_message": a.alarm_message, "cause_code": a.cause_code,
            "cause_fa": CAUSES.get(a.cause_code, a.cause_code), "action_text": a.action_text, "status": a.status,
            "created_by": a.created_by, "closed_by": a.closed_by,
            "closed_at": a.closed_at.isoformat() if a.closed_at else None}


@router.get("/causes")
def causes(_: User = Depends(current_user)):
    return [{"code": k, "name_fa": v} for k, v in CAUSES.items()]


@router.post("/alarms/{alarm_id}/action", status_code=201)
def record_action(alarm_id: int, body: ActionIn, e=Depends(engine), db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """Record cause + corrective action for an alarm (active or recently cleared). Also acknowledges it."""
    if body.cause_code not in CAUSES:
        raise HTTPException(422, "unknown cause code")
    alarm = next((a for a in list(e.alarms.active.values()) + list(e.alarms.history) if a.id == alarm_id), None)
    if alarm is None:
        raise HTTPException(404, "alarm not found (only active and recent alarms can be actioned)")
    if alarm.cleared_at is None:
        e.alarms.ack(alarm.id, user.username, e.clock())
    row = CorrectiveAction(alarm_id=alarm.id, sensor_key=alarm.sensor_id, raised_at=alarm.raised_at, line=alarm.line,
                           severity=alarm.severity, alarm_message=alarm.message, cause_code=body.cause_code,
                           action_text=body.action_text, status="done" if body.done else "open", created_by=user.username,
                           closed_by=user.username if body.done else None,
                           closed_at=datetime.now(timezone.utc) if body.done else None)
    db.add(row)
    db.commit()
    e._audit("corrective_action", alarm.line, "info", f"اقدام اصلاحی ثبت شد ({user.username}): {body.action_text[:80]}",
             {"alarm_id": alarm.id, "sensor": alarm.sensor_id, "raised_at": alarm.raised_at})
    return _action_row(row)


@router.get("/actions")
def list_actions(status: str | None = None, line: int | None = None, days: int = 30, db: Session = Depends(get_db),
                 _: User = Depends(current_user)):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = (select(CorrectiveAction).where(CorrectiveAction.created_at >= since)
         .order_by(CorrectiveAction.created_at.desc()).limit(300))
    if status:
        q = q.where(CorrectiveAction.status == status)
    if line:
        q = q.where(CorrectiveAction.line == line)
    return [_action_row(a) for a in db.scalars(q)]


@router.patch("/actions/{action_id}")
def update_action(action_id: int, body: ActionPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = db.get(CorrectiveAction, action_id)
    if not a:
        raise HTTPException(404, "action not found")
    if body.action_text:
        a.action_text = body.action_text
    a.status = body.status
    if body.status == "done":
        a.closed_by, a.closed_at = user.username, datetime.now(timezone.utc)
    else:
        a.closed_by = a.closed_at = None
    db.commit()
    return _action_row(a)


@router.get("/actions/coverage")
def action_coverage(days: int = 7, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Share of critical alarms that have a recorded corrective action (matched by sensor key + raised time)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    raised = [r for r in db.scalars(select(PlantEvent).where(PlantEvent.kind == "alarm_raised", PlantEvent.ts >= since))
              if (r.payload or {}).get("severity") == "critical"]
    done = {(a.sensor_key, a.raised_at) for a in
            db.scalars(select(CorrectiveAction).where(CorrectiveAction.created_at >= since))}
    missing = [r for r in raised if (r.payload.get("sensor"), r.payload.get("raised_at")) not in done]
    return {"days": days, "critical_total": len(raised), "with_action": len(raised) - len(missing),
            "coverage": round((len(raised) - len(missing)) / len(raised), 3) if raised else None,
            "without_action": [{"ts": _ts(r.ts), "line": r.line, "summary": r.summary,
                                "alarm_id": r.payload.get("alarm_id")} for r in missing[:30]]}


# ---------------------------------------------------------------------------------------------- OEE
@router.get("/oee")
def oee(request: Request, days: int = 7, line: int | None = None, db: Session = Depends(get_db),
        _: User = Depends(current_user)):
    """Availability x Performance x Quality with the loss tree, from real downtime events, the shift schedule and inspections."""
    from sqlalchemy import func

    from ..models import Inspection, Product
    from ..models import MonitorBeat
    from ..plant.oee import by_shift, clip_to_observed, observed_intervals, oee_breakdown, scheduled_windows
    from ..plant.shifts import ShiftSchedule

    days = max(1, min(days, 90))
    e = getattr(request.app.state, "plant", None)
    schedule = e.schedule if e else ShiftSchedule()
    end = _now(request)
    start = end - timedelta(days=days)
    scheduled = scheduled_windows(schedule, start, end)
    beats = [_ts_dt(b.ts) for b in db.scalars(select(MonitorBeat).where(MonitorBeat.ts >= start))]
    coverage_note = None
    if beats:
        windows = clip_to_observed(scheduled, observed_intervals(beats))
    else:  # no heartbeat records at all (e.g. a deployment without the sink): assume full coverage, but say so
        windows = scheduled
        coverage_note = "no monitoring heartbeat records: availability assumes the monitoring system ran during the whole window"
    sched_min = sum(w.minutes for w in scheduled)
    obs_min = sum(w.minutes for w in windows)
    end_ts = end.timestamp()

    rate = db.scalar(select(func.avg(Product.rate_per_min))) or 30.0
    stalls = sum((r.payload or {}).get("gap_s", 0.0) for r in db.scalars(
        select(PlantEvent).where(PlantEvent.kind == "engine_stall", PlantEvent.ts >= start))) / 60.0

    def load(line_no: int | None):
        q = select(DowntimeEvent).where((DowntimeEvent.end.is_(None)) | (DowntimeEvent.end >= start))
        iq = select(Inspection.created_at, Inspection.status).where(Inspection.created_at >= start)
        if line_no:
            q, iq = q.where(DowntimeEvent.line == line_no), iq.where(Inspection.line_id == line_no)
        stops = [{"start": datetime.fromtimestamp(_ts(r.start), timezone.utc),
                  "end": datetime.fromtimestamp(_ts(r.end) if r.end else end_ts, timezone.utc),
                  "reason_code": r.reason_code} for r in db.scalars(q)]
        ins = [{"ts": datetime.fromtimestamp(_ts(t), timezone.utc), "status": s.value if hasattr(s, "value") else s}
               for t, s in db.execute(iq)]
        return stops, ins

    stops, ins = load(line)
    out = {"days": days, "line": line, "schedule_assumed": schedule.assumed,
           **oee_breakdown(windows, stops, ins, float(rate), unobserved_minutes=stalls),
           "scheduled_minutes": round(sched_min, 1), "observed_minutes": round(obs_min, 1),
           "coverage": None if sched_min == 0 else round(obs_min / sched_min, 3),
           "by_shift": by_shift(windows, stops, ins, float(rate))}
    if line is None and e:
        out["by_line"] = []
        for n in e.autopilots:
            st, i2 = load(n)
            out["by_line"].append({"line": n, **oee_breakdown(windows, st, i2, float(rate), unobserved_minutes=stalls)})
    if schedule.assumed:
        out["warnings"].append("shift hours are the assumed default, not the factory's confirmed schedule")
    if coverage_note:
        out["warnings"].append(coverage_note)
    elif out["coverage"] is not None and out["coverage"] < 0.95:
        out["warnings"].append(f"only {out['coverage']:.0%} of the scheduled time was observed by the monitoring system; OEE covers that part only")
    return out
