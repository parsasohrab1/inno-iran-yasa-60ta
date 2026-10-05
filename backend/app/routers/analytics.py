from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import require
from ..db import get_db
from ..models import Role
from ..services import analytics

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
_view = require(Role.QC, Role.MANAGER, Role.MAINTENANCE)


@router.get("/trend")
def trend(
    period: Literal["day", "week", "month"] = "day",
    days: int = Query(30, ge=1, le=365),
    line_id: int | None = None,
    _=Depends(_view),
    db: Session = Depends(get_db),
):
    return analytics.trend(db, period, days, line_id)


@router.get("/pareto")
def pareto(days: int = Query(30, ge=1, le=365), line_id: int | None = None,
           _=Depends(_view), db: Session = Depends(get_db)):
    return analytics.pareto(db, days, line_id)


@router.get("/spc")
def spc(days: int = Query(30, ge=1, le=365), line_id: int | None = None,
        _=Depends(_view), db: Session = Depends(get_db)):
    return analytics.spc_p_chart(db, days, line_id)
