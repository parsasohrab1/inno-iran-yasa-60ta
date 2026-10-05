import enum
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String
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
    inspection: Mapped[Inspection] = relationship(back_populates="defects")
