"""OPC-UA adapter: reads the real plant through a PLC/SCADA OPC-UA server and (optionally) writes to it.

It plays two roles that the engine already knows from the simulator:
  * data source  - `states` / `line_running` / `step(now)`; the same SensorState objects, so alarms, anomaly detection,
                   downtime tracking and the dashboard work unchanged;
  * PLC          - `read_setpoint`, `write_setpoint`, `request_safe_stop`, `restart`, `heartbeat`.

SAFETY DEFAULTS
  * READ-ONLY unless `write_enabled` is true in the mapping file. In read-only mode nothing is ever written, which also means
    the autopilot cannot be engaged and a safe stop cannot be requested from here (the API refuses, `can_write` is False).
    Commission in read-only mode first and compare the dashboard with the HMI before enabling writes.
  * Every setpoint write is clamped to the equipment's hard envelope (the sensor's lo_lo/lo .. hi_hi/hi limits).
  * Bad status codes, stale source timestamps and a lost connection mark the data BAD (quality alarms; critical for safety
    sensors) - the adapter never invents or holds a value. It reconnects with a back-off.
  * The autopilot heartbeat is written to a PLC node; the PLC program must drop to manual/safe state when it stops changing.
    That watchdog lives in the PLC, not here, and must exist before autonomous operation on a real line.
  * Credentials come from the environment (OPCUA_USER / OPCUA_PASSWORD), never from the mapping file or the repository.

Mapping file (data/opcua_map.json), node ids as strings, e.g. "ns=2;s=Line1.Press1.PlateTemp":
  {"endpoint": "opc.tcp://10.0.0.5:4840", "write_enabled": false, "timeout_s": 2, "stale_after_s": 10,
   "sensors": {"L1.press1.plate_temp": "ns=2;s=..."}, "setpoints": {"L1.press1.plate_temp": "ns=2;s=..."},
   "running": {"1": "ns=2;s=Line1.Running"}, "safe_stop": {"1": "ns=2;s=Line1.SafeStopRequest"},
   "heartbeat": {"1": "ns=2;s=Line1.AutopilotHeartbeat"}}
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .simulator import SensorState

log = logging.getLogger("plant.opcua")


@dataclass
class OpcUaMap:
    endpoint: str
    sensors: dict[str, str]
    setpoints: dict[str, str] = field(default_factory=dict)
    running: dict[int, str] = field(default_factory=dict)
    safe_stop: dict[int, str] = field(default_factory=dict)
    heartbeat: dict[int, str] = field(default_factory=dict)
    write_enabled: bool = False
    timeout_s: float = 2.0
    stale_after_s: float = 10.0
    reconnect_s: float = 5.0

    @classmethod
    def from_json(cls, path: str) -> "OpcUaMap":
        d = json.load(open(path, encoding="utf-8"))
        for k in ("running", "safe_stop", "heartbeat"):
            d[k] = {int(n): v for n, v in d.get(k, {}).items()}
        return cls(**d)

    def validate(self, equipment) -> list[str]:
        """Problems in the mapping relative to the plant model (unknown ids, controlled sensors without a setpoint node)."""
        ids = {s.id: s for e in equipment for s in e.sensors}
        problems = [f"sensor {k} is not in the plant model" for k in self.sensors if k not in ids]
        problems += [f"setpoint {k} is not a controlled sensor" for k in self.setpoints if k not in ids or not ids[k].spec.controlled]
        if self.write_enabled:
            problems += [f"controlled sensor {k} has no setpoint node" for k, s in ids.items()
                         if s.spec.controlled and k in self.sensors and k not in self.setpoints]
        return problems


class OpcUaPlant:
    kind = "opcua"

    def __init__(self, equipment, cfg: OpcUaMap, client_factory=None, clock=time.time):
        self.equipment, self.cfg, self.clock = equipment, cfg, clock
        self._client_factory = client_factory or self._default_client
        self.client = None
        self.connected = False
        self.last_error: str | None = None
        self._next_try = 0.0
        self._nodes: dict[str, object] = {}
        self._hb = 0
        self.stopped: dict[int, str] = {}
        self.states: dict[str, SensorState] = {}
        self.line_running = {e.line: True for e in equipment}
        for e in equipment:
            for s in e.sensors:
                rec = s.spec.nominal if s.spec.controlled else None
                self.states[s.id] = SensorState(s, None, quality="bad", setpoint=None, recipe_setpoint=rec, bad_since=None)
        self._spec = {sid: st.sensor.spec for sid, st in self.states.items()}

    # ------------------------------------------------------------------ connection
    @property
    def can_write(self) -> bool:
        return self.cfg.write_enabled and self.connected

    def _default_client(self):
        from asyncua.sync import Client

        c = Client(self.cfg.endpoint, timeout=self.cfg.timeout_s)
        user, pw = os.getenv("OPCUA_USER"), os.getenv("OPCUA_PASSWORD")
        if user:
            c.set_user(user)
            c.set_password(pw or "")
        return c

    def connect(self) -> bool:
        try:
            self.client = self._client_factory()
            self.client.connect()
            self._nodes = {}
            self.connected, self.last_error = True, None
            log.info("OPC-UA connected to %s (write_enabled=%s)", self.cfg.endpoint, self.cfg.write_enabled)
        except Exception as e:  # the endpoint is the only thing logged; never credentials
            self.connected, self.last_error = False, f"{type(e).__name__}: {e}"
            self.client = None
        return self.connected

    def close(self):
        if self.client is not None:
            try:
                self.client.disconnect()
            except Exception:
                pass
        self.client, self.connected = None, False

    def _node(self, nid: str):
        n = self._nodes.get(nid)
        if n is None:
            n = self._nodes[nid] = self.client.get_node(nid)
        return n

    # ------------------------------------------------------------------ data source
    def step(self, now: float):
        if not self.connected:
            if now >= self._next_try:
                self._next_try = now + self.cfg.reconnect_s
                self.connect()
            if not self.connected:
                self._mark_bad(now)
                return
        try:
            self._read(now)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            self.close()
            self._next_try = now + self.cfg.reconnect_s
            self._mark_bad(now)

    def _mark_bad(self, now: float):
        for st in self.states.values():
            st.value, st.quality = None, "bad"
            st.bad_since = st.bad_since if st.bad_since is not None else now

    def _read(self, now: float):
        from asyncua import ua

        keys, nodes = [], []
        for kind, mapping in (("v", self.cfg.sensors), ("s", self.cfg.setpoints)):
            for sid, nid in mapping.items():
                keys.append((kind, sid))
                nodes.append(self._node(nid))
        for line, nid in self.cfg.running.items():
            keys.append(("r", line))
            nodes.append(self._node(nid))
        if not nodes:
            return
        dvs = self.client.read_attributes(nodes, ua.AttributeIds.Value)
        seen_good = set()
        wall = datetime.now(timezone.utc)
        for (kind, key), dv in zip(keys, dvs):
            ok = dv.StatusCode.is_good() and dv.Value is not None and dv.Value.Value is not None
            if ok and dv.SourceTimestamp is not None:
                ts = dv.SourceTimestamp if dv.SourceTimestamp.tzinfo else dv.SourceTimestamp.replace(tzinfo=timezone.utc)
                if (wall - ts).total_seconds() > self.cfg.stale_after_s:
                    ok_quality = "stale"
                else:
                    ok_quality = "good"
            else:
                ok_quality = "good" if ok else "bad"
            if kind == "r":
                if ok:
                    self.line_running[key] = bool(dv.Value.Value)
                continue
            st = self.states[key]
            if kind == "s":
                if ok:
                    st.setpoint = float(dv.Value.Value)
                continue
            if not ok:
                st.value, st.quality = None, "bad"
            else:
                st.value = round(float(dv.Value.Value), st.sensor.spec.decimals)
                st.quality = ok_quality
            if st.quality == "good":
                st.bad_since = None
                seen_good.add(key)
            else:
                st.bad_since = st.bad_since if st.bad_since is not None else now
        for sid, st in self.states.items():  # sensors that are not mapped at all stay bad: nothing is invented
            if sid not in self.cfg.sensors:
                st.value, st.quality = None, "bad"
                st.bad_since = st.bad_since if st.bad_since is not None else now

    def inject(self, *a, **k):
        raise NotImplementedError("fault injection exists only on the simulator")

    # ------------------------------------------------------------------ PLC interface
    def read_setpoint(self, sensor_id: str) -> float:
        sp = self.states[sensor_id].setpoint
        if sp is None:
            raise ValueError(f"no setpoint available for {sensor_id}")
        return sp

    def _require_write(self):
        if not self.cfg.write_enabled:
            raise PermissionError("PLC writes are disabled (read-only commissioning mode)")
        if not self.connected:
            raise ConnectionError("PLC not connected")

    def write_setpoint(self, sensor_id: str, value: float) -> float:
        self._require_write()
        nid = self.cfg.setpoints.get(sensor_id)
        if nid is None:
            raise PermissionError(f"{sensor_id} has no mapped setpoint node")
        spec = self._spec[sensor_id]
        lo = spec.lo_lo if spec.lo_lo is not None else spec.lo
        hi = spec.hi_hi if spec.hi_hi is not None else spec.hi
        if lo is not None:
            value = max(value, lo)
        if hi is not None:
            value = min(value, hi)
        self._node(nid).set_value(float(value))
        self.states[sensor_id].setpoint = float(value)
        return float(value)

    def request_safe_stop(self, line: int, reason: str):
        self._require_write()
        nid = self.cfg.safe_stop.get(line)
        if nid is None:
            raise PermissionError(f"line {line} has no mapped safe-stop node")
        self._node(nid).set_value(True)
        self.stopped[line] = reason

    def restart(self, line: int):
        """Clears OUR stop request only. Actually restarting the machines is a human action at the PLC/HMI."""
        self._require_write()
        nid = self.cfg.safe_stop.get(line)
        if nid is not None:
            self._node(nid).set_value(False)
        self.stopped.pop(line, None)

    def heartbeat(self, line: int):
        nid = self.cfg.heartbeat.get(line)
        if nid is None or not self.can_write:
            return
        self._hb = (self._hb + 1) % 1_000_000
        try:
            self._node(nid).set_value(self._hb)
        except Exception as e:
            log.warning("heartbeat write failed on line %s: %s", line, type(e).__name__)

    # engine/simulator compatibility
    def stop_line(self, line: int):
        self.request_safe_stop(line, "stop")

    def restart_line(self, line: int):
        self.restart(line)
