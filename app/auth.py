"""Authentication and roles. Username/password users in the database, a session cookie, an optional
API key for the JSON API. PBKDF2 from the standard library, no third-party auth package.

Roles
  viewer  can read everything, change nothing (except their own password)
  editor  can edit titles and contributors, import, and generate or send feeds
  admin   can also manage users, settings, recipients and the Cin7/Shopify connections
"""
from __future__ import annotations

import base64
import contextvars
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
ADMIN_PREFIXES = ("/settings", "/users", "/recipients", "/sync", "/media/harvest", "/subjects/load")
SELF_SERVICE = ("/account", "/logout")
ITERATIONS = 200_000
ROLES = ("viewer", "editor", "admin")

current_user: contextvars.ContextVar[str] = contextvars.ContextVar("current_user", default="system")


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


def _load_user(username: str):
    with SessionLocal() as db:
        u = db.scalar(select(m.User).where(m.User.username == username))
        return (u.role, bool(u.is_active)) if u else None


def _deny(request: Request, msg: str):
    request.session["flash"] = {"msg": msg, "kind": "error"}
    back = request.headers.get("referer") or "/"
    return RedirectResponse(back if request.method != "GET" else "/", status_code=303)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(PUBLIC_PREFIXES) and not path.startswith("/media/harvest"):
            return await call_next(request)
        username = request.session.get("user")
        if path.startswith("/api/"):
            key = api_key()
            if username or not key or request.headers.get("x-api-key") == key or request.query_params.get("key") == key:
                return await call_next(request)
            return JSONResponse({"error": "API key required (X-API-Key header or ?key=)"}, status_code=401)
        if not username:
            if user_count() == 0:
                return RedirectResponse("/setup", status_code=303)
            return RedirectResponse(f"/login?next={path}", status_code=303)
        # Look the user up on every request so a role change or deactivation applies at once.
        found = _load_user(username)
        if not found or not found[1]:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)
        role = found[0]
        request.session["role"] = role
        current_user.set(username)
        if path.startswith(SELF_SERVICE):
            return await call_next(request)
        if path.startswith(ADMIN_PREFIXES) and role != "admin":
            return _deny(request, "That page is for administrators. Ask an admin if you need a setting changed.")
        if request.method not in ("GET", "HEAD", "OPTIONS") and role == "viewer":
            return _deny(request, "Your account is read-only, so nothing was changed. Ask an admin for editor access.")
        return await call_next(request)
