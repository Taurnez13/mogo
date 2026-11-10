"""
ONIX 3.0 importer. Accepts short-tag or reference-tag ONIX 3.x and loads
products into the database, creating/merging contributors, series and
suppliers. Re-importing the same record updates it in place (matched on
RecordReference).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Iterator, Optional

from lxml import etree
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from .codelists import short_to_ref


def _localname(el) -> str:
    return etree.QName(el).localname


def normalise(el: etree._Element) -> None:
    """Rewrite short tags to reference tags in place so one parser handles both."""
    mp = short_to_ref()
    for e in el.iter():
        if not isinstance(e.tag, str):
            continue
        ln = _localname(e)
        ref = mp.get(ln)
        if ref:
            e.tag = ref
        else:
            e.tag = ln  # strip namespace


def iter_products(path: str) -> Iterator[tuple[dict, etree._Element]]:
    """Stream products from a (possibly large) ONIX file. Yields (header dict, product element)."""
    header: dict = {}
    ctx = etree.iterparse(path, events=("end",), huge_tree=True)
    for _, el in ctx:
        ln = _localname(el)
        if ln in ("header", "Header"):
            normalise(el)
            header = {c.tag: (c.text or "").strip() for c in el.iter() if c.text and c.text.strip()}
        elif ln in ("product", "Product"):
            normalise(el)
            yield header, el
            el.clear()
            while el.getprevious() is not None:
                del el.getparent()[0]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def t(el, path: str) -> Optional[str]:
    """findtext with whitespace strip, None when missing/empty."""
    v = el.findtext(path)
    if v is None:
        return None
    v = v.strip()
    return v or None


def tl(el, path: str) -> list[str]:
    return [x.text.strip() for x in el.findall(path) if x.text and x.text.strip()]


def dec(v: Optional[str]) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def to_date(v: Optional[str], fmt: Optional[str] = "00") -> Optional[date]:
    if not v:
        return None
    v = re.sub(r"\D", "", v)
    try:
        if len(v) >= 8:
            return datetime.strptime(v[:8], "%Y%m%d").date()
        if len(v) == 6:
            return datetime.strptime(v, "%Y%m").date()
        if len(v) == 4:
            return date(int(v), 1, 1)
    except ValueError:
        return None
    return None


def inner_text(el) -> str:
    """Text of a <Text> element preserving any embedded XHTML markup."""
    if el is None:
        return ""
    parts = [el.text or ""]
    for c in el:
        parts.append(etree.tostring(c, encoding="unicode", with_tail=True))
    return "".join(parts).strip()


# --------------------------------------------------------------------------- #
# importer
# --------------------------------------------------------------------------- #

class ImportStats:
    def __init__(self):
        self.created = 0
        self.updated = 0
        self.errors: list[str] = []

    def __repr__(self):
        return f"created={self.created} updated={self.updated} errors={len(self.errors)}"


class OnixImporter:
    def __init__(self, db: Session):
        self.db = db
        self._contrib_cache: dict[str, m.Contributor] = {}
        self._series_cache: dict[str, m.Series] = {}
        self._supplier_cache: dict[str, m.Supplier] = {}
        self.stats = ImportStats()

    # ---- master-data lookups ------------------------------------------------

    def _series(self, title: str, ctype: str, legacy_id: Optional[str]) -> m.Series:
        key = title.strip().lower()
        if key in self._series_cache:
            return self._series_cache[key]
        s = self.db.scalar(select(m.Series).where(m.Series.title == title.strip()))
        if not s:
            s = m.Series(title=title.strip(), collection_type=ctype or "10", legacy_id=legacy_id)
            self.db.add(s)
            self.db.flush()
        self._series_cache[key] = s
        return s

    def _supplier(self, el) -> m.Supplier:
        name = t(el, "SupplierName") or "Unknown supplier"
        if name in self._supplier_cache:
            return self._supplier_cache[name]
        s = self.db.scalar(select(m.Supplier).where(m.Supplier.name == name))
        if not s:
            sid = el.find("SupplierIdentifier")
            s = m.Supplier(
                name=name,
                role=t(el, "SupplierRole") or "01",
                id_type=t(sid, "SupplierIDType") if sid is not None else None,
                id_type_name=t(sid, "IDTypeName") if sid is not None else None,
                id_value=t(sid, "IDValue") if sid is not None else None,
                telephone=t(el, "TelephoneNumber"),
                email=t(el, "EmailAddress"),
                website=t(el, "Website/WebsiteLink"),
                website_role=t(el, "Website/WebsiteRole"),
            )
            self.db.add(s)
            self.db.flush()
        self._supplier_cache[name] = s
        return s

    def _contributor(self, el) -> m.Contributor:
        legacy = None
        isni = None
        for ni in el.findall("NameIdentifier"):
            typ, val = t(ni, "NameIDType"), t(ni, "IDValue")
            if typ == "01":
                legacy = val
            elif typ == "16":
                isni = val
        corporate = t(el, "CorporateName")
        person = t(el, "PersonName")
        inverted = t(el, "PersonNameInverted")
        key = legacy or f"name:{(corporate or inverted or person or '').lower()}"
        if key in self._contrib_cache:
            c = self._contrib_cache[key]
        else:
            c = None
            if legacy:
                c = self.db.scalar(select(m.Contributor).where(m.Contributor.legacy_id == legacy))
            if not c and corporate:
                c = self.db.scalar(select(m.Contributor).where(m.Contributor.corporate_name == corporate))
            if not c and inverted:
                c = self.db.scalar(select(m.Contributor).where(m.Contributor.person_name_inverted == inverted))
            if not c:
                c = m.Contributor()
                self.db.add(c)
            self._contrib_cache[key] = c
        # update master fields from this occurrence (fills gaps, keeps latest)
        c.is_corporate = bool(corporate)
        c.corporate_name = corporate or c.corporate_name
        c.person_name = person or c.person_name
        c.person_name_inverted = inverted or c.person_name_inverted
        c.titles_before_names = t(el, "TitlesBeforeNames") or c.titles_before_names
        c.names_before_key = t(el, "NamesBeforeKey") or c.names_before_key
        c.prefix_to_key = t(el, "PrefixToKey") or c.prefix_to_key
        c.key_names = t(el, "KeyNames") or c.key_names
        c.names_after_key = t(el, "NamesAfterKey") or c.names_after_key
        c.suffix_to_key = t(el, "SuffixToKey") or c.suffix_to_key
        c.letters_after_names = t(el, "LettersAfterNames") or c.letters_after_names
        c.titles_after_names = t(el, "TitlesAfterNames") or c.titles_after_names
        bio = t(el, "BiographicalNote")
        if bio:
            c.biographical_note = bio
        c.website = t(el, "Website/WebsiteLink") or c.website
        c.legacy_id = legacy or c.legacy_id
        c.isni = isni or c.isni
        if c.id is None:
            self.db.flush()
        if not c.places:
            for pl in el.findall("ContributorPlace"):
                c.places.append(m.ContributorPlace(
                    relator=t(pl, "ContributorPlaceRelator") or "00",
                    country_code=t(pl, "CountryCode"),
                    region_code=t(pl, "RegionCode"),
                    location_name=t(pl, "LocationName"),
                ))
        if not c.affiliations:
            for af in el.findall("ProfessionalAffiliation"):
                c.affiliations.append(m.ContributorAffiliation(
                    professional_position=t(af, "ProfessionalPosition"),
                    affiliation=t(af, "Affiliation"),
                ))
        return c

    # ---- main ---------------------------------------------------------------

    def import_product(self, el) -> m.Product:
        ref = t(el, "RecordReference")
        if not ref:
            raise ValueError("Product without RecordReference")
        p = self.db.scalar(select(m.Product).where(m.Product.record_reference == ref))
        if p is None:
            p = m.Product(record_reference=ref)
            self.db.add(p)
            self.stats.created += 1
        else:
            self.stats.updated += 1
            # wipe child collections; we rebuild from the record
            for coll in ("identifiers", "measures", "classifications", "form_details", "form_features",
                         "parts", "series_links", "contributors", "languages", "extents", "subjects",
                         "audiences", "audience_ranges", "ancillary_content", "texts", "resources",
                         "publishers", "publishing_dates", "sales_rights", "related_works",
                         "related_products", "supply_details"):
                getattr(p, coll).clear()

        extra: dict = {}
        p.notification_type = t(el, "NotificationType") or "03"
        p.record_source_type = t(el, "RecordSourceType")
        rsi = el.find("RecordSourceIdentifier")
        if rsi is not None:
            extra["record_source_identifier"] = {
                "type": t(rsi, "RecordSourceIDType"), "name": t(rsi, "IDTypeName"), "value": t(rsi, "IDValue")}

        # identifiers
        for pi in el.findall("ProductIdentifier"):
            typ, val = t(pi, "ProductIDType"), t(pi, "IDValue")
            if not (typ and val):
                continue
            p.identifiers.append(m.ProductIdentifier(id_type=typ, id_type_name=t(pi, "IDTypeName"), value=val))
            if typ == "15":
                p.isbn13 = val
            elif typ == "03":
                p.gtin13 = val
            elif typ == "02":
                p.isbn10 = val
        if not p.isbn13 and p.gtin13 and p.gtin13.startswith(("978", "979")):
            p.isbn13 = p.gtin13

        # ---- DescriptiveDetail
        dd = el.find("DescriptiveDetail")
        if dd is not None:
            p.product_composition = t(dd, "ProductComposition") or "00"
            p.product_form = t(dd, "ProductForm") or "00"
            for code in tl(dd, "ProductFormDetail"):
                if code not in [f.code for f in p.form_details]:
                    p.form_details.append(m.ProductFormDetail(code=code))
            for ff in dd.findall("ProductFormFeature"):
                p.form_features.append(m.ProductFormFeature(
                    feature_type=t(ff, "ProductFormFeatureType") or "", feature_value=t(ff, "ProductFormFeatureValue"),
                    feature_description=t(ff, "ProductFormFeatureDescription")))
            p.product_form_description = t(dd, "ProductFormDescription")
            p.product_packaging = t(dd, "ProductPackaging")
            p.trade_category = t(dd, "TradeCategory")
            p.primary_content_type = t(dd, "PrimaryContentType")
            for ms in dd.findall("Measure"):
                v = dec(t(ms, "Measurement"))
                if v is not None:
                    p.measures.append(m.Measure(measure_type=t(ms, "MeasureType") or "", value=v, unit=t(ms, "MeasureUnitCode") or "mm"))
            p.country_of_manufacture = t(dd, "CountryOfManufacture")
            for pc in dd.findall("ProductClassification"):
                p.classifications.append(m.ProductClassification(
                    classification_type=t(pc, "ProductClassificationType") or "", code=t(pc, "ProductClassificationCode") or ""))
            for pp in dd.findall("ProductPart"):
                n = t(pp, "NumberOfItemsOfThisForm")
                p.parts.append(m.ProductPart(
                    product_form=t(pp, "ProductForm") or "00", product_form_description=t(pp, "ProductFormDescription"),
                    number_of_items=int(n) if n and n.isdigit() else None,
                    part_isbn13=next((t(x, "IDValue") for x in pp.findall("ProductIdentifier") if t(x, "ProductIDType") == "15"), None)))
            # collections
            p.no_collection = dd.find("NoCollection") is not None
            for col in dd.findall("Collection"):
                title = t(col, "TitleDetail/TitleElement/TitleText") or t(col, "TitleDetail/TitleElement/TitleWithoutPrefix")
                if not title:
                    continue
                legacy = next((t(ci, "IDValue") for ci in col.findall("CollectionIdentifier")), None)
                s = self._series(title, t(col, "CollectionType") or "10", legacy)
                seq = col.find("CollectionSequence")
                p.series_links.append(m.ProductSeries(
                    series=s,
                    sequence_type=t(seq, "CollectionSequenceType") if seq is not None else None,
                    sequence_number=t(seq, "CollectionSequenceNumber") if seq is not None else None))
            # title
            td = dd.find("TitleDetail")
            if td is not None:
                p.title_type = t(td, "TitleType") or "01"
                te = next((x for x in td.findall("TitleElement") if t(x, "TitleElementLevel") == "01"), td.find("TitleElement"))
                if te is not None:
                    p.title_prefix = t(te, "TitlePrefix")
                    p.title_without_prefix = t(te, "TitleWithoutPrefix") or t(te, "TitleText") or ""
                    p.subtitle = t(te, "Subtitle")
            p.title = p.compute_title()
            # contributors
            p.no_contributor = dd.find("NoContributor") is not None
            for i, ce in enumerate(dd.findall("Contributor"), start=1):
                c = self._contributor(ce)
                seq = t(ce, "SequenceNumber")
                roles = tl(ce, "ContributorRole") or ["A01"]
                for role in roles:
                    p.contributors.append(m.ProductContributor(
                        contributor=c, sequence=int(seq) if seq and seq.isdigit() else i, role=role,
                        biographical_note=t(ce, "BiographicalNote")))
            p.contributor_statement = t(dd, "ContributorStatement")
            # edition
            p.no_edition = dd.find("NoEdition") is not None
            p.edition_type = t(dd, "EditionType")
            en = t(dd, "EditionNumber")
            p.edition_number = int(en) if en and en.isdigit() else None
            p.edition_statement = t(dd, "EditionStatement")
            for lg in dd.findall("Language"):
                p.languages.append(m.Language(role=t(lg, "LanguageRole") or "01", code=t(lg, "LanguageCode") or "eng", country_code=t(lg, "CountryCode")))
            for ex in dd.findall("Extent"):
                p.extents.append(m.Extent(extent_type=t(ex, "ExtentType") or "", value=t(ex, "ExtentValue") or "", unit=t(ex, "ExtentUnit") or "03"))
            p.illustrations_note = t(dd, "IllustrationsNote")
            for ac in dd.findall("AncillaryContent"):
                n = t(ac, "Number")
                p.ancillary_content.append(m.AncillaryContent(content_type=t(ac, "AncillaryContentType") or "", number=int(n) if n and n.isdigit() else None, description=t(ac, "AncillaryContentDescription")))
            for sj in dd.findall("Subject"):
                p.subjects.append(m.Subject(
                    main_subject=sj.find("MainSubject") is not None,
                    scheme=t(sj, "SubjectSchemeIdentifier") or "", scheme_name=t(sj, "SubjectSchemeName"),
                    scheme_version=t(sj, "SubjectSchemeVersion"), code=t(sj, "SubjectCode"), heading=t(sj, "SubjectHeadingText")))
            for au in dd.findall("Audience"):
                p.audiences.append(m.Audience(code_type=t(au, "AudienceCodeType") or "01", code_value=t(au, "AudienceCodeValue") or ""))
            for ar in dd.findall("AudienceRange"):
                precs = tl(ar, "AudienceRangePrecision"); vals = tl(ar, "AudienceRangeValue")
                p.audience_ranges.append(m.AudienceRange(
                    qualifier=t(ar, "AudienceRangeQualifier") or "",
                    precision_from=precs[0] if precs else None, value_from=vals[0] if vals else None,
                    precision_to=precs[1] if len(precs) > 1 else None, value_to=vals[1] if len(vals) > 1 else None))

        # ---- CollateralDetail
        cd = el.find("CollateralDetail")
        if cd is not None:
            for tc in cd.findall("TextContent"):
                te = tc.find("Text")
                txt = inner_text(te)
                if not txt:
                    continue
                p.texts.append(m.TextContent(
                    text_type=t(tc, "TextType") or "03", content_audience=t(tc, "ContentAudience") or "00",
                    text_format=(te.get("textformat") if te is not None else None) or "02",
                    text=txt, text_author=t(tc, "TextAuthor"), source_title=t(tc, "SourceTitle")))
            for sr in cd.findall("SupportingResource"):
                rv = sr.find("ResourceVersion")
                feats = {}
                fmt = fname = link = None
                cdate = cdrole = None
                if rv is not None:
                    link = t(rv, "ResourceLink")
                    for f in rv.findall("ResourceVersionFeature"):
                        ft, fv = t(f, "ResourceVersionFeatureType"), t(f, "FeatureValue")
                        if ft == "01":
                            fmt = fv
                        elif ft == "04":
                            fname = fv
                        else:
                            feats[ft or "?"] = fv
                    cdate = to_date(t(rv, "ContentDate/Date")); cdrole = t(rv, "ContentDate/ContentDateRole")
                for f in sr.findall("ResourceFeature"):
                    feats["rf:" + (t(f, "ResourceFeatureType") or "?")] = t(f, "FeatureValue") or t(f, "FeatureNote")
                p.resources.append(m.SupportingResource(
                    content_type=t(sr, "ResourceContentType") or "01", content_audience=t(sr, "ContentAudience") or "00",
                    resource_mode=t(sr, "ResourceMode") or "03", resource_form=t(rv, "ResourceForm") if rv is not None else "02",
                    file_format=fmt, filename=fname, remote_url=link, content_date=cdate, content_date_role=cdrole or "17",
                    features=feats or None))

        # ---- PublishingDetail
        pd_ = el.find("PublishingDetail")
        if pd_ is not None:
            p.imprint_name = t(pd_, "Imprint/ImprintName")
            for pb in pd_.findall("Publisher"):
                p.publishers.append(m.Publisher(role=t(pb, "PublishingRole") or "01", name=t(pb, "PublisherName") or "",
                                                website=t(pb, "Website/WebsiteLink"), website_role=t(pb, "Website/WebsiteRole")))
            p.city_of_publication = t(pd_, "CityOfPublication")
            p.country_of_publication = t(pd_, "CountryOfPublication")
            p.publishing_status = t(pd_, "PublishingStatus") or "02"
            for pdt in pd_.findall("PublishingDate"):
                d = pdt.find("Date")
                role = t(pdt, "PublishingDateRole") or "01"
                val = t(pdt, "Date") or ""
                p.publishing_dates.append(m.PublishingDate(role=role, date_format=(d.get("dateformat") if d is not None else None) or "00", value=val))
                if role == "01":
                    p.publication_date = to_date(val)
            years = []
            for cs in pd_.findall("CopyrightStatement"):
                years += tl(cs, "CopyrightYear")
            p.copyright_year = " ".join(years) or None
            for sr in pd_.findall("SalesRights"):
                tr = sr.find("Territory")
                p.sales_rights.append(m.SalesRights(
                    rights_type=t(sr, "SalesRightsType") or "01",
                    countries_included=t(tr, "CountriesIncluded") if tr is not None else None,
                    regions_included=t(tr, "RegionsIncluded") if tr is not None else None,
                    countries_excluded=t(tr, "CountriesExcluded") if tr is not None else None,
                    regions_excluded=t(tr, "RegionsExcluded") if tr is not None else None))
            p.row_sales_rights_type = t(pd_, "ROWSalesRightsType")

        # ---- RelatedMaterial
        rm = el.find("RelatedMaterial")
        if rm is not None:
            for rw in rm.findall("RelatedWork"):
                for wi in rw.findall("WorkIdentifier"):
                    p.related_works.append(m.RelatedWork(relation_code=t(rw, "WorkRelationCode") or "01",
                                                         id_type=t(wi, "WorkIDType") or "01", id_type_name=t(wi, "IDTypeName"), value=t(wi, "IDValue") or ""))
            for rp in rm.findall("RelatedProduct"):
                isbn = None; others = []
                for pi in rp.findall("ProductIdentifier"):
                    typ, val = t(pi, "ProductIDType"), t(pi, "IDValue")
                    if typ == "15":
                        isbn = val
                    elif typ == "03" and not isbn and val and val.startswith("978"):
                        isbn = val
                    else:
                        others.append({"type": typ, "name": t(pi, "IDTypeName"), "value": val})
                p.related_products.append(m.RelatedProduct(
                    relation_codes=" ".join(tl(rp, "ProductRelationCode")) or "06", isbn13=isbn,
                    other_identifiers=others or None, product_form=t(rp, "ProductForm"), product_form_detail=t(rp, "ProductFormDetail")))

        # ---- ProductSupply
        p.rrp_gbp = None
        for ps in el.findall("ProductSupply"):
            mk = ps.find("Market/Territory")
            mpd = ps.find("MarketPublishingDetail")
            for sd in ps.findall("SupplyDetail"):
                sup = self._supplier(sd.find("Supplier")) if sd.find("Supplier") is not None else self._supplier(etree.Element("Supplier"))
                pq = t(sd, "PackQuantity")
                s = m.SupplyDetail(
                    supplier=sup,
                    market_countries=t(mk, "CountriesIncluded") if mk is not None else None,
                    market_regions=t(mk, "RegionsIncluded") if mk is not None else None,
                    market_publishing_status=t(mpd, "MarketPublishingStatus") if mpd is not None else None,
                    promotion_campaign=t(mpd, "PromotionCampaign") if mpd is not None else None,
                    availability=t(sd, "ProductAvailability") or "20",
                    pack_quantity=int(pq) if pq and pq.isdigit() else None)
                for sdt in sd.findall("SupplyDate"):
                    d = sdt.find("Date")
                    s.supply_dates.append(m.SupplyDate(role=t(sdt, "SupplyDateRole") or "08",
                                                       date_format=(d.get("dateformat") if d is not None else None) or "00", value=t(sdt, "Date") or ""))
                for pr in sd.findall("Price"):
                    amt = dec(t(pr, "PriceAmount"))
                    if amt is None:
                        continue
                    tx = pr.find("Tax")
                    tr = pr.find("Territory")
                    price = m.Price(
                        price_type=t(pr, "PriceType") or "02", price_status=t(pr, "PriceStatus"), amount=amt,
                        currency=t(pr, "CurrencyCode") or "GBP",
                        tax_rate_code=t(tx, "TaxRateCode") if tx is not None else None,
                        tax_rate_percent=dec(t(tx, "TaxRatePercent")) if tx is not None else None,
                        taxable_amount=dec(t(tx, "TaxableAmount")) if tx is not None else None,
                        tax_amount=dec(t(tx, "TaxAmount")) if tx is not None else None,
                        countries_included=t(tr, "CountriesIncluded") if tr is not None else None,
                        regions_included=t(tr, "RegionsIncluded") if tr is not None else None)
                    s.prices.append(price)
                    if p.rrp_gbp is None and price.price_type == "02":
                        p.rrp_gbp = amt
                p.supply_details.append(s)

        p.extra = extra or None
        p.updated_at = datetime.utcnow()
        return p

    def import_file(self, path: str, commit_every: int = 200, progress=None) -> ImportStats:
        n = 0
        for header, el in iter_products(path):
            try:
                self.import_product(el)
            except Exception as e:  # keep going; report
                self.stats.errors.append(f"{t(el, 'RecordReference')}: {e}")
                self.db.rollback()
                continue
            n += 1
            if n % commit_every == 0:
                self.db.commit()
                if progress:
                    progress(n)
        self.db.commit()
        return self.stats
