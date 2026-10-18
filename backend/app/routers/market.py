"""Market development API: quality insights, competitor benchmarking, export readiness, demand forecast, production scenarios."""
import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..auth import current_user, require
from ..db import get_db
from ..market import insights as ins
from ..market import planning as plan
from ..market.forecast import add_months, consumption_profile, fit_forecast, month_floor, monthly_series
from ..models import (BomItem, Certification, Competitor, CompetitorScore, Customer, FinishedStock, Inspection, Material,
                      MarketSetting, PlantEvent, PriceQuote, Product, Role, SalesRecord, StockMovement, User)

router = APIRouter(prefix="/api/market", tags=["market"])
_write = require(Role.MANAGER)  # sales / production managers; admin always allowed


def _row(o) -> dict:
    return {c.name: getattr(o, c.name) for c in o.__table__.columns}


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _today() -> date:
    return datetime.now(timezone.utc).date()


# ------------------------------------------------------------------------------------------------ settings
def _setting(db: Session, key: str, default):
    r = db.get(MarketSetting, key)
    return default if r is None else r.value


class SettingsIn(BaseModel):
    planning: dict | None = None  # overrides for planning.DEFAULTS (lines, hours_per_day, target_oee ...)
    weights: dict | None = None  # {"domestic": {...}, "export": {...}} criterion weights
    cost_per_ng: float | None = Field(default=None, ge=0)


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return {"planning": {**plan.DEFAULTS, **_setting(db, "planning", {})}, "weights": {**ins.WEIGHTS, **_setting(db, "weights", {})},
            "cost_per_ng": _setting(db, "cost_per_ng", {}).get("v") if isinstance(_setting(db, "cost_per_ng", {}), dict) else None,
            "criteria": ins.CRITERIA, "note": "defaults are assumptions until the factory's real values are entered"}


@router.put("/settings")
def put_settings(b: SettingsIn, db: Session = Depends(get_db), _: User = Depends(_write)):
    def save(key, val):
        r = db.get(MarketSetting, key)
        if r:
            r.value = val
        else:
            db.add(MarketSetting(key=key, value=val))

    if b.planning is not None:
        bad = [k for k in b.planning if k not in plan.DEFAULTS]
        if bad:
            raise HTTPException(422, f"unknown planning keys: {bad}")
        save("planning", b.planning)
    if b.weights is not None:
        for seg, w in b.weights.items():
            if seg not in ("domestic", "export") or any(c not in ins.CRITERIA for c in w) or not (0.99 <= sum(w.values()) <= 1.01):
                raise HTTPException(422, "weights must cover known criteria per segment and sum to 1")
        save("weights", b.weights)
    if b.cost_per_ng is not None:
        save("cost_per_ng", {"v": b.cost_per_ng})
    db.commit()
    return get_settings(db)


# ------------------------------------------------------------------------------------------------ products / sales
class ProductIn(BaseModel):
    code: str
    name_fa: str
    name_en: str | None = None
    unit_cost: float | None = Field(default=None, ge=0)
    unit_price: float | None = Field(default=None, ge=0)
    rate_per_min: float = Field(default=30.0, gt=0)
    fpy_default: float = Field(default=0.97, gt=0.5, le=1)


class BomIn(BaseModel):
    material_id: int
    qty_per_unit: float = Field(gt=0)


class StockIn(BaseModel):
    qty: float = Field(ge=0)


class SaleIn(BaseModel):
    customer_id: int
    product_id: int
    period: date
    qty: float = Field(ge=0)
    revenue: float | None = Field(default=None, ge=0)


@router.get("/products")
def products(db: Session = Depends(get_db), _: User = Depends(current_user)):
    out = []
    for p in db.scalars(select(Product).order_by(Product.code)):
        bom = [{"material_id": b.material_id, "qty_per_unit": b.qty_per_unit}
               for b in db.scalars(select(BomItem).where(BomItem.product_id == p.id))]
        fg = db.scalar(select(FinishedStock).where(FinishedStock.product_id == p.id))
        out.append({**_row(p), "bom": bom, "fg_stock": fg.qty if fg else 0.0})
    return out


@router.post("/products", status_code=201)
def add_product(b: ProductIn, db: Session = Depends(get_db), _: User = Depends(_write)):
    if db.scalar(select(Product).where(Product.code == b.code)):
        raise HTTPException(409, "product code exists")
    p = Product(**b.model_dump())
    db.add(p)
    db.commit()
    return _row(p)


@router.put("/products/{pid}/bom")
def set_bom(pid: int, items: list[BomIn], db: Session = Depends(get_db), _: User = Depends(_write)):
    if not db.get(Product, pid):
        raise HTTPException(404, "product not found")
    for i in items:
        if not db.get(Material, i.material_id):
            raise HTTPException(404, f"material {i.material_id} not found")
    db.execute(delete(BomItem).where(BomItem.product_id == pid))
    db.add_all(BomItem(product_id=pid, **i.model_dump()) for i in items)
    db.commit()
    return {"ok": True, "items": len(items)}


@router.put("/products/{pid}/stock")
def set_stock(pid: int, b: StockIn, db: Session = Depends(get_db), _: User = Depends(_write)):
    if not db.get(Product, pid):
        raise HTTPException(404, "product not found")
    fg = db.scalar(select(FinishedStock).where(FinishedStock.product_id == pid))
    if fg:
        fg.qty, fg.as_of = b.qty, _today()
    else:
        db.add(FinishedStock(product_id=pid, qty=b.qty, as_of=_today()))
    db.commit()
    return {"ok": True}


@router.post("/sales/bulk", status_code=201)
def sales_bulk(items: list[SaleIn], db: Session = Depends(get_db), _: User = Depends(_write)):
    if len(items) > 20000:
        raise HTTPException(413, "too many rows")
    cust = {c.id for c in db.scalars(select(Customer))}
    prod = {p.id for p in db.scalars(select(Product))}
    bad = [i for i, s in enumerate(items) if s.customer_id not in cust or s.product_id not in prod]
    if bad:
        raise HTTPException(422, f"unknown customer/product in rows {bad[:10]}")
    db.add_all(SalesRecord(customer_id=s.customer_id, product_id=s.product_id, period=month_floor(s.period), qty=s.qty,
                           revenue=s.revenue) for s in items)
    db.commit()
    return {"ok": True, "rows": len(items)}


@router.get("/sales/summary")
def sales_summary(db: Session = Depends(get_db), _: User = Depends(current_user)):
    n, lo, hi = db.execute(select(func.count(), func.min(SalesRecord.period), func.max(SalesRecord.period))).one()
    return {"rows": n, "first": lo.isoformat() if lo else None, "last": hi.isoformat() if hi else None,
            "demo_rows": db.scalar(select(func.count()).select_from(SalesRecord).where(SalesRecord.is_demo))}


def _series(db: Session, product_id: int | None = None, customer_id: int | None = None):
    q = select(SalesRecord.period, SalesRecord.qty)
    if product_id:
        q = q.where(SalesRecord.product_id == product_id)
    if customer_id:
        q = q.where(SalesRecord.customer_id == customer_id)
    rows = [(p, float(qty)) for p, qty in db.execute(q)]
    last_full = add_months(month_floor(_today()), -1)  # the current month is incomplete: never train on it
    rows = [(p, q) for p, q in rows if p <= last_full]
    return monthly_series(rows, until=last_full if rows else None)


@router.get("/forecast")
def forecast(product_id: int | None = None, customer_id: int | None = None, horizon: int = 6,
             db: Session = Depends(get_db), _: User = Depends(current_user)):
    horizon = max(1, min(horizon, 24))
    months, vals = _series(db, product_id, customer_id)
    f = fit_forecast(vals, horizon)
    start = add_months(month_floor(_today()), 0)
    return {"history": [{"month": m.isoformat(), "qty": v} for m, v in zip(months, vals)],
            "forecast": {**f.to_dict(), "months": [add_months(start, k).isoformat() for k in range(horizon)]},
            "demo": bool(db.scalar(select(func.count()).select_from(SalesRecord).where(SalesRecord.is_demo)))}


@router.get("/customers/consumption")
def customers_consumption(horizon: int = 3, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Per customer: consumption rate, trend, risk signals and the next months' forecast."""
    out = []
    for c in db.scalars(select(Customer).order_by(Customer.kind, Customer.name)):
        months, vals = _series(db, None, c.id)
        if not vals:
            out.append({"customer_id": c.id, "name": c.name, "kind": c.kind, "is_demo": c.is_demo, "profile": None,
                        "forecast": None})
            continue
        f = fit_forecast(vals, horizon)
        out.append({"customer_id": c.id, "name": c.name, "kind": c.kind, "is_demo": c.is_demo,
                    "profile": consumption_profile(months, vals),
                    "forecast": {"method": f.method, "p10": f.p10, "p50": f.p50, "p90": f.p90, "confidence": f.confidence,
                                 "wape": f.wape, "warnings": f.warnings}})
    order = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    out.sort(key=lambda r: order.get((r["profile"] or {}).get("risk", "unknown"), 3))
    return out


# ------------------------------------------------------------------------------------------------ production scenarios
def _measured_fpy(db: Session, days: int = 30) -> float | None:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    total = db.scalar(select(func.count()).select_from(Inspection).where(Inspection.created_at >= since)) or 0
    if total < ins.MIN_GROUP:
        return None
    ng = db.scalar(select(func.count()).select_from(Inspection).where(Inspection.created_at >= since,
                                                                       Inspection.status == "NG")) or 0
    return 1 - ng / total


def _inventory(db: Session) -> dict[int, float]:
    return {mid: float(t or 0) for mid, t in db.execute(
        select(StockMovement.material_id, func.sum(StockMovement.qty)).group_by(StockMovement.material_id))}


@router.get("/scenarios")
def scenarios(months: int = 6, uplift: float | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    months = max(1, min(months, 12))
    cfg = {**plan.DEFAULTS, **_setting(db, "planning", {})}
    if uplift is not None:
        cfg["export_uplift"] = max(0.0, min(uplift, 2.0))
    products_db = list(db.scalars(select(Product)))
    if not products_db:
        raise HTTPException(409, "no products defined: add products (and sales history) first")
    fpy = _measured_fpy(db)
    on_hand = _inventory(db)
    prods, quant, notes = [], {}, []
    for p in products_db:
        fg = db.scalar(select(FinishedStock).where(FinishedStock.product_id == p.id))
        bom = [(b.material_id, b.qty_per_unit) for b in db.scalars(select(BomItem).where(BomItem.product_id == p.id))]
        if not bom:
            notes.append(f"محصول {p.code}: BOM تعریف نشده؛ نیاز مواد محاسبه نمی‌شود")
        _, vals = _series(db, p.id)
        f = fit_forecast(vals, months)
        if f.confidence in ("low", "none"):
            notes.append(f"پیش‌بینی محصول {p.code} اعتماد {f.confidence} دارد: " + "; ".join(f.warnings or ["داده کم"]))
        quant[p.id] = {"p10": f.p10, "p50": f.p50, "p90": f.p90}
        prods.append(plan.Product(p.id, p.code, p.name_fa, p.rate_per_min, p.fpy_default, p.unit_price, fg.qty if fg else 0.0, bom))
    mats = [plan.Material(m.id, m.code, m.name_fa, on_hand.get(m.id, 0.0), m.lead_time_days, m.safety_stock)
            for m in db.scalars(select(Material))]
    res = plan.scenarios(prods, mats, quant, months, cfg, measured_fpy=fpy)
    res["measured_fpy"] = None if fpy is None else round(fpy, 4)
    res["notes"] = notes
    res["start_month"] = add_months(month_floor(_today()), 0).isoformat()
    return res


# ------------------------------------------------------------------------------------------------ quality insights
@router.get("/quality")
def quality(days: int = 30, db: Session = Depends(get_db), _: User = Depends(current_user)):
    days = max(1, min(days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    for i in db.scalars(select(Inspection).where(Inspection.created_at >= since).limit(200000)):
        rows.append({"ts": _utc(i.created_at), "line": i.line_id, "shift": i.shift, "status": i.status.value,
                     "defects": [d.class_name for d in i.defects]})
    summary = ins.quality_summary(rows, days)
    raised: dict[tuple, datetime] = {}
    intervals: dict[int, list] = defaultdict(list)
    for e in db.scalars(select(PlantEvent).where(PlantEvent.kind.in_(("alarm_raised", "alarm_cleared")),
                                                 PlantEvent.ts >= since).order_by(PlantEvent.ts)):
        key = (e.line, (e.payload or {}).get("sensor"))
        if e.kind == "alarm_raised":
            raised[key] = _utc(e.ts)
        elif key in raised:
            intervals[e.line or 0].append((raised.pop(key), _utc(e.ts)))
    assoc = ins.alarm_association(rows, {ln: iv for ln, iv in intervals.items() if ln})
    cost = _setting(db, "cost_per_ng", {}).get("v") if isinstance(_setting(db, "cost_per_ng", {}), dict) else None
    return {**summary, "alarm_association": assoc, "opportunities": ins.opportunities(summary, assoc, cost),
            "cost_per_ng": cost}


# ------------------------------------------------------------------------------------------------ competitors
class CompetitorIn(BaseModel):
    name: str
    kind: str = Field(default="domestic", pattern="^(domestic|international)$")
    is_own: bool = False
    notes: str | None = None
    source_url: str | None = None


class ScoreIn(BaseModel):
    criterion: str
    score: float = Field(ge=1, le=5)
    evidence: str = Field(min_length=3, max_length=300)  # a score without a reason is not accepted
    as_of: date


PUBLIC_COMPETITORS = [  # candidates from public press (names only, NO scores); dates/sources in `notes`
    ("بارز (Barez)", "domestic", "بزرگ‌ترین تولیدکننده‌ی تایر ایران طبق گزارش ۲۰۱۸؛ ظرفیت اعلامی حدود ۵ میلیون تایر سواری و ۲۰۰ هزار تایر سنگین در سال؛ ادعای E-mark برای تأمین OE.",
     "https://www.european-rubber-journal.com/article/2067176/iran-tire-maker-signs-mou-with-french-industry-group"),
    ("یزد تایر (Yazd Tire)", "domestic", "ظرفیت حدود ۳٫۵ میلیون تایر در سال و ۱۰ تا ۱۲٪ بازار داخل (گزارش قدیمی)؛ قرارداد تأمین ۵۰ هزار تایر برای رنو الجزایر (۲۰۱۷)؛ صادرات به آفریقا/آسیای مرکزی/خاورمیانه.",
     "https://tiresandparts.net/news/tires/yazd-tire-supply-tyres-renault/"),
    ("کویر تایر (Kavir Tire)", "domestic", "از سه تولیدکننده‌ی اصلی بازار داخل در گزارش‌های خبری؛ سهم دقیق در منابع یافت‌شده نبود.",
     "https://www.digikala.com/mag/what-is-the-best-iranian-tire-brand/"),
    ("گلدستون (Goldstone)", "domestic", "در گزارش ۲۰۱۴ از برندهای مسلط داخلی نام برده شده؛ داده‌ی بیشتری یافت نشد.",
     "https://tyrepress.com/2014/01/iranian-tyre-manufacturers-optimistic-about-export-future-as-sanctions-ease"),
    ("ایران تایر (Iran Tire)", "domestic", "در جدول قیمت لاستیک ایرانی ۱۴۰۵ حضور دارد؛ سهم بازار در منابع یافت‌شده نبود.",
     "https://www.hamrah-mechanic.com/mag/best-iranian-tires/"),
    ("واردات چینی (Chinese imports)", "international", "گزارش ۲۰۱۵: حدود ۳۰٪ لاستیک سبک و ۶۰٪ سنگین بازار ایران؛ داده‌ی قدیمی.",
     "https://www.european-rubber-journal.com/2015/07/14/nuclear-deal-can-boost-iran-tire-industry"),
]


@router.get("/competitors")
def competitors(db: Session = Depends(get_db), _: User = Depends(current_user)):
    out = []
    for c in db.scalars(select(Competitor).order_by(Competitor.is_own.desc(), Competitor.name)):
        sc = [{"criterion": s.criterion, "score": s.score, "evidence": s.evidence, "as_of": s.as_of.isoformat()}
              for s in db.scalars(select(CompetitorScore).where(CompetitorScore.competitor_id == c.id).order_by(CompetitorScore.as_of))]
        out.append({**_row(c), "scores": sc})
    return out


@router.post("/competitors", status_code=201)
def add_competitor(b: CompetitorIn, db: Session = Depends(get_db), _: User = Depends(_write)):
    if db.scalar(select(Competitor).where(Competitor.name == b.name)):
        raise HTTPException(409, "competitor exists")
    if b.is_own and db.scalar(select(Competitor).where(Competitor.is_own)):
        raise HTTPException(409, "the company itself is already defined")
    c = Competitor(**b.model_dump(), origin="manual", as_of=_today())
    db.add(c)
    db.commit()
    return _row(c)


@router.post("/competitors/{cid}/scores", status_code=201)
def add_score(cid: int, b: ScoreIn, db: Session = Depends(get_db), _: User = Depends(_write)):
    if not db.get(Competitor, cid):
        raise HTTPException(404, "competitor not found")
    if b.criterion not in ins.CRITERIA:
        raise HTTPException(422, f"criterion must be one of {list(ins.CRITERIA)}")
    s = CompetitorScore(competitor_id=cid, **b.model_dump())
    db.add(s)
    db.commit()
    return _row(s)


@router.post("/competitors/seed-public")
def seed_public(db: Session = Depends(get_db), _: User = Depends(require())):
    """Load competitor CANDIDATES from public press (names, notes, sources). No scores: those need evidence from the factory."""
    added = 0
    for name, kind, notes, url in PUBLIC_COMPETITORS:
        if not db.scalar(select(Competitor).where(Competitor.name == name)):
            db.add(Competitor(name=name, kind=kind, origin="public", notes=notes, source_url=url))
            added += 1
    db.commit()
    return {"added": added}


def _latest_scores(db: Session, cid: int) -> tuple[dict[str, float], dict[str, int]]:
    best: dict[str, CompetitorScore] = {}
    for s in db.scalars(select(CompetitorScore).where(CompetitorScore.competitor_id == cid).order_by(CompetitorScore.as_of)):
        best[s.criterion] = s
    today = _today()
    return {k: v.score for k, v in best.items()}, {k: (today - v.as_of).days for k, v in best.items()}


@router.get("/competitive")
def competitive(segment: str = "domestic", db: Session = Depends(get_db), _: User = Depends(current_user)):
    if segment not in ("domestic", "export"):
        raise HTTPException(422, "segment must be domestic or export")
    weights = {**ins.WEIGHTS, **_setting(db, "weights", {})}[segment]
    own = db.scalar(select(Competitor).where(Competitor.is_own))
    own_sc = _latest_scores(db, own.id)[0] if own else {}
    rivals, ages = {}, {}
    for c in db.scalars(select(Competitor).where(Competitor.is_own.is_(False))):
        # domestic benchmarking compares with domestic rivals; export with all (international rivals matter abroad)
        if segment == "domestic" and c.kind != "domestic":
            continue
        sc, age = _latest_scores(db, c.id)
        if sc:
            rivals[c.name], ages[c.name] = sc, age
    res = ins.competitive_gap(own_sc, rivals, weights, ages)
    res["segment"] = segment
    res["own_defined"] = own is not None
    if own is None:
        res["warnings"].insert(0, "شرکت خودتان را به‌عنوان «خود شرکت» در فهرست رقبا تعریف و امتیازدهی کنید")
    if not rivals:
        res["warnings"].insert(0, "هیچ رقیبی امتیاز ندارد: مقایسه‌ی بدون مستندات ممکن نیست")
    return res


# ------------------------------------------------------------------------------------------------ export readiness
REFERENCE_CERTS = [  # (name, scope, market, note). Typical requirements; applicability depends on the product - confirm with the buyer
    ("ISO 9001 (مدیریت کیفیت)", "general", "همه بازارها", "پایه‌ی اغلب مناقصه‌ها و مشتریان صنعتی"),
    ("IATF 16949 (کیفیت خودرویی)", "automotive", "خودروسازان/OEM", "برای تأمین قطعه‌ی خودرو؛ هسته‌ی آن کنترل فرایند و ردیابی است"),
    ("ISO 14001 (مدیریت محیط‌زیست)", "general", "اروپا و مشتریان بزرگ", ""),
    ("ISO 45001 (ایمنی و بهداشت شغلی)", "general", "مشتریان بزرگ", ""),
    ("REACH (مواد شیمیایی)", "eu", "اتحادیه‌ی اروپا", "مواد اولیه/روغن‌های افزودنی لاستیک باید با محدودیت‌های اروپا سازگار باشند"),
    ("UN ECE R30 / R54 (تایر)", "tires", "اروپا و کشورهای عضو ECE", "فقط اگر محصول تایر است؛ علامت E-mark"),
    ("برچسب تایر اتحادیه‌ی اروپا (EU 2020/740)", "tires", "اتحادیه‌ی اروپا", "فقط تایر"),
    ("DOT / FMVSS 109 (تایر)", "tires", "ایالات متحده", "فقط تایر؛ اجرای تحریم‌ها را جدا بررسی کنید"),
]


class CertIn(BaseModel):
    name: str
    scope: str = "general"
    status: str = Field(default="missing", pattern="^(have|in_progress|missing|na)$")
    target_market: str | None = None
    note: str | None = None


class CertPatch(BaseModel):
    status: str = Field(pattern="^(have|in_progress|missing|na)$")
    note: str | None = None


@router.get("/export-readiness")
def export_readiness(db: Session = Depends(get_db), _: User = Depends(current_user)):
    certs = [_row(c) for c in db.scalars(select(Certification).order_by(Certification.scope, Certification.name))]
    counts = defaultdict(int)
    for c in certs:
        counts[c["status"]] += 1
    applicable = [c for c in certs if c["status"] != "na"]
    return {"certifications": certs, "counts": dict(counts),
            "readiness": None if not applicable else round(sum(c["status"] == "have" for c in applicable) / len(applicable), 3),
            "note": "ماتریس مرجع فقط راهنماست؛ الزامات هر بازار/محصول را با خریدار و مرجع رسمی تأیید کنید"}


@router.post("/certifications", status_code=201)
def add_cert(b: CertIn, db: Session = Depends(get_db), _: User = Depends(_write)):
    if db.scalar(select(Certification).where(Certification.name == b.name)):
        raise HTTPException(409, "certification exists")
    c = Certification(**b.model_dump(), as_of=_today())
    db.add(c)
    db.commit()
    return _row(c)


@router.patch("/certifications/{cid}")
def patch_cert(cid: int, b: CertPatch, db: Session = Depends(get_db), _: User = Depends(_write)):
    c = db.get(Certification, cid)
    if not c:
        raise HTTPException(404, "not found")
    c.status, c.as_of = b.status, _today()
    if b.note is not None:
        c.note = b.note
    db.commit()
    return _row(c)


@router.post("/certifications/seed-reference")
def seed_reference_certs(db: Session = Depends(get_db), _: User = Depends(_write)):
    added = 0
    for name, scope, market, note in REFERENCE_CERTS:
        if not db.scalar(select(Certification).where(Certification.name == name)):
            db.add(Certification(name=name, scope=scope, status="missing", target_market=market, note=note))
            added += 1
    db.commit()
    return {"added": added}


# ------------------------------------------------------------------------------------------------ demo data
@router.post("/demo-seed")
def demo_seed(db: Session = Depends(get_db), _: User = Depends(require())):
    """Flagged DEMO data: invented customers/products and a synthetic 36-month sales history (seasonality, trend, noise)."""
    if db.scalar(select(func.count()).select_from(Product).where(Product.is_demo)):
        raise HTTPException(409, "market demo data already loaded")
    rng = random.Random(7)
    mats = {m.code: m for m in db.scalars(select(Material))}
    for code, fa in (("RM-SBR", "کائوچوی مصنوعی SBR"), ("RM-CB", "دوده صنعتی"), ("RM-CURE", "مواد پخت")):
        if code not in mats:
            m = Material(code=code, name_fa=fa, category="rubber", reorder_point=1000, safety_stock=500, lead_time_days=45, is_demo=True)
            db.add(m)
            db.flush()
            db.add(StockMovement(material_id=m.id, qty=60000, kind="receipt", ref="DEMO-market", is_demo=True))
            mats[code] = m
    prods = []
    for code, fa, price, rate in (("DEMO-A", "محصول نمونه A (واشر)", 90_000, 30.0), ("DEMO-B", "محصول نمونه B (اورینگ)", 45_000, 40.0)):
        p = Product(code=code, name_fa=fa, unit_price=price, rate_per_min=rate, fpy_default=0.96, is_demo=True)
        db.add(p)
        db.flush()
        for mc, q in (("RM-SBR", 0.18), ("RM-CB", 0.09), ("RM-CURE", 0.012)):
            db.add(BomItem(product_id=p.id, material_id=mats[mc].id, qty_per_unit=q))
        db.add(FinishedStock(product_id=p.id, qty=20000, as_of=_today()))
        prods.append(p)
    custs = []
    for name, kind in (("مشتری داخلی نمونه الف", "domestic"), ("مشتری داخلی نمونه ب", "domestic"), ("مشتری داخلی نمونه ج (رو به افول)", "domestic"),
                       ("مشتری صادراتی نمونه ۱", "international"), ("مشتری صادراتی نمونه ۲ (جدید)", "international"),
                       ("مشتری فصلی نمونه", "domestic")):
        c = db.scalar(select(Customer).where(Customer.name == name)) or Customer(name=name, kind=kind, products="نمونه", is_demo=True)
        db.add(c)
        db.flush()
        custs.append(c)
    first = add_months(month_floor(_today()), -36)
    for ci, c in enumerate(custs):
        for pi, p in enumerate(prods):
            base = 33 * (9000, 6000, 5000, 3000, 1500, 2500)[ci] * (1.0 if pi == 0 else 1.4)  # sized to a 4-line plant
            for k in range(36):
                if ci == 4 and k < 26:  # new customer: only the last 10 months
                    continue
                if ci == 5 and k % 3 != 2:  # quarterly buyer
                    continue
                trend = 1 + (0.012 * k if ci in (0, 1, 3) else -0.025 * max(0, k - 20) if ci == 2 else 0.0)
                season = 1 + 0.22 * math.sin(2 * math.pi * (k % 12) / 12 + ci)
                qty = max(0.0, base * trend * season * rng.uniform(0.9, 1.1))
                if ci == 2 and k >= 33:
                    qty = 0.0
                db.add(SalesRecord(customer_id=c.id, product_id=p.id, period=add_months(first, k), qty=round(qty),
                                   revenue=round(qty * (p.unit_price or 0)), is_demo=True))
    db.commit()
    return {"ok": True, "products": len(prods), "customers": len(custs)}


@router.delete("/demo-seed")
def demo_clear(db: Session = Depends(get_db), _: User = Depends(require())):
    pids = [p.id for p in db.scalars(select(Product).where(Product.is_demo))]
    db.execute(delete(SalesRecord).where(SalesRecord.is_demo))
    db.execute(delete(BomItem).where(BomItem.product_id.in_(pids)))
    db.execute(delete(FinishedStock).where(FinishedStock.product_id.in_(pids)))
    db.execute(delete(Product).where(Product.is_demo))
    db.commit()
    return {"ok": True}
