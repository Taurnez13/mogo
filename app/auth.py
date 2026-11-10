"""Minimal authentication: username/password users stored in the database, session cookie,
optional API key for the JSON API. No third-party auth library — PBKDF2 from the standard library."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from fastapi import Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy import select, func
from starlette.middleware.base import BaseHTTPMiddleware

from . import models as m
from .db import SessionLocal

PUBLIC_PREFIXES = ("/static/", "/media/", "/login", "/setup", "/healthz")
ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return "pbkdf2$%d$%s$%s" % (ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iters))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


def user_count() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count(m.User.id))) or 0


def api_key() -> str:
    with SessionLocal() as db:
        row = db.get(m.Setting, "api_key")
        return (row.value or "") if row else ""


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)
        if path.startswith("/api/"):
            key = api_key()
            if request.session.get("user") or not key or request.headers.get("x-api-key") == key or request.query_params.get("key") == key:
                return await call_next(request)
            return JSONResponse({"error": "API key required (X-API-Key header or ?key=)"}, status_code=401)
        if not request.session.get("user"):
            if user_count() == 0:
                return RedirectResponse("/setup", status_code=303)
            return RedirectResponse(f"/login?next={path}", status_code=303)
        return await call_next(request)
