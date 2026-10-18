"""Safety-bounded supervisory autopilot for a production line.

WHAT IT IS: a supervisory layer that keeps a line inside its recipe when no operator is present, by nudging a short whitelist
of setpoints inside narrow bands, and that asks the PLC for a SAFE STOP the moment anything leaves its envelope.

WHAT IT IS NOT, BY DESIGN
  * It never touches E-stops, guards, interlocks or safety relays: those stay hardwired / in the safety PLC. Unattended
    operation of presses and ovens needs an HSE risk assessment and written approval before it is ever enabled for real.
  * It cannot start a stopped line: a safe stop is LATCHED until a human restarts it.
  * It does not "try harder": if it keeps having to act, it stops the line instead.
  * Against a real PLC it needs the PLC-side watchdog: the PLC must drop to manual/safe state if the autopilot heartbeat
    stops. `SimulatedPlc` below is the only implementation shipped; real writes are off by default.

Modes
  off         nothing
  advisory    watches and records what it WOULD do; never writes
  autonomous  acts only while the operator is absent (no heartbeat for `absent_after_s`); the moment the operator is
              back it stands by and hands control over. Auto-expires after `max_autonomous_s` unless renewed.
"""
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .simulator import CONVEYOR_RECIPE, PlantSimulator, SensorState


@dataclass
class AutopilotConfig:
    absent_after_s: float = 120.0
    max_autonomous_s: float = 4 * 3600.0
    action_cooldown_s: float = 60.0
    max_actions_per_hour: int = 30
    stale_failsafe_s: float = 30.0
    # recipe bands: setpoint may move at most +-band from the recipe value; PV must be off by `trigger` to act
    press_band: float = 3.0
    press_trigger: float = 2.0
    press_step: float = 1.0
    oven_band: float = 5.0
    oven_trigger: float = 4.0
    oven_step: float = 2.0
    persist_s: float = 60.0  # deviation must persist this long before acting (no reacting to noise)
    ema_alpha: float = 0.1  # smoothing of the process value used for decisions
    # quality -> speed
    defect_high: float = 0.15
    defect_ok: float = 0.07
    min_quality_samples: int = 30
    speed_min_ratio: float = 0.7
    speed_step: float = 0.1
    speed_cooldown_s: float = 120.0


class PlcInterface(Protocol):
    def read_setpoint(self, sensor_id: str) -> float: ...
    def write_setpoint(self, sensor_id: str, value: float) -> float: ...  # returns the value actually applied
    def request_safe_stop(self, line: int, reason: str) -> None: ...


class SimulatedPlc:
    """The only PLC implementation shipped. Writes are clamped to the equipment's hard envelope (what a real PLC would do)."""

    def __init__(self, sim: PlantSimulator):
        self.sim = sim
        self.stopped: dict[int, str] = {}

    def read_setpoint(self, sensor_id):
        return self.sim.states[sensor_id].setpoint

    def write_setpoint(self, sensor_id, value):
        spec = self.sim.states[sensor_id].sensor.spec
        lo = spec.lo_lo if spec.lo_lo is not None else spec.lo
        hi = spec.hi_hi if spec.hi_hi is not None else spec.hi
        if lo is not None:
            value = max(value, lo)
        if hi is not None:
            value = min(value, hi)
        self.sim.set_setpoint(sensor_id, value)
        return value

    def request_safe_stop(self, line, reason):
        self.stopped[line] = reason
        self.sim.stop_line(line)

    def restart(self, line):
        self.stopped.pop(line, None)
        self.sim.restart_line(line)

    can_write = True

    def heartbeat(self, line):
        pass


@dataclass
class ApAction:
    ts: float
    line: int
    kind: str  # action | recommendation | safe_stop | handover | mode
    summary: str
    sensor_id: str | None = None
    old: float | None = None
    new: float | None = None


@dataclass
class LineAutopilot:
    line: int
    cfg: AutopilotConfig
    mode: str = "off"
    engaged: bool = False
    safe_stop: str | None = None  # latched reason
    last_heartbeat: float = 0.0
    enabled_by: str | None = None
    expires_at: float | None = None
    actions: deque = field(default_factory=lambda: deque(maxlen=200))
    _hour: deque = field(default_factory=deque)  # timestamps of actions in the last hour
    _deviation_since: dict = field(default_factory=dict)
    _ema: dict = field(default_factory=dict)  # smoothed PV per controlled sensor (noise must not reset the timers)
    _last_act: dict = field(default_factory=dict)
    _last_rec: dict = field(default_factory=dict)
    _was_engaged: bool = False
    pending_restore: bool = False

    # ------------------------------------------------------------------ operator / mode
    def on_discontinuity(self, now: float, gap_s: float):
        """The supervisory loop stalled (sleep, overload). Timers and smoothed values describe a world that no longer
        exists, so drop them: the autopilot must re-observe before it acts again."""
        self._deviation_since.clear()
        self._ema.clear()
        self._log(now, "mode", f"وقفه {gap_s:.0f} ثانیه‌ای در حلقه کنترل؛ شمارنده‌ها بازنشانی شد")

    def heartbeat(self, now: float):
        self.last_heartbeat = now

    def set_mode(self, mode: str, user: str, now: float):
        if mode not in ("off", "advisory", "autonomous"):
            raise ValueError("mode must be off|advisory|autonomous")
        self.mode, self.enabled_by = mode, user
        self.expires_at = now + self.cfg.max_autonomous_s if mode == "autonomous" else None
        if mode != "autonomous":
            self.pending_restore = self.pending_restore or self._was_engaged
            self.engaged = False
            self._was_engaged = False
        self._log(now, "mode", f"حالت اتوپایلوت روی «{mode}» تنظیم شد توسط {user}")

    def operator_absent(self, now: float) -> bool:
        return now - self.last_heartbeat > self.cfg.absent_after_s

    def reset_safe_stop(self, user: str, now: float):
        self.safe_stop = None
        self._log(now, "mode", f"توقف ایمن توسط {user} بازنشانی شد (راه‌اندازی مجدد خط دستی است)")

    # ------------------------------------------------------------------ main step
    def step(self, now: float, states: dict[str, SensorState], quality: tuple[int, float] | None, plc: PlcInterface,
             emit: Callable[[str, str, str, str], None]):
        """emit(kind, severity, subject, body) -> engine fans out to notifications + audit."""
        if self.pending_restore:
            self.pending_restore = False
            self._restore_recipe(now, states, plc, emit)
        if self.mode == "off":
            self.engaged = False
            return
        if self.mode == "autonomous" and self.expires_at and now >= self.expires_at:
            self._was_engaged = False
            self._restore_recipe(now, states, plc, emit)
            self.mode, self.engaged = "off", False
            self._log(now, "mode", "اتوپایلوت به‌دلیل پایان مدت مجاز غیرفعال شد")
            emit("autopilot", "warning", f"اتوپایلوت خط {self.line} منقضی شد",
                 "مدت مجاز کار خودکار تمام شد؛ کنترل به اپراتور بازمی‌گردد. برای ادامه باید دوباره فعال شود.")
            return
        if self.safe_stop:
            self.engaged = False
            return

        absent = self.operator_absent(now)
        self.engaged = self.mode == "autonomous" and absent
        if self._was_engaged and not self.engaged and self.mode == "autonomous":
            self._restore_recipe(now, states, plc, emit)
            self._log(now, "handover", "اپراتور بازگشت؛ کنترل تحویل داده شد")
            emit("autopilot", "info", f"تحویل کنترل خط {self.line}", "اپراتور بازگشته است؛ اتوپایلوت در حالت آماده‌باش.")
        if self.engaged and not self._was_engaged:
            self._log(now, "mode", "اپراتور حاضر نیست؛ اتوپایلوت کنترل نظارتی را در دست گرفت")
            emit("autopilot", "warning", f"اتوپایلوت خط {self.line} فعال شد",
                 f"اپراتور بیش از {int(self.cfg.absent_after_s)} ثانیه حاضر نبود؛ اتوپایلوت وارد عمل شد.")
        self._was_engaged = self.engaged

        # advisory mode, or autonomous while the operator is present: observe and recommend only
        acting = self.engaged
        # own line + shared utilities (air/steam/power); the mixing hall does not stop other lines
        line_states = {k: s for k, s in states.items()
                       if s.sensor.line == self.line or (s.sensor.line == 0 and "utilities" in s.sensor.equipment_id)}

        if self._failsafe(now, line_states, plc, acting, emit):
            return
        self._control_temperatures(now, line_states, plc, acting, emit)
        self._control_speed(now, states, quality, plc, acting, emit)

    # ------------------------------------------------------------------ fail-safe
    def _failsafe(self, now, line_states, plc, acting, emit) -> bool:
        reason = None
        for s in line_states.values():
            spec = s.sensor.spec
            if not spec.safety:
                continue
            if s.bad_since is not None and now - s.bad_since > self.cfg.stale_failsafe_s:
                reason = f"سنسور ایمنی «{spec.name_fa}» ({s.sensor.id}) بیش از {int(self.cfg.stale_failsafe_s)} ثانیه داده معتبر ندارد"
                break
            if s.value is not None:
                for name in ("hi_hi", "lo_lo"):
                    lim = getattr(spec, name)
                    if lim is not None and (s.value >= lim if name == "hi_hi" else s.value <= lim):
                        reason = f"{spec.name_fa} ({s.sensor.id}) = {s.value} {spec.unit} از حد بحرانی گذشت"
                        break
            if reason:
                break
        self._hour = deque(t for t in self._hour if now - t < 3600)
        if reason is None and len(self._hour) >= self.cfg.max_actions_per_hour:
            reason = f"بیش از {self.cfg.max_actions_per_hour} اقدام در یک ساعت؛ کنترل‌کننده همگرا نمی‌شود"
        if reason is None:
            return False
        if acting:
            plc.request_safe_stop(self.line, reason)
            self.safe_stop = reason
            self._log(now, "safe_stop", f"درخواست توقف ایمن: {reason}")
            emit("safe_stop", "critical", f"توقف ایمن خط {self.line}",
                 f"اتوپایلوت درخواست توقف ایمن داد: {reason}. راه‌اندازی مجدد فقط دستی و پس از بررسی است.")
        else:
            self._recommend(now, "safe_stop", f"توصیه: توقف ایمن – {reason}")
        return True

    # ------------------------------------------------------------------ rules
    def _control_temperatures(self, now, line_states, plc, acting, emit):
        c = self.cfg
        for sid, s in line_states.items():
            spec = s.sensor.spec
            if not (spec.controlled and spec.kind == "temperature") or s.value is None or s.quality != "good":
                continue
            if s.sensor.equipment_id.split(".")[1].startswith("oven"):
                band, trigger, step = c.oven_band, c.oven_trigger, c.oven_step
            else:
                band, trigger, step = c.press_band, c.press_trigger, c.press_step
            recipe, sp = s.recipe_setpoint, plc.read_setpoint(sid)
            pv = self._ema[sid] = self._ema.get(sid, s.value) + c.ema_alpha * (s.value - self._ema.get(sid, s.value))
            err = pv - recipe
            if abs(err) <= trigger:
                self._deviation_since.pop(sid, None)  # in tolerance: do nothing; never undo a correction by itself
                continue
            since = self._deviation_since.setdefault(sid, now)
            if now - since < c.persist_s:
                continue
            new = sp - step if err > 0 else sp + step
            new = max(recipe - band, min(recipe + band, new))
            if abs(new - sp) < 1e-9 or now - self._last_act.get(sid, -1e9) < c.action_cooldown_s:
                continue
            self._apply(now, sid, sp, new, f"{spec.name_fa} {pv:.1f} {spec.unit} (دستور {recipe})", plc, acting, emit)

    def _control_speed(self, now, states, quality, plc, acting, emit):
        c = self.cfg
        sid = f"L{self.line}.conveyor.speed"
        if sid not in states or quality is None or quality[0] < c.min_quality_samples:
            return
        n, rate = quality
        sp = plc.read_setpoint(sid)
        ratio = sp / CONVEYOR_RECIPE
        if rate > c.defect_high and ratio > c.speed_min_ratio + 1e-9:
            new = max(c.speed_min_ratio, ratio - c.speed_step) * CONVEYOR_RECIPE
            why = f"نرخ عیب {rate:.0%} (از {n} قطعه اخیر) بالاتر از {c.defect_high:.0%}"
        elif rate < c.defect_ok and ratio < 1 - 1e-9:
            new = min(1.0, ratio + c.speed_step / 2) * CONVEYOR_RECIPE
            why = f"نرخ عیب به {rate:.0%} برگشت"
        else:
            return
        if now - self._last_act.get(sid, -1e9) < c.speed_cooldown_s:
            return
        self._apply(now, sid, sp, new, why, plc, acting, emit)

    # ------------------------------------------------------------------ helpers
    def _restore_recipe(self, now, states, plc, emit):
        """Undo every setpoint this autopilot moved: leave the line at its recipe, never at a silent override."""
        for sid, s in states.items():
            if s.sensor.line != self.line or s.recipe_setpoint is None:
                continue
            sp = plc.read_setpoint(sid)
            if abs(sp - s.recipe_setpoint) > 1e-9:
                applied = plc.write_setpoint(sid, s.recipe_setpoint)
                self._log(now, "action", f"{sid}: بازگشت به دستور {s.recipe_setpoint:.1f} (از {sp:.1f})", sid, sp, applied)
                emit("action", "info", f"بازگردانی دستور – خط {self.line}",
                     f"{sid}: {sp:.1f} → {applied:.1f} (دستور اصلی)")

    def _apply(self, now, sid, old, new, why, plc, acting, emit):
        if not acting:
            self._recommend(now, sid, f"توصیه: {sid} از {old:.1f} به {new:.1f} – {why}", sid, old, new)
            return
        try:
            applied = plc.write_setpoint(sid, new)
        except (PermissionError, ConnectionError) as e:
            self._recommend(now, sid, f"توصیه (نوشتن روی PLC ممکن نیست: {type(e).__name__}): {sid} از {old:.1f} به {new:.1f} – {why}", sid, old, new)
            return
        self._last_act[sid] = now
        self._hour.append(now)
        self._log(now, "action", f"{sid}: {old:.1f} → {applied:.1f} – {why}", sid, old, applied)
        emit("action", "info", f"اقدام اتوپایلوت – خط {self.line}", f"{sid}: {old:.1f} → {applied:.1f}. دلیل: {why}")

    def _recommend(self, now, key, summary, sid=None, old=None, new=None):
        if now - self._last_rec.get(key, -1e9) >= 300:  # do not repeat the same advice every tick
            self._last_rec[key] = now
            self._log(now, "recommendation", summary, sid, old, new)

    def _log(self, now, kind, summary, sid=None, old=None, new=None):
        self.actions.appendleft(ApAction(now, self.line, kind, summary, sid, old, new))

    def status(self, now: float) -> dict:
        return {
            "line": self.line, "mode": self.mode, "engaged": self.engaged, "safe_stop": self.safe_stop,
            "operator_absent": self.operator_absent(now), "enabled_by": self.enabled_by,
            "expires_in_s": None if not self.expires_at else max(0, int(self.expires_at - now)),
            "actions_last_hour": len([t for t in self._hour if now - t < 3600]),
            "recent": [a.__dict__ for a in list(self.actions)[:12]],
        }
