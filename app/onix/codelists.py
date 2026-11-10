"""ONIX code list lookups, built from the EDItEUR schema bundle."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data"


@lru_cache(maxsize=1)
def _lists() -> dict[str, dict[str, str]]:
    return json.loads((DATA / "codelists.json").read_text())


@lru_cache(maxsize=1)
def short_to_ref() -> dict[str, str]:
    return json.loads((DATA / "short_to_ref.json").read_text())


@lru_cache(maxsize=1)

def ref_to_short() -> dict[str, str]:
    return {v: k for k, v in short_to_ref().items()}


def label(list_no: int, code: str | None, default: str | None = None) -> str:
    if code is None:
        return default or ""
    return _lists().get(f"List{list_no}", {}).get(code, default if default is not None else code)


def options(list_no: int) -> list[tuple[str, str]]:
    """[(code, label)] sorted by code — for <select> boxes."""
    d = _lists().get(f"List{list_no}", {})
    return sorted(d.items())


# Friendly aliases for the lists the UI uses most
LISTS = {
    "notification_type": 1,
    "product_composition": 2,
    "product_id_type": 5,
    "trade_category": 12,
    "title_type": 15,
    "contributor_role": 17,
    "edition_type": 21,
    "language_role": 22,
    "extent_type": 23,
    "extent_unit": 24,
    "subject_scheme": 27,
    "audience_code_type": 29,
    "audience_range_qualifier": 30,
    "text_format": 34,
    "publishing_role": 45,
    "sales_rights_type": 46,
    "measure_type": 48,
    "measure_unit": 50,
    "product_relation": 51,
    "price_type": 58,
    "price_status": 61,
    "tax_rate_code": 62,
    "publishing_status": 64,
    "product_availability": 65,
    "language_code": 74,
    "primary_content_type": 81,
    "country": 91,
    "supplier_role": 93,
    "product_form": 150,
    "text_type": 153,
    "content_audience": 154,
    "resource_content_type": 158,
    "resource_mode": 159,
    "resource_form": 161,
    "publishing_date_role": 163,
    "supply_date_role": 166,
    "product_form_detail": 175,
    "file_format": 178,
}
