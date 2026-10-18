"""Simulated live process data for every sensor in the plant model.

SIMULATED: values are generated, not measured. Used for the demo dashboard, alarm/autopilot logic tests and
HMI rehearsal until a real PLC/SCADA adapter replaces it (the engine only needs `states` and a PlcInterface).
"""
import math
import random
from dataclasses import dataclass

from .catalog import Equipment, Sensor

CONVEYOR_RECIPE = 12.0


@dataclass
class Fault:
    kind: str  # drift | spike | stuck | dropout
    magnitude: float  # in multiples of spec.span()
    start: float
    until: float
    frozen: float | None = None


@dataclass
class SensorState:
    sensor: Sensor
    value: float | None
    quality: str = "good"  # good | stale | bad
    setpoint: float | None = None
    recipe_setpoint: float | None = None
    fault: Fault | None = None
    dev: float = 0.0  # AR(1) noise state
    base: float = 0.0  # slow process value (tracks the setpoint for controlled sensors)
    bad_since: float | None = None
    phase: float = 0.0


class PlantSimulator:
    PHI = 0.97
    TAU = {"conveyor": 5.0}  # first-order response time of controlled variables, seconds (default 25)

    def __init__(self, equipment: list[Equipment], seed: int | None = None, fault_rate: float = 5e-5, now: float = 0.0):
        self.rng = random.Random(seed)
        self.fault_rate = fault_rate  # random faults per sensor per second
        self.equipment = equipment
        self.states: dict[str, SensorState] = {}
        self.line_running: dict[int, bool] = {}
        self._t = now
        for eq in equipment:
            self.line_running.setdefault(eq.line, True)
            for s in eq.sensors:
                sp = s.spec.nominal if s.spec.controlled else None
                self.states[s.id] = SensorState(
                    s, s.spec.nominal, setpoint=sp, recipe_setpoint=sp, base=s.spec.nominal,
                    phase=self.rng.uniform(0, 2 * math.pi))

    # ----------------------------------------------------------------- control surface (what a PLC exposes)
    def set_setpoint(self, sensor_id: str, value: float):
        st = self.states[sensor_id]
        if st.setpoint is None:
            raise ValueError(f"{sensor_id} is not a controlled variable")
        st.setpoint = value

    def stop_line(self, line: int):
        self.line_running[line] = False
        for eq in self.equipment:
            if eq.line == line and eq.type == "conveyor":
                self.states[f"{eq.id}.speed"].setpoint = 0.0

    def restart_line(self, line: int):
        self.line_running[line] = True
        for eq in self.equipment:
            if eq.line == line:
                for s in eq.sensors:
                    st = self.states[s.id]
                    if st.recipe_setpoint is not None:
                        st.setpoint = st.recipe_setpoint

    # ----------------------------------------------------------------- faults
    def inject(self, sensor_id: str, kind: str, magnitude: float, duration_s: float, now: float):
        if kind not in ("drift", "spike", "stuck", "dropout"):
            raise ValueError("unknown fault kind")
        st = self.states[sensor_id]
        st.fault = Fault(kind, magnitude, now, now + duration_s, frozen=st.value)

    def _random_fault(self, st: SensorState, now: float):
        kind = self.rng.choices(["drift", "spike", "stuck", "dropout"], [0.5, 0.2, 0.15, 0.15])[0]
        sign = -1.0 if (st.sensor.spec.lo is not None and self.rng.random() < 0.4) else 1.0
        st.fault = Fault(kind, sign * self.rng.uniform(0.9, 1.7), now, now + self.rng.uniform(60, 180), frozen=st.value)

    # ----------------------------------------------------------------- dynamics
    def _line_speed_ratio(self, line: int) -> float:
        st = self.states.get(f"L{line}.conveyor.speed")
        return (st.value or 0.0) / CONVEYOR_RECIPE if st and st.value is not None else 1.0

    def step(self, now: float):
        dt = min(max(now - self._t, 1e-6), 5.0)  # simulated time does not leap across a stall of the real loop
        self._t = now
        for sid, st in self.states.items():
            spec = st.sensor.spec
            # process value
            if st.setpoint is not None:
                tau = self.TAU.get(st.sensor.equipment_id.split(".")[1].rstrip("0123456789"), 25.0)
                st.base += (st.setpoint - st.base) * (1 - math.exp(-dt / tau))
                target = st.base
            else:
                target = spec.nominal
                line = st.sensor.line
                if line and not self.line_running.get(line, True) and spec.kind in ("vision", "speed", "current", "flow"):
                    target = 0.0 if spec.key in ("throughput", "part_count", "output") else target * 0.3
                elif line and spec.key in ("throughput", "part_count", "output"):
                    target = spec.nominal * self._line_speed_ratio(line)
            st.dev = self.PHI * st.dev + self.rng.gauss(0, spec.noise * math.sqrt(1 - self.PHI ** 2))
            wander = spec.noise * 1.2 * math.sin(2 * math.pi * now / 180.0 + st.phase)
            value = target + st.dev + wander

            # faults
            f = st.fault
            if f and now >= f.until:
                st.fault = f = None
            if f is None and self.fault_rate and self.rng.random() < self.fault_rate * dt and spec.key != "speed":
                self._random_fault(st, now)
                f = st.fault
            quality = "good"
            if f:
                span = spec.span()
                age = now - f.start
                if f.kind == "drift":
                    value += f.magnitude * span * min(1.0, age / max(0.6 * (f.until - f.start), 1.0))
                elif f.kind == "spike":
                    value += f.magnitude * span if age < 5 else 0.0
                elif f.kind == "stuck":
                    value = f.frozen if f.frozen is not None else value
                    quality = "stale" if age > 8 else "good"
                elif f.kind == "dropout":
                    value, quality = None, "bad"
            if quality != "good":
                st.bad_since = st.bad_since if st.bad_since is not None else now
            else:
                st.bad_since = None
            st.quality = quality
            st.value = None if value is None else round(max(value, 0.0) if spec.unit not in ("°C",) else value, spec.decimals)
