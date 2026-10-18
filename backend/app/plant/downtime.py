"""Downtime tracking with reason codes, and the Pareto of stops."""
from dataclasses import dataclass
from typing import Callable

# code -> (Persian name, category). "unknown" stays until a human assigns a reason: unassigned minutes are reported openly.
REASONS: dict[str, tuple[str, str]] = {
    "mechanical": ("خرابی مکانیکی", "unplanned"),
    "electrical": ("خرابی برقی/کنترلی", "unplanned"),
    "utility": ("قطع تأسیسات (برق/هوا/بخار/آب)", "unplanned"),
    "material": ("کمبود یا تأخیر مواد اولیه", "unplanned"),
    "quality_hold": ("توقف کیفی (عیب زیاد/بررسی)", "unplanned"),
    "operator_absent": ("نبود اپراتور", "unplanned"),
    "safe_stop": ("توقف ایمن خودکار", "unplanned"),
    "sensor": ("خطای سنسور/اندازه‌گیری", "unplanned"),
    "changeover": ("تعویض محصول/قالب", "planned"),
    "maintenance": ("تعمیرات و سرویس برنامه‌ریزی‌شده", "planned"),
    "break": ("استراحت/تعویض شیفت", "planned"),
    "no_order": ("نبود سفارش", "planned"),
    "unknown": ("نامشخص (نیازمند ثبت دلیل)", "unplanned"),
}

CAUSES: dict[str, str] = {  # corrective-action cause codes
    "sensor_fault": "خرابی/کالیبراسیون سنسور",
    "process_deviation": "انحراف فرایند",
    "equipment_wear": "فرسودگی تجهیز",
    "material_issue": "مشکل ماده اولیه",
    "operator_error": "خطای عملیاتی",
    "utility_failure": "مشکل تأسیسات",
    "calibration": "نیاز به کالیبراسیون",
    "other": "سایر",
}


@dataclass
class Stop:
    line: int
    start: float
    reason: str = "unknown"
    source: str = "auto"
    note: str | None = None
    set_by: str | None = None
    db_id: int | None = None
    end: float | None = None


class DowntimeTracker:
    """Opens a Stop when a line stops and closes it when it runs again. `sink(kind, stop)` persists (kind: open|close|update);
    on 'open' the sink may return the database id."""

    def __init__(self, sink: Callable[[str, Stop], int | None] | None = None):
        self.sink = sink
        self.open: dict[int, Stop] = {}
        self.recent: list[Stop] = []

    def observe(self, line: int, running: bool, now: float, reason: str = "unknown", source: str = "auto",
                note: str | None = None, set_by: str | None = None):
        cur = self.open.get(line)
        if not running and cur is None:
            st = Stop(line, now, reason, source, note, set_by)
            self.open[line] = st
            if self.sink:
                st.db_id = self.sink("open", st)
        elif running and cur is not None:
            cur.end = now
            del self.open[line]
            self.recent = ([cur] + self.recent)[:50]
            if self.sink:
                self.sink("close", cur)

    def assign(self, line: int, reason: str, source: str, note: str | None, set_by: str | None):
        """Set the reason on the open stop of a line (when the stop is still in progress)."""
        cur = self.open.get(line)
        if cur is None:
            return None
        cur.reason, cur.source, cur.note, cur.set_by = reason, source, note, set_by
        if self.sink:
            self.sink("update", cur)
        return cur


def pareto(events: list[dict], now_ts: float, window_start_ts: float) -> dict:
    """events: [{start, end|None, reason_code}] with timestamps as floats. Minutes are clipped to the window."""
    minutes: dict[str, float] = {}
    counts: dict[str, int] = {}
    for e in events:
        end = e["end"] if e["end"] is not None else now_ts
        start = max(e["start"], window_start_ts)
        if end <= start:
            continue
        minutes[e["reason_code"]] = minutes.get(e["reason_code"], 0.0) + (end - start) / 60.0
        counts[e["reason_code"]] = counts.get(e["reason_code"], 0) + 1
    total = sum(minutes.values())
    rows, cum = [], 0.0
    for code, m in sorted(minutes.items(), key=lambda kv: -kv[1]):
        cum += m
        name, cat = REASONS.get(code, (code, "unplanned"))
        rows.append({"reason_code": code, "name_fa": name, "category": cat, "minutes": round(m, 1), "count": counts[code],
                     "pct": round(m / total, 4) if total else 0.0, "cumulative_pct": round(cum / total, 4) if total else 0.0})
    cat_min = {"planned": 0.0, "unplanned": 0.0}
    for r in rows:
        cat_min[r["category"]] += r["minutes"]
    return {"total_minutes": round(total, 1), "unassigned_minutes": round(minutes.get("unknown", 0.0), 1),
            "planned_minutes": round(cat_min["planned"], 1), "unplanned_minutes": round(cat_min["unplanned"], 1), "rows": rows}
