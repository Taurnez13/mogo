from __future__ import annotations

import io
import math
import re
import shutil
import tempfile
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload

from . import models as m
from .db import get_session, EXPORT_DIR, MEDIA_DIR
from .onix.codelists import label, options, LISTS
from .onix.importer import OnixImporter
from .onix.exporter import OnixExporter, finalise, serialise, validate
from .services import (get_settings, set_setting, product_query, count, dashboard_stats, log_change,
                       ensure_supply, set_rrp)
from .media import harvest_covers, save_cover_upload
from .connectors import cin7 as cin7c, shopify as shopc
from .auth import hash_password, verify_password
from .csvio import import_csv, export_csv, template_csv
from .delivery import test_connection, deliver, DeliveryError
from .services import RECIPIENT_PRESETS, build_feed, feed_products

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals.update(label=label, options=options, LISTS=LISTS, now=datetime.utcnow)
SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€", "AUD": "A$", "CAD": "C$", "NZD": "NZ$", "JPY": "¥", "INR": "₹"}


def _money(v, cur: str = "GBP") -> str:
    if v is None:
        return ""
    sym = SYMBOLS.get(cur or "GBP")
    return f"{sym}{Decimal(v):,.2f}" if sym else f"{cur} {Decimal(v):,.2f}"


templates.env.filters["money"] = _money
templates.env.filters["onixdate"] = lambda v: (
    f"{v[6:8]}/{v[4:6]}/{v[:4]}" if v and len(v) == 8 else (f"{v[4:6]}/{v[:4]}" if v and len(v) == 6 else v or ""))

STATUS_CLASS = {"02": "forthcoming", "04": "active", "07": "op", "11": "withdrawn", "08": "inactive", "09": "unknown"}
PAGE = 50


def render(request: Request, name: str, **ctx):
    flash = request.session.pop("flash", None)
    org = request.session.get("org")
    cur = request.session.get("currency") or "GBP"
    return templates.TemplateResponse(request, name, {"request": request, "flash": flash, "status_class": STATUS_CLASS,
                                                      "user": request.session.get("user"), "org_name": org, "cur": cur, **ctx})


def flash(request: Request, msg: str, kind: str = "ok"):
    request.session["flash"] = {"msg": msg, "kind": kind}


def _dec(v: Optional[str]) -> Optional[Decimal]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return Decimal(str(v).replace("£", "").replace(",", "").strip())
    except InvalidOperation:
        return None


def _date(v: Optional[str]) -> Optional[date]:
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(v.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _clean_isbn(v: str) -> str:
    return re.sub(r"[^0-9Xx]", "", v or "")


def isbn13_valid(isbn: str) -> bool:
    if not re.fullmatch(r"\d{13}", isbn):
        return False
    s = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn[:12]))
    return (10 - s % 10) % 10 == int(isbn[12])


# --------------------------------------------------------------------------- #
# Setup / login
# --------------------------------------------------------------------------- #

@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_session)):
    if db.scalar(select(func.count(m.User.id))):
        return RedirectResponse("/login", status_code=303)
    return render(request, "setup.html", error=None)


@router.post("/setup")
async def setup_save(request: Request, db: Session = Depends(get_session)):
    if db.scalar(select(func.count(m.User.id))):
        return RedirectResponse("/login", status_code=303)
    f = await request.form()
    name = (f.get("sender_name") or "").strip()
    username, password = (f.get("username") or "").strip(), f.get("password") or ""
    if not name or not username or len(password) < 8:
        return render(request, "setup.html", error="Publisher name, username and a password of at least 8 characters are required.")
    for k in ("sender_name", "contact_name", "contact_email", "currency", "default_country", "default_language",
              "tax_book_rate", "tax_book_code", "tax_standard_rate"):
        set_setting(db, k, (f.get(k) or "").strip())
    for k in ("default_imprint", "default_publisher", "default_supplier"):
        set_setting(db, k, name)
    db.add(m.User(username=username, password_hash=hash_password(password), role="admin"))
    db.commit()
    request.session["user"] = username
    request.session["org"] = name
    request.session["role"] = "admin"
    request.session["currency"] = (f.get("currency") or "GBP").strip().upper()
    flash(request, "Set up. Import an ONIX file or add your first title.")
    return RedirectResponse("/", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return render(request, "login.html", error=None, next=next)


@router.post("/login")
def login(request: Request, db: Session = Depends(get_session), username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    u = db.scalar(select(m.User).where(m.User.username == username.strip()))
    if not u or not verify_password(password, u.password_hash):
        return render(request, "login.html", error="Wrong username or password.", next=next)
    request.session["user"] = u.username
    request.session["role"] = u.role
    st = get_settings(db)
    request.session["org"] = st["sender_name"]
    request.session["currency"] = st["currency"]
    return RedirectResponse(next if next.startswith("/") else "/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.post("/users")
def user_add(request: Request, db: Session = Depends(get_session), username: str = Form(...), password: str = Form(...), role: str = Form("editor")):
    if request.session.get("role") != "admin":
        raise HTTPException(403, "Admins only")
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
    elif db.scalar(select(m.User).where(m.User.username == username.strip())):
        flash(request, "That username already exists.", "error")
    else:
        db.add(m.User(username=username.strip(), password_hash=hash_password(password), role=role)); db.commit()
        flash(request, f"Added user {username}.")
    return RedirectResponse("/settings", status_code=303)


@router.post("/users/{uid}/delete")
def user_delete(request: Request, uid: int, db: Session = Depends(get_session)):
    if request.session.get("role") != "admin":
        raise HTTPException(403, "Admins only")
    u = db.get(m.User, uid)
    if u and u.username != request.session.get("user"):
        db.delete(u); db.commit(); flash(request, f"Removed {u.username}.")
    return RedirectResponse("/settings", status_code=303)


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_session)):
    return render(request, "dashboard.html", stats=dashboard_stats(db))


# --------------------------------------------------------------------------- #
# Titles
# --------------------------------------------------------------------------- #

@router.get("/titles", response_class=HTMLResponse)
def titles(request: Request, db: Session = Depends(get_session), q: str = "", form: str = "", status: str = "",
           imprint: str = "", series_id: Optional[int] = None, contributor_id: Optional[int] = None,
           missing: str = "", page: int = 1, sort: str = "title"):
    stmt = product_query(db, q, form, status, imprint, series_id, contributor_id, missing)
    total = count(db, stmt)
    order = {"title": m.Product.title, "isbn": m.Product.isbn13, "date": m.Product.publication_date.desc(),
             "updated": m.Product.updated_at.desc(), "price": m.Product.rrp_gbp}.get(sort, m.Product.title)
    rows = db.scalars(stmt.options(selectinload(m.Product.contributors).selectinload(m.ProductContributor.contributor),
                                   selectinload(m.Product.resources))
                      .order_by(order).offset((page - 1) * PAGE).limit(PAGE)).all()
    forms = db.execute(select(m.Product.product_form, func.count()).group_by(m.Product.product_form).order_by(func.count().desc())).all()
    statuses = db.execute(select(m.Product.publishing_status, func.count()).group_by(m.Product.publishing_status)).all()
    imprints = [r[0] for r in db.execute(select(m.Product.imprint_name).distinct().order_by(m.Product.imprint_name)) if r[0]]
    series = db.scalars(select(m.Series).order_by(m.Series.title)).all()
    return render(request, "titles.html", rows=rows, total=total, page=page, pages=max(1, math.ceil(total / PAGE)),
                  q=q, form=form, status=status, imprint=imprint, series_id=series_id, contributor_id=contributor_id,
                  missing=missing, sort=sort, forms=forms, statuses=statuses, imprints=imprints, series=series)


@router.get("/titles/new", response_class=HTMLResponse)
def title_new(request: Request, db: Session = Depends(get_session)):
    s = get_settings(db)
    return render(request, "title_new.html", settings=s)


@router.post("/titles/new")
def title_create(request: Request, db: Session = Depends(get_session), isbn13: str = Form(...), title: str = Form(...),
                 subtitle: str = Form(""), product_form: str = Form("BC"), publication_date: str = Form(""),
                 rrp: str = Form(""), vat_standard: Optional[str] = Form(None), publishing_status: str = Form("02")):
    s = get_settings(db)
    isbn = _clean_isbn(isbn13)
    if not isbn13_valid(isbn):
        flash(request, f"{isbn13} is not a valid ISBN-13 (check digit failed).", "error")
        return RedirectResponse("/titles/new", status_code=303)
    if db.scalar(select(m.Product).where(m.Product.isbn13 == isbn)):
        flash(request, f"ISBN {isbn} already exists.", "error")
        return RedirectResponse("/titles/new", status_code=303)
    p = m.Product(record_reference=isbn, isbn13=isbn, title_without_prefix=title.strip(), subtitle=subtitle.strip() or None,
                  product_form=product_form, publishing_status=publishing_status, publication_date=_date(publication_date),
                  imprint_name=s["default_imprint"] or None, country_of_publication=s["default_country"] or None, record_source_type="01")
    # strip a leading article into TitlePrefix
    mt = re.match(r"^(The|A|An)\s+(.+)$", p.title_without_prefix)
    if mt:
        p.title_prefix, p.title_without_prefix = mt.group(1), mt.group(2)
    p.title = p.compute_title()
    p.identifiers.append(m.ProductIdentifier(id_type="15", value=isbn))
    p.identifiers.append(m.ProductIdentifier(id_type="03", value=isbn))
    p.languages.append(m.Language(role="01", code=s["default_language"] or "eng"))
    p.audiences.append(m.Audience(code_type="01", code_value="01"))
    p.publishers.append(m.Publisher(role="01", name=s["default_publisher"] or s["sender_name"]))
    p.sales_rights.append(m.SalesRights(rights_type="01", regions_included="WORLD"))
    if p.publication_date:
        p.publishing_dates.append(m.PublishingDate(role="01", value=p.publication_date.strftime("%Y%m%d")))
    if product_form == "ED":
        p.form_details.append(m.ProductFormDetail(code="E101"))
        p.primary_content_type = "10"
    db.add(p); db.flush()
    set_rrp(db, p, _dec(rrp), bool(vat_standard), s)
    db.add(m.ChangeLog(product_id=p.id, field="created", old_value="", new_value=isbn, changed_by="web"))
    db.commit()
    flash(request, f"Created {p.title}.")
    return RedirectResponse(f"/titles/{p.id}", status_code=303)


def _load_product(db: Session, pid: int) -> m.Product:
    p = db.get(m.Product, pid)
    if not p:
        raise HTTPException(404, "Title not found")
    return p


@router.get("/titles/{pid}", response_class=HTMLResponse)
def title_detail(request: Request, pid: int, db: Session = Depends(get_session), tab: str = "overview"):
    p = _load_product(db, pid)
    related = {}
    for rp in p.related_products:
        if rp.isbn13:
            related[rp.isbn13] = db.scalar(select(m.Product).where(m.Product.isbn13 == rp.isbn13))
    sd = p.supply_details[0] if p.supply_details else None
    st = get_settings(db)
    gbp_inc = next((pr for pr in (sd.prices if sd else []) if pr.currency == (st["currency"] or "GBP") and pr.price_type == "02"), None)
    links = {l.system: l for l in p.external_ids}
    all_series = db.scalars(select(m.Series).order_by(m.Series.title)).all()
    return render(request, "title_detail.html", p=p, tab=tab, related=related, sd=sd, gbp_inc=gbp_inc, links=links,
                  all_series=all_series, settings=st)


@router.post("/titles/{pid}/edit")
async def title_edit(request: Request, pid: int, db: Session = Depends(get_session)):
    p = _load_product(db, pid)
    f = await request.form()
    s = get_settings(db)
    changed = 0

    def upd(field, new):
        nonlocal changed
        old = getattr(p, field)
        if log_change(db, p, field, old, new):
            setattr(p, field, new); changed += 1

    text_fields = ["title_prefix", "title_without_prefix", "subtitle", "product_form", "product_form_description",
                   "primary_content_type", "imprint_name", "publishing_status", "edition_statement", "illustrations_note",
                   "country_of_publication", "city_of_publication", "contributor_statement", "copyright_year", "notes",
                   "country_of_manufacture", "trade_category"]
    for fld in text_fields:
        if fld in f:
            v = (f.get(fld) or "").strip() or None
            upd(fld, v)
    if "edition_number" in f:
        v = f.get("edition_number"); upd("edition_number", int(v) if v and v.isdigit() else None)
    if "publication_date" in f:
        nd = _date(f.get("publication_date"))
        if log_change(db, p, "publication_date", p.publication_date, nd):
            p.publication_date = nd; changed += 1
            pdt = next((d for d in p.publishing_dates if d.role == "01"), None)
            if nd:
                if pdt:
                    pdt.value, pdt.date_format = nd.strftime("%Y%m%d"), "00"
                else:
                    p.publishing_dates.append(m.PublishingDate(role="01", value=nd.strftime("%Y%m%d")))
            elif pdt:
                p.publishing_dates.remove(pdt)
    if "pages" in f:
        v = (f.get("pages") or "").strip()
        ext = next((e for e in p.extents if e.extent_type in ("00", "05", "10", "11")), None)
        old = ext.value if ext else None
        if log_change(db, p, "pages", old, v or None):
            changed += 1
            if v:
                if ext:
                    ext.value = v
                else:
                    p.extents.append(m.Extent(extent_type="00" if p.product_form != "ED" else "10", value=v, unit="03"))
            elif ext:
                p.extents.remove(ext)
    # measures (mm / g)
    for mtype, key in (("01", "height_mm"), ("02", "width_mm"), ("03", "thickness_mm"), ("08", "weight_g")):
        if key in f:
            v = _dec(f.get(key))
            unit = "gr" if mtype == "08" else "mm"
            cur = next((x for x in p.measures if x.measure_type == mtype and x.unit == unit), None)
            old = cur.value if cur else None
            if log_change(db, p, key, old, v):
                changed += 1
                if v is None or v <= 0:
                    if cur:
                        p.measures.remove(cur)
                else:
                    if cur:
                        cur.value = v
                    else:
                        p.measures.append(m.Measure(measure_type=mtype, value=v, unit=unit))
    if "rrp" in f:
        amt = _dec(f.get("rrp"))
        vat = bool(f.get("vat_standard"))
        sd = p.supply_details[0] if p.supply_details else None
        cur = next((pr for pr in (sd.prices if sd else []) if pr.currency == (s["currency"] or "GBP") and pr.price_type == "02"), None)
        old_vat = (cur.tax_rate_code == "S") if cur else False
        if log_change(db, p, "rrp_gbp", p.rrp_gbp, amt) | log_change(db, p, "vat_standard", old_vat, vat):
            set_rrp(db, p, amt, vat, s); changed += 1
    if "availability" in f:
        sd = ensure_supply(db, p, s)
        v = f.get("availability")
        if log_change(db, p, "availability", sd.availability, v):
            sd.availability = v; changed += 1
    if "is_active" in f or "overview_form" in f:
        pass
    p.title = p.compute_title()
    db.commit()
    flash(request, f"Saved {changed} change{'s' if changed != 1 else ''}." if changed else "No changes.")
    return RedirectResponse(f"/titles/{pid}?tab={f.get('tab', 'overview')}", status_code=303)


@router.post("/titles/{pid}/texts")
def text_add(request: Request, pid: int, db: Session = Depends(get_session), text_type: str = Form(...),
             text: str = Form(...), text_author: str = Form(""), text_id: Optional[int] = Form(None), delete: Optional[str] = Form(None)):
    p = _load_product(db, pid)
    if text_id:
        tc = next((t for t in p.texts if t.id == text_id), None)
        if tc:
            if delete:
                log_change(db, p, f"text:{tc.text_type}", tc.text[:80], None); p.texts.remove(tc)
            else:
                log_change(db, p, f"text:{tc.text_type}", tc.text[:80], text[:80])
                tc.text, tc.text_type, tc.text_author = text.strip(), text_type, text_author.strip() or None
                tc.text_format = "02" if "<" in text else "06"
    elif text.strip():
        p.texts.append(m.TextContent(text_type=text_type, text=text.strip(), text_author=text_author.strip() or None,
                                     text_format="02" if "<" in text else "06", content_audience="00"))
        log_change(db, p, f"text:{text_type}", None, text[:80])
    db.commit()
    return RedirectResponse(f"/titles/{pid}?tab=texts", status_code=303)


@router.post("/titles/{pid}/subjects")
def subject_add(request: Request, pid: int, db: Session = Depends(get_session), scheme: str = Form(...), code: str = Form(""),
                heading: str = Form(""), main_subject: Optional[str] = Form(None), subject_id: Optional[int] = Form(None)):
    p = _load_product(db, pid)
    if subject_id:
        sj = next((x for x in p.subjects if x.id == subject_id), None)
        if sj:
            log_change(db, p, f"subject:{sj.scheme}", sj.code or sj.heading, None); p.subjects.remove(sj)
    elif code.strip() or heading.strip():
        version = {"93": "1.6", "10": "2024", "12": "2.1"}.get(scheme)
        name = (get_settings(db)["sender_name"] + " categories") if scheme == "24" else None
        if main_subject:
            for x in p.subjects:
                if x.scheme == scheme:
                    x.main_subject = False
        p.subjects.append(m.Subject(scheme=scheme, scheme_name=name, scheme_version=version, code=code.strip() or None,
                                    heading=heading.strip() or None, main_subject=bool(main_subject)))
        log_change(db, p, f"subject:{scheme}", None, code or heading)
    db.commit()
    return RedirectResponse(f"/titles/{pid}?tab=subjects", status_code=303)


@router.post("/titles/{pid}/contributors")
def contributor_link(request: Request, pid: int, db: Session = Depends(get_session), contributor_id: Optional[str] = Form(None),
                     new_name: str = Form(""), corporate: Optional[str] = Form(None), role: str = Form("A01"),
                     link_id: Optional[int] = Form(None), bio: str = Form("")):
    p = _load_product(db, pid)
    if link_id:  # remove
        pc = next((x for x in p.contributors if x.id == link_id), None)
        if pc:
            log_change(db, p, "contributor", f"{pc.contributor.display_name} ({pc.role})", None); p.contributors.remove(pc)
    else:
        c = db.get(m.Contributor, int(contributor_id)) if contributor_id and contributor_id.isdigit() else None
        if not c and new_name.strip():
            name = new_name.strip()
            if corporate:
                c = m.Contributor(is_corporate=True, corporate_name=name)
            else:
                parts = name.split()
                c = m.Contributor(person_name=name, key_names=parts[-1], names_before_key=" ".join(parts[:-1]) or None,
                                  person_name_inverted=f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else name)
            if bio.strip():
                c.biographical_note = bio.strip()
            db.add(c); db.flush()
        if c:
            seq = max([x.sequence for x in p.contributors], default=0) + 1
            p.contributors.append(m.ProductContributor(contributor=c, role=role, sequence=seq))
            p.no_contributor = False
            log_change(db, p, "contributor", None, f"{c.display_name} ({role})")
    db.commit()
    return RedirectResponse(f"/titles/{pid}?tab=contributors", status_code=303)


@router.post("/titles/{pid}/related")
def related_add(request: Request, pid: int, db: Session = Depends(get_session), isbn13: str = Form(""),
                relation: str = Form("06"), related_id: Optional[int] = Form(None), reciprocal: Optional[str] = Form(None)):
    p = _load_product(db, pid)
    if related_id:
        rp = next((x for x in p.related_products if x.id == related_id), None)
        if rp:
            log_change(db, p, "related", f"{rp.relation_codes} {rp.isbn13}", None); p.related_products.remove(rp)
    else:
        isbn = _clean_isbn(isbn13)
        if isbn13_valid(isbn):
            other = db.scalar(select(m.Product).where(m.Product.isbn13 == isbn))
            p.related_products.append(m.RelatedProduct(relation_codes=relation, isbn13=isbn,
                                                       product_form=other.product_form if other else None))
            log_change(db, p, "related", None, f"{relation} {isbn}")
            if reciprocal and other and p.isbn13:
                other.related_products.append(m.RelatedProduct(relation_codes=relation, isbn13=p.isbn13, product_form=p.product_form))
                log_change(db, other, "related", None, f"{relation} {p.isbn13}")
        else:
            flash(request, f"{isbn13} is not a valid ISBN-13.", "error")
    db.commit()
    return RedirectResponse(f"/titles/{pid}?tab=related", status_code=303)


@router.post("/titles/{pid}/series")
def series_link(request: Request, pid: int, db: Session = Depends(get_session), series_id: str = Form(""),
                new_series: str = Form(""), sequence: str = Form(""), link_id: Optional[int] = Form(None)):
    p = _load_product(db, pid)
    if link_id:
        sl = next((x for x in p.series_links if x.id == link_id), None)
        if sl:
            log_change(db, p, "series", sl.series.title, None); p.series_links.remove(sl)
    else:
        s = db.get(m.Series, int(series_id)) if series_id.isdigit() else None
        if not s and new_series.strip():
            s = db.scalar(select(m.Series).where(m.Series.title == new_series.strip())) or m.Series(title=new_series.strip())
            db.add(s); db.flush()
        if s:
            p.series_links.append(m.ProductSeries(series=s, sequence_number=sequence.strip() or None, sequence_type="02" if sequence.strip() else None))
            p.no_collection = False
            log_change(db, p, "series", None, s.title)
    db.commit()
    return RedirectResponse(f"/titles/{pid}?tab=overview", status_code=303)


@router.post("/titles/{pid}/cover")
async def cover_upload(request: Request, pid: int, db: Session = Depends(get_session), file: UploadFile = File(...)):
    p = _load_product(db, pid)
    data = await file.read()
    try:
        save_cover_upload(db, p, data, file.filename or "cover.jpg")
        log_change(db, p, "cover", None, file.filename)
        db.commit()
        flash(request, "Cover updated.")
    except ValueError as e:
        flash(request, str(e), "error")
    return RedirectResponse(f"/titles/{pid}", status_code=303)


@router.post("/titles/{pid}/delete")
def title_delete(request: Request, pid: int, db: Session = Depends(get_session)):
    p = _load_product(db, pid)
    db.delete(p); db.commit()
    flash(request, f"Deleted {p.title}.")
    return RedirectResponse("/titles", status_code=303)


# --------------------------------------------------------------------------- #
# Contributors & series
# --------------------------------------------------------------------------- #

@router.get("/contributors", response_class=HTMLResponse)
def contributors(request: Request, db: Session = Depends(get_session), q: str = ""):
    stmt = select(m.Contributor, func.count(m.ProductContributor.id)).outerjoin(m.ProductContributor).group_by(m.Contributor.id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(m.Contributor.person_name.ilike(like) | m.Contributor.person_name_inverted.ilike(like) | m.Contributor.corporate_name.ilike(like))
    rows = db.execute(stmt.order_by(m.Contributor.person_name_inverted, m.Contributor.corporate_name)).all()
    return render(request, "contributors.html", rows=rows, q=q)


@router.get("/contributors/{cid}", response_class=HTMLResponse)
def contributor_detail(request: Request, cid: int, db: Session = Depends(get_session)):
    c = db.get(m.Contributor, cid)
    if not c:
        raise HTTPException(404)
    return render(request, "contributor_detail.html", c=c)


@router.post("/contributors/{cid}")
async def contributor_edit(request: Request, cid: int, db: Session = Depends(get_session)):
    c = db.get(m.Contributor, cid)
    if not c:
        raise HTTPException(404)
    f = await request.form()
    for fld in ("person_name", "person_name_inverted", "corporate_name", "titles_before_names", "names_before_key",
                "key_names", "letters_after_names", "biographical_note", "website", "isni"):
        if fld in f:
            setattr(c, fld, (f.get(fld) or "").strip() or None)
    c.is_corporate = bool(f.get("is_corporate"))
    db.commit()
    flash(request, "Saved.")
    return RedirectResponse(f"/contributors/{cid}", status_code=303)


@router.get("/series", response_class=HTMLResponse)
def series_list(request: Request, db: Session = Depends(get_session)):
    rows = db.execute(select(m.Series, func.count(m.ProductSeries.id)).outerjoin(m.ProductSeries).group_by(m.Series.id).order_by(m.Series.title)).all()
    return render(request, "series.html", rows=rows)


# --------------------------------------------------------------------------- #
# Import / export
# --------------------------------------------------------------------------- #

@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, db: Session = Depends(get_session)):
    return render(request, "import.html")


@router.post("/import")
async def import_run(request: Request, db: Session = Depends(get_session), file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile("wb", suffix=".xml", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp); path = tmp.name
    st = OnixImporter(db).import_file(path)
    Path(path).unlink(missing_ok=True)
    flash(request, f"Import finished: {st.created} created, {st.updated} updated, {len(st.errors)} errors."
          + (" First error: " + st.errors[0] if st.errors else ""), "ok" if not st.errors else "warn")
    return RedirectResponse("/titles?sort=updated", status_code=303)


@router.post("/import/csv")
async def import_csv_run(request: Request, db: Session = Depends(get_session), file: UploadFile = File(...)):
    res = import_csv(db, await file.read())
    flash(request, f"CSV import: {res['created']} created, {res['updated']} updated, {len(res['errors'])} rows skipped."
          + (" " + "; ".join(res["errors"][:5]) if res["errors"] else ""), "ok" if not res["errors"] else "warn")
    return RedirectResponse("/titles?sort=updated", status_code=303)


@router.get("/import/template.csv")
def import_template():
    return Response(template_csv(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=mogo_template.csv"})


@router.get("/export/titles.csv")
def export_titles_csv(db: Session = Depends(get_session), q: str = "", form: str = "", status: str = ""):
    prods = db.scalars(product_query(db, q=q, form=form, status=status).order_by(m.Product.title)).all()
    return Response(export_csv(db, prods), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=titles.csv"})


@router.get("/export", response_class=HTMLResponse)
def export_page(request: Request, db: Session = Depends(get_session)):
    runs = db.scalars(select(m.ExportRun).order_by(m.ExportRun.id.desc()).limit(25)).all()
    recipients = db.scalars(select(m.Recipient).order_by(m.Recipient.name)).all()
    pending = {r.id: len(feed_products(db, r, "delta")) for r in recipients}
    pending_any = count(db, product_query(db, missing="unexported"))
    return render(request, "export.html", runs=runs, recipients=recipients, pending=pending, pending_any=pending_any,
                  settings=get_settings(db))


@router.post("/export")
def export_run(request: Request, db: Session = Depends(get_session), mode: str = Form("full"), tag_style: str = Form(""),
               forms: str = Form(""), recipient_id: str = Form(""), deliver_now: Optional[str] = Form(None),
               mark_sent: Optional[str] = Form(None)):
    s = get_settings(db)
    r = db.get(m.Recipient, int(recipient_id)) if recipient_id.isdigit() else None
    if mode == "delta" and not feed_products(db, r, "delta"):
        flash(request, f"Nothing has changed since {r.name if r else 'the last export'} was last sent, so no file was made.")
        return RedirectResponse(f"/recipients/{r.id}" if r else "/export", status_code=303)
    run, prods = build_feed(db, r, mode, s, EXPORT_DIR, tag_style=tag_style or None, forms=forms)
    msg = f"{run.product_count} products in {run.filename}." + (" Schema valid." if run.valid else f" {len((run.validation_errors or '').splitlines())} validation errors, not delivered.")
    kind = "ok" if run.valid else "warn"
    if r and deliver_now and r.delivery != "download":
        if run.valid:
            try:
                run.remote_path = deliver(r, EXPORT_DIR / run.filename)
                run.delivered, run.delivered_at = True, datetime.utcnow()
                msg += f" Delivered to {r.name} ({run.remote_path})."
            except DeliveryError as e:
                run.delivery_error = str(e); kind = "error"
                msg += f" Delivery to {r.name} failed: {e}"
    if mark_sent and run.valid and (run.delivered or not deliver_now):
        now = datetime.utcnow()
        if r:
            r.last_sent_at = now
        else:
            for p in prods:
                p.last_exported_at = now
    db.commit()
    flash(request, msg, kind)
    back = request.headers.get("referer") or ""
    return RedirectResponse(f"/recipients/{r.id}" if (r and "/recipients/" in back) else "/export", status_code=303)


@router.post("/export/{run_id}/deliver")
def export_deliver(request: Request, run_id: int, db: Session = Depends(get_session)):
    run = db.get(m.ExportRun, run_id)
    if not run or not run.recipient:
        raise HTTPException(404)
    if not run.valid:
        flash(request, "This file has validation errors and will not be delivered.", "error")
        return RedirectResponse("/export", status_code=303)
    try:
        run.remote_path = deliver(run.recipient, EXPORT_DIR / run.filename)
        run.delivered, run.delivered_at, run.delivery_error = True, datetime.utcnow(), None
        run.recipient.last_sent_at = run.delivered_at
        flash(request, f"Delivered {run.filename} to {run.recipient.name}.")
    except DeliveryError as e:
        run.delivery_error = str(e)
        flash(request, f"Delivery failed: {e}", "error")
    db.commit()
    return RedirectResponse("/export", status_code=303)


# --------------------------------------------------------------------------- #
# Recipients
# --------------------------------------------------------------------------- #

RECIPIENT_FIELDS = ("name", "addressee_name", "tag_style", "release", "include_forms", "statuses", "filename_prefix",
                    "delivery", "host", "username", "remote_dir", "notes")


@router.get("/recipients", response_class=HTMLResponse)
def recipients(request: Request, db: Session = Depends(get_session)):
    rows = db.scalars(select(m.Recipient).order_by(m.Recipient.name)).all()
    pending = {r.id: len(feed_products(db, r, "delta")) for r in rows}
    return render(request, "recipients.html", rows=rows, pending=pending, presets=RECIPIENT_PRESETS)


@router.post("/recipients/new")
def recipient_new(request: Request, db: Session = Depends(get_session), preset: str = Form("download")):
    data = dict(RECIPIENT_PRESETS.get(preset, RECIPIENT_PRESETS["download"]))
    base = data["name"]; n = 2
    while db.scalar(select(m.Recipient).where(m.Recipient.name == data["name"])):
        data["name"] = f"{base} {n}"; n += 1
    r = m.Recipient(**data)
    db.add(r); db.commit()
    return RedirectResponse(f"/recipients/{r.id}", status_code=303)


@router.get("/recipients/{rid}", response_class=HTMLResponse)
def recipient_detail(request: Request, rid: int, db: Session = Depends(get_session)):
    r = db.get(m.Recipient, rid)
    if not r:
        raise HTTPException(404)
    return render(request, "recipient_detail.html", r=r, pending=len(feed_products(db, r, "delta")),
                  total=len(feed_products(db, r, "full")))


@router.post("/recipients/{rid}")
async def recipient_save(request: Request, rid: int, db: Session = Depends(get_session)):
    r = db.get(m.Recipient, rid)
    if not r:
        raise HTTPException(404)
    f = await request.form()
    for k in RECIPIENT_FIELDS:
        if k in f:
            setattr(r, k, (f.get(k) or "").strip() or None)
    r.name = r.name or "Unnamed recipient"
    r.tag_style = r.tag_style or "reference"; r.release = r.release or "3.0"; r.delivery = r.delivery or "download"
    port = (f.get("port") or "").strip()
    r.port = int(port) if port.isdigit() else None
    r.exclude_digital = bool(f.get("exclude_digital"))
    r.include_barcode = bool(f.get("include_barcode"))
    pw = f.get("password") or ""
    if pw and pw != "••••••••":
        r.password = pw
    key = (f.get("private_key") or "").strip()
    if key and key != "(key stored)":
        r.private_key = key
    elif not key:
        r.private_key = None
    if f.get("reset_last_sent"):
        r.last_sent_at = None
    db.commit()
    flash(request, "Recipient saved.")
    return RedirectResponse(f"/recipients/{rid}", status_code=303)


@router.post("/recipients/{rid}/test")
def recipient_test(request: Request, rid: int, db: Session = Depends(get_session)):
    r = db.get(m.Recipient, rid)
    if not r:
        raise HTTPException(404)
    try:
        result = test_connection(r); kind = "ok"
    except DeliveryError as e:
        result = str(e); kind = "error"
    except Exception as e:  # unexpected, still show it
        result = f"Unexpected error: {e}"; kind = "error"
    r.last_tested_at, r.last_test_result = datetime.utcnow(), result
    db.commit()
    flash(request, result, kind)
    return RedirectResponse(f"/recipients/{rid}", status_code=303)


@router.post("/recipients/{rid}/delete")
def recipient_delete(request: Request, rid: int, db: Session = Depends(get_session)):
    r = db.get(m.Recipient, rid)
    if r:
        for run in r.runs:
            run.recipient_id = None
        db.delete(r); db.commit()
        flash(request, f"Removed {r.name}. Its past export files are kept.")
    return RedirectResponse("/recipients", status_code=303)


@router.get("/export/download/{fname}")
def export_download(fname: str):
    path = EXPORT_DIR / Path(fname).name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path), media_type="application/xml", filename=path.name)


@router.get("/titles/{pid}/onix.xml")
def product_onix(pid: int, db: Session = Depends(get_session), style: str = "reference"):
    p = _load_product(db, pid)
    s = get_settings(db)
    ex = OnixExporter(db, s["sender_name"], s["contact_name"], s["contact_email"], media_base_url=s["media_base_url"] or None)
    root = finalise(ex.build_message([p]), style)
    errs = validate(root)
    body = serialise(root).decode()
    if errs:
        body = "<!-- VALIDATION ERRORS:\n" + "\n".join(e.replace("--", "- -") for e in errs) + "\n-->\n" + body
    return Response(body, media_type="application/xml")


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #

@router.post("/media/harvest")
def media_harvest(request: Request, db: Session = Depends(get_session), limit: int = Form(2000)):
    res = harvest_covers(db, limit=limit)
    flash(request, f"Covers: {res['downloaded']} downloaded, {res['skipped']} already local, {res['failed']} failed.",
          "ok" if not res["failed"] else "warn")
    return RedirectResponse("/settings", status_code=303)


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #

@router.get("/sync", response_class=HTMLResponse)
def sync_page(request: Request, db: Session = Depends(get_session)):
    s = get_settings(db)
    logs = db.scalars(select(m.SyncLog).order_by(m.SyncLog.id.desc()).limit(30)).all()
    linked = dict(db.execute(select(m.ExternalLink.system, func.count()).group_by(m.ExternalLink.system)).all())
    total = db.scalar(select(func.count(m.Product.id)))
    return render(request, "sync.html", settings=s, logs=logs, linked=linked, total=total,
                  cin7_ready=bool(s["cin7_account_id"] and s["cin7_api_key"]),
                  shopify_ready=bool(s["shopify_shop"] and s["shopify_token"]))


@router.post("/sync/{system}/{direction}")
def sync_run(request: Request, system: str, direction: str, db: Session = Depends(get_session), dry_run: Optional[str] = Form(None)):
    s = get_settings(db)
    try:
        if system == "cin7":
            client = cin7c.Cin7Client(s["cin7_account_id"], s["cin7_api_key"])
            summary = cin7c.pull(db, client) if direction == "pull" else cin7c.push(db, client, dry_run=bool(dry_run))
        elif system == "shopify":
            client = shopc.ShopifyClient(s["shopify_shop"], s["shopify_token"])
            summary = shopc.pull(db, client) if direction == "pull" else shopc.push(db, client, dry_run=bool(dry_run))
        else:
            raise HTTPException(404)
        db.add(m.SyncLog(system=system, direction=direction, summary=summary, ok=True)); db.commit()
        flash(request, summary)
    except Exception as e:  # surface connector errors in the UI
        db.rollback()
        db.add(m.SyncLog(system=system, direction=direction, summary=str(e)[:2000], ok=False)); db.commit()
        flash(request, f"{system} {direction} failed: {e}", "error")
    return RedirectResponse("/sync", status_code=303)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_session)):
    s = get_settings(db)
    covers_total = db.scalar(select(func.count(m.SupportingResource.id)).where(m.SupportingResource.content_type == "01"))
    covers_local = db.scalar(select(func.count(m.SupportingResource.id)).where(m.SupportingResource.content_type == "01", m.SupportingResource.local_path.isnot(None)))
    users = db.scalars(select(m.User).order_by(m.User.username)).all()
    return render(request, "settings.html", settings=s, covers_total=covers_total, covers_local=covers_local, users=users)


@router.post("/settings")
async def settings_save(request: Request, db: Session = Depends(get_session)):
    f = await request.form()
    from .services import DEFAULT_SETTINGS
    for k in DEFAULT_SETTINGS:
        if k in f:
            v = (f.get(k) or "").strip()
            if k in ("cin7_api_key", "shopify_token") and v == "••••••••":
                continue
            set_setting(db, k, v)
    db.commit()
    st = get_settings(db)
    request.session["org"], request.session["currency"] = st["sender_name"], st["currency"]
    flash(request, "Settings saved.")
    return RedirectResponse("/settings", status_code=303)
