"""
MOGO — title metadata database.

Design: a normalised relational model that mirrors the ONIX 3.0 block structure
closely enough to round-trip a full publisher feed without loss, while keeping
the common fields (title, ISBN, form, status, price) as first-class columns
for searching and editing.

Code values are stored as ONIX codes (e.g. ProductForm 'BC'); labels come from
app.onix.codelists at display time.
"""
from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List

from sqlalchemy import (
    String, Integer, Text, Boolean, ForeignKey, DateTime, Date, Numeric,
    UniqueConstraint, Index, JSON, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.utcnow()


# --------------------------------------------------------------------------- #
# Core product
# --------------------------------------------------------------------------- #

class Product(Base):
    __tablename__ = "product"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ONIX RecordReference — defaults to the ISBN-13 but kept
    # independent so a record can survive an ISBN correction.
    record_reference: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    isbn13: Mapped[Optional[str]] = mapped_column(String(13), index=True)
    isbn10: Mapped[Optional[str]] = mapped_column(String(10))
    gtin13: Mapped[Optional[str]] = mapped_column(String(14))

    notification_type: Mapped[str] = mapped_column(String(2), default="03")  # List 1
    record_source_type: Mapped[Optional[str]] = mapped_column(String(2))     # List 3

    # Descriptive detail (block 1)
    product_composition: Mapped[str] = mapped_column(String(2), default="00")  # List 2
    product_form: Mapped[str] = mapped_column(String(2), default="BC")         # List 150
    product_form_description: Mapped[Optional[str]] = mapped_column(String(200))
    primary_content_type: Mapped[Optional[str]] = mapped_column(String(2))    # List 81
    product_packaging: Mapped[Optional[str]] = mapped_column(String(2))       # List 80
    trade_category: Mapped[Optional[str]] = mapped_column(String(2))          # List 12
    country_of_manufacture: Mapped[Optional[str]] = mapped_column(String(2))
    contributor_statement: Mapped[Optional[str]] = mapped_column(Text)
    no_contributor: Mapped[bool] = mapped_column(Boolean, default=False)
    no_collection: Mapped[bool] = mapped_column(Boolean, default=False)
    no_edition: Mapped[bool] = mapped_column(Boolean, default=True)
    edition_type: Mapped[Optional[str]] = mapped_column(String(3))            # List 21
    edition_number: Mapped[Optional[int]] = mapped_column(Integer)
    edition_statement: Mapped[Optional[str]] = mapped_column(String(300))
    illustrations_note: Mapped[Optional[str]] = mapped_column(Text)

    # Title (one distinctive title per product; TitleType List 15)
    title_type: Mapped[str] = mapped_column(String(2), default="01")
    title_prefix: Mapped[Optional[str]] = mapped_column(String(20))
    title_without_prefix: Mapped[str] = mapped_column(String(500), default="")
    subtitle: Mapped[Optional[str]] = mapped_column(String(500))
    # Convenience full title, maintained on save
    title: Mapped[str] = mapped_column(String(600), default="", index=True)

    # Publishing detail (block 4)
    imprint_name: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    publishing_status: Mapped[str] = mapped_column(String(2), default="02", index=True)  # List 64
    city_of_publication: Mapped[Optional[str]] = mapped_column(String(100))
    country_of_publication: Mapped[Optional[str]] = mapped_column(String(2))
    publication_date: Mapped[Optional[date]] = mapped_column(Date, index=True)  # role 01
    copyright_year: Mapped[Optional[str]] = mapped_column(String(40))          # one or more years, space-separated
    row_sales_rights_type: Mapped[Optional[str]] = mapped_column(String(2))    # List 46

    # Shortcut pricing (mirrors the first GBP RRP price; authoritative data in Price)
    rrp_gbp: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))

    # Workflow / housekeeping
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)  # bumped only on real metadata changes
    last_exported_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Anything from an imported record we do not model explicitly (kept so nothing is lost)
    extra: Mapped[Optional[dict]] = mapped_column(JSON)

    # Relationships
    identifiers: Mapped[List["ProductIdentifier"]] = relationship(back_populates="product", cascade="all, delete-orphan", order_by="ProductIdentifier.id")
    measures: Mapped[List["Measure"]] = relationship(cascade="all, delete-orphan", order_by="Measure.id")
    classifications: Mapped[List["ProductClassification"]] = relationship(cascade="all, delete-orphan")
    form_details: Mapped[List["ProductFormDetail"]] = relationship(cascade="all, delete-orphan")
    form_features: Mapped[List["ProductFormFeature"]] = relationship(cascade="all, delete-orphan")
    parts: Mapped[List["ProductPart"]] = relationship(cascade="all, delete-orphan")
    series_links: Mapped[List["ProductSeries"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    contributors: Mapped[List["ProductContributor"]] = relationship(back_populates="product", cascade="all, delete-orphan", order_by="ProductContributor.sequence")
    languages: Mapped[List["Language"]] = relationship(cascade="all, delete-orphan")
    extents: Mapped[List["Extent"]] = relationship(cascade="all, delete-orphan")
    subjects: Mapped[List["Subject"]] = relationship(cascade="all, delete-orphan", order_by="Subject.id")
    audiences: Mapped[List["Audience"]] = relationship(cascade="all, delete-orphan")
    audience_ranges: Mapped[List["AudienceRange"]] = relationship(cascade="all, delete-orphan")
    ancillary_content: Mapped[List["AncillaryContent"]] = relationship(cascade="all, delete-orphan")
    texts: Mapped[List["TextContent"]] = relationship(cascade="all, delete-orphan", order_by="TextContent.id")
    resources: Mapped[List["SupportingResource"]] = relationship(cascade="all, delete-orphan")
    publishers: Mapped[List["Publisher"]] = relationship(cascade="all, delete-orphan")
    publishing_dates: Mapped[List["PublishingDate"]] = relationship(cascade="all, delete-orphan")
    sales_rights: Mapped[List["SalesRights"]] = relationship(cascade="all, delete-orphan")
    related_works: Mapped[List["RelatedWork"]] = relationship(cascade="all, delete-orphan")
    related_products: Mapped[List["RelatedProduct"]] = relationship(cascade="all, delete-orphan")
    supply_details: Mapped[List["SupplyDetail"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    changes: Mapped[List["ChangeLog"]] = relationship(cascade="all, delete-orphan", order_by="ChangeLog.id.desc()")
    external_ids: Mapped[List["ExternalLink"]] = relationship(cascade="all, delete-orphan")

    def compute_title(self) -> str:
        t = " ".join(x for x in [self.title_prefix, self.title_without_prefix] if x)
        return f"{t}: {self.subtitle}" if self.subtitle else t

    @property
    def main_cover(self) -> Optional["SupportingResource"]:
        for r in self.resources:
            if r.content_type == "01":
                return r
        return None

    @property
    def main_description(self) -> Optional[str]:
        for code in ("03", "02", "01"):
            for t in self.texts:
                if t.text_type == code:
                    return t.text
        return None


class ProductIdentifier(Base):
    """ProductIdentifier composite (List 5). 01 = proprietary, 02 = ISBN-10, 03 = GTIN-13, 15 = ISBN-13."""
    __tablename__ = "product_identifier"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    id_type: Mapped[str] = mapped_column(String(2))
    id_type_name: Mapped[Optional[str]] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(100), index=True)
    product: Mapped["Product"] = relationship(back_populates="identifiers")


class Measure(Base):
    __tablename__ = "measure"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    measure_type: Mapped[str] = mapped_column(String(2))   # List 48: 01 height 02 width 03 thickness 08 weight
    value: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    unit: Mapped[str] = mapped_column(String(2))           # List 50: mm, cm, in, gr, oz, lb


class ProductClassification(Base):
    __tablename__ = "product_classification"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    classification_type: Mapped[str] = mapped_column(String(2))  # List 9 (03 = UK Tariff / commodity code)
    code: Mapped[str] = mapped_column(String(30))


class ProductFormDetail(Base):
    __tablename__ = "product_form_detail"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(4))  # List 175
    __table_args__ = (UniqueConstraint("product_id", "code"),)


class ProductFormFeature(Base):
    __tablename__ = "product_form_feature"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    feature_type: Mapped[str] = mapped_column(String(2))  # List 79
    feature_value: Mapped[Optional[str]] = mapped_column(String(100))
    feature_description: Mapped[Optional[str]] = mapped_column(Text)


class ProductPart(Base):
    """For packs / multi-item products (ProductComposition 10 etc.)."""
    __tablename__ = "product_part"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    product_form: Mapped[str] = mapped_column(String(2))
    product_form_description: Mapped[Optional[str]] = mapped_column(String(200))
    number_of_items: Mapped[Optional[int]] = mapped_column(Integer)
    part_isbn13: Mapped[Optional[str]] = mapped_column(String(13))


# --------------------------------------------------------------------------- #
# Series / collections
# --------------------------------------------------------------------------- #

class Series(Base):
    __tablename__ = "series"
    id: Mapped[int] = mapped_column(primary_key=True)
    collection_type: Mapped[str] = mapped_column(String(2), default="10")  # List 148: 10 publisher collection
    title: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    legacy_id: Mapped[Optional[str]] = mapped_column(String(100))
    products: Mapped[List["ProductSeries"]] = relationship(back_populates="series")


class ProductSeries(Base):
    __tablename__ = "product_series"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), index=True)
    sequence_type: Mapped[Optional[str]] = mapped_column(String(2))   # List 197
    sequence_number: Mapped[Optional[str]] = mapped_column(String(20))
    product: Mapped["Product"] = relationship(back_populates="series_links")
    series: Mapped["Series"] = relationship(back_populates="products")


# --------------------------------------------------------------------------- #
# Contributors
# --------------------------------------------------------------------------- #

class Contributor(Base):
    """A person or corporate body. Master record shared across products."""
    __tablename__ = "contributor"
    id: Mapped[int] = mapped_column(primary_key=True)
    is_corporate: Mapped[bool] = mapped_column(Boolean, default=False)
    corporate_name: Mapped[Optional[str]] = mapped_column(String(300))
    person_name: Mapped[Optional[str]] = mapped_column(String(300), index=True)
    person_name_inverted: Mapped[Optional[str]] = mapped_column(String(300), index=True)
    titles_before_names: Mapped[Optional[str]] = mapped_column(String(100))
    names_before_key: Mapped[Optional[str]] = mapped_column(String(200))
    prefix_to_key: Mapped[Optional[str]] = mapped_column(String(50))
    key_names: Mapped[Optional[str]] = mapped_column(String(200))
    names_after_key: Mapped[Optional[str]] = mapped_column(String(100))
    suffix_to_key: Mapped[Optional[str]] = mapped_column(String(50))
    letters_after_names: Mapped[Optional[str]] = mapped_column(String(100))
    titles_after_names: Mapped[Optional[str]] = mapped_column(String(100))
    biographical_note: Mapped[Optional[str]] = mapped_column(Text)
    website: Mapped[Optional[str]] = mapped_column(String(500))
    isni: Mapped[Optional[str]] = mapped_column(String(16))
    orcid: Mapped[Optional[str]] = mapped_column(String(19))
    legacy_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)  # id from the system the record was imported from
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    products: Mapped[List["ProductContributor"]] = relationship(back_populates="contributor")
    places: Mapped[List["ContributorPlace"]] = relationship(cascade="all, delete-orphan")
    affiliations: Mapped[List["ContributorAffiliation"]] = relationship(cascade="all, delete-orphan")

    @property
    def display_name(self) -> str:
        return self.corporate_name or self.person_name or self.person_name_inverted or "(unnamed)"


class ContributorPlace(Base):
    __tablename__ = "contributor_place"
    id: Mapped[int] = mapped_column(primary_key=True)
    contributor_id: Mapped[int] = mapped_column(ForeignKey("contributor.id", ondelete="CASCADE"), index=True)
    relator: Mapped[str] = mapped_column(String(2))  # List 151
    country_code: Mapped[Optional[str]] = mapped_column(String(2))
    region_code: Mapped[Optional[str]] = mapped_column(String(10))
    location_name: Mapped[Optional[str]] = mapped_column(String(200))


class ContributorAffiliation(Base):
    __tablename__ = "contributor_affiliation"
    id: Mapped[int] = mapped_column(primary_key=True)
    contributor_id: Mapped[int] = mapped_column(ForeignKey("contributor.id", ondelete="CASCADE"), index=True)
    professional_position: Mapped[Optional[str]] = mapped_column(String(200))
    affiliation: Mapped[Optional[str]] = mapped_column(String(300))


class ProductContributor(Base):
    __tablename__ = "product_contributor"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    contributor_id: Mapped[int] = mapped_column(ForeignKey("contributor.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    role: Mapped[str] = mapped_column(String(3), default="A01")  # List 17
    # Product-specific bio override (ONIX bios can differ by title)
    biographical_note: Mapped[Optional[str]] = mapped_column(Text)
    product: Mapped["Product"] = relationship(back_populates="contributors")
    contributor: Mapped["Contributor"] = relationship(back_populates="products")


class Language(Base):
    __tablename__ = "language"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(2), default="01")  # List 22
    code: Mapped[str] = mapped_column(String(3), default="eng")  # List 74
    country_code: Mapped[Optional[str]] = mapped_column(String(2))


class Extent(Base):
    __tablename__ = "extent"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    extent_type: Mapped[str] = mapped_column(String(2))   # List 23 (00 main content pages, 05 total pages, 10 content pages...)
    value: Mapped[str] = mapped_column(String(20))
    unit: Mapped[str] = mapped_column(String(2), default="03")  # List 24 (03 pages)


class Subject(Base):
    __tablename__ = "subject"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    main_subject: Mapped[bool] = mapped_column(Boolean, default=False)
    scheme: Mapped[str] = mapped_column(String(2), index=True)  # List 27: 10 BISAC, 12 BIC, 20 keywords, 23 proprietary, 93 Thema
    scheme_name: Mapped[Optional[str]] = mapped_column(String(100))
    scheme_version: Mapped[Optional[str]] = mapped_column(String(20))
    code: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    heading: Mapped[Optional[str]] = mapped_column(Text)


class Audience(Base):
    __tablename__ = "audience"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    code_type: Mapped[str] = mapped_column(String(2), default="01")  # List 29
    code_value: Mapped[str] = mapped_column(String(10))


class AudienceRange(Base):
    __tablename__ = "audience_range"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    qualifier: Mapped[str] = mapped_column(String(2))  # List 30
    precision_from: Mapped[Optional[str]] = mapped_column(String(2))  # List 31
    value_from: Mapped[Optional[str]] = mapped_column(String(10))
    precision_to: Mapped[Optional[str]] = mapped_column(String(2))
    value_to: Mapped[Optional[str]] = mapped_column(String(10))


class AncillaryContent(Base):
    __tablename__ = "ancillary_content"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    content_type: Mapped[str] = mapped_column(String(2))  # List 25
    number: Mapped[Optional[int]] = mapped_column(Integer)
    description: Mapped[Optional[str]] = mapped_column(Text)


# --------------------------------------------------------------------------- #
# Collateral
# --------------------------------------------------------------------------- #

class TextContent(Base):
    __tablename__ = "text_content"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    text_type: Mapped[str] = mapped_column(String(2))         # List 153: 02 short, 03 description, 04 TOC, 05 review quote, 06 quote, 10 promo headline, 12 endorsement
    content_audience: Mapped[str] = mapped_column(String(2), default="00")  # List 154
    text_format: Mapped[str] = mapped_column(String(2), default="02")  # List 34: 02 HTML, 06 plain text
    text: Mapped[str] = mapped_column(Text)
    text_author: Mapped[Optional[str]] = mapped_column(String(300))
    source_title: Mapped[Optional[str]] = mapped_column(String(300))


class SupportingResource(Base):
    __tablename__ = "supporting_resource"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    content_type: Mapped[str] = mapped_column(String(2))        # List 158: 01 front cover, 28 ...
    content_audience: Mapped[str] = mapped_column(String(2), default="00")
    resource_mode: Mapped[str] = mapped_column(String(2), default="03")  # List 159: 03 image
    resource_form: Mapped[str] = mapped_column(String(2), default="02")  # List 161: 02 downloadable file
    file_format: Mapped[Optional[str]] = mapped_column(String(4))  # List 178 e.g. D502 JPEG
    filename: Mapped[Optional[str]] = mapped_column(String(300))
    remote_url: Mapped[Optional[str]] = mapped_column(String(1000))   # original / legacy URL
    local_path: Mapped[Optional[str]] = mapped_column(String(500))    # path under media/ once harvested
    content_date: Mapped[Optional[date]] = mapped_column(Date)
    content_date_role: Mapped[Optional[str]] = mapped_column(String(2), default="17")  # List 155: 17 last updated
    features: Mapped[Optional[dict]] = mapped_column(JSON)  # any ResourceFeature (e.g. caption) not modelled


# --------------------------------------------------------------------------- #
# Publishing detail
# --------------------------------------------------------------------------- #

class Publisher(Base):
    __tablename__ = "publisher"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(2), default="01")  # List 45
    name: Mapped[str] = mapped_column(String(200))
    website: Mapped[Optional[str]] = mapped_column(String(500))
    website_role: Mapped[Optional[str]] = mapped_column(String(2))


class PublishingDate(Base):
    __tablename__ = "publishing_date"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(2))  # List 163: 01 publication, 11 first published
    date_format: Mapped[str] = mapped_column(String(2), default="00")  # List 55
    value: Mapped[str] = mapped_column(String(20))  # as sent, e.g. 20240315 or 202403


class SalesRights(Base):
    __tablename__ = "sales_rights"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    rights_type: Mapped[str] = mapped_column(String(2))  # List 46: 01 exclusive, 02 non-exclusive
    countries_included: Mapped[Optional[str]] = mapped_column(String(1000))  # space-separated ISO codes
    regions_included: Mapped[Optional[str]] = mapped_column(String(200))     # e.g. WORLD
    countries_excluded: Mapped[Optional[str]] = mapped_column(String(1000))
    regions_excluded: Mapped[Optional[str]] = mapped_column(String(200))


# --------------------------------------------------------------------------- #
# Related material
# --------------------------------------------------------------------------- #

class RelatedWork(Base):
    __tablename__ = "related_work"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    relation_code: Mapped[str] = mapped_column(String(2))  # List 164: 01 manifestation of
    id_type: Mapped[str] = mapped_column(String(2))        # List 16
    id_type_name: Mapped[Optional[str]] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(100))


class RelatedProduct(Base):
    __tablename__ = "related_product"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    relation_codes: Mapped[str] = mapped_column(String(50))  # List 51, space separated (06 alternative format, 13 epub, 23 similar, 27 ...)
    isbn13: Mapped[Optional[str]] = mapped_column(String(13), index=True)
    other_identifiers: Mapped[Optional[dict]] = mapped_column(JSON)  # [{type,name,value}]
    product_form: Mapped[Optional[str]] = mapped_column(String(2))
    product_form_detail: Mapped[Optional[str]] = mapped_column(String(4))


# --------------------------------------------------------------------------- #
# Product supply
# --------------------------------------------------------------------------- #

class Supplier(Base):
    __tablename__ = "supplier"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    role: Mapped[str] = mapped_column(String(2), default="01")  # List 93: 01 publisher to retailers
    id_type: Mapped[Optional[str]] = mapped_column(String(2))
    id_type_name: Mapped[Optional[str]] = mapped_column(String(100))
    id_value: Mapped[Optional[str]] = mapped_column(String(100))
    telephone: Mapped[Optional[str]] = mapped_column(String(50))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    website: Mapped[Optional[str]] = mapped_column(String(500))
    website_role: Mapped[Optional[str]] = mapped_column(String(2))


class SupplyDetail(Base):
    __tablename__ = "supply_detail"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("supplier.id"))
    # Market territory for this ProductSupply block
    market_countries: Mapped[Optional[str]] = mapped_column(String(1000))
    market_regions: Mapped[Optional[str]] = mapped_column(String(200))
    market_publishing_status: Mapped[Optional[str]] = mapped_column(String(2))
    promotion_campaign: Mapped[Optional[str]] = mapped_column(Text)
    availability: Mapped[str] = mapped_column(String(2), default="20")  # List 65
    pack_quantity: Mapped[Optional[int]] = mapped_column(Integer)
    product: Mapped["Product"] = relationship(back_populates="supply_details")
    supplier: Mapped["Supplier"] = relationship()
    prices: Mapped[List["Price"]] = relationship(cascade="all, delete-orphan", order_by="Price.id")
    supply_dates: Mapped[List["SupplyDate"]] = relationship(cascade="all, delete-orphan")


class SupplyDate(Base):
    __tablename__ = "supply_date"
    id: Mapped[int] = mapped_column(primary_key=True)
    supply_detail_id: Mapped[int] = mapped_column(ForeignKey("supply_detail.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(2))  # List 166: 08 expected availability
    date_format: Mapped[str] = mapped_column(String(2), default="00")
    value: Mapped[str] = mapped_column(String(20))


class Price(Base):
    __tablename__ = "price"
    id: Mapped[int] = mapped_column(primary_key=True)
    supply_detail_id: Mapped[int] = mapped_column(ForeignKey("supply_detail.id", ondelete="CASCADE"), index=True)
    price_type: Mapped[str] = mapped_column(String(2))   # List 58: 01 RRP excl tax, 02 RRP incl tax
    price_status: Mapped[Optional[str]] = mapped_column(String(2))  # List 61
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    tax_rate_code: Mapped[Optional[str]] = mapped_column(String(1))  # List 62: Z zero, S standard
    tax_rate_percent: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    taxable_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    tax_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    countries_included: Mapped[Optional[str]] = mapped_column(String(1000))
    regions_included: Mapped[Optional[str]] = mapped_column(String(200))


# --------------------------------------------------------------------------- #
# Application tables
# --------------------------------------------------------------------------- #

class ChangeLog(Base):
    __tablename__ = "change_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    changed_by: Mapped[Optional[str]] = mapped_column(String(100))
    field: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)


class ExternalLink(Base):
    """Links a product to its record in Cin7 Core / Shopify."""
    __tablename__ = "external_link"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    system: Mapped[str] = mapped_column(String(20), index=True)   # 'cin7' | 'shopify'
    external_id: Mapped[str] = mapped_column(String(100))
    secondary_id: Mapped[Optional[str]] = mapped_column(String(100))  # e.g. Shopify variant id / Cin7 SKU
    last_pulled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_pushed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    snapshot: Mapped[Optional[dict]] = mapped_column(JSON)  # last-seen remote record (stock, price etc.)
    __table_args__ = (UniqueConstraint("product_id", "system"),)


class Recipient(Base):
    """An ONIX feed recipient (Nielsen, Gardners, Ingram...) with its own profile and delivery."""
    __tablename__ = "recipient"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    addressee_name: Mapped[Optional[str]] = mapped_column(String(200))  # ONIX <AddresseeName>
    # ---- profile
    tag_style: Mapped[str] = mapped_column(String(10), default="reference")  # 'reference' | 'short'
    release: Mapped[str] = mapped_column(String(4), default="3.0")
    include_forms: Mapped[Optional[str]] = mapped_column(String(100))  # e.g. "BC BB ED" or null = all
    exclude_digital: Mapped[bool] = mapped_column(Boolean, default=False)
    include_barcode: Mapped[bool] = mapped_column(Boolean, default=True)   # <Barcode> composite
    statuses: Mapped[Optional[str]] = mapped_column(String(100))  # publishing statuses to send, e.g. "02 04 07"; null = all
    filename_prefix: Mapped[Optional[str]] = mapped_column(String(60))
    # ---- delivery
    delivery: Mapped[str] = mapped_column(String(10), default="download")  # download | sftp | ftp | ftps
    host: Mapped[Optional[str]] = mapped_column(String(200))
    port: Mapped[Optional[int]] = mapped_column(Integer)
    username: Mapped[Optional[str]] = mapped_column(String(100))
    password: Mapped[Optional[str]] = mapped_column(String(200))
    private_key: Mapped[Optional[str]] = mapped_column(Text)  # OpenSSH private key (SFTP), optional
    remote_dir: Mapped[Optional[str]] = mapped_column(String(300))
    # ---- state
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_test_result: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    runs: Mapped[List["ExportRun"]] = relationship(back_populates="recipient", order_by="ExportRun.id.desc()")


class ExportRun(Base):
    __tablename__ = "export_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    recipient_id: Mapped[Optional[int]] = mapped_column(ForeignKey("recipient.id"))
    mode: Mapped[str] = mapped_column(String(10))   # full | delta
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    filename: Mapped[str] = mapped_column(String(300))
    valid: Mapped[Optional[bool]] = mapped_column(Boolean)
    validation_errors: Mapped[Optional[str]] = mapped_column(Text)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    delivery_error: Mapped[Optional[str]] = mapped_column(Text)
    remote_path: Mapped[Optional[str]] = mapped_column(String(400))
    recipient: Mapped[Optional["Recipient"]] = relationship(back_populates="runs")


class SyncLog(Base):
    __tablename__ = "sync_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    system: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(10))  # pull | push
    summary: Mapped[Optional[str]] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)


class Setting(Base):
    """Key/value settings editable in the UI (sender name, contact, default supplier...)."""
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(10), default="editor")  # admin | editor | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SubjectCode(Base):
    """Lookup list behind the subject picker: Thema, BIC, BISAC or any scheme the publisher loads.
    Filled from the official scheme file (Settings) and from codes already used in the catalogue."""
    __tablename__ = "subject_code"
    id: Mapped[int] = mapped_column(primary_key=True)
    scheme: Mapped[str] = mapped_column(String(2), index=True)   # ONIX List 27
    code: Mapped[str] = mapped_column(String(40), index=True)
    heading: Mapped[str] = mapped_column(String(400))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), default="catalogue")  # 'official' | 'catalogue'
    __table_args__ = (UniqueConstraint("scheme", "code"),)
