"""Report export for MES/ERP and managers (FR-3-7)."""
import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require
from ..db import get_db
from ..models import Inspection, Role

router = APIRouter(prefix="/api/reports", tags=["reports"])
_HEADER = ["id", "part_id", "line_id", "shift", "created_at", "status", "defect_count", "defects", "reviewed_by"]


def _rows(db: Session, line_id: int | None, limit: int):
    q = select(Inspection).order_by(Inspection.created_at.desc()).limit(limit)
    if line_id:
        q = q.where(Inspection.line_id == line_id)
    for i in db.scalars(q):
        yield [
            i.id, i.part_id, i.line_id, i.shift, i.created_at.isoformat(), i.status.value,
            len(i.defects), ";".join(d.class_name for d in i.defects), i.reviewed_by or "",
        ]


@router.get("/inspections.csv")
def csv_report(line_id: int | None = None, limit: int = Query(10000, le=100000),
               _=Depends(require(Role.QC, Role.MANAGER)), db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    w.writerows(_rows(db, line_id, limit))
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=inspections.csv"})


@router.get("/inspections.xlsx")
def xlsx_report(line_id: int | None = None, limit: int = Query(10000, le=100000),
                _=Depends(require(Role.QC, Role.MANAGER)), db: Session = Depends(get_db)):
    wb = Workbook()
    ws = wb.active
    ws.append(_HEADER)
    for row in _rows(db, line_id, limit):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=inspections.xlsx"},
    )
