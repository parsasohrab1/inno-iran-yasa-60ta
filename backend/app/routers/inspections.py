from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, require
from ..db import get_db
from ..models import Defect, Inspection, Role, Status, User
from ..schemas import InspectionIn, InspectionOut, ReviewIn
from ..services.decision import decide, severity_level
from ..services.ws import hub

router = APIRouter(prefix="/api/inspections", tags=["inspections"])


@router.post("", response_model=InspectionOut, status_code=201)
async def create_inspection(
    body: InspectionIn, _: User = Depends(current_user), db: Session = Depends(get_db)
):
    insp = Inspection(
        **body.model_dump(exclude={"defects"}),
        status=decide(body.defects),
        defects=[
            Defect(**d.model_dump(), severity=severity_level(d.severity_score))
            for d in body.defects
        ],
    )
    db.add(insp)
    db.commit()
    db.refresh(insp)
    out = InspectionOut.model_validate(insp)
    await hub.broadcast(out.model_dump(mode="json"))
    return out


@router.get("", response_model=list[InspectionOut])
def list_inspections(
    line_id: int | None = None,
    status: Status | None = None,
    limit: int = 50,
    offset: int = 0,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    q = select(Inspection).order_by(Inspection.created_at.desc()).limit(min(limit, 500)).offset(offset)
    if line_id:
        q = q.where(Inspection.line_id == line_id)
    if status:
        q = q.where(Inspection.status == status)
    return db.scalars(q).all()


@router.get("/{inspection_id}", response_model=InspectionOut)
def get_inspection(inspection_id: int, _: User = Depends(current_user), db: Session = Depends(get_db)):
    insp = db.get(Inspection, inspection_id)
    if not insp:
        raise HTTPException(404, "Inspection not found")
    return insp


@router.patch("/{inspection_id}/review", response_model=InspectionOut)
async def review_inspection(
    inspection_id: int,
    body: ReviewIn,
    user: User = Depends(require(Role.QC)),
    db: Session = Depends(get_db),
):
    """QC verdict on an inspection; confirmed/rejected defects become training labels (FR-2-5)."""
    insp = db.get(Inspection, inspection_id)
    if not insp:
        raise HTTPException(404, "Inspection not found")
    if body.status == Status.REVIEW:
        raise HTTPException(422, "Final verdict must be OK or NG")
    insp.status = body.status
    insp.review_note = body.note
    insp.reviewed_by = user.username
    insp.reviewed_at = datetime.now(timezone.utc)
    if body.confirmed_defect_ids is not None:
        for d in insp.defects:
            d.confirmed = d.id in body.confirmed_defect_ids
    db.commit()
    db.refresh(insp)
    out = InspectionOut.model_validate(insp)
    await hub.broadcast(out.model_dump(mode="json"))
    return out
