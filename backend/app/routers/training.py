"""Export of QC-reviewed inspections for continuous learning (FR-2-5)."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require
from ..db import get_db
from ..models import Inspection, Role
from ..schemas import ReviewedDefect, ReviewedSample

router = APIRouter(prefix="/api/training", tags=["training"])


@router.get("/reviewed", response_model=list[ReviewedSample])
def reviewed(
    since: datetime | None = None,
    limit: int = Query(1000, ge=1, le=5000),
    _=Depends(require(Role.QC)),
    db: Session = Depends(get_db),
):
    """Inspections a human has reviewed and that still have a stored image, oldest review first."""
    q = (
        select(Inspection)
        .where(Inspection.reviewed_at.is_not(None), Inspection.image_key.is_not(None))
        .order_by(Inspection.reviewed_at)
        .limit(limit)
    )
    if since:
        q = q.where(Inspection.reviewed_at > since)
    return [
        ReviewedSample(
            inspection_id=i.id, part_id=i.part_id, line_id=i.line_id, image_key=i.image_key,
            status=i.status, reviewed_by=i.reviewed_by, reviewed_at=i.reviewed_at,
            defects=[ReviewedDefect(class_id=d.class_id, class_name=d.class_name, bbox=d.bbox,
                                    confidence=d.confidence, confirmed=d.confirmed) for d in i.defects],
        )
        for i in db.scalars(q)
    ]
