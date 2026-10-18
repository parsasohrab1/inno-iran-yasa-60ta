import enum
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now():
    return datetime.now(timezone.utc)


class Status(str, enum.Enum):
    OK = "OK"
    NG = "NG"
    REVIEW = "Review"


class Severity(str, enum.Enum):
    LOW = "Low"
    MEDIUM = "Med"
    HIGH = "High"


class Role(str, enum.Enum):  # SRS 2-2
    OPERATOR = "operator"
    QC = "qc"
    MANAGER = "manager"
    MAINTENANCE = "maintenance"
    ADMIN = "admin"


class User(Base):  # FR-3-8
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[Role] = mapped_column(Enum(Role))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Inspection(Base):  # FR-3-1
    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(primary_key=True)
    part_id: Mapped[str] = mapped_column(String(64), index=True)
    line_id: Mapped[int] = mapped_column(Integer, index=True)
    shift: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    status: Mapped[Status] = mapped_column(Enum(Status))
    latency_ms: Mapped[float | None] = mapped_column(Float)
    image_key: Mapped[str | None] = mapped_column(String(256))  # object key in MinIO/S3
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(512))
    defects: Mapped[list["Defect"]] = relationship(back_populates="inspection", cascade="all, delete-orphan")


class Defect(Base):  # FR-2-4
    __tablename__ = "defects"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)
    class_id: Mapped[int] = mapped_column(Integer)
    class_name: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    severity_score: Mapped[float] = mapped_column(Float)  # 0-1
    severity: Mapped[Severity] = mapped_column(Enum(Severity))
    bbox: Mapped[list] = mapped_column(JSON)  # [x1, y1, x2, y2]
    confirmed: Mapped[bool | None] = mapped_column(Boolean)  # human label, feeds continuous learning (FR-2-5)
    inspection: Mapped[Inspection] = relationship(back_populates="defects")


class PlantEvent(Base):
    """Audit trail: alarms, acknowledgements, notifications, autopilot actions and mode changes."""
    __tablename__ = "plant_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    line: Mapped[int | None] = mapped_column(Integer, index=True)
    severity: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="domestic")  # domestic | international
    country: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(256))


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name_fa: Mapped[str] = mapped_column(String(128))
    name_en: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), default="rubber")  # rubber | filler | chemical | reinforcement | other
    unit: Mapped[str] = mapped_column(String(16), default="kg")
    reorder_point: Mapped[float] = mapped_column(Float, default=0.0)
    safety_stock: Mapped[float] = mapped_column(Float, default=0.0)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=30)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    qty: Mapped[float] = mapped_column(Float)  # + receipt / - issue
    kind: Mapped[str] = mapped_column(String(16))  # receipt | issue | adjust
    ref: Mapped[str | None] = mapped_column(String(64))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class PriceQuote(Base):
    __tablename__ = "price_quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    per_unit: Mapped[str] = mapped_column(String(16), default="kg")
    source: Mapped[str] = mapped_column(String(16), default="manual")  # ime | supplier | market | manual | demo
    as_of: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(256))


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | ordered | received | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    eta: Mapped[date | None] = mapped_column(Date)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="domestic")  # domestic | international
    country: Mapped[str | None] = mapped_column(String(64))
    products: Mapped[str | None] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class DowntimeEvent(Base):
    """A period during which a line was stopped, with its reason (for the downtime Pareto and OEE availability)."""
    __tablename__ = "downtime_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    line: Mapped[int] = mapped_column(Integer, index=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason_code: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    reason_source: Mapped[str] = mapped_column(String(16), default="auto")  # auto | manual
    note: Mapped[str | None] = mapped_column(String(256))
    set_by: Mapped[str | None] = mapped_column(String(64))


class CorrectiveAction(Base):
    """What was done about an alarm: cause + action, with closure. Self-contained (alarm ids restart with the process)."""
    __tablename__ = "corrective_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    alarm_id: Mapped[int] = mapped_column(Integer)
    sensor_key: Mapped[str] = mapped_column(String(96), index=True)
    raised_at: Mapped[float] = mapped_column(Float)  # engine clock of the alarm; with sensor_key identifies it
    line: Mapped[int] = mapped_column(Integer, index=True)
    severity: Mapped[str] = mapped_column(String(16))
    alarm_message: Mapped[str] = mapped_column(Text)
    cause_code: Mapped[str] = mapped_column(String(32))
    action_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | done
    created_by: Mapped[str] = mapped_column(String(64))
    closed_by: Mapped[str | None] = mapped_column(String(64))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------------------------- market development
class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name_fa: Mapped[str] = mapped_column(String(128))
    name_en: Mapped[str | None] = mapped_column(String(128))
    unit_cost: Mapped[float | None] = mapped_column(Float)  # cost of one unit (for the cost of poor quality); optional
    unit_price: Mapped[float | None] = mapped_column(Float)
    rate_per_min: Mapped[float] = mapped_column(Float, default=30.0)  # parts/min of one line at nominal speed
    fpy_default: Mapped[float] = mapped_column(Float, default=0.97)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class BomItem(Base):
    __tablename__ = "bom_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    qty_per_unit: Mapped[float] = mapped_column(Float)  # material units (e.g. kg) per product unit


class SalesRecord(Base):
    __tablename__ = "sales_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    period: Mapped[date] = mapped_column(Date, index=True)  # first day of the month
    qty: Mapped[float] = mapped_column(Float)
    revenue: Mapped[float | None] = mapped_column(Float)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class FinishedStock(Base):
    __tablename__ = "finished_stock"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), unique=True)
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    as_of: Mapped[date] = mapped_column(Date)


class Competitor(Base):
    __tablename__ = "competitors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="domestic")  # domestic | international
    is_own: Mapped[bool] = mapped_column(Boolean, default=False)  # the company itself, scored on the same criteria
    origin: Mapped[str] = mapped_column(String(16), default="manual")  # public | manual | demo
    notes: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(300))
    as_of: Mapped[date | None] = mapped_column(Date)


class CompetitorScore(Base):
    __tablename__ = "competitor_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"), index=True)
    criterion: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float] = mapped_column(Float)  # 1 (weak) .. 5 (excellent)
    evidence: Mapped[str] = mapped_column(String(300))  # why this score: test result, price list, audit ...
    as_of: Mapped[date] = mapped_column(Date)


class Certification(Base):
    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    scope: Mapped[str] = mapped_column(String(32), default="general")  # general | tires | automotive | eu | usa
    status: Mapped[str] = mapped_column(String(16), default="missing")  # have | in_progress | missing | na
    target_market: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(300))
    as_of: Mapped[date | None] = mapped_column(Date)


class MarketSetting(Base):
    __tablename__ = "market_settings"

    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class MonitorBeat(Base):
    """One row per minute while the plant monitoring loop is running: the basis for 'observed time' in OEE."""
    __tablename__ = "monitor_beats"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
