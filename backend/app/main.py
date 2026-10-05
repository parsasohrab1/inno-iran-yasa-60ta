from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from .db import Base, engine
from .routers import inspections, kpi
from .services.ws import hub


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)  # TODO: replace with Alembic migrations
    yield


app = FastAPI(title="Iran Yasa Rubber Inspection API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"]
)
app.include_router(inspections.router)
app.include_router(kpi.router)
Instrumentator().instrument(app).expose(app)  # NFR-7: /metrics


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws/inspections")
async def ws_inspections(ws: WebSocket):
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)
