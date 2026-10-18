"""Procurement, warehouse and customers.

DATA POLICY: nothing here is real plant data until the factory enters it (or an ERP/exchange feed is attached). The demo
seed is explicitly flagged (is_demo / source="demo") and contains no real company names or real prices.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..auth import current_user, require
from ..db import get_db
from ..models import Customer, Material, PriceQuote, PurchaseOrder, Role, StockMovement, Supplier, User

router = APIRouter(prefix="/api/business", tags=["business"])
_buyer = require(Role.MANAGER)  # procurement / production manager; admin always allowed


class SupplierIn(BaseModel):
    name: str
    kind: str = Field(default="domestic", pattern="^(domestic|international)$")
    country: str | None = None
    note: str | None = None


class MaterialIn(BaseModel):
    code: str
    name_fa: str
    name_en: str | None = None
    category: str = "rubber"
    unit: str = "kg"
    reorder_point: float = 0
    safety_stock: float = 0
    lead_time_days: int = 30
    supplier_id: int | None = None


class MovementIn(BaseModel):
    material_id: int
    qty: float
    kind: str = Field(pattern="^(receipt|issue|adjust)$")
    ref: str | None = None


class PriceIn(BaseModel):
    material_id: int
    price: float = Field(gt=0)
    currency: str = "IRR"
    per_unit: str = "kg"
    source: str = Field(default="manual", pattern="^(ime|supplier|market|manual)$")  # "demo" is reserved for the seed
    as_of: date
    note: str | None = None


class CustomerIn(BaseModel):
    name: str
    kind: str = Field(default="domestic", pattern="^(domestic|international)$")
    country: str | None = None
    products: str | None = None


class OrderIn(BaseModel):
    material_id: int
    supplier_id: int | None = None
    qty: float = Field(gt=0)
    price: float | None = None
    currency: str = "IRR"
    eta: date | None = None


def _row(o) -> dict:
    return {c.name: getattr(o, c.name) for c in o.__table__.columns}


@router.get("/suppliers")
def suppliers(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [_row(r) for r in db.scalars(select(Supplier).order_by(Supplier.name))]


@router.post("/suppliers", status_code=201)
def add_supplier(b: SupplierIn, db: Session = Depends(get_db), _: User = Depends(_buyer)):
    s = Supplier(**b.model_dump())
    db.add(s)
    db.commit()
    return _row(s)


@router.get("/materials")
def materials(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [_row(r) for r in db.scalars(select(Material).order_by(Material.code))]


@router.post("/materials", status_code=201)
def add_material(b: MaterialIn, db: Session = Depends(get_db), _: User = Depends(_buyer)):
    if db.scalar(select(Material).where(Material.code == b.code)):
        raise HTTPException(409, "material code exists")
    m = Material(**b.model_dump())
    db.add(m)
    db.commit()
    return _row(m)


@router.post("/stock", status_code=201)
def add_movement(b: MovementIn, db: Session = Depends(get_db), _: User = Depends(_buyer)):
    if not db.get(Material, b.material_id):
        raise HTTPException(404, "material not found")
    qty = abs(b.qty) if b.kind == "receipt" else -abs(b.qty) if b.kind == "issue" else b.qty
    mv = StockMovement(material_id=b.material_id, qty=qty, kind=b.kind, ref=b.ref)
    db.add(mv)
    db.commit()
    return _row(mv)


@router.get("/inventory")
def inventory(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """On-hand, recent consumption, days of cover and a reorder status per material."""
    since = datetime.now(timezone.utc) - timedelta(days=14)
    on_hand = {mid: tot for mid, tot in db.execute(
        select(StockMovement.material_id, func.sum(StockMovement.qty)).group_by(StockMovement.material_id))}
    used = {mid: tot for mid, tot in db.execute(
        select(StockMovement.material_id, func.sum(-StockMovement.qty))
        .where(StockMovement.kind == "issue", StockMovement.ts >= since).group_by(StockMovement.material_id))}
    last_price = {}
    for q in db.scalars(select(PriceQuote).order_by(PriceQuote.as_of)):
        last_price[q.material_id] = q
    out = []
    for m in db.scalars(select(Material).order_by(Material.code)):
        oh = float(on_hand.get(m.id) or 0.0)
        daily = float(used.get(m.id) or 0.0) / 14.0
        cover = None if daily <= 0 else round(oh / daily, 1)
        if oh <= m.safety_stock:
            status = "critical"
        elif oh <= m.reorder_point or (cover is not None and cover <= m.lead_time_days):
            status = "reorder"
        else:
            status = "ok"
        p = last_price.get(m.id)
        out.append({**_row(m), "on_hand": round(oh, 1), "daily_use": round(daily, 1), "days_of_cover": cover,
                    "status": status,
                    "last_price": None if not p else {"price": p.price, "currency": p.currency, "source": p.source,
                                                      "as_of": p.as_of.isoformat(), "note": p.note}})
    return out


@router.get("/prices/{material_id}")
def price_history(material_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    q = select(PriceQuote).where(PriceQuote.material_id == material_id).order_by(PriceQuote.as_of)
    return [_row(r) for r in db.scalars(q)]


@router.post("/prices", status_code=201)
def add_price(b: PriceIn, db: Session = Depends(get_db), _: User = Depends(_buyer)):
    if not db.get(Material, b.material_id):
        raise HTTPException(404, "material not found")
    p = PriceQuote(**b.model_dump())
    db.add(p)
    db.commit()
    return _row(p)


@router.get("/customers")
def customers(kind: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    q = select(Customer).order_by(Customer.kind, Customer.name)
    if kind:
        q = q.where(Customer.kind == kind)
    return [_row(r) for r in db.scalars(q)]


@router.post("/customers", status_code=201)
def add_customer(b: CustomerIn, db: Session = Depends(get_db), _: User = Depends(_buyer)):
    c = Customer(**b.model_dump())
    db.add(c)
    db.commit()
    return _row(c)


@router.get("/orders")
def orders(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [_row(r) for r in db.scalars(select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()))]


@router.post("/orders", status_code=201)
def add_order(b: OrderIn, db: Session = Depends(get_db), _: User = Depends(_buyer)):
    o = PurchaseOrder(**b.model_dump())
    db.add(o)
    db.commit()
    return _row(o)


@router.post("/demo-seed")
def demo_seed(db: Session = Depends(get_db), _: User = Depends(require())):
    """Load clearly-flagged DEMO rows (no real companies, no real prices) so the screens can be reviewed."""
    if db.scalar(select(func.count()).select_from(Material).where(Material.is_demo)):
        raise HTTPException(409, "demo data already loaded")
    items = [  # raw materials named in the public annual-report summary; quantities are invented
        ("RM-SBR", "کائوچوی مصنوعی SBR", "SBR synthetic rubber", "rubber", 20000, 8000, 45, 52000, 600),
        ("RM-NR", "کائوچوی طبیعی", "Natural rubber", "rubber", 12000, 5000, 60, 14000, 420),
        ("RM-BR", "پلی‌بوتادین BR", "Polybutadiene rubber", "rubber", 8000, 3000, 45, 7000, 250),
        ("RM-IIR", "بوتیل‌رابر", "Butyl rubber", "rubber", 5000, 2000, 60, 3000, 120),
        ("RM-CB", "دوده صنعتی", "Carbon black", "filler", 25000, 10000, 30, 60000, 900),
        ("RM-CORD", "کورد و نخ تقویتی", "Reinforcing cord / yarn", "reinforcement", 3000, 1000, 75, 2500, 80),
        ("RM-WIRE", "سیم فولادی", "Steel wire", "reinforcement", 4000, 1500, 75, 9000, 150),
        ("RM-CURE", "مواد پخت (گوگرد/شتاب‌دهنده)", "Curing chemicals", "chemical", 2000, 800, 30, 1200, 60),
    ]
    for code, fa, en, cat, rp, ss, lt, stock, daily in items:
        m = Material(code=code, name_fa=fa, name_en=en, category=cat, reorder_point=rp, safety_stock=ss,
                     lead_time_days=lt, is_demo=True)
        db.add(m)
        db.flush()
        db.add(StockMovement(material_id=m.id, qty=stock, kind="receipt", ref="DEMO-opening", is_demo=True))
        for d in range(1, 15):
            db.add(StockMovement(material_id=m.id, qty=-daily * (0.9 + 0.02 * (d % 10)), kind="issue", ref="DEMO",
                                 ts=datetime.now(timezone.utc) - timedelta(days=d), is_demo=True))
        db.add(PriceQuote(material_id=m.id, price=0.0001, currency="IRR", source="demo", as_of=date.today(),
                          note="نمونه – قیمت واقعی نیست؛ با نرخ بورس کالا/تأمین‌کننده جایگزین شود"))
    for name, kind, country in [("مشتری داخلی نمونه ۱", "domestic", "ایران"), ("مشتری داخلی نمونه ۲", "domestic", "ایران"),
                                ("مشتری صادراتی نمونه ۱", "international", None), ("مشتری صادراتی نمونه ۲", "international", None)]:
        db.add(Customer(name=name, kind=kind, country=country, products="نمونه", is_demo=True))
    db.commit()
    return {"ok": True, "materials": len(items), "customers": 4}


@router.delete("/demo-seed")
def demo_clear(db: Session = Depends(get_db), _: User = Depends(require())):
    demo_ids = [m.id for m in db.scalars(select(Material).where(Material.is_demo))]
    db.execute(delete(PriceQuote).where(PriceQuote.material_id.in_(demo_ids)))
    db.execute(delete(StockMovement).where(StockMovement.is_demo))
    db.execute(delete(Material).where(Material.is_demo))
    db.execute(delete(Customer).where(Customer.is_demo))
    db.commit()
    return {"ok": True}
