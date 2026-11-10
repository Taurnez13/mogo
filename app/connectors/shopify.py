"""
Shopify connector (Admin GraphQL API).

Setup in Shopify admin: Settings > Apps and sales channels > Develop apps > Create app,
grant read_products / write_products (and read_inventory), install, copy the Admin API
access token into Settings here together with the shop domain (xxx.myshopify.com).

Matching rule: variant barcode == ISBN-13 (fallback: variant SKU == ISBN-13).
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Iterator, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..services import get_settings

API_VERSION = "2025-10"

PRODUCTS_QUERY = """
query($cursor: String) {
  products(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title handle status descriptionHtml vendor productType tags
      featuredMedia { preview { image { url } } }
      variants(first: 10) { nodes { id sku barcode price compareAtPrice inventoryQuantity } }
    }
  }
}
"""


class ShopifyClient:
    def __init__(self, shop: str, token: str, timeout: float = 60.0):
        if not (shop and token):
            raise RuntimeError("Shopify credentials are not set — add them under Settings.")
        shop = shop.replace("https://", "").strip("/")
        self.url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
        self.h = httpx.Client(timeout=timeout, headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"})

    def gql(self, query: str, variables: Optional[dict] = None) -> dict:
        for attempt in range(5):
            r = self.h.post(self.url, json={"query": query, "variables": variables or {}})
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1)); continue
            r.raise_for_status()
            data = r.json()
            if data.get("errors"):
                if any("THROTTLED" in str(e.get("extensions", {}).get("code", "")) for e in data["errors"]):
                    time.sleep(2 * (attempt + 1)); continue
                raise RuntimeError(str(data["errors"])[:500])
            return data["data"]
        raise RuntimeError("Shopify API throttled repeatedly")

    def products(self) -> Iterator[dict]:
        cursor = None
        while True:
            d = self.gql(PRODUCTS_QUERY, {"cursor": cursor})["products"]
            yield from d["nodes"]
            if not d["pageInfo"]["hasNextPage"]:
                break
            cursor = d["pageInfo"]["endCursor"]

    def update_product(self, product_id: str, title: str, description_html: str, vendor: str) -> dict:
        q = """mutation($input: ProductInput!){ productUpdate(input:$input){ product{id} userErrors{field message} } }"""
        d = self.gql(q, {"input": {"id": product_id, "title": title, "descriptionHtml": description_html, "vendor": vendor}})
        if d["productUpdate"]["userErrors"]:
            raise RuntimeError(str(d["productUpdate"]["userErrors"]))
        return d

    def update_variant_price(self, product_id: str, variant_id: str, price: str) -> dict:
        q = """mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!){
                 productVariantsBulkUpdate(productId:$productId, variants:$variants){ userErrors{field message} } }"""
        d = self.gql(q, {"productId": product_id, "variants": [{"id": variant_id, "price": price}]})
        if d["productVariantsBulkUpdate"]["userErrors"]:
            raise RuntimeError(str(d["productVariantsBulkUpdate"]["userErrors"]))
        return d

    def create_product(self, title: str, description_html: str, vendor: str, barcode: str, price: str, product_type: str) -> dict:
        q = """mutation($input: ProductInput!){ productCreate(input:$input){ product{ id variants(first:1){nodes{id}} } userErrors{field message} } }"""
        d = self.gql(q, {"input": {"title": title, "descriptionHtml": description_html, "vendor": vendor,
                                   "productType": product_type, "status": "DRAFT"}})
        pc = d["productCreate"]
        if pc["userErrors"]:
            raise RuntimeError(str(pc["userErrors"]))
        pid = pc["product"]["id"]; vid = pc["product"]["variants"]["nodes"][0]["id"]
        q2 = """mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!){
                  productVariantsBulkUpdate(productId:$productId, variants:$variants){ userErrors{field message} } }"""
        self.gql(q2, {"productId": pid, "variants": [{"id": vid, "price": price, "barcode": barcode,
                                                     "inventoryItem": {"sku": barcode, "tracked": True}}]})
        return {"id": pid, "variant_id": vid}

    def add_image(self, product_id: str, url: str, alt: str) -> None:
        q = """mutation($productId: ID!, $media: [CreateMediaInput!]!){
                 productCreateMedia(productId:$productId, media:$media){ mediaUserErrors{field message} } }"""
        self.gql(q, {"productId": product_id, "media": [{"originalSource": url, "alt": alt, "mediaContentType": "IMAGE"}]})


def _link(db: Session, p: m.Product) -> m.ExternalLink:
    for l in p.external_ids:
        if l.system == "shopify":
            return l
    l = m.ExternalLink(system="shopify", external_id="")
    p.external_ids.append(l)
    return l


def pull(db: Session, client: ShopifyClient) -> str:
    by_isbn = {p.isbn13: p for p in db.scalars(select(m.Product).where(m.Product.isbn13.isnot(None)))}
    matched = unmatched = 0
    examples = []
    for prod in client.products():
        hit = None
        for v in prod["variants"]["nodes"]:
            key = (v.get("barcode") or "").strip() or (v.get("sku") or "").strip()
            if key in by_isbn:
                hit = (by_isbn[key], v); break
            key2 = (v.get("sku") or "").strip()
            if key2 in by_isbn:
                hit = (by_isbn[key2], v); break
        if not hit:
            unmatched += 1
            if len(examples) < 10:
                examples.append(prod["title"][:40])
            continue
        p, v = hit
        l = _link(db, p)
        l.external_id = prod["id"]; l.secondary_id = v["id"]; l.last_pulled_at = datetime.utcnow()
        img = (prod.get("featuredMedia") or {}).get("preview", {}).get("image", {}) or {}
        l.snapshot = {"title": prod["title"], "handle": prod["handle"], "status": prod["status"], "vendor": prod["vendor"],
                      "product_type": prod["productType"], "tags": prod["tags"], "price": v.get("price"),
                      "compare_at": v.get("compareAtPrice"), "inventory": v.get("inventoryQuantity"),
                      "sku": v.get("sku"), "barcode": v.get("barcode"), "image": img.get("url"),
                      "description_len": len(prod.get("descriptionHtml") or "")}
        matched += 1
    db.commit()
    msg = f"Shopify pull: {matched} titles matched, {unmatched} Shopify products not in catalogue."
    if examples:
        msg += " Unmatched e.g. " + "; ".join(examples)
    return msg


def push(db: Session, client: ShopifyClient, dry_run: bool = True) -> str:
    """Push title, description, vendor and price to linked Shopify products; create drafts for active titles with none."""
    s = get_settings(db)
    updated = created = 0
    actions = []
    prods = db.scalars(select(m.Product).where(m.Product.isbn13.isnot(None), m.Product.publishing_status.in_(["02", "04"]))).all()
    for p in prods:
        if p.product_form == "ED":
            continue  # ebooks are not sold through the Shopify store
        l = next((x for x in p.external_ids if x.system == "shopify"), None)
        desc = p.main_description or ""
        price = f"{p.rrp_gbp:.2f}" if p.rrp_gbp else "0.00"
        vendor = p.imprint_name or s["default_publisher"]
        if l and l.external_id:
            snap = l.snapshot or {}
            if snap.get("title") != p.title or snap.get("price") != price:
                actions.append(f"update {p.isbn13} {p.title[:40]}")
                if not dry_run:
                    client.update_product(l.external_id, p.title, desc, vendor)
                    if l.secondary_id and snap.get("price") != price:
                        client.update_variant_price(l.external_id, l.secondary_id, price)
                    l.last_pushed_at = datetime.utcnow()
                updated += 1
        else:
            actions.append(f"create draft {p.isbn13} {p.title[:40]}")
            if not dry_run:
                res = client.create_product(p.title, desc, vendor, p.isbn13, price, "Book")
                nl = _link(db, p); nl.external_id = res["id"]; nl.secondary_id = res["variant_id"]; nl.last_pushed_at = datetime.utcnow()
                cover = p.main_cover
                base = (s.get("media_base_url") or "").rstrip("/")
                url = f"{base}/media/{cover.local_path}" if (cover and cover.local_path and base) else (cover.remote_url if cover else None)
                if url:
                    client.add_image(res["id"], url, p.title)
            created += 1
    db.commit()
    prefix = "DRY RUN — " if dry_run else ""
    return f"{prefix}Shopify push: {created} drafts to create, {updated} to update. " + ("; ".join(actions[:8]) + (" …" if len(actions) > 8 else ""))
