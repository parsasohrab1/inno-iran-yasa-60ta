from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import Defect, Inspection, Status, User
from ..schemas import KPIOut

router = APIRouter(prefix="/api/kpi", tags=["kpi"])


@router.get("", response_model=KPIOut)  # FR-3-6
def kpi(line_id: int | None = None, _: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Inspection.status, func.count()).group_by(Inspection.status)
    if line_id:
        q = q.where(Inspection.line_id == line_id)
    counts = {s: n for s, n in db.execute(q)}
    ok, ng, review = (counts.get(s, 0) for s in (Status.OK, Status.NG, Status.REVIEW))
    total = ok + ng + review

    dq = (
        select(Defect.class_name, func.count().label("n"))
        .group_by(Defect.class_name)
        .order_by(func.count().desc())
        .limit(5)
    )
    top = [{"class_name": c, "count": n} for c, n in db.execute(dq)]
    return KPIOut(
        total=total, ok=ok, ng=ng, review=review,
        defect_rate=ng / total if total else 0.0,
        fpy=ok / total if total else 0.0,
        top_defects=top,
    )
