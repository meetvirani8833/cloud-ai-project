"""FastAPI application: API routers plus the background replay loop.

Run (single worker: the replay engine must not run twice):
    cd backend && ../.venv/Scripts/python -m uvicorn app.main:app --port 8000
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, auth, dashboard, recommendations
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import ReplayState
from app.services.bootstrap import seed
from app.services.engine import Engine
from app.services.runtime import Runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cloudopt")


def _replay_loop(app: FastAPI, stop: threading.Event) -> None:
    engine: Engine = app.state.engine
    while not stop.is_set():
        started = time.monotonic()
        try:
            engine.tick()
        except Exception:  # keep the loop alive; the next tick retries
            log.exception("Replay tick failed")
        with SessionLocal() as db:
            st = db.get(ReplayState, 1)
            period = st.seconds_per_slot if st else 5.0
        stop.wait(max(0.05, period - (time.monotonic() - started)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    runtime = Runtime()
    with SessionLocal() as db:
        seed(db, runtime)
    app.state.engine = Engine(runtime)
    stop = threading.Event()
    thread = None
    if settings.replay_enabled:
        thread = threading.Thread(target=_replay_loop, args=(app, stop), daemon=True, name="replay")
        thread.start()
    yield
    stop.set()
    if thread:
        thread.join(timeout=10)


app = FastAPI(title="CloudOpt API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
for r in (auth.router, dashboard.router, recommendations.router, admin.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok"}
