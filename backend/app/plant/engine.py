"""PlantEngine: ties simulator, alarms, notifications, shifts and per-line autopilots into one control loop."""
import asyncio
import time
from collections import deque
from datetime import datetime
from typing import Callable

from .alarms import AlarmManager
from .anomaly import AnomalyDetector
from .autopilot import AutopilotConfig, LineAutopilot, SimulatedPlc
from .catalog import build_plant
from .downtime import REASONS, DowntimeTracker
from .notify import Notifier, default_contacts
from .shifts import IRAN_TZ, ShiftSchedule
from .simulator import CONVEYOR_RECIPE, PlantSimulator

Quality = Callable[[int], "tuple[int, float] | None"]


class PlantEngine:
    def __init__(self, lines: int = 4, seed: int | None = None, fault_rate: float = 5e-5,
                 clock: Callable[[], float] = time.time, contacts=None, channels=None,
                 audit: Callable[..., None] | None = None, quality: Quality | None = None,
                 autopilot_cfg: AutopilotConfig | None = None, escalation: dict | None = None,
                 schedule: ShiftSchedule | None = None, anomaly_min_samples: int = 900,
                 downtime_sink=None, equipment=None, source=None, plc=None, beat_sink=None):
        self.clock = clock
        self.lines = lines
        self.schedule = schedule or ShiftSchedule()
        self.equipment = equipment or build_plant(lines)
        now = clock()
        # `source` (e.g. OpcUaPlant) replaces the simulator as the data source and, unless given, as the PLC too
        self.sim = source or PlantSimulator(self.equipment, seed, fault_rate, now=now)
        self.simulated = source is None
        self.plc = plc or (source if source is not None else SimulatedPlc(self.sim))
        self.audit_sink = audit
        self.events: deque[dict] = deque(maxlen=300)
        self.notifier = Notifier(contacts or default_contacts(lines), channels, sink=self._on_notification, clock=clock)
        self.alarms = AlarmManager(self.notifier, escalation, clock, shift_of=self._shift_key_at, audit=self._audit)
        self.alarms.register_equipment({e.id: e.name_fa for e in self.equipment})
        cfg = autopilot_cfg or AutopilotConfig()
        self.autopilots = {n: LineAutopilot(n, cfg, last_heartbeat=now) for n in range(1, lines + 1)}
        self.sim_operator_present = {n: True for n in range(1, lines + 1)}  # demo stand-in for badge/HMI/camera presence
        self.quality = quality or (lambda line: None)
        self.anomaly = AnomalyDetector(self.equipment, min_samples=anomaly_min_samples)
        self.downtime = DowntimeTracker(downtime_sink)
        self.beat_sink, self._last_beat = beat_sink, None
        self._stop_reason: dict[int, tuple] = {}  # reason given with a manual stop request
        self.history: dict[str, deque] = {sid: deque(maxlen=60) for sid in self.sim.states}
        self._quality_cache: dict[int, tuple[float, object]] = {}
        self._clients: set = set()
        self.max_step_gap_s = 10.0  # a longer pause between steps is treated as a fault, not silently skipped
        self._last_step: float | None = None

    # ------------------------------------------------------------------ plumbing
    def _shift_key_at(self, ts: float) -> str:
        return self.schedule.at(datetime.fromtimestamp(ts, IRAN_TZ)).key

    def _audit(self, kind: str, line: int | None, severity: str, summary: str, payload: dict | None = None):
        ev = {"ts": self.clock(), "kind": kind, "line": line, "severity": severity, "summary": summary,
              "payload": payload or {}}
        self.events.appendleft(ev)
        if self.audit_sink:
            try:
                self.audit_sink(ev)
            except Exception:
                pass

    def _on_notification(self, n):
        self._audit("notification", None, n.severity, f"{n.role}: {n.subject}",
                    {"to": n.name, "channel": n.channel, "ok": n.ok, "ref": n.ref})

    def _ap_emit(self, line: int):
        roles = {"action": ["production_manager"], "autopilot": ["production_manager", "plant_manager"],
                 "safe_stop": ["operator", "production_manager", "plant_manager"]}

        def emit(kind, severity, subject, body):
            self._audit(f"autopilot_{kind}", line, severity, f"{subject} – {body}")
            for r in roles.get(kind, ["production_manager"]):
                self.notifier.send(r, line, subject, body, severity, ref=f"autopilot:{line}")
        return emit

    def _quality(self, line: int):
        now = self.clock()
        t, v = self._quality_cache.get(line, (-1e9, None))
        if now - t > 10:
            try:
                v = self.quality(line)
            except Exception:
                v = None
            self._quality_cache[line] = (now, v)
        return v

    # ------------------------------------------------------------------ loop
    def step(self, now: float | None = None):
        now = self.clock() if now is None else now
        if self._last_step is not None and now - self._last_step > self.max_step_gap_s:
            gap = now - self._last_step
            self._audit("engine_stall", None, "warning", f"حلقه کنترل {gap:.0f} ثانیه متوقف بود (خواب سیستم/بار زیاد)؛ داده‌های این بازه ثبت نشده",
                        {"gap_s": round(gap, 1)})
            self.notifier.send("production_manager", None, "وقفه در سامانه نظارت",
                               f"حلقه نظارت {gap:.0f} ثانیه متوقف بود؛ در این مدت آلارمی ثبت یا ارسال نشده است.",
                               "warning", ref="engine_stall")
            for ap in self.autopilots.values():
                ap.on_discontinuity(now, gap)
        dt = 1.0 if self._last_step is None else now - self._last_step
        self._last_step = now
        self.sim.step(now)
        for sid, st in self.sim.states.items():
            if st.value is not None:
                self.history[sid].append((round(now, 1), st.value))
            self.alarms.update(st, now)
        self._anomalies(now, dt)
        self.alarms.tick(now)
        for n, ap in self.autopilots.items():
            if self.simulated and self.sim_operator_present.get(n, True):
                ap.heartbeat(now)  # demo stand-in for badge/HMI presence; a real plant calls the heartbeat endpoint
            if ap.mode == "autonomous" and hasattr(self.plc, "heartbeat"):
                self.plc.heartbeat(n)  # PLC-side watchdog input
            ap.step(now, self.sim.states, self._quality(n), self.plc, self._ap_emit(n))
        self._track_downtime(now)
        if self.beat_sink and (self._last_beat is None or now - self._last_beat >= 60.0):
            self._last_beat = now
            try:
                self.beat_sink(now)
            except Exception:
                pass

    def _track_downtime(self, now: float):
        for n, ap in self.autopilots.items():
            running = self.sim.line_running.get(n, True)
            if running:
                self._stop_reason.pop(n, None)
            if ap.safe_stop:
                args = ("safe_stop", "auto", ap.safe_stop, None)
            elif n in self._stop_reason:
                args = self._stop_reason[n]
            else:
                args = ("unknown", "auto", None, None)
            self.downtime.observe(n, running, now, *args)

    def stop_line(self, line: int, reason_code: str, note: str | None, user: str):
        """Manual (operator/manager) stop with a reason, e.g. a planned changeover."""
        if reason_code not in REASONS:
            raise ValueError(f"unknown reason code {reason_code}")
        self._stop_reason[line] = (reason_code, "manual", note, user)
        self.plc.request_safe_stop(line, f"manual:{reason_code}")
        self._audit("line_stop", line, "warning", f"توقف دستی خط {line} ({REASONS[reason_code][0]}) توسط {user}")

    def _anomalies(self, now: float, dt: float):
        for eq in self.equipment:
            keys = [s.id for s in eq.sensors]
            healthy = all(self.sim.states[k].quality == "good" and k not in self.alarms.active for k in keys)
            self.anomaly.learn(eq.id, self.sim.states, healthy)
            found = self.anomaly.update(eq.id, self.sim.states, now, dt)
            key = f"anomaly:{eq.id}"
            if found:
                names = {s.id: s.spec.name_fa for s in eq.sensors}
                what = "، ".join(f"{names[x['id']]} ({x['z']:+.1f}σ)" for x in found["sensors"])
                where = "سالن مشترک" if eq.line == 0 else f"خط {eq.line}"
                kind = "رانش تدریجی" if found["kind"] == "drift" else "ترکیب غیرعادی مقادیر"
                self.alarms.raise_custom(key, eq.line, eq.name_fa, "رفتار غیرعادی", "warning", "anomaly",
                                         f"{eq.name_fa} ({where}) – {kind} نسبت به تولید عادی: {what}", now)
            else:
                self.alarms.clear_custom(key, now)

    async def run(self, tick: float = 1.0):
        while True:
            t0 = time.perf_counter()
            await asyncio.to_thread(self.step)  # webhooks / DB writes happen off the event loop
            snap = self.snapshot(spark=False)
            for ws in list(self._clients):
                try:
                    await ws.send_json(snap)
                except Exception:
                    self._clients.discard(ws)
            await asyncio.sleep(max(0.0, tick - (time.perf_counter() - t0)))

    # ------------------------------------------------------------------ views
    def sensor_state(self, st) -> str:
        spec = st.sensor.spec
        if st.quality != "good" or st.value is None:
            return "fault"
        v = st.value
        if (spec.hi_hi is not None and v >= spec.hi_hi) or (spec.lo_lo is not None and v <= spec.lo_lo):
            return "critical"
        if (spec.hi is not None and v >= spec.hi) or (spec.lo is not None and v <= spec.lo):
            return "warning"
        return "ok"

    def line_summary(self, n: int) -> dict:
        now = self.clock()
        running = self.sim.line_running.get(n, True)
        thr = self.sim.states[f"L{n}.inspection.throughput"]
        spd = self.sim.states[f"L{n}.conveyor.speed"]
        q = self._quality(n)
        quality_rate = None if q is None else 1 - q[1]
        perf = min(1.0, (thr.value or 0) / thr.sensor.spec.nominal)
        oee = None if quality_rate is None else round((1.0 if running else 0.0) * perf * quality_rate, 3)
        return {"line": n, "state": "running" if running else "stopped",
                "stop_reason": self.plc.stopped.get(n), "throughput": thr.value,
                "speed_ratio": None if spd.value is None else round(spd.value / CONVEYOR_RECIPE, 2),
                "defect_rate": None if q is None else round(q[1], 3), "samples": None if q is None else q[0],
                "oee": oee, "operator_present": self.sim_operator_present.get(n, True),
                "autopilot": self.autopilots[n].status(now)}

    def snapshot(self, spark: bool = True) -> dict:
        now = self.clock()
        sh = self.schedule.at(datetime.fromtimestamp(now, IRAN_TZ))
        eqs = []
        for e in self.equipment:
            sens = []
            for s in e.sensors:
                st = self.sim.states[s.id]
                sp = s.spec
                item = {"id": s.id, "key": sp.key, "name_fa": sp.name_fa, "unit": sp.unit, "kind": sp.kind,
                        "value": st.value, "quality": st.quality, "state": self.sensor_state(st),
                        "setpoint": st.setpoint, "lo": sp.lo, "hi": sp.hi, "lo_lo": sp.lo_lo, "hi_hi": sp.hi_hi,
                        "nominal": sp.nominal, "decimals": sp.decimals}
                if spark:
                    item["spark"] = [v for _, v in list(self.history[s.id])[-40:]]
                sens.append(item)
            eqs.append({"id": e.id, "line": e.line, "type": e.type, "name_fa": e.name_fa, "name_en": e.name_en,
                        "verified": e.verified, "sensors": sens})
        return {
            "ts": now, "simulated": self.simulated,
            "source": {"kind": getattr(self.sim, "kind", "simulator"), "connected": getattr(self.sim, "connected", True),
                       "can_write": getattr(self.plc, "can_write", False), "error": getattr(self.sim, "last_error", None)},
            "shift": {"key": sh.key, "name_fa": sh.name_fa, "date": sh.shift_date, "working": sh.working,
                      "ends_at": sh.end.isoformat(), **{"assumed": self.schedule.assumed}},
            "lines": [self.line_summary(n) for n in range(1, self.lines + 1)],
            "equipment": eqs,
            "alarms": {"active": sorted((a.to_dict() for a in self.alarms.active.values()),
                                        key=lambda a: (a["severity"] != "critical", a["raised_at"])),
                       "recent": [a.to_dict() for a in list(self.alarms.history)[:15]]},
            "anomaly": self.anomaly.status(),
            "downtime": {"open": [{"line": st.line, "start": st.start, "reason": st.reason, "source": st.source,
                                   "minutes": round((now - st.start) / 60.0, 1), "id": st.db_id}
                                  for st in self.downtime.open.values()]},
            "notifications": [n.to_dict() for n in list(self.notifier.history)[-15:]][::-1],
            "events": list(self.events)[:25],
        }
