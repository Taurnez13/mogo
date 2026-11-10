"""Round-trip: source ONIX -> DB -> ONIX (reference) -> DB2, then compare key fields product by product."""
import os, sys, tempfile
from pathlib import Path
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import models as m
from app.onix.importer import OnixImporter
from app.onix.exporter import OnixExporter, finalise, serialise, validate

SRC = os.environ.get("ONIX_SRC", str(Path(__file__).resolve().parent.parent / "examples" / "sample_onix30.xml"))


def load(path):
    eng = create_engine("sqlite://")
    m.Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    db = S()
    st = OnixImporter(db).import_file(path)
    assert not st.errors, st.errors[:3]
    return db


def fingerprint(p: m.Product) -> dict:
    sd = p.supply_details[0] if p.supply_details else None
    return {
        "isbn": p.isbn13, "title": p.title, "form": p.product_form,
        "form_details": sorted(f.code for f in p.form_details),
        "status": p.publishing_status, "pubdate": str(p.publication_date), "imprint": p.imprint_name,
        "contribs": sorted((c.contributor.display_name, c.role) for c in p.contributors),
        "subjects": sorted((s.scheme, s.code or s.heading) for s in p.subjects),
        "texts": sorted((t.text_type, len(t.text) > 0) for t in p.texts),
        "prices": sorted((pr.price_type, float(pr.amount), pr.currency, pr.tax_rate_code) for pr in (sd.prices if sd else [])),
        "availability": sd.availability if sd else None,
        "related": sorted((r.relation_codes, r.isbn13) for r in p.related_products),
        "measures": sorted((x.measure_type, float(x.value), x.unit) for x in p.measures if x.value > 0),
        "extents": sorted((e.extent_type, e.value) for e in p.extents),
        "cover": bool(p.main_cover and p.main_cover.remote_url),
        "series": sorted(s.series.title for s in p.series_links),
        "rights": sorted((r.rights_type, r.countries_included, r.regions_included) for r in p.sales_rights),
    }


def main():
    db1 = load(SRC)
    prods = db1.scalars(select(m.Product).order_by(m.Product.id)).all()
    root = OnixExporter(db1, "Test Publisher").build_message(prods)
    ref = finalise(root, "reference")
    errs = validate(ref)
    assert not errs, errs[:5]
    with tempfile.NamedTemporaryFile("wb", suffix=".xml", delete=False) as f:
        f.write(serialise(ref)); tmp = f.name
    db2 = load(tmp)
    fp1 = {p.record_reference: fingerprint(p) for p in prods}
    fp2 = {p.record_reference: fingerprint(p) for p in db2.scalars(select(m.Product)).all()}
    assert set(fp1) == set(fp2), "product sets differ"
    diffs = 0
    for k in fp1:
        for field in fp1[k]:
            if fp1[k][field] != fp2[k][field]:
                diffs += 1
                if diffs <= 10:
                    print("DIFF", k, field, fp1[k][field], "=>", fp2[k][field])
    print(f"products={len(fp1)} field-diffs={diffs}")
    assert diffs == 0
    print("ROUND TRIP OK")


if __name__ == "__main__":
    main()
