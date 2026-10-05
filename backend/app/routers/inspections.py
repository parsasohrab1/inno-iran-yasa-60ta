from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Defect, Inspection
from ..schemas import InspectionIn, InspectionOut
from ..services.decision import decide, severity_level
from ..services.ws import hub

router = APIRouter(prefix="/api/inspections", tags=["inspections"])


@router.post("", response_model=InspectionOut, status_code=201)
async def create_inspection(body: InspectionIn, db: Session = Depends(get_db)):
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
    line_id: int | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)
):
    q = select(Inspection).order_by(Inspection.created_at.desc()).limit(limit).offset(offset)
    if line_id:
        q = q.where(Inspection.line_id == line_id)
    return db.scalars(q).all()


@router.get("/{inspection_id}", response_model=InspectionOut)
def get_inspection(inspection_id: int, db: Session = Depends(get_db)):
    insp = db.get(Inspection, inspection_id)
    if not insp:
        raise HTTPException(404, "Inspection not found")
    return insp
