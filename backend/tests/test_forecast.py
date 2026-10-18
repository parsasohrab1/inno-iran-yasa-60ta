from datetime import date

import numpy as np
import pytest

from app.market.forecast import (add_months, consumption_profile, fit_forecast, month_floor, monthly_series)


def series(n, level=1000.0, trend=0.0, amp=0.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    y = level + trend * t + amp * np.sin(2 * np.pi * t / 12) + rng.normal(0, noise, n)
    return np.maximum(y, 0.0)


def holdout_wape(y_full, n_train, h):
    f = fit_forecast(list(y_full[:n_train]), h)
    actual = y_full[n_train:n_train + h]
    return float(np.abs(actual - np.array(f.p50)).sum() / np.abs(actual).sum()), f, actual


def test_month_helpers():
    assert month_floor(date(2026, 3, 17)) == date(2026, 3, 1)
    assert add_months(date(2026, 11, 1), 3) == date(2027, 2, 1)
    assert add_months(date(2026, 1, 1), -2) == date(2025, 11, 1)


def test_monthly_series_sums_and_fills_gaps_with_zero():
    rows = [(date(2026, 1, 5), 10), (date(2026, 1, 20), 5), (date(2026, 4, 2), 7)]
    months, vals = monthly_series(rows)
    assert months == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]
    assert vals == [15, 0, 0, 7]
    assert monthly_series([]) == ([], [])
    assert len(monthly_series(rows, until=date(2026, 6, 10))[0]) == 6


def test_flat_demand_is_forecast_flat():
    y = series(30, noise=20)
    wape, f, _ = holdout_wape(series(36, noise=20, seed=1), 30, 6)
    assert wape < 0.08 and f.confidence in ("high", "medium")
    assert all(900 < v < 1100 for v in f.p50)


def test_trend_is_followed():
    full = series(36, level=500, trend=25, noise=15, seed=2)
    wape, f, actual = holdout_wape(full, 30, 6)
    assert wape < 0.10
    assert f.p50[-1] > f.p50[0]  # rising demand stays rising


def test_seasonality_is_learned_with_two_years_of_history():
    full = series(48, level=1000, amp=300, noise=25, seed=3)
    wape, f, actual = holdout_wape(full, 36, 12)
    assert f.method in ("holt_winters", "snaive") and f.n == 36
    assert wape < 0.10  # a non-seasonal method cannot do this on a +-30 % cycle
    # the forecast reproduces the shape: peak and trough months line up with the truth
    assert abs(int(np.argmax(f.p50)) - int(np.argmax(actual))) <= 1
    assert abs(int(np.argmin(f.p50)) - int(np.argmin(actual))) <= 1


def test_intervals_are_ordered_non_negative_and_widen_with_horizon():
    f = fit_forecast(list(series(36, level=800, amp=100, noise=60, seed=4)), 9)
    assert all(lo <= p <= hi for lo, p, hi in zip(f.p10, f.p50, f.p90))
    assert min(f.p10) >= 0
    assert (f.p90[-1] - f.p10[-1]) >= (f.p90[0] - f.p10[0])


def test_interval_coverage_on_unseen_months_is_reasonable():
    """P10-P90 should contain roughly 80 % of future actuals; we accept 60-100 % over many series."""
    hits = total = 0
    for seed in range(40):
        full = series(42, level=1000, trend=5, amp=150, noise=70, seed=100 + seed)
        f = fit_forecast(list(full[:36]), 6)
        for k in range(6):
            total += 1
            hits += f.p10[k] <= full[36 + k] <= f.p90[k]
    assert 0.60 <= hits / total <= 1.0, hits / total


def test_short_history_gives_wide_low_confidence_forecast():
    f = fit_forecast([100, 120, 90], 4)
    assert f.method == "mean" and f.confidence == "low" and f.wape is None
    assert (f.p90[0] - f.p10[0]) > 0.4 * f.p50[0]
    assert any("3 months" in w for w in f.warnings)


def test_no_history_is_reported_not_invented():
    f = fit_forecast([], 3)
    assert f.method == "none" and f.confidence == "none" and f.p50 == [0.0, 0.0, 0.0] and f.warnings


def test_noisy_series_is_labelled_low_confidence():
    f = fit_forecast(list(series(18, level=500, noise=450, seed=5)), 6)
    assert f.confidence == "low" and f.wape is not None and f.wape > 0.3
    assert any("backtest error is high" in w for w in f.warnings)


def test_intermittent_demand_is_flagged_and_handled():
    rng = np.random.default_rng(7)
    y = np.where(rng.random(36) < 0.35, rng.integers(200, 400, 36), 0).astype(float)
    f = fit_forecast(list(y), 6)
    assert any("intermittent" in w for w in f.warnings)
    assert f.p50[0] >= 0 and f.p90[0] >= f.p50[0]


def test_all_zero_series_does_not_crash():
    f = fit_forecast([0.0] * 12, 3)
    assert f.p50 == [0.0, 0.0, 0.0] and f.confidence == "low"


def test_forecast_is_deterministic():
    y = list(series(30, noise=50, amp=100, seed=9))
    assert fit_forecast(y, 6).to_dict() == fit_forecast(y, 6).to_dict()


def test_consumption_profile_flags_a_customer_that_is_fading():
    healthy = consumption_profile([], [100, 110, 95, 105, 120, 100, 110, 115, 100, 105, 110, 108])
    assert healthy["risk"] == "low" and healthy["months_since_last"] == 0
    fading = consumption_profile([], [100, 110, 95, 105, 120, 100, 110, 115, 60, 40, 30, 20])
    assert fading["risk"] == "high" and fading["trend_pct_per_month"] < 0
    lapsed = consumption_profile([], [100, 100, 100, 100, 100, 100, 100, 100, 100, 0, 0, 0])
    assert lapsed["risk"] == "high" and lapsed["months_since_last"] == 3
    quarterly = consumption_profile([], [0, 0, 300, 0, 0, 300, 0, 0, 300, 0, 0, 300])
    assert quarterly["typical_interval"] == 3 and quarterly["risk"] == "low"  # a quarterly buyer is not "lapsed"
    assert consumption_profile([], [0, 0, 0])["risk"] == "unknown"
