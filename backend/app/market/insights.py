"""Quality-improvement insights from the plant's own inspection data, and competitor gap analysis.

Associations are reported as associations, with sample sizes and a significance test; nothing here claims causation, and results
are withheld when there is too little data.
"""
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

MIN_GROUP = 30  # inspections per group before a comparison is reported

CRITERIA = {  # key -> (Persian name)
    "quality": "کیفیت محصول", "price": "قیمت", "delivery": "تحویل و پایبندی به زمان", "certifications": "گواهینامه‌ها و استانداردها",
    "brand": "برند و اعتماد بازار", "service": "خدمات پس از فروش", "capacity": "ظرفیت و تداوم تأمین", "innovation": "نوآوری و تنوع محصول",
}
WEIGHTS = {  # ASSUMED defaults, editable via settings; each set sums to 1
    "domestic": {"quality": 0.25, "price": 0.25, "delivery": 0.15, "brand": 0.10, "service": 0.10, "capacity": 0.10,
                 "certifications": 0.05, "innovation": 0.0},
    "export": {"quality": 0.25, "certifications": 0.25, "price": 0.20, "delivery": 0.10, "capacity": 0.10, "service": 0.05,
               "brand": 0.05, "innovation": 0.0},
}
LEVERS = {  # what usually moves each criterion (generic guidance, not company-specific)
    "quality": "کاهش نرخ عیب با کنترل فرایند (SPC)، تحلیل پارتوی عیوب، و بازبینی هوشمند؛ گزارش FPY و گواهی آزمون به مشتری",
    "price": "کاهش ضایعات و افزایش بازده، خرید مواد اولیه با قرارداد/بورس، بهبود OEE و مصرف انرژی",
    "delivery": "پیش‌تولید در ماه‌های کم‌بار، موجودی ایمنی محصول، برنامه‌ریزی بر اساس پیش‌بینی تقاضا",
    "certifications": "اخذ/تمدید گواهینامه‌های لازم بازار هدف (به تب آمادگی صادرات مراجعه کنید)",
    "brand": "نمونه‌ی موفق مشتریان مرجع، انتشار گزارش کیفیت، حضور در نمایشگاه‌ها",
    "service": "زمان پاسخ به شکایت، ردیابی دسته‌ی تولید، تضمین و جایگزینی",
    "capacity": "رفع گلوگاه‌ها، پایش OEE، نگهداری پیش‌بینانه، ظرفیت اضافه‌ی برنامه‌ریزی‌شده",
    "innovation": "توسعه‌ی محصول با مشتری کلیدی، آزمایش مواد/فرمول جدید",
}


def z_two_prop(x1: int, n1: int, x2: int, n2: int) -> tuple[float, float]:
    """(z, two-sided p) for H0: equal proportions."""
    if min(n1, n2) == 0:
        return 0.0, 1.0
    p1, p2, p = x1 / n1, x2 / n2, (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


def quality_summary(rows: list[dict], days: int) -> dict:
    """rows: [{ts: datetime, line, shift, status, defects: [class_name]}] (NG/Review/OK from inspections)."""
    total = len(rows)
    ng = sum(r["status"] == "NG" for r in rows)
    review = sum(r["status"] == "Review" for r in rows)
    out = {"days": days, "total": total, "ng": ng, "review": review, "fpy": None if not total else round(1 - ng / total, 4),
           "defect_rate": None if not total else round(ng / total, 4), "warnings": []}
    if total < MIN_GROUP:
        out["warnings"].append(f"only {total} inspections in the window: conclusions are not reliable")
    # Pareto of defect classes
    cnt = Counter(c for r in rows for c in r["defects"])
    tot = sum(cnt.values())
    cum, pareto = 0, []
    for name, n in cnt.most_common():
        cum += n
        pareto.append({"class_name": name, "count": n, "pct": round(n / tot, 3), "cumulative_pct": round(cum / tot, 3)})
    out["pareto"] = pareto
    # daily trend
    days_map: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        d = r["ts"].date().isoformat()
        days_map[d][0] += 1
        days_map[d][1] += r["status"] == "NG"
    out["trend"] = [{"date": d, "total": t, "defect_rate": round(b / t, 4)} for d, (t, b) in sorted(days_map.items())]
    # by line / shift
    for key in ("line", "shift"):
        g: dict = defaultdict(lambda: [0, 0])
        for r in rows:
            g[r[key]][0] += 1
            g[r[key]][1] += r["status"] == "NG"
        items = []
        for k, (t, b) in sorted(g.items(), key=lambda kv: str(kv[0])):
            others_t, others_b = total - t, ng - b
            z, p = z_two_prop(b, t, others_b, others_t) if t >= MIN_GROUP and others_t >= MIN_GROUP else (None, None)
            items.append({key: k, "total": t, "ng": b, "defect_rate": round(b / t, 4),
                          "vs_rest_z": None if z is None else round(z, 2), "p_value": None if p is None else round(p, 4)})
        out["by_" + key] = items
    # trend of the whole window: first half vs second half
    if total >= 2 * MIN_GROUP:
        rs = sorted(rows, key=lambda r: r["ts"])
        h = len(rs) // 2
        a, b = rs[:h], rs[h:]
        z, p = z_two_prop(sum(r["status"] == "NG" for r in b), len(b), sum(r["status"] == "NG" for r in a), len(a))
        out["half_over_half"] = {"first": round(sum(r["status"] == "NG" for r in a) / len(a), 4),
                                 "second": round(sum(r["status"] == "NG" for r in b) / len(b), 4), "z": round(z, 2),
                                 "p_value": round(p, 4)}
    return out


def alarm_association(rows: list[dict], intervals: dict[int, list[tuple[datetime, datetime]]]) -> list[dict]:
    """Per line: defect rate while any alarm was active vs. while none was (hourly buckets with enough inspections)."""
    res = []
    for line, ivs in intervals.items():
        buckets: dict[datetime, list[int]] = defaultdict(lambda: [0, 0])
        for r in rows:
            if r["line"] != line:
                continue
            h = r["ts"].replace(minute=0, second=0, microsecond=0)
            buckets[h][0] += 1
            buckets[h][1] += r["status"] == "NG"
        wt = wb = nt = nb = 0
        for h, (t, b) in buckets.items():
            end = h + timedelta(hours=1)
            alarmed = any(s < end and e > h for s, e in ivs)
            if alarmed:
                wt, wb = wt + t, wb + b
            else:
                nt, nb = nt + t, nb + b
        if wt >= MIN_GROUP and nt >= MIN_GROUP:
            z, p = z_two_prop(wb, wt, nb, nt)
            res.append({"line": line, "rate_with_alarm": round(wb / wt, 4), "rate_without_alarm": round(nb / nt, 4),
                        "n_with": wt, "n_without": nt, "z": round(z, 2), "p_value": round(p, 4),
                        "note": "association only, not proof of cause"})
        else:
            res.append({"line": line, "insufficient_data": True, "n_with": wt, "n_without": nt})
    return res


def opportunities(summary: dict, assoc: list[dict], cost_per_ng: float | None) -> list[dict]:
    """Ranked, evidence-backed improvement opportunities."""
    out = []
    for r in summary.get("pareto", [])[:2]:
        if r["pct"] >= 0.25:
            out.append({"priority": 1, "text_fa": f"عیب «{r['class_name']}» {r['pct']:.0%} از کل عیوب است؛ تحلیل ریشه‌ای و اقدام اصلاحی روی آن بیشترین اثر را دارد.",
                        "evidence": f"{r['count']} occurrences"})
    for item in summary.get("by_line", []):
        if item["p_value"] is not None and item["p_value"] < 0.05 and item["vs_rest_z"] > 0:
            out.append({"priority": 2, "text_fa": f"خط {item['line']} به‌طور معنی‌دار بدتر از بقیه است (نرخ عیب {item['defect_rate']:.1%}).",
                        "evidence": f"z={item['vs_rest_z']}, p={item['p_value']}, n={item['total']}"})
    for item in summary.get("by_shift", []):
        if item["p_value"] is not None and item["p_value"] < 0.05 and item["vs_rest_z"] > 0:
            out.append({"priority": 2, "text_fa": f"شیفت «{item['shift']}» نرخ عیب معنی‌دار بالاتری دارد ({item['defect_rate']:.1%}); آموزش/شرایط شیفت را بررسی کنید.",
                        "evidence": f"z={item['vs_rest_z']}, p={item['p_value']}, n={item['total']}"})
    hh = summary.get("half_over_half")
    if hh and hh["p_value"] < 0.05 and hh["z"] > 0:
        out.append({"priority": 1, "text_fa": f"نرخ عیب در نیمه‌ی دوم بازه از {hh['first']:.1%} به {hh['second']:.1%} رسیده است (روند بدتر شدن).",
                    "evidence": f"z={hh['z']}, p={hh['p_value']}"})
    for a in assoc:
        if not a.get("insufficient_data") and a["p_value"] < 0.05 and a["rate_with_alarm"] > a["rate_without_alarm"]:
            out.append({"priority": 2, "text_fa": f"در خط {a['line']} هنگام وجود آلارم فرایندی نرخ عیب بالاتر است ({a['rate_with_alarm']:.1%} در برابر {a['rate_without_alarm']:.1%}); آلارم‌های آن خط را علت‌یابی کنید.",
                        "evidence": f"z={a['z']}, p={a['p_value']} ({a['note']})"})
    if cost_per_ng and summary["ng"]:
        out.append({"priority": 3, "text_fa": f"هزینه‌ی تخمینی عیب در این بازه: {summary['ng'] * cost_per_ng:,.0f} (تعداد NG × هزینه‌ی واحد وارد‌شده).",
                    "evidence": f"{summary['ng']} NG × {cost_per_ng}"})
    return sorted(out, key=lambda o: o["priority"])


# ----------------------------------------------------------------------------------------------- competitors
def competitive_gap(own: dict[str, float], rivals: dict[str, dict[str, float]], weights: dict[str, float],
                    evidence_age_days: dict[str, dict[str, int]] | None = None) -> dict:
    """own: criterion -> score; rivals: name -> criterion -> score. Weighted totals, per-criterion gap to the best rival."""
    crits = [c for c in CRITERIA if weights.get(c, 0) > 0]
    scored_own = {c: own[c] for c in crits if c in own}
    rows = []
    for c in crits:
        best_name, best = None, None
        for name, sc in rivals.items():
            if c in sc and (best is None or sc[c] > best):
                best_name, best = name, sc[c]
        gap = None if (best is None or c not in own) else round(best - own[c], 2)
        rows.append({"criterion": c, "name_fa": CRITERIA[c], "weight": weights[c], "own": own.get(c), "best_rival": best,
                     "best_rival_name": best_name, "gap": gap, "weighted_gap": None if gap is None else round(gap * weights[c], 3),
                     "lever": LEVERS[c] if gap is not None and gap > 0 else None})

    def total(sc):
        have = [c for c in crits if c in sc]
        w = sum(weights[c] for c in have)
        return None if w == 0 else round(sum(sc[c] * weights[c] for c in have) / w, 2), round(w, 2)

    own_total = total(scored_own)
    rival_totals = {n: total({c: v for c, v in s.items() if c in crits}) for n, s in rivals.items()}
    warnings = []
    missing_own = [CRITERIA[c] for c in crits if c not in own]
    if missing_own:
        warnings.append("امتیاز شرکت برای این معیارها وارد نشده: " + "، ".join(missing_own))
    for n, (t, w) in rival_totals.items():
        if w < 0.6:
            warnings.append(f"امتیاز رقیب «{n}» فقط {w:.0%} از وزن معیارها را پوشش می‌دهد؛ مقایسه‌ی کل کم‌اعتبار است")
    stale = [f"{n}/{c}" for n, m in (evidence_age_days or {}).items() for c, d in m.items() if d > 365]
    if stale:
        warnings.append(f"{len(stale)} امتیاز مستندات قدیمی‌تر از یک سال دارند")
    ranked = sorted((r for r in rows if r["weighted_gap"] is not None and r["weighted_gap"] > 0), key=lambda r: -r["weighted_gap"])
    return {"rows": rows, "own_total": own_total[0] if own_total else None, "own_coverage": own_total[1] if own_total else 0,
            "rivals": {n: {"total": t, "coverage": w} for n, (t, w) in rival_totals.items()},
            "priorities": ranked[:3], "warnings": warnings}
