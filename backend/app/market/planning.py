"""Predictive production scenarios: turns demand forecasts into capacity, shift and raw-material needs, with suggested actions.

DECISION SUPPORT ONLY. Nothing here is sent to a PLC or changes a plan by itself; every suggestion is for a planner to accept.
Inputs that are assumptions (hours per day, target OEE, shift length) are returned with the result so nobody mistakes them for
measured plant data.
"""
import math
from dataclasses import dataclass, field

DEFAULTS = {
    "lines": 4,
    "days_per_month": 26,      # Friday off (assumed schedule)
    "hours_per_day": 24,       # 3 x 8 h shifts (assumed schedule)
    "shift_hours": 8,
    "target_oee": 0.65,        # planning OEE, to be replaced by the measured value
    "export_uplift": 0.20,     # "export push" scenario: extra demand if an export contract materialises
    "quality_gain_pts": 0.02,  # "quality improvement" scenario: FPY +2 percentage points
    "safety_stock_months": 0.5,
    "util_high": 0.95,
    "util_low": 0.50,
}


@dataclass
class Product:
    id: int
    code: str
    name: str
    rate_per_min: float
    fpy: float
    unit_price: float | None = None
    fg_stock: float = 0.0
    bom: list[tuple[int, float]] = field(default_factory=list)  # (material_id, qty per unit)


@dataclass
class Material:
    id: int
    code: str
    name: str
    on_hand: float
    lead_time_days: int
    safety_stock: float = 0.0


def _fpy(p: Product, extra: float = 0.0) -> float:
    return min(max(p.fpy + extra, 0.5), 0.999)


def build_scenario(name: str, label: str, products: list[Product], materials: list[Material], demand: dict[int, list[float]],
                   months: int, cfg: dict, fpy_gain: float = 0.0) -> dict:
    """demand[product_id] = monthly demand (good units) for `months` months."""
    lines, dpm, hpd = cfg["lines"], cfg["days_per_month"], cfg["hours_per_day"]
    avail_h_line = dpm * hpd
    rows = []
    mat_need = {m.id: [0.0] * months for m in materials}
    lost_units = [0.0] * months
    for k in range(months):
        machine_h = 0.0
        demand_total = 0.0
        revenue_at_risk = 0.0
        for p in products:
            d = demand.get(p.id, [0.0] * months)[k]
            units = d / _fpy(p, fpy_gain)  # gross units to run, to end with `d` good ones
            machine_h += units / (p.rate_per_min * 60.0 * cfg["target_oee"])
            demand_total += d
            for mid, q in p.bom:
                if mid in mat_need:
                    mat_need[mid][k] += units * q
        util = machine_h / (lines * avail_h_line) if lines else 0.0
        need_lines = machine_h / avail_h_line
        unmet_share = max(0.0, 1.0 - 1.0 / util) if util > 1.0 else 0.0
        for p in products:
            d = demand.get(p.id, [0.0] * months)[k]
            lost_units[k] += d * unmet_share
            if p.unit_price:
                revenue_at_risk += d * unmet_share * p.unit_price
        rows.append({
            "month_offset": k + 1, "demand_units": round(demand_total), "machine_hours": round(machine_h),
            "utilization": round(util, 3), "lines_needed": round(need_lines, 2),
            "shifts_per_day": min(3, max(1, math.ceil(machine_h / (lines * dpm * cfg["shift_hours"])))) if machine_h > 0 else 0,
            "unmet_units": round(lost_units[k]), "revenue_at_risk": round(revenue_at_risk) if revenue_at_risk else None,
        })
    materials_out = []
    for m in materials:
        need = mat_need[m.id]
        if not any(need):
            continue
        stock, short_k, cum = m.on_hand, None, 0.0
        for k, nd in enumerate(need):
            cum += nd
            if short_k is None and cum > stock - m.safety_stock:
                short_k = k
        month_days = 30.0
        order_by_day = None if short_k is None else max(0, short_k * month_days - m.lead_time_days)
        materials_out.append({
            "material_id": m.id, "code": m.code, "name": m.name, "on_hand": round(m.on_hand),
            "need_total": round(cum), "need_monthly": [round(x) for x in need], "lead_time_days": m.lead_time_days,
            "shortfall_month": None if short_k is None else short_k + 1,
            "order_within_days": None if order_by_day is None else int(order_by_day),
            "order_qty": 0 if short_k is None else round(max(0.0, cum - (m.on_hand - m.safety_stock))),
        })
    return {"name": name, "label": label, "months": rows, "materials": materials_out}


def recommendations(sc: dict, cfg: dict) -> list[dict]:
    """Rule-based suggestions for one scenario. Each carries the evidence it is based on."""
    out = []
    rows = sc["months"]
    hi, lo = cfg["util_high"], cfg["util_low"]
    peak = max(rows, key=lambda r: r["utilization"])
    trough = min(rows, key=lambda r: r["utilization"])
    if peak["utilization"] > 1.0:
        extra = math.ceil(peak["lines_needed"]) - cfg["lines"]
        out.append({"severity": "critical", "kind": "capacity",
                    "text_fa": f"ماه {peak['month_offset']}: نیاز به {peak['lines_needed']} خط در برابر {cfg['lines']} خط موجود؛ "
                               f"حدود {peak['unmet_units']:,} واحد پوشش داده نمی‌شود. اقدام: اضافه‌کاری/شیفت جمعه، برون‌سپاری، یا پیش‌تولید در ماه‌های کم‌بار.",
                    "why": f"utilization {peak['utilization']:.0%} > 100%", "extra_lines": max(extra, 0)})
    elif peak["utilization"] > hi:
        out.append({"severity": "warning", "kind": "capacity",
                    "text_fa": f"ماه {peak['month_offset']}: بهره‌برداری {peak['utilization']:.0%}؛ حاشیه‌ی ظرفیت برای خرابی و تعویض قالب ندارید. "
                               "پیش‌تولید در ماه‌های کم‌بار و تعویق سرویس‌های غیرضروری را بررسی کنید.",
                    "why": f"utilization {peak['utilization']:.0%} > {hi:.0%}"})
    if trough["utilization"] < lo and trough["month_offset"] != peak["month_offset"]:
        out.append({"severity": "info", "kind": "maintenance",
                    "text_fa": f"ماه {trough['month_offset']}: بهره‌برداری فقط {trough['utilization']:.0%}؛ بهترین فرصت برای نگهداری برنامه‌ریزی‌شده، "
                               "تعویض قالب بزرگ، آموزش و آزمایش محصول جدید، یا پیش‌تولید برای ماه‌های اوج.",
                    "why": f"utilization {trough['utilization']:.0%} < {lo:.0%}"})
    ranked = sorted(sc["materials"], key=lambda m: (m["shortfall_month"] is None, m["shortfall_month"] or 0))
    for m in ranked:
        if m["shortfall_month"] is not None:
            late = m["order_within_days"] is not None and m["order_within_days"] <= 0
            out.append({"severity": "critical" if late else "warning", "kind": "material",
                        "text_fa": f"{m['name']}: موجودی ({m['on_hand']:,}) تا ماه {m['shortfall_month']} کافی نیست؛ "
                                   + ("زمان تأمین (" + str(m["lead_time_days"]) + " روز) از مهلت گذشته؛ سفارش فوری یا تأمین‌کننده‌ی جایگزین لازم است."
                                      if late else f"حداکثر تا {m['order_within_days']} روز دیگر سفارش دهید (زمان تأمین {m['lead_time_days']} روز)."),
                        "why": f"cumulative need {m['need_total']:,} > stock", "material": m["code"]})
    return out


def scenarios(products: list[Product], materials: list[Material], quantiles: dict[int, dict[str, list[float]]], months: int,
              cfg: dict | None = None, measured_fpy: float | None = None) -> dict:
    """quantiles[product_id] = {"p10": [...], "p50": [...], "p90": [...]} (monthly good units)."""
    c = {**DEFAULTS, **(cfg or {})}
    if measured_fpy:  # actual first-pass yield replaces the per-product default for planning
        for p in products:
            p.fpy = measured_fpy
    pick = lambda key, mult=1.0: {pid: [v * mult for v in q[key][:months]] for pid, q in quantiles.items()}  # noqa: E731
    defs = [
        ("base", "پایه (P50)", pick("p50"), 0.0),
        ("growth", "رشد (P90)", pick("p90"), 0.0),
        ("downturn", "رکود (P10)", pick("p10"), 0.0),
        ("export_push", f"فشار صادراتی (P50 + {c['export_uplift']:.0%})", pick("p50", 1 + c["export_uplift"]), 0.0),
        ("quality_plus", f"بهبود کیفیت (FPY +{c['quality_gain_pts'] * 100:.0f} واحد درصد)", pick("p50"), c["quality_gain_pts"]),
    ]
    out = []
    for name, label, demand, gain in defs:
        sc = build_scenario(name, label, products, materials, demand, months, c, gain)
        sc["recommendations"] = recommendations(sc, c)
        out.append(sc)
    base = next(s for s in out if s["name"] == "base")
    quality = next(s for s in out if s["name"] == "quality_plus")
    saved = sum(r["machine_hours"] for r in base["months"]) - sum(r["machine_hours"] for r in quality["months"])
    return {"assumptions": c, "assumptions_note": "ساعات کاری، OEE هدف و طول شیفت فرضی‌اند تا مقدار واقعی کارخانه جایگزین شود",
            "scenarios": out, "quality_plus_hours_saved": round(saved)}
