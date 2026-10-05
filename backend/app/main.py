from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from .auth import decode_token, seed_admin
from .db import Base, SessionLocal, engine
from .routers import analytics, auth, images, inspections, kpi, reports
from .services.ws import hub


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)  # TODO: replace with Alembic migrations
    with SessionLocal() as db:
        seed_admin(db)
    yield


app = FastAPI(title="Iran Yasa Rubber Inspection API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"]
)
for r in (auth, inspections, kpi, analytics, reports, images):
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
