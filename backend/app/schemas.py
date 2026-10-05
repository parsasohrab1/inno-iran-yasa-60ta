from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import Severity, Status


class DefectIn(BaseModel):
    class_id: int = Field(ge=1, le=7)
    class_name: str
    confidence: float = Field(ge=0, le=1)
    severity_score: float = Field(ge=0, le=1)
    bbox: list[float] = Field(min_length=4, max_length=4)


class InspectionIn(BaseModel):
    part_id: str
    line_id: int = Field(ge=1, le=4)  # NFR-6: up to 4 lines
    shift: str
    latency_ms: float | None = None
    image_key: str | None = None
    defects: list[DefectIn] = []


class DefectOut(DefectIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    severity: Severity


class InspectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    part_id: str
    line_id: int
    shift: str
    created_at: datetime
    status: Status
    latency_ms: float | None
    image_key: str | None
    defects: list[DefectOut]


class KPIOut(BaseModel):
    total: int
    ok: int
    ng: int
    review: int
    defect_rate: float
    fpy: float  # first pass yield
    top_defects: list[dict]
