"""Limit alarms with hysteresis, acknowledgement and time-based escalation operator -> production manager -> plant manager."""
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable

from .notify import ROLE_FA, ROLES, Notifier
from .simulator import SensorState

SEVERITY_RANK = {"warning": 1, "critical": 2}
LIMIT_FA = {"hi": "حد بالا", "hi_hi": "حد بالای بحرانی", "lo": "حد پایین", "lo_lo": "حد پایین بحرانی", "quality": "کیفیت داده", "anomaly": "ناهنجاری"}
DEFAULT_ESCALATION = {  # seconds after raise at which operator / production manager / plant manager are told
    "critical": [0, 120, 300],
    "warning": [0, 600, 1800],
}


@dataclass
class Alarm:
    id: int
    sensor_id: str
    line: int
    equipment_name: str
    sensor_name: str
    unit: str
    severity: str
    limit: str
    limit_value: float | None
    value: float | None
    raised_at: float
    message: str
    esc_base: float  # escalation clock start (reset when severity is upgraded)
    level: int = -1  # highest chain level notified so far
    acked_by: str | None = None
    acked_at: float | None = None
    cleared_at: float | None = None
    shift: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class AlarmManager:
    def __init__(self, notifier: Notifier, escalation: dict | None = None, clock: Callable[[], float] = time.time,
                 shift_of: Callable[[float], str] | None = None, audit: Callable[..., None] | None = None):
        self.notifier = notifier
        self.escalation = escalation or DEFAULT_ESCALATION
        self.clock = clock
        self.shift_of = shift_of
        self.audit = audit
        self.active: dict[str, Alarm] = {}  # by sensor id
        self.history: deque[Alarm] = deque(maxlen=500)
        self._next_id = 1
        self._names: dict[str, str] = {}

    # ------------------------------------------------------------------ evaluation
    @staticmethod
    def _violated(name: str, limit: float, v: float, relaxed: bool) -> bool:
        db = 0.02 * max(abs(limit), 1.0) if relaxed else 0.0
        return v >= limit - db if name.startswith("hi") else v <= limit + db

    def _condition(self, st: SensorState, current: Alarm | None):
        spec = st.sensor.spec
        if st.quality != "good" or st.value is None:
            return "quality", None, "critical" if spec.safety else "warning"
        for name, sev in (("hi_hi", "critical"), ("lo_lo", "critical"), ("hi", "warning"), ("lo", "warning")):
            limit = getattr(spec, name)
            if limit is not None and self._violated(name, limit, st.value,
                                                    relaxed=bool(current and current.limit == name)):
                return name, limit, sev
        return None

    def update(self, st: SensorState, now: float):
        cur = self.active.get(st.sensor.id)
        cond = self._condition(st, cur)
        if cond is None:
            if cur:
                cur.cleared_at = now
                self.history.appendleft(cur)
                del self.active[st.sensor.id]
                self._audit("alarm_cleared", cur, "info", f"برگشت به محدوده مجاز: {cur.sensor_name}")
            return
        limit, limit_value, sev = cond
        if cur and SEVERITY_RANK[sev] <= SEVERITY_RANK[cur.severity]:  # same or lower severity: keep the alarm (latched severity)
            cur.value = st.value
            return
        spec, eq = st.sensor.spec, st.sensor.equipment_id
        where = "سالن مشترک" if st.sensor.line == 0 else f"خط {st.sensor.line}"
        eq_name = self._equipment_name(eq)
        if limit == "quality":
            msg = f"{eq_name} ({where}) – {spec.name_fa}: خطای سنسور/عدم دریافت داده"
        else:
            msg = f"{eq_name} ({where}) – {spec.name_fa}: {st.value} {spec.unit} ({LIMIT_FA[limit]} {limit_value})"
        a = Alarm(self._next_id, st.sensor.id, st.sensor.line, eq_name, spec.name_fa, spec.unit, sev, limit,
                  limit_value, st.value, now, msg, esc_base=now,
                  shift=self.shift_of(now) if self.shift_of else None)
        if cur:  # severity upgrade: new alarm id, escalation clock restarts
            cur.cleared_at = now
            self.history.appendleft(cur)
        self._next_id += 1
        self.active[st.sensor.id] = a
        self._audit("alarm_raised", a, sev, msg)

    def register_equipment(self, names: dict[str, str]):
        self._names = names

    def _equipment_name(self, eid: str) -> str:
        return self._names.get(eid, eid)

    # ------------------------------------------------------------------ custom (non-limit) alarms, e.g. anomalies
    def raise_custom(self, key: str, line: int, equipment_name: str, sensor_name: str, severity: str, limit: str,
                     message: str, now: float):
        cur = self.active.get(key)
        if cur:
            cur.message = message
            return cur
        a = Alarm(self._next_id, key, line, equipment_name, sensor_name, "", severity, limit, None, None, now, message,
                  esc_base=now, shift=self.shift_of(now) if self.shift_of else None)
        self._next_id += 1
        self.active[key] = a
        self._audit("alarm_raised", a, severity, message)
        return a

    def clear_custom(self, key: str, now: float):
        cur = self.active.pop(key, None)
        if cur:
            cur.cleared_at = now
            self.history.appendleft(cur)
            self._audit("alarm_cleared", cur, "info", f"برگشت به رفتار عادی: {cur.sensor_name}")

    # ------------------------------------------------------------------ escalation
    def tick(self, now: float):
        for a in list(self.active.values()):
            if a.acked_by:
                continue
            times = self.escalation[a.severity]
            while a.level + 1 < len(times) and now - a.esc_base >= times[a.level + 1]:
                a.level += 1
                role = ROLES[a.level]
                prefix = "🔴 بحرانی" if a.severity == "critical" else "🟠 هشدار"
                extra = "" if a.level == 0 else f" (بدون تأیید اپراتور پس از {int(now - a.esc_base)} ثانیه)"
                self.notifier.send(role, a.line or None, f"{prefix} – {ROLE_FA[role]}",
                                   f"{prefix}: {a.message}{extra}", a.severity, ref=f"alarm:{a.id}")
                if a.level > 0:
                    self._audit("alarm_escalated", a, a.severity, f"ارجاع به {ROLE_FA[role]}")

    def ack(self, alarm_id: int, user: str, now: float) -> Alarm | None:
        for a in self.active.values():
            if a.id == alarm_id:
                if not a.acked_by:
                    a.acked_by, a.acked_at = user, now
                    self._audit("alarm_acked", a, "info", f"تأیید توسط {user}")
                return a
        return None

    def _audit(self, kind: str, a: Alarm, severity: str, summary: str):
        if self.audit:
            self.audit(kind, a.line, severity, summary, {"alarm_id": a.id, "sensor": a.sensor_id, "value": a.value,
                                                         "raised_at": a.raised_at, "severity": a.severity})
