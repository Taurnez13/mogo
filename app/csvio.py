"""CSV import/export — the migration path for publishers coming from a spreadsheet.

Columns (header row required, order free, unknown columns ignored):
  isbn13*, title*, subtitle, product_form (ONIX List 150, default BC), publication_date (YYYY-MM-DD),
  rrp (inc tax), standard_rated (yes/no), pages, height_mm, width_mm, weight_g, imprint, series, series_number,
  contributors ("Jane Smith|A01; John Doe|B01" — role defaults to A01), description, short_description,
  thema ("QRMP;QRVJ"), bic, bisac, keywords, status (ONIX List 64, default 04), language, cover_url
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .services import get_settings, set_rrp, log_change

COLUMNS = ["isbn13", "title", "subtitle", "product_form", "publication_date", "rrp", "standard_rated", "pages",
           "height_mm", "width_mm", "weight_g", "imprint", "series", "series_number", "contributors", "description",
           "short_description", "thema", "bic", "bisac", "keywords", "status", "language", "cover_url"]


def _isbn(v: str) -> str:
    return re.sub(r"[^0-9Xx]", "", v or "")


def isbn13_valid(isbn: str) -> bool:
    if not re.fullmatch(r"\d{13}", isbn):
        return False
    s = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn[:12]))
    return (10 - s % 10) % 10 == int(isbn[12])


def _dec(v) -> Optional[Decimal]:
    try:
        return Decimal(str(v).replace("£", "").replace("$", "").replace("€", "").replace(",", "").strip()) if str(v).strip() else None
    except InvalidOperation:
        return None


def _date(v):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            pass
    return None


def _split(v: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;|,]", v or "") if x.strip()]


def template_csv() -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(COLUMNS)
    w.writerow(["9780000000002", "Example Title", "A subtitle", "BC", "2027-01-15", "9.99", "no", "128", "198", "129", "180",
                "Example Press", "Example Series", "3", "Jane Smith|A01; John Doe|B06", "<p>Full description.</p>",
                "One-line description.", "FBA;FXD", "", "FIC000000", "example;keywords", "02", "eng", "https://example.org/covers/9780000000002.jpg"])
    return out.getvalue()


def import_csv(db: Session, data: bytes) -> dict:
    s = get_settings(db)
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    created = updated = 0
    errors: list[str] = []
    for n, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        isbn = _isbn(row.get("isbn13") or row.get("isbn") or "")
        if not isbn13_valid(isbn):
            errors.append(f"row {n}: invalid ISBN-13 '{row.get('isbn13')}'"); continue
        if not row.get("title"):
            errors.append(f"row {n}: missing title"); continue
        p = db.scalar(select(m.Product).where(m.Product.isbn13 == isbn))
        if p is None:
            p = m.Product(record_reference=isbn, isbn13=isbn, record_source_type="01")
            p.identifiers.append(m.ProductIdentifier(id_type="15", value=isbn))
            p.identifiers.append(m.ProductIdentifier(id_type="03", value=isbn))
            p.publishers.append(m.Publisher(role="01", name=s["default_publisher"] or s["sender_name"]))
            p.sales_rights.append(m.SalesRights(rights_type="01", regions_included="WORLD"))
            p.audiences.append(m.Audience(code_type="01", code_value="01"))
            db.add(p); db.flush()
            created += 1
            log_change(db, p, "created", None, "csv import", who="csv")
        else:
            updated += 1
        title = row["title"]
        mt = re.match(r"^(The|A|An)\s+(.+)$", title)
        p.title_prefix, p.title_without_prefix = (mt.group(1), mt.group(2)) if mt else (None, title)
        p.subtitle = row.get("subtitle") or None
        p.title = p.compute_title()
        p.product_form = (row.get("product_form") or p.product_form or "BC").upper()
        if p.product_form == "ED" and not p.form_details:
            p.form_details.append(m.ProductFormDetail(code="E101")); p.primary_content_type = "10"
        p.publishing_status = row.get("status") or p.publishing_status or "04"
        p.imprint_name = row.get("imprint") or p.imprint_name or s["default_imprint"] or None
        p.country_of_publication = p.country_of_publication or s["default_country"] or None
        if not p.languages:
            p.languages.append(m.Language(role="01", code=row.get("language") or s["default_language"] or "eng"))
        d = _date(row.get("publication_date"))
        if d:
            p.publication_date = d
            pdt = next((x for x in p.publishing_dates if x.role == "01"), None)
            if pdt:
                pdt.value = d.strftime("%Y%m%d")
            else:
                p.publishing_dates.append(m.PublishingDate(role="01", value=d.strftime("%Y%m%d")))
        if row.get("rrp"):
            set_rrp(db, p, _dec(row["rrp"]), row.get("standard_rated", "").lower() in ("yes", "y", "true", "1"), s)
        if row.get("pages"):
            ext = next((e for e in p.extents if e.extent_type in ("00", "05", "10", "11")), None)
            if ext:
                ext.value = row["pages"]
            else:
                p.extents.append(m.Extent(extent_type="00" if p.product_form != "ED" else "10", value=row["pages"], unit="03"))
        for key, mtype, unit in (("height_mm", "01", "mm"), ("width_mm", "02", "mm"), ("weight_g", "08", "gr")):
            v = _dec(row.get(key))
            if v:
                cur = next((x for x in p.measures if x.measure_type == mtype and x.unit == unit), None)
                if cur:
                    cur.value = v
                else:
                    p.measures.append(m.Measure(measure_type=mtype, value=v, unit=unit))
        if row.get("series"):
            sr = db.scalar(select(m.Series).where(m.Series.title == row["series"])) or m.Series(title=row["series"])
            db.add(sr); db.flush()
            if not any(sl.series_id == sr.id for sl in p.series_links):
                p.series_links.append(m.ProductSeries(series=sr, sequence_number=row.get("series_number") or None,
                                                      sequence_type="02" if row.get("series_number") else None))
        if row.get("contributors"):
            p.contributors.clear()
            for i, item in enumerate([x.strip() for x in row["contributors"].split(";") if x.strip()], start=1):
                name, _, role = item.partition("|")
                name, role = name.strip(), (role.strip().upper() or "A01")
                c = db.scalar(select(m.Contributor).where(m.Contributor.person_name == name)) or \
                    db.scalar(select(m.Contributor).where(m.Contributor.corporate_name == name))
                if not c:
                    parts = name.split()
                    c = m.Contributor(person_name=name, key_names=parts[-1], names_before_key=" ".join(parts[:-1]) or None,
                                      person_name_inverted=f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else name)
                    db.add(c); db.flush()
                p.contributors.append(m.ProductContributor(contributor=c, role=role, sequence=i))
        for key, ttype in (("description", "03"), ("short_description", "02")):
            if row.get(key):
                tc = next((t for t in p.texts if t.text_type == ttype), None)
                fmt = "02" if "<" in row[key] else "06"
                if tc:
                    tc.text, tc.text_format = row[key], fmt
                else:
                    p.texts.append(m.TextContent(text_type=ttype, text=row[key], text_format=fmt, content_audience="00"))
        for key, scheme, version in (("thema", "93", "1.6"), ("bic", "12", "2.1"), ("bisac", "10", "2024")):
            codes = _split(row.get(key, ""))
            if codes:
                for sj in [x for x in p.subjects if x.scheme == scheme]:
                    p.subjects.remove(sj)
                for i, code in enumerate(codes):
                    p.subjects.append(m.Subject(scheme=scheme, scheme_version=version, code=code.upper(), main_subject=(i == 0)))
        if row.get("keywords"):
            kw = next((x for x in p.subjects if x.scheme == "20"), None)
            if kw:
                kw.heading = row["keywords"]
            else:
                p.subjects.append(m.Subject(scheme="20", heading=row["keywords"]))
        if row.get("cover_url") and not p.main_cover:
            p.resources.append(m.SupportingResource(content_type="01", content_audience="00", resource_mode="03",
                                                    resource_form="02", remote_url=row["cover_url"]))
        p.updated_at = datetime.utcnow()
    db.commit()
    return {"created": created, "updated": updated, "errors": errors}


def export_csv(db: Session, products) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(COLUMNS)
    for p in products:
        sd = p.supply_details[0] if p.supply_details else None
        inc = next((pr for pr in (sd.prices if sd else []) if pr.price_type == "02"), None)
        def meas(t, u):
            v = next((x.value for x in p.measures if x.measure_type == t and x.unit == u), None)
            return f"{v.normalize():f}" if v is not None else ""
        cov = p.main_cover
        w.writerow([
            p.isbn13 or "", (p.title_prefix + " " if p.title_prefix else "") + p.title_without_prefix, p.subtitle or "",
            p.product_form, p.publication_date.isoformat() if p.publication_date else "",
            f"{p.rrp_gbp:.2f}" if p.rrp_gbp is not None else "", "yes" if inc and inc.tax_rate_code == "S" else "no",
            next((e.value for e in p.extents if e.extent_type in ("00", "05", "10", "11")), ""),
            meas("01", "mm"), meas("02", "mm"), meas("08", "gr"), p.imprint_name or "",
            "; ".join(sl.series.title for sl in p.series_links), "; ".join(sl.sequence_number or "" for sl in p.series_links),
            "; ".join(f"{c.contributor.display_name}|{c.role}" for c in p.contributors),
            next((t.text for t in p.texts if t.text_type == "03"), ""), next((t.text for t in p.texts if t.text_type == "02"), ""),
            ";".join(s.code for s in p.subjects if s.scheme == "93" and s.code), ";".join(s.code for s in p.subjects if s.scheme == "12" and s.code),
            ";".join(s.code for s in p.subjects if s.scheme == "10" and s.code), next((s.heading for s in p.subjects if s.scheme == "20"), "") or "",
            p.publishing_status, next((l.code for l in p.languages), ""), (cov.remote_url or "") if cov else "",
        ])
    return out.getvalue()
