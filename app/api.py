"""Small read-only JSON API — handy for Power Automate, the daily dashboard, or the website."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .db import get_session
from .onix.codelists import label
from .services import product_query

router = APIRouter()


def _product_json(p: m.Product) -> dict:
    sd = p.supply_details[0] if p.supply_details else None
    return {
        "id": p.id, "isbn13": p.isbn13, "title": p.title, "subtitle": p.subtitle,
        "product_form": p.product_form, "product_form_label": label(150, p.product_form),
        "publishing_status": p.publishing_status, "publishing_status_label": label(64, p.publishing_status),
        "publication_date": p.publication_date.isoformat() if p.publication_date else None,
        "imprint": p.imprint_name, "rrp_gbp": float(p.rrp_gbp) if p.rrp_gbp is not None else None,
        "availability": sd.availability if sd else None,
        "contributors": [{"name": c.contributor.display_name, "role": c.role, "role_label": label(17, c.role)} for c in p.contributors],
        "series": [s.series.title for s in p.series_links],
        "thema": [s.code for s in p.subjects if s.scheme == "93" and s.code],
        "bic": [s.code for s in p.subjects if s.scheme == "12" and s.code],
        "bisac": [s.code for s in p.subjects if s.scheme == "10" and s.code],
        "own_categories": [s.heading for s in p.subjects if s.scheme in ("23", "24")],
        "keywords": next((s.heading for s in p.subjects if s.scheme == "20"), None),
        "pages": next((e.value for e in p.extents if e.extent_type in ("00", "05", "10", "11")), None),
        "description": p.main_description,
        "cover": (f"/media/{p.main_cover.local_path}" if p.main_cover and p.main_cover.local_path
                  else (p.main_cover.remote_url if p.main_cover else None)),
        "related": [{"relation": r.relation_codes, "isbn13": r.isbn13} for r in p.related_products],
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/products")
def products(db: Session = Depends(get_session), q: str = "", form: str = "", status: str = "", limit: int = 200, offset: int = 0):
    rows = db.scalars(product_query(db, q=q, form=form, status=status).order_by(m.Product.title).offset(offset).limit(min(limit, 1000))).all()
    return [_product_json(p) for p in rows]


@router.get("/products/{isbn}")
def product(isbn: str, db: Session = Depends(get_session)):
    p = db.scalar(select(m.Product).where(m.Product.isbn13 == isbn))
    if not p:
        raise HTTPException(404)
    return _product_json(p)
