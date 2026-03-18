from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import routes
from backend.database import ensure_schema
from backend.session_manager import SessionManager

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {"idle_threshold_seconds": 300}


config = load_config()
session_manager = SessionManager(
    idle_threshold_seconds=int(config.get("idle_threshold_seconds", 300))
)
routes.session_manager = session_manager

app = FastAPI(title="Avala Tracker Pro", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)


@app.on_event("startup")
def on_startup() -> None:
    ensure_schema()
    session_manager.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    session_manager.stop()
