"""Recipient profiles: format/status filters, per-recipient deltas, Addressee and Barcode in the output."""
import sys, tempfile
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import models as m
from app.onix.importer import OnixImporter
from app.services import feed_products, build_feed, DEFAULT_SETTINGS

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample_onix30.xml"


def main():
    eng = create_engine("sqlite://"); m.Base.metadata.create_all(eng); db = sessionmaker(bind=eng, expire_on_commit=False)()
    OnixImporter(db).import_file(str(SAMPLE))
    settings = dict(DEFAULT_SETTINGS, sender_name="Test Publisher")
    nielsen = m.Recipient(name="Nielsen", addressee_name="Nielsen BookData", include_barcode=True)
    gardners = m.Recipient(name="Gardners", exclude_digital=True, statuses="02 04", include_barcode=False)
    db.add_all([nielsen, gardners]); db.commit()

    assert len(feed_products(db, nielsen, "full")) == 4
    g = feed_products(db, gardners, "full")
    assert len(g) == 3 and all(p.product_form != "ED" for p in g), "print-only filter"
    # delta: nothing sent yet -> everything; after marking sent -> nothing; after an edit -> one
    assert len(feed_products(db, nielsen, "delta")) == 4
    nielsen.last_sent_at = datetime.utcnow(); db.commit()
    assert feed_products(db, nielsen, "delta") == []
    p = db.scalar(select(m.Product).where(m.Product.product_form == "ED"))
    p.updated_at = datetime.utcnow() + timedelta(seconds=1); db.commit()
    assert [x.id for x in feed_products(db, nielsen, "delta")] == [p.id]
    assert feed_products(db, gardners, "delta") != [] and p.id not in [x.id for x in feed_products(db, gardners, "delta")]

    with tempfile.TemporaryDirectory() as d:
        run, prods = build_feed(db, nielsen, "full", settings, Path(d))
        xml = (Path(d) / run.filename).read_text()
        assert run.valid, run.validation_errors
        assert "<AddresseeName>Nielsen BookData</AddresseeName>" in xml
        assert xml.count("<Barcode>") == 3, "barcode on the 3 physical products only"
        run2, _ = build_feed(db, gardners, "full", settings, Path(d))
        xml2 = (Path(d) / run2.filename).read_text()
        assert run2.valid and "<Addressee>" not in xml2 and "<Barcode>" not in xml2 and xml2.count("<Product>") == 3
    print("RECIPIENTS OK")


if __name__ == "__main__":
    main()
