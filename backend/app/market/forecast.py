"""Monthly demand forecasting with method selection by rolling-origin backtest and empirical prediction intervals.

Methods: mean of last 3 / 12 months, simple exponential smoothing, damped Holt trend, seasonal naive, additive Holt-Winters
(needs >= 24 months), Croston-SBA (intermittent demand). The winner is the one with the lowest backtest WAPE (multi-step, last
months held out). Intervals (P10/P90) come from that method's own backtest errors, scaled by sqrt(horizon) beyond 3 steps.

The result always carries its backtest error and a confidence label: with a short or erratic history the forecast says so
instead of looking precise.
"""
import itertools
import math
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np

MIN_POINTS = 6
SEASON = 12
BACKTEST_H = 3


@dataclass
class Forecast:
    method: str
    horizon: int
    p10: list[float]
    p50: list[float]
    p90: list[float]
    wape: float | None
    bias: float | None
    n: int
    confidence: str
    warnings: list[str] = field(default_factory=list)
    backtest_errors: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------------------------- series helpers
def month_floor(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, k: int) -> date:
    m = d.year * 12 + d.month - 1 + k
    return date(m // 12, m % 12 + 1, 1)


def monthly_series(rows: list[tuple[date, float]], until: date | None = None) -> tuple[list[date], list[float]]:
    """Sum quantities per month, fill gaps with 0 between the first and last month (or `until`)."""
    if not rows:
        return [], []
    tot: dict[date, float] = {}
    for d, q in rows:
        tot[month_floor(d)] = tot.get(month_floor(d), 0.0) + q
    first, last = min(tot), max(tot)
    if until is not None:
        last = max(last, month_floor(until))
    months, k = [], 0
    while add_months(first, k) <= last:
        months.append(add_months(first, k))
        k += 1
    return months, [tot.get(m, 0.0) for m in months]


# ----------------------------------------------------------------------------------------------- point forecasters
def _ses_fit(y: np.ndarray, alpha: float):
    level, sse = y[0], 0.0
    for v in y[1:]:
        sse += (v - level) ** 2
        level = alpha * v + (1 - alpha) * level
    return level, sse


def f_mean3(y, h):
    return np.full(h, y[-3:].mean())


def f_mean12(y, h):
    return np.full(h, y[-12:].mean())


def f_ses(y, h):
    best = min(((a, *_ses_fit(y, a)) for a in (0.1, 0.2, 0.3, 0.5, 0.7)), key=lambda t: t[2])
    return np.full(h, best[1])


def _holt(y, alpha, beta, phi):
    level, trend, sse = y[0], (y[min(3, len(y) - 1)] - y[0]) / max(min(3, len(y) - 1), 1), 0.0
    for v in y[1:]:
        pred = level + phi * trend
        sse += (v - pred) ** 2
        new_level = alpha * v + (1 - alpha) * pred
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        level = new_level
    return level, trend, sse


def f_holt(y, h, phi=0.9):
    best = min(((a, b, *_holt(y, a, b, phi)) for a, b in itertools.product((0.2, 0.4, 0.6), (0.05, 0.1, 0.2))),
               key=lambda t: t[4])
    _, _, level, trend, _ = best
    damp = np.cumsum([phi ** (i + 1) for i in range(h)])
    return level + trend * damp


def f_snaive(y, h):
    n = len(y)
    return np.array([y[n - SEASON + (i % SEASON)] for i in range(h)])


def _hw(y, alpha, beta, gamma):
    m = SEASON
    level = y[:m].mean()
    trend = (y[m:2 * m].mean() - y[:m].mean()) / m
    seas = list(y[:m] - level)
    sse = 0.0
    for t in range(m, len(y)):
        s = seas[t - m]
        pred = level + trend + s
        sse += (y[t] - pred) ** 2
        new_level = alpha * (y[t] - s) + (1 - alpha) * (level + trend)
        trend = beta * (new_level - level) + (1 - beta) * trend
        seas.append(gamma * (y[t] - new_level) + (1 - gamma) * s)
        level = new_level
    return level, trend, seas, sse


def f_hw(y, h):
    best = min(((a, b, g, *_hw(y, a, b, g)) for a, b, g in itertools.product((0.1, 0.3), (0.0, 0.05), (0.1, 0.3))),
               key=lambda t: t[6])
    _, _, _, level, trend, seas, _ = best
    n, m = len(y), SEASON
    return np.array([level + (i + 1) * trend + seas[n - m + (i % m)] for i in range(h)])


def f_croston(y, h, alpha=0.1):
    nz = np.flatnonzero(y > 0)
    if len(nz) == 0:
        return np.zeros(h)
    z, p, last = y[nz[0]], float(nz[0] + 1), nz[0]
    for i in nz[1:]:
        z = alpha * y[i] + (1 - alpha) * z
        p = alpha * (i - last) + (1 - alpha) * p
        last = i
    return np.full(h, (1 - alpha / 2) * z / max(p, 1.0))


METHODS = {  # name -> (function, minimum training length)
    "mean3": (f_mean3, 3), "mean12": (f_mean12, 3), "ses": (f_ses, 4), "holt": (f_holt, 6),
    "snaive": (f_snaive, SEASON + 1), "holt_winters": (f_hw, 2 * SEASON), "croston": (f_croston, 6),
}


# ----------------------------------------------------------------------------------------------- selection + intervals
def _backtest(y: np.ndarray, fn, min_train: int, origins: int):
    """Errors (actual - forecast) per horizon 1..BACKTEST_H over the last `origins` origins."""
    errs: dict[int, list[float]] = {h: [] for h in range(1, BACKTEST_H + 1)}
    abs_sum = act_sum = bias_sum = 0.0
    n = len(y)
    for t in range(max(n - origins - BACKTEST_H + 1, min_train), n):
        hb = min(BACKTEST_H, n - t)
        if hb < 1 or t < min_train:
            continue
        fc = np.maximum(fn(y[:t], hb), 0.0)
        for k in range(hb):
            e = float(y[t + k] - fc[k])
            errs[k + 1].append(e)
            abs_sum += abs(e)
            act_sum += abs(y[t + k])
            bias_sum += -e
    return errs, abs_sum, act_sum, bias_sum


def fit_forecast(values: list[float], horizon: int = 6) -> Forecast:
    y = np.array(values, dtype=float)
    n = len(y)
    warnings: list[str] = []
    if n == 0:
        return Forecast("none", horizon, [0.0] * horizon, [0.0] * horizon, [0.0] * horizon, None, None, 0, "none",
                        ["no sales history"])
    zeros = float(np.mean(y == 0))
    if n < MIN_POINTS:
        mu = float(y.mean())
        sd = max(float(y.std()), 0.3 * mu)
        warnings.append(f"only {n} months of history: constant forecast with wide interval")
        p50 = [mu] * horizon
        return Forecast("mean", horizon, [max(0.0, mu - 1.28 * sd)] * horizon, p50, [mu + 1.28 * sd] * horizon,
                        None, None, n, "low", warnings)
    if n < 12:
        warnings.append("short history (< 12 months): seasonality cannot be learned")
    if zeros > 0.3:
        warnings.append(f"intermittent demand ({zeros:.0%} of months are zero)")

    origins = min(8, n - MIN_POINTS)
    best = None
    for name, (fn, min_train) in METHODS.items():
        if n < min_train + 1 or (name == "croston" and zeros <= 0.15):
            continue
        errs, a, s, b = _backtest(y, fn, max(min_train, MIN_POINTS), origins)
        if s <= 0 or not errs[1]:
            continue
        wape = a / s
        if best is None or wape < best[1]:
            best = (name, wape, errs, b / s)
    if best is None:  # nothing could be back-tested (e.g. all zeros)
        mu = float(y[-6:].mean())
        warnings.append("no usable backtest")
        return Forecast("mean3", horizon, [0.0] * horizon, [mu] * horizon, [mu * 2] * horizon, None, None, n, "low", warnings)
    name, wape, errs, bias = best
    point = np.maximum(METHODS[name][0](y, horizon), 0.0)
    p10, p90 = [], []
    for k in range(1, horizon + 1):
        kk = min(k, BACKTEST_H)
        e = np.array(errs[kk])
        scale = math.sqrt(k / kk)
        if len(e) >= 5:
            lo, hi = np.quantile(e, 0.1) * scale, np.quantile(e, 0.9) * scale
        else:  # too few errors: fall back on the dispersion of the recent data
            sd = max(float(y[-12:].std()), 0.15 * float(y[-12:].mean())) * math.sqrt(k)
            lo, hi = -1.28 * sd, 1.28 * sd
        lo, hi = min(lo, 0.0), max(hi, 0.0)
        p10.append(max(0.0, float(point[k - 1] + lo)))
        p90.append(float(point[k - 1] + hi))
    confidence = "high" if (n >= 24 and wape < 0.15) else "medium" if (n >= 12 and wape < 0.30) else "low"
    if wape >= 0.30:
        warnings.append(f"backtest error is high (WAPE {wape:.0%}): treat as a rough guide")
    return Forecast(name, horizon, [round(v, 1) for v in p10], [round(float(v), 1) for v in point],
                    [round(v, 1) for v in p90], round(wape, 4), round(bias, 4), n, confidence, warnings,
                    backtest_errors=len(errs[1]))


# ----------------------------------------------------------------------------------------------- customer consumption
def consumption_profile(months: list[date], values: list[float]) -> dict:
    """The consumption rate of one customer and early-warning signals for the sales team."""
    y = np.array(values, dtype=float)
    n = len(y)
    if n == 0 or y.sum() == 0:
        return {"months": n, "rate_per_month": 0.0, "last3": 0.0, "avg12": 0.0, "trend_pct_per_month": None,
                "months_since_last": None, "typical_interval": None, "risk": "unknown"}
    last3 = float(y[-3:].mean())
    avg12 = float(y[-12:].mean())
    trend = None
    if n >= 6:
        w = y[-12:]
        slope = float(np.polyfit(np.arange(len(w)), w, 1)[0])
        trend = round(100.0 * slope / avg12, 2) if avg12 > 0 else None
    nz = np.flatnonzero(y > 0)
    since = int(n - 1 - nz[-1])
    gaps = np.diff(nz)
    interval = float(np.median(gaps)) if len(gaps) else None
    risk = "low"
    if since >= max(3, 2 * (interval or 1)) or (avg12 > 0 and last3 < 0.4 * avg12):
        risk = "high"
    elif avg12 > 0 and last3 < 0.7 * avg12:
        risk = "medium"
    return {"months": n, "rate_per_month": round(avg12, 1), "last3": round(last3, 1), "avg12": round(avg12, 1),
            "trend_pct_per_month": trend, "months_since_last": since, "typical_interval": interval, "risk": risk}
