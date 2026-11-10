"""
Cin7 Core (formerly DEAR) connector.

API docs: https://dearinventory.docs.apiary.io/
Auth: two headers — api-auth-accountid and api-auth-applicationkey — created in
Cin7 Core under Integrations > API.  Rate limit is 60 calls/minute, so we page
at the maximum page size (1000) and sleep between calls.

Matching rule: Cin7 Barcode == ISBN-13 (fallback: SKU == ISBN-13).
"""
from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from typing import Iterator, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..services import set_rrp, get_settings

BASE = "https://inventory.dearsystems.com/ExternalApi/v2"


class Cin7Client:
    def __init__(self, account_id: str, api_key: str, timeout: float = 60.0):
        if not (account_id and api_key):
            raise RuntimeError("Cin7 Core credentials are not set — add them under Settings.")
        self.h = httpx.Client(base_url=BASE, timeout=timeout, headers={
            "api-auth-accountid": account_id, "api-auth-applicationkey": api_key,
            "Content-Type": "application/json", "Accept": "application/json"})

    def _get(self, path: str, **params) -> dict:
        for attempt in range(4):
            r = self.h.get(path, params=params)
            if r.status_code == 503 or r.status_code == 429:  # throttled
                time.sleep(15 * (attempt + 1)); continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()
        return {}

    def products(self) -> Iterator[dict]:
        page = 1
        while True:
            data = self._get("/product", Page=page, Limit=1000, IncludeDeprecated="false")
            items = data.get("Products", [])
            yield from items
            if len(items) < 1000:
                break
            page += 1
            time.sleep(1.1)

    def availability(self) -> Iterator[dict]:
        page = 1
        while True:
            data = self._get("/ref/productavailability", Page=page, Limit=1000)
            items = data.get("ProductAvailabilityList", [])
            yield from items
            if len(items) < 1000:
                break
            page += 1
            time.sleep(1.1)

    def create_product(self, body: dict) -> dict:
        r = self.h.post("/product", json=body); r.raise_for_status(); return r.json()

    def update_product(self, body: dict) -> dict:
        r = self.h.put("/product", json=body); r.raise_for_status(); return r.json()


def _link(db: Session, p: m.Product, system: str) -> m.ExternalLink:
    for l in p.external_ids:
        if l.system == system:
            return l
    l = m.ExternalLink(system=system, external_id="")
    p.external_ids.append(l)
    return l


def pull(db: Session, client: Cin7Client) -> str:
    """Match Cin7 products to titles by barcode/SKU, store a snapshot (SKU, price tiers, stock, status)."""
    by_isbn = {p.isbn13: p for p in db.scalars(select(m.Product).where(m.Product.isbn13.isnot(None)))}
    matched = unmatched = 0
    unmatched_examples = []
    cin7_by_sku: dict[str, m.ExternalLink] = {}
    for item in client.products():
        key = (item.get("Barcode") or "").strip() or (item.get("SKU") or "").strip()
        p = by_isbn.get(key) or by_isbn.get((item.get("SKU") or "").strip())
        if not p:
            unmatched += 1
            if len(unmatched_examples) < 10:
                unmatched_examples.append(f"{item.get('SKU')} {item.get('Name', '')[:40]}")
            continue
        l = _link(db, p, "cin7")
        l.external_id = item.get("ID", "")
        l.secondary_id = item.get("SKU")
        l.last_pulled_at = datetime.utcnow()
        l.snapshot = {
            "name": item.get("Name"), "sku": item.get("SKU"), "barcode": item.get("Barcode"),
            "status": item.get("Status"), "category": item.get("Category"), "brand": item.get("Brand"),
            "price_tier1": item.get("PriceTier1"), "price_tiers": {k: v for k, v in item.items() if k.startswith("PriceTier")},
            "average_cost": item.get("AverageCost"), "weight": item.get("Weight"),
            "revenue_account": item.get("RevenueAccount"), "cogs_account": item.get("COGSAccount"),
            "inventory_account": item.get("InventoryAccount"), "tax_rule": item.get("SaleTaxRule"),
        }
        cin7_by_sku[item.get("SKU", "")] = l
        matched += 1
    # stock
    stocked = 0
    for av in client.availability():
        l = cin7_by_sku.get(av.get("SKU", ""))
        if l is None:
            continue
        snap = dict(l.snapshot or {})
        stock = snap.setdefault("stock", {})
        stock[av.get("Location", "?")] = {"on_hand": av.get("OnHand"), "available": av.get("Available"), "on_order": av.get("OnOrder")}
        snap["stock_total_available"] = sum((v.get("available") or 0) for v in stock.values())
        l.snapshot = snap
        stocked += 1
    db.commit()
    msg = f"Cin7 pull: {matched} titles matched, {unmatched} Cin7 products not in catalogue, {stocked} stock rows."
    if unmatched_examples:
        msg += " Unmatched e.g. " + "; ".join(unmatched_examples)
    return msg


def push(db: Session, client: Cin7Client, dry_run: bool = True) -> str:
    """Create missing Cin7 products for titles (SKU = ISBN, Barcode = ISBN) and update name/price on linked ones."""
    s = get_settings(db)
    created = updated = 0
    actions = []
    prods = db.scalars(select(m.Product).where(m.Product.isbn13.isnot(None), m.Product.publishing_status.in_(["02", "04"]))).all()
    for p in prods:
        l = next((x for x in p.external_ids if x.system == "cin7"), None)
        body = {
            "SKU": p.isbn13, "Name": p.title[:256], "Barcode": p.isbn13, "Category": "Books",
            "Type": "Stock", "CostingMethod": "FIFO", "UOM": "Item", "Status": "Active",
            "PriceTier1": float(p.rrp_gbp) if p.rrp_gbp else 0.0,
            "Weight": float(next((x.value for x in p.measures if x.measure_type == "08" and x.unit == "gr"), 0) or 0),
        }
        if p.product_form == "ED":
            body["Type"] = "Service"  # e-books carry no stock
        if l and l.external_id:
            snap = l.snapshot or {}
            if snap.get("name") != body["Name"] or (snap.get("price_tier1") or 0) != body["PriceTier1"]:
                actions.append(f"update {p.isbn13} {p.title[:40]}")
                if not dry_run:
                    client.update_product({"ID": l.external_id, "Name": body["Name"], "PriceTier1": body["PriceTier1"]})
                    l.last_pushed_at = datetime.utcnow()
                updated += 1
        else:
            actions.append(f"create {p.isbn13} {p.title[:40]}")
            if not dry_run:
                res = client.create_product(body)
                nl = _link(db, p, "cin7")
                nl.external_id = (res.get("Products") or [{}])[0].get("ID", "") if isinstance(res, dict) else ""
                nl.secondary_id = p.isbn13
                nl.last_pushed_at = datetime.utcnow()
                time.sleep(1.1)
            created += 1
    db.commit()
    prefix = "DRY RUN — " if dry_run else ""
    return f"{prefix}Cin7 push: {created} to create, {updated} to update. " + ("; ".join(actions[:8]) + (" …" if len(actions) > 8 else ""))
