import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import select

from .auth import decode_token, seed_admin
from .config import settings
from .db import Base, SessionLocal, engine
from .models import DowntimeEvent, Inspection, MonitorBeat, PlantEvent, Status
from .routers import analytics, auth, business, images, inspections, kpi, market, plant, reports, training
from .services.ws import hub


def _audit_to_db(ev: dict):
    from datetime import datetime, timezone

    with SessionLocal() as db:
        db.add(PlantEvent(ts=datetime.fromtimestamp(ev["ts"], timezone.utc), kind=ev["kind"], line=ev["line"],
                          severity=ev["severity"], summary=ev["summary"], payload=ev["payload"]))
        db.commit()


def _downtime_to_db(kind: str, st):
    """Persist a stop: open -> insert (returns its id), update/close -> reason and end time."""
    from datetime import datetime, timezone

    ts = lambda x: None if x is None else datetime.fromtimestamp(x, timezone.utc)  # noqa: E731
    with SessionLocal() as db:
        if kind == "open":
            r = DowntimeEvent(line=st.line, start=ts(st.start), reason_code=st.reason, reason_source=st.source,
                              note=st.note, set_by=st.set_by)
            db.add(r)
            db.commit()
            return r.id
        r = db.get(DowntimeEvent, st.db_id) if st.db_id else None
        if r:
            r.reason_code, r.reason_source, r.note, r.set_by, r.end = st.reason, st.source, st.note, st.set_by, ts(st.end)
            db.commit()


def _beat_to_db(ts: float):
    from datetime import datetime, timezone

    with SessionLocal() as db:
        db.add(MonitorBeat(ts=datetime.fromtimestamp(ts, timezone.utc)))
        db.commit()


def _quality_from_db(line: int):
    """Defect rate over the last 100 inspections of a line: (n, NG share)."""
    with SessionLocal() as db:
        rows = list(db.scalars(select(Inspection.status).where(Inspection.line_id == line)
                                .order_by(Inspection.created_at.desc()).limit(100)))
    return (len(rows), sum(r == Status.NG for r in rows) / len(rows)) if rows else None


def build_plant_engine():
    import os

    from .plant.catalog import build_plant
    from .plant.engine import PlantEngine
    from .plant.notify import LogChannel, WebhookChannel, load_contacts
    from .plant.shifts import ShiftSchedule

    schedule = ShiftSchedule.from_json(settings.plant_shifts_path) if os.path.exists(settings.plant_shifts_path) else None
    channels = [LogChannel(), WebhookChannel()]
    if settings.sms_provider:
        from .plant.sms import HttpSmsChannel

        channels.append(HttpSmsChannel(settings.sms_provider, sender=settings.sms_sender or None,
                                       min_severity=settings.sms_min_severity, url=settings.sms_generic_url or None))
    equipment = build_plant(settings.plant_lines)
    source = None
    if settings.plant_source == "opcua":
        from .plant.opcua import OpcUaMap, OpcUaPlant

        cfg = OpcUaMap.from_json(settings.plant_opcua_map_path)
        problems = cfg.validate(equipment)
        if problems:  # a wrong mapping must stop the start, not produce plausible-looking wrong data
            raise RuntimeError("invalid OPC-UA mapping: " + "; ".join(problems[:5]))
        source = OpcUaPlant(equipment, cfg)
    return PlantEngine(lines=settings.plant_lines, audit=_audit_to_db, quality=_quality_from_db,
                       downtime_sink=_downtime_to_db, beat_sink=_beat_to_db, equipment=equipment, source=source, channels=channels,
                       contacts=load_contacts(settings.plant_contacts_path, settings.plant_lines), schedule=schedule)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # TODO: replace with Alembic migrations
    with SessionLocal() as db:
        seed_admin(db)
    task = None
    if settings.plant_sim_enabled and getattr(app.state, "plant", None) is None:
        app.state.plant = build_plant_engine()
    if getattr(app.state, "plant", None) is not None and settings.plant_sim_enabled:
        task = asyncio.create_task(app.state.plant.run(settings.plant_sim_tick_s))
    yield
    if task:
        task.cancel()


app = FastAPI(title="Iran Yasa Rubber Inspection API", version="0.3.0", lifespan=lifespan)
app.state.plant = None
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"]
)
for r in (auth, inspections, kpi, analytics, reports, images, training, plant, business, market):
    app.include_router(r.router)
Instrumentator().instrument(app).expose(app)  # NFR-7: /metrics


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws/inspections")
async def ws_inspections(ws: WebSocket, token: str = ""):
    try:
        decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)


@app.websocket("/ws/plant")
async def ws_plant(ws: WebSocket, token: str = ""):
    try:
        decode_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    eng = app.state.plant
    if eng is None:
        await ws.close(code=1013)
        return
    await ws.accept()
    eng._clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        eng._clients.discard(ws)
