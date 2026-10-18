"""Anomaly detection learned from normal production (instead of fixed limits only).

Per equipment, two detectors share one learned model of "normal":
  * drift     - each feature's z-score against its learned mean/std stays beyond `z_thr` for `hold_s` seconds
                (catches a slow drift or offset long before a fixed alarm limit is reached);
  * pattern   - Mahalanobis distance of the whole feature vector (catches unusual *combinations* even when each value
                is individually in range).

Features: raw value; for controlled variables the control ERROR (pv - setpoint) so that legitimate setpoint moves (autopilot,
operator) are not anomalies; speed-dependent measurements (rates, conveyor current) are left to the fixed limits.

Learning: samples are used only while the equipment is healthy (good data quality, no active alarm on it). The threshold is
calibrated on the learning data itself (99.9 % quantile with a margin) after trimming the most extreme 2 % of samples.
For a real plant learn from >= 24 h covering all shifts and products; the demo default is a few minutes of simulated data.
"""
import json
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

# chi-square 99.9 % points, used as a floor for the pattern threshold
CHI2_999 = {1: 10.8, 2: 13.8, 3: 16.3, 4: 18.5, 5: 20.5, 6: 22.5, 7: 24.3, 8: 26.1}
SPEED_DEPENDENT = {"throughput", "part_count", "output", "motor_current"}  # on conveyor/extruder/mill/deflash lines


def feature_ids(equipment) -> list[str]:
    """Sensor ids that make up the feature vector of one equipment item."""
    ids = []
    for s in equipment.sensors:
        sp = s.spec
        if sp.controlled or not (sp.key in SPEED_DEPENDENT and equipment.type in ("conveyor", "extruder", "deflash", "inspection", "mill")):
            ids.append(s.id)
    return ids


@dataclass
class EquipModel:
    ids: list[str]
    mean: np.ndarray
    std: np.ndarray
    cov_inv: np.ndarray
    pattern_thr: float
    n: int
    n_eff: float = 0.0


@dataclass
class _Track:
    consec: dict = field(default_factory=dict)  # feature id -> seconds beyond z threshold
    pattern: float = 0.0
    since: float | None = None
    last: dict | None = None
    ok_for: float = 0.0


class AnomalyDetector:
    def __init__(self, equipment: list, min_samples: int = 900, z_thr: float = 3.5, hold_s: float = 30.0,
                 pattern_hold_s: float = 15.0, margin: float = 1.5, clear_s: float = 30.0):
        self.equipment = {e.id: e for e in equipment}
        self.min_samples, self.z_thr, self.hold_s = min_samples, z_thr, hold_s
        self.pattern_hold_s, self.margin, self.clear_s = pattern_hold_s, margin, clear_s
        self.reset()

    # ------------------------------------------------------------------ learning
    def reset(self):
        self.samples: dict[str, list] = {eid: [] for eid in self.equipment}
        self.models: dict[str, EquipModel] = {}
        self._tracks: dict[str, _Track] = {eid: _Track() for eid in self.equipment}
        self._last_t: float | None = None

    @property
    def learning(self) -> bool:
        return len(self.models) < len(self.equipment)

    def progress(self) -> float:
        if not self.equipment:
            return 1.0
        done = [1.0 if e in self.models else min(1.0, len(self.samples[e]) / self.min_samples) for e in self.equipment]
        return sum(done) / len(done)

    @staticmethod
    def vector(eq, states, ids: list[str]):
        """Feature vector or None if any needed sensor is unavailable."""
        v = []
        for sid in ids:
            st = states[sid]
            if st.quality != "good" or st.value is None:
                return None
            v.append(st.value - st.setpoint if st.setpoint is not None else st.value)
        return np.array(v, dtype=float)

    def _fit(self, eid: str):
        eq = self.equipment[eid]
        ids = feature_ids(eq)
        X = np.array(self.samples[eid])
        # Process signals are strongly autocorrelated, so N samples carry far fewer than N independent observations. With the
        # effective sample size n_eff the learned spread is underestimated; widen it so that little data means a cautious model.
        rho = max((float(np.corrcoef(X[:-1, j], X[1:, j])[0, 1]) for j in range(X.shape[1]) if X[:, j].std() > 0), default=0.0)
        rho = min(max(rho, 0.0), 0.995)
        n_eff = max(len(X) * (1 - rho) / (1 + rho), 2.0)
        infl = 1.0 + 1.5 / np.sqrt(n_eff)
        for _ in range(2):  # trim the most extreme 2 % and refit: faults inside the learning window must not widen "normal"
            mu = X.mean(0)
            floor = np.array([(0.5 * next(s.spec.noise for s in eq.sensors if s.id == i)) ** 2 for i in ids])
            cov = np.atleast_2d(np.cov(X, rowvar=False)) if X.shape[1] > 1 else np.array([[X.var()]])
            cov = cov * infl ** 2 + np.diag(floor)
            inv = np.linalg.inv(cov)
            d2 = np.einsum("ij,jk,ik->i", X - mu, inv, X - mu)
            X = X[d2 <= np.quantile(d2, 0.98)]
        mu = X.mean(0)
        cov = (np.atleast_2d(np.cov(X, rowvar=False)) if X.shape[1] > 1 else np.array([[X.var()]])) * infl ** 2 + np.diag(floor)
        inv = np.linalg.inv(cov)
        d2 = np.einsum("ij,jk,ik->i", X - mu, inv, X - mu)
        thr = max(self.margin * float(np.quantile(d2, 0.999)), CHI2_999.get(len(ids), 26.1))
        std = np.sqrt(np.diag(cov))
        self.models[eid] = EquipModel(ids, mu, std, inv, thr, len(X), round(float(n_eff), 1))

    def learn(self, eid: str, states, healthy: bool):
        if eid in self.models or not healthy:
            return
        eq = self.equipment[eid]
        v = self.vector(eq, states, feature_ids(eq))
        if v is None:
            return
        self.samples[eid].append(v)
        if len(self.samples[eid]) >= self.min_samples:
            self._fit(eid)
            self.samples[eid] = []

    # ------------------------------------------------------------------ scoring
    def update(self, eid: str, states, now: float, dt: float) -> dict | None:
        """Returns the anomaly description while one is active for this equipment, else None."""
        model = self.models.get(eid)
        if model is None:
            return None
        tr = self._tracks[eid]
        v = self.vector(self.equipment[eid], states, model.ids)
        if v is None:  # data-quality problems are the alarm manager's business
            return tr.last
        z = (v - model.mean) / model.std
        d2 = float((v - model.mean) @ model.cov_inv @ (v - model.mean))
        dt = min(dt, 5.0)
        for i, sid in enumerate(model.ids):
            tr.consec[sid] = tr.consec.get(sid, 0.0) + dt if abs(z[i]) > self.z_thr else max(0.0, tr.consec.get(sid, 0.0) - dt)
        tr.pattern = tr.pattern + dt if d2 > model.pattern_thr else max(0.0, tr.pattern - dt)

        drift = [(sid, float(z[i])) for i, sid in enumerate(model.ids) if tr.consec.get(sid, 0.0) >= self.hold_s]
        pattern = tr.pattern >= self.pattern_hold_s
        if drift or pattern:
            tr.ok_for = 0.0
            tr.since = tr.since or now
            top = sorted(((sid, float(z[i])) for i, sid in enumerate(model.ids)), key=lambda x: -abs(x[1]))[:3]
            tr.last = {"equipment_id": eid, "kind": "drift" if drift else "pattern", "since": tr.since,
                       "score": round(d2 / model.pattern_thr, 2), "sensors": [{"id": s, "z": round(zz, 1)} for s, zz in (drift or top)]}
            return tr.last
        if tr.last:
            tr.ok_for += dt
            if tr.ok_for >= self.clear_s:
                tr.last, tr.since = None, None
            return tr.last
        return None

    # ------------------------------------------------------------------ persistence / status
    def status(self) -> dict:
        return {"state": "learning" if self.learning else "active", "progress": round(self.progress(), 2),
                "min_samples": self.min_samples, "models": len(self.models), "equipment": len(self.equipment),
                "active": [t.last for t in self._tracks.values() if t.last]}

    def save(self, path: str):
        d = {eid: {"ids": m.ids, "mean": m.mean.tolist(), "std": m.std.tolist(), "cov_inv": m.cov_inv.tolist(),
                   "pattern_thr": m.pattern_thr, "n": m.n, "n_eff": m.n_eff} for eid, m in self.models.items()}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"saved_at": time.time(), "models": d}, fh)

    def load(self, path: str) -> int:
        d = json.load(open(path, encoding="utf-8"))["models"]
        for eid, m in d.items():
            if eid in self.equipment and m["ids"] == feature_ids(self.equipment[eid]):
                self.models[eid] = EquipModel(m["ids"], np.array(m["mean"]), np.array(m["std"]), np.array(m["cov_inv"]),
                                              m["pattern_thr"], m["n"], m.get("n_eff", 0.0))
        return len(self.models)
