"""OEE = Availability x Performance x Quality, with the loss tree (as in MES products such as Siemens Opcenter OEE).

Definitions used (stated so nobody has to guess):
  planned time   scheduled shift time in the window, minus planned stops (changeover, planned maintenance, breaks, no order)
  availability   (planned time - unplanned stops) / planned time
  performance    parts inspected / (run time x ideal rate), capped at 1
  quality        first-pass yield = 1 - NG / parts inspected
Loss tree (minutes): planned time -> unplanned stops by reason -> speed loss -> quality loss -> fully productive time.

Honest limits: performance uses the inspection count as the produced count (the inspection station sees every part); stops are
only known while the monitoring system was running (unobserved gaps are reported, not guessed).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from .downtime import REASONS
from .shifts import IRAN_TZ, ShiftSchedule


@dataclass
class Window:
    start: datetime
    end: datetime
    shift: str

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


def scheduled_windows(schedule: ShiftSchedule, start: datetime, end: datetime) -> list[Window]:
    """Working shift windows between start and end (clipped to them)."""
    start, end = start.astimezone(IRAN_TZ), end.astimezone(IRAN_TZ)
    out = []
    d = start.date() - timedelta(days=1)
    while d <= end.date():
        if d.weekday() not in schedule.off_weekdays:
            for s in schedule.shifts:
                overnight = s.start >= s.end
                ws = datetime.combine(d, s.start, IRAN_TZ)
                we = datetime.combine(d + timedelta(days=1) if overnight else d, s.end, IRAN_TZ)
                ws, we = max(ws, start), min(we, end)
                if we > ws:
                    out.append(Window(ws, we, s.key))
        d += timedelta(days=1)
    return out


def observed_intervals(beats: list[datetime], max_gap_s: float = 150.0) -> list[tuple[datetime, datetime]]:
    """Time the monitoring loop was demonstrably running: consecutive heartbeats closer than `max_gap_s` bridge the gap between them."""
    ivs: list[tuple[datetime, datetime]] = []
    for t in sorted(beats):
        if ivs and (t - ivs[-1][1]).total_seconds() <= max_gap_s:
            ivs[-1] = (ivs[-1][0], t)
        else:
            ivs.append((t, t))
    return [(a, b) for a, b in ivs if b > a]


def clip_to_observed(windows: list[Window], observed: list[tuple[datetime, datetime]]) -> list[Window]:
    out = []
    for w in windows:
        for a, b in observed:
            s, e = max(w.start, a), min(w.end, b)
            if e > s:
                out.append(Window(s, e, w.shift))
    return out


def _overlap(a: datetime, b: datetime, w: Window) -> float:
    return max(0.0, (min(b, w.end) - max(a, w.start)).total_seconds() / 60.0)


def oee_breakdown(windows: list[Window], stops: list[dict], inspections: list[dict], ideal_rate: float,
                  unobserved_minutes: float = 0.0) -> dict:
    """stops: [{start, end, reason_code}] (datetimes); inspections: [{ts, status}]. Returns the OEE with its loss tree."""
    scheduled = sum(w.minutes for w in windows)
    planned_stop = 0.0
    unplanned: dict[str, float] = {}
    for st in stops:
        cat = REASONS.get(st["reason_code"], ("", "unplanned"))[1]
        m = sum(_overlap(st["start"], st["end"], w) for w in windows)
        if m <= 0:
            continue
        if cat == "planned":
            planned_stop += m
        else:
            unplanned[st["reason_code"]] = unplanned.get(st["reason_code"], 0.0) + m
    planned = max(scheduled - planned_stop, 0.0)
    down = min(sum(unplanned.values()), planned)
    run = planned - down
    total = ng = outside = 0
    for r in inspections:
        if any(w.start <= r["ts"] < w.end for w in windows):
            total += 1
            ng += r["status"] == "NG"
        else:
            outside += 1
    warnings = []
    A = None if planned <= 0 else run / planned
    P = Q = None
    if total and run > 0 and ideal_rate > 0:
        raw = total / (run * ideal_rate)
        P = min(1.0, raw)
        if raw > 1.05:
            warnings.append(f"performance > 100% ({raw:.0%}): the ideal rate ({ideal_rate}/min) is too low or stop data is incomplete")
    if total:
        Q = 1 - ng / total
    else:
        warnings.append("no inspections inside the scheduled windows: performance and quality are unknown")
    if not windows:
        warnings.append("no scheduled shift time in this window (non-working days?)")
    if unobserved_minutes > 0:
        warnings.append(f"{unobserved_minutes:.0f} min of monitoring outages: stops during them are not known")
    if outside:
        warnings.append(f"{outside} inspections happened outside scheduled shifts and were ignored")
    oee = None if None in (A, P, Q) else A * P * Q
    speed_loss = run * (1 - P) if P is not None else None
    quality_loss = run * P * (1 - Q) if P is not None and Q is not None else None
    productive = run * P * Q if oee is not None else None
    r = lambda x, n=3: None if x is None else round(x, n)  # noqa: E731
    return {
        "oee": r(oee), "availability": r(A), "performance": r(P), "quality": r(Q),
        "parts": total, "ng": ng,
        "minutes": {"scheduled": r(scheduled, 1), "planned_stops": r(planned_stop, 1), "planned": r(planned, 1),
                    "unplanned_stops": r(down, 1), "run": r(run, 1), "speed_loss": r(speed_loss, 1),
                    "quality_loss": r(quality_loss, 1), "fully_productive": r(productive, 1)},
        "stop_reasons": [{"reason_code": k, "name_fa": REASONS.get(k, (k,))[0], "minutes": round(v, 1),
                          "pct_of_planned": r(v / planned) if planned else None}
                         for k, v in sorted(unplanned.items(), key=lambda kv: -kv[1])],
        "ideal_rate": ideal_rate, "warnings": warnings,
    }


def by_shift(windows: list[Window], stops, inspections, ideal_rate) -> list[dict]:
    keys = sorted({w.shift for w in windows})
    return [{"shift": k, **oee_breakdown([w for w in windows if w.shift == k], stops, inspections, ideal_rate)} for k in keys]
