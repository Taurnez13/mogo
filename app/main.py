from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .db import init_db, MEDIA_DIR, BASE_DIR
from .web import router as web_router
from .api import router as api_router
from .auth import AuthMiddleware

app = FastAPI(title="Taurnez", docs_url="/api/docs", redoc_url=None)
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "change-me-in-production"), max_age=14 * 24 * 3600)


@app.get("/healthz")
def healthz():
    return {"ok": True}

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")
app.include_router(web_router)
app.include_router(api_router, prefix="/api")


@app.on_event("startup")
def _startup():
    init_db()
