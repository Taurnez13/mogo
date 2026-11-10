"""Cover image storage: download remotely-hosted cover URLs into local media, accept uploads."""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .db import MEDIA_DIR

COVERS = MEDIA_DIR / "covers"
COVERS.mkdir(parents=True, exist_ok=True)

FORMATS = {b"\xff\xd8": ("jpg", "D502"), b"\x89PNG": ("png", "D503"), b"GIF8": ("gif", "D501")}


def _sniff(data: bytes) -> tuple[str, str]:
    for sig, v in FORMATS.items():
        if data.startswith(sig):
            return v
    raise ValueError("File is not a JPEG, PNG or GIF image.")


def harvest_covers(db: Session, limit: int = 2000, timeout: float = 20.0) -> dict:
    """Download every front-cover resource that has a remote URL but no local file."""
    res = {"downloaded": 0, "skipped": 0, "failed": 0, "errors": []}
    rows = db.scalars(select(m.SupportingResource).where(m.SupportingResource.content_type == "01",
                                                         m.SupportingResource.remote_url.isnot(None))).all()
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": "MOGO/1.0"}) as client:
        for sr in rows[:limit]:
            if sr.local_path and (MEDIA_DIR / sr.local_path).exists():
                res["skipped"] += 1
                continue
            try:
                r = client.get(sr.remote_url)
                r.raise_for_status()
                ext, fmt = _sniff(r.content)
                p = db.get(m.Product, sr.product_id)
                stem = p.isbn13 or p.record_reference
                fname = f"{stem}.{ext}"
                (COVERS / fname).write_bytes(r.content)
                sr.local_path = f"covers/{fname}"
                sr.filename = fname
                sr.file_format = fmt
                res["downloaded"] += 1
                if res["downloaded"] % 50 == 0:
                    db.commit()
            except Exception as e:
                res["failed"] += 1
                res["errors"].append(f"{sr.remote_url}: {e}")
    db.commit()
    return res


def save_cover_upload(db: Session, p: m.Product, data: bytes, filename: str) -> m.SupportingResource:
    ext, fmt = _sniff(data)
    stem = p.isbn13 or p.record_reference
    fname = f"{stem}.{ext}"
    (COVERS / fname).write_bytes(data)
    sr = p.main_cover
    if sr is None:
        sr = m.SupportingResource(content_type="01", content_audience="00", resource_mode="03", resource_form="02")
        p.resources.append(sr)
    sr.local_path = f"covers/{fname}"
    sr.filename = fname
    sr.file_format = fmt
    sr.content_date = date.today()
    sr.content_date_role = "17"
    return sr
