"""Trend, Pareto and SPC p-chart computations (FR-3-4, FR-3-5)."""
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Defect, Inspection, Status


def _bucket(ts: datetime, period: str) -> str:
    if period == "month":
        return ts.strftime("%Y-%m")
    if period == "week":
        d = ts - timedelta(days=ts.weekday())
        return d.strftime("%Y-%m-%d")
    return ts.strftime("%Y-%m-%d")


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _rows(db: Session, days: int, line_id: int | None):
    q = select(Inspection.created_at, Inspection.status).where(Inspection.created_at >= _since(days))
    if line_id:
        q = q.where(Inspection.line_id == line_id)
    return db.execute(q).all()


def trend(db: Session, period: str, days: int, line_id: int | None):
    buckets: dict[str, Counter] = defaultdict(Counter)
    for ts, status in _rows(db, days, line_id):
        buckets[_bucket(ts, period)][status] += 1
    out = []
    for key in sorted(buckets):
        c = buckets[key]
        total = sum(c.values())
        out.append({
            "bucket": key, "total": total, "ng": c[Status.NG], "review": c[Status.REVIEW],
            "defect_rate": c[Status.NG] / total if total else 0.0,
        })
    return out


def pareto(db: Session, days: int, line_id: int | None):
    q = (
        select(Defect.class_name)
        .join(Inspection, Inspection.id == Defect.inspection_id)
        .where(Inspection.created_at >= _since(days))
    )
    if line_id:
        q = q.where(Inspection.line_id == line_id)
    counts = Counter(db.scalars(q))
    total = sum(counts.values())
    cum, out = 0, []
    for name, n in counts.most_common():
        cum += n
        out.append({"class_name": name, "count": n, "cumulative_pct": cum / total})
    return out


def spc_p_chart(db: Session, days: int, line_id: int | None):
    """Daily p-chart with 3-sigma limits; flags out-of-control days."""
    daily = trend(db, "day", days, line_id)
    total_n = sum(d["total"] for d in daily)
    if not total_n:
        return {"p_bar": 0.0, "points": []}
    p_bar = sum(d["ng"] for d in daily) / total_n
    points = []
    for d in daily:
        n = d["total"]
        sigma = math.sqrt(p_bar * (1 - p_bar) / n)
        ucl, lcl = min(1.0, p_bar + 3 * sigma), max(0.0, p_bar - 3 * sigma)
        points.append({
            "bucket": d["bucket"], "p": d["defect_rate"], "ucl": ucl, "lcl": lcl,
            "out_of_control": d["defect_rate"] > ucl or d["defect_rate"] < lcl,
        })
    return {"p_bar": p_bar, "points": points}
