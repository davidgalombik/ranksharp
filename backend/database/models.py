"""SQLAlchemy models for the trend tracker database."""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Text, Float, Integer, Boolean, DateTime, JSON,
    ForeignKey, UniqueConstraint, Index, Enum as SAEnum
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector
import enum


class Base(DeclarativeBase):
    pass


class ScrapeTier(str, enum.Enum):
    API = "api"
    HTTP = "http"
    BROWSER = "browser"


class ScrapeStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class TrendStatus(str, enum.Enum):
    RISING = "rising"
    PLATEAU = "plateau"
    DECLINING = "declining"
    NEW = "new"


class Retailer(Base):
    __tablename__ = "retailers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    country: Mapped[str] = mapped_column(String(10), default="US")
    tier: Mapped[ScrapeTier] = mapped_column(SAEnum(ScrapeTier), nullable=False)
    adapter_class: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    categories: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    scrape_jobs: Mapped[list["ScrapeJob"]] = relationship(back_populates="retailer")
    products: Mapped[list["Product"]] = relationship(back_populates="retailer")


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    retailer_id: Mapped[int] = mapped_column(ForeignKey("retailers.id"), nullable=False)
    status: Mapped[ScrapeStatus] = mapped_column(SAEnum(ScrapeStatus), default=ScrapeStatus.PENDING)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    products_found: Mapped[int] = mapped_column(Integer, default=0)
    products_new: Mapped[int] = mapped_column(Integer, default=0)
    products_updated: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    retailer: Mapped["Retailer"] = relationship(back_populates="scrape_jobs")
    products: Mapped[list["Product"]] = relationship(back_populates="scrape_job")


class Product(Base):
    """Raw scraped product — one row per unique product URL."""
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    retailer_id: Mapped[int] = mapped_column(ForeignKey("retailers.id"), nullable=False)
    scrape_job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scrape_jobs.id"), nullable=True)

    # Identity
    external_id: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    sku: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Core fields
    name: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(5), default="USD")
    category: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Images
    image_urls: Mapped[list] = mapped_column(JSON, default=list)
    primary_image_url: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)

    # Raw metadata
    raw_attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    # Scrape timestamps
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_best_seller: Mapped[bool] = mapped_column(Boolean, default=False)
    has_patent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_new: Mapped[bool] = mapped_column(Boolean, default=True)

    # Processing state
    analysis_status: Mapped[ScrapeStatus] = mapped_column(SAEnum(ScrapeStatus), default=ScrapeStatus.PENDING)
    analysed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("retailer_id", "url", name="uq_product_retailer_url"),
        Index("ix_product_retailer_id", "retailer_id"),
        Index("ix_product_analysis_status", "analysis_status"),
        Index("ix_product_is_best_seller", "is_best_seller"),
    )

    retailer: Mapped["Retailer"] = relationship(back_populates="products")
    scrape_job: Mapped[Optional["ScrapeJob"]] = relationship(back_populates="products")
    attributes: Mapped[Optional["ProductAttributes"]] = relationship(back_populates="product", uselist=False)
    trend_examples: Mapped[list["TrendExample"]] = relationship(back_populates="product")
    fragrance_trend_examples: Mapped[list["FragranceTrendExample"]] = relationship(back_populates="product")


class ProductAttributes(Base):
    """AI-enriched product attributes — extracted by vision + NLP models."""
    __tablename__ = "product_attributes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), unique=True, nullable=False)

    # Visual attributes (from vision model)
    colours: Mapped[list] = mapped_column(JSON, default=list)       # ["sage green", "cream", "terracotta"]
    colour_hex: Mapped[list] = mapped_column(JSON, default=list)    # ["#8FA678", "#F5F0E8", ...]
    shape: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    size_descriptor: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    finish: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    style_tags: Mapped[list] = mapped_column(JSON, default=list)    # ["minimalist", "coastal", "rustic"]

    # Text attributes (from NLP model)
    materials: Mapped[list] = mapped_column(JSON, default=list)     # ["ceramic", "linen", "rattan"]
    patterns: Mapped[list] = mapped_column(JSON, default=list)      # ["striped", "floral", "geometric"]
    fragrance: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    season: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    occasion: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    room: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    function_tags: Mapped[list] = mapped_column(JSON, default=list)

    # Embedding (1536-dim for cross-retailer similarity matching)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(1536), nullable=True)

    # Confidence scores
    vision_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    nlp_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product: Mapped["Product"] = relationship(back_populates="attributes")

    __table_args__ = (
        Index("ix_pa_product_id", "product_id"),
    )


class Trend(Base):
    """An identified trend cluster from a given week's analysis run."""
    __tablename__ = "trends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Trend identity
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)  # Claude's explanation

    # Trend classification
    category: Mapped[str] = mapped_column(String(100), nullable=False)  # colour / material / pattern / style / shape
    status: Mapped[TrendStatus] = mapped_column(SAEnum(TrendStatus), default=TrendStatus.NEW)

    # Metrics
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    retailer_count: Mapped[int] = mapped_column(Integer, default=0)
    retailer_names: Mapped[list] = mapped_column(JSON, default=list)
    avg_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Momentum vs previous week
    momentum_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prev_trend_id: Mapped[Optional[int]] = mapped_column(ForeignKey("trends.id"), nullable=True)

    # Top attributes driving the cluster
    dominant_colours: Mapped[list] = mapped_column(JSON, default=list)
    dominant_materials: Mapped[list] = mapped_column(JSON, default=list)
    dominant_patterns: Mapped[list] = mapped_column(JSON, default=list)
    dominant_styles: Mapped[list] = mapped_column(JSON, default=list)

    # Geographic and pricing context (from Claude analysis)
    markets: Mapped[list] = mapped_column(JSON, default=list)           # ["US", "AU", "GB"]
    price_tier: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # budget|mid|premium|luxury

    generation: Mapped[int] = mapped_column(Integer, default=1)  # which Try Again run produced this trend
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_trend_week_start", "week_start"),
        Index("ix_trend_category", "category"),
        Index("ix_trend_generation", "week_start", "generation"),
    )

    examples: Mapped[list["TrendExample"]] = relationship(back_populates="trend")


class TrendExample(Base):
    """Product examples that best represent a trend."""
    __tablename__ = "trend_examples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trend_id: Mapped[int] = mapped_column(ForeignKey("trends.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0)
    is_hero: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("trend_id", "product_id"),
    )

    trend: Mapped["Trend"] = relationship(back_populates="examples")
    product: Mapped["Product"] = relationship(back_populates="trend_examples")


class AldiUploadStatus(str, enum.Enum):
    UPLOADING = "uploading"   # NEW: session is accepting more files
    PENDING = "pending"
    ANALYSING = "analysing"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


class AldiSession(Base):
    """A group of uploaded Aldi trend documents analysed together."""
    __tablename__ = "aldi_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[AldiUploadStatus] = mapped_column(
        SAEnum(AldiUploadStatus), default=AldiUploadStatus.PENDING
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Merged trend intelligence (combined from all uploads in session)
    themes: Mapped[list] = mapped_column(JSON, default=list)
    colour_palette: Mapped[list] = mapped_column(JSON, default=list)
    colour_hex: Mapped[list] = mapped_column(JSON, default=list)
    key_materials: Mapped[list] = mapped_column(JSON, default=list)
    key_prints: Mapped[list] = mapped_column(JSON, default=list)
    product_categories: Mapped[list] = mapped_column(JSON, default=list)
    season_occasion: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    mood_descriptors: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    uploads: Mapped[list["AldiUpload"]] = relationship(
        back_populates="session", lazy="selectin"
    )
    ideas: Mapped[list["AldiProductIdea"]] = relationship(
        "AldiProductIdea",
        primaryjoin="AldiProductIdea.session_id == AldiSession.id",
        foreign_keys="[AldiProductIdea.session_id]",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_aldi_sessions_status", "status"),
    )


class AldiUpload(Base):
    """An uploaded Aldi trend mood-board document."""
    __tablename__ = "aldi_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("aldi_sessions.id"), nullable=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[AldiUploadStatus] = mapped_column(SAEnum(AldiUploadStatus), default=AldiUploadStatus.PENDING)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Extracted trend intelligence (per-document)
    themes: Mapped[list] = mapped_column(JSON, default=list)
    colour_palette: Mapped[list] = mapped_column(JSON, default=list)
    colour_hex: Mapped[list] = mapped_column(JSON, default=list)
    key_materials: Mapped[list] = mapped_column(JSON, default=list)
    key_prints: Mapped[list] = mapped_column(JSON, default=list)
    product_categories: Mapped[list] = mapped_column(JSON, default=list)
    season_occasion: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    mood_descriptors: Mapped[list] = mapped_column(JSON, default=list)
    raw_analysis: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    session: Mapped[Optional["AldiSession"]] = relationship(back_populates="uploads")
    ideas: Mapped[list["AldiProductIdea"]] = relationship(
        back_populates="upload",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="[AldiProductIdea.upload_id]",
    )

    __table_args__ = (
        Index("ix_aldi_uploads_status", "status"),
        Index("ix_aldi_uploads_session_id", "session_id"),
    )


class AldiProductIdea(Base):
    """A Claude-generated product idea from an Aldi trend document or session."""
    __tablename__ = "aldi_product_ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[Optional[int]] = mapped_column(ForeignKey("aldi_uploads.id"), nullable=True)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("aldi_sessions.id"), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(200), nullable=False)
    price_point: Mapped[str] = mapped_column(String(100), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    inspired_by_product_ids: Mapped[list] = mapped_column(JSON, default=list)
    inspired_by_products: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    upload: Mapped[Optional["AldiUpload"]] = relationship(
        back_populates="ideas",
        foreign_keys="[AldiProductIdea.upload_id]",
    )
    session: Mapped[Optional["AldiSession"]] = relationship(
        back_populates="ideas",
        foreign_keys="[AldiProductIdea.session_id]",
    )

    __table_args__ = (
        Index("ix_aldi_ideas_upload_id", "upload_id"),
        Index("ix_aldi_ideas_session_id", "session_id"),
    )


class FragranceTrend(Base):
    """An identified candle/fragrance trend."""
    __tablename__ = "fragrance_trends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[TrendStatus] = mapped_column(SAEnum(TrendStatus), default=TrendStatus.NEW)

    product_count: Mapped[int] = mapped_column(Integer, default=0)
    retailer_count: Mapped[int] = mapped_column(Integer, default=0)
    retailer_names: Mapped[list] = mapped_column(JSON, default=list)
    avg_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    momentum_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prev_trend_id: Mapped[Optional[int]] = mapped_column(ForeignKey("fragrance_trends.id"), nullable=True)

    dominant_colours: Mapped[list] = mapped_column(JSON, default=list)
    dominant_materials: Mapped[list] = mapped_column(JSON, default=list)
    container_styles: Mapped[list] = mapped_column(JSON, default=list)
    scent_families: Mapped[list] = mapped_column(JSON, default=list)
    sustainability_signals: Mapped[list] = mapped_column(JSON, default=list)
    markets: Mapped[list] = mapped_column(JSON, default=list)
    price_tier: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_fragrance_trend_week_start", "week_start"),
        Index("ix_fragrance_trend_category", "category"),
        Index("ix_fragrance_trend_generation", "week_start", "generation"),
    )

    examples: Mapped[list["FragranceTrendExample"]] = relationship(back_populates="trend")


class FragranceTrendExample(Base):
    """Product examples that best represent a fragrance trend."""
    __tablename__ = "fragrance_trend_examples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trend_id: Mapped[int] = mapped_column(ForeignKey("fragrance_trends.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0)
    is_hero: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("trend_id", "product_id"),
    )

    trend: Mapped["FragranceTrend"] = relationship(back_populates="examples")
    product: Mapped["Product"] = relationship(back_populates="fragrance_trend_examples")


class FragranceTrendReport(Base):
    """Fragrance trend report metadata."""
    __tablename__ = "fragrance_trend_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    trend_ids: Mapped[list] = mapped_column(JSON, default=list)
    total_products_analysed: Mapped[int] = mapped_column(Integer, default=0)
    retailers_covered: Mapped[int] = mapped_column(Integer, default=0)
    generation_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TrendReport(Base):
    """Weekly trend report metadata."""
    __tablename__ = "trend_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    trend_ids: Mapped[list] = mapped_column(JSON, default=list)
    html_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    pdf_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    total_products_analysed: Mapped[int] = mapped_column(Integer, default=0)
    retailers_covered: Mapped[int] = mapped_column(Integer, default=0)
    generation_count: Mapped[int] = mapped_column(Integer, default=1)  # how many Try Again runs
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── In-store Products feature ─────────────────────────────────────────────────

class InStoreStatus(str, enum.Enum):
    UPLOADING = "uploading"   # NEW
    PENDING = "pending"
    ANALYSING = "analysing"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


class InStoreSession(Base):
    __tablename__ = "instore_sessions"
    id = mapped_column(Integer, primary_key=True)
    name = mapped_column(String, nullable=True)
    status = mapped_column(SAEnum(InStoreStatus, name="instorestatus"), default=InStoreStatus.PENDING, nullable=False)
    error_message = mapped_column(Text, nullable=True)
    trend_report = mapped_column(JSON, nullable=True)          # latest generation (backward compat)
    generation_count = mapped_column(Integer, default=1)       # how many generations have been run
    trend_report_all = mapped_column(JSON, default=list)       # all generations [{generation, lens, created_at, trends}]
    created_at = mapped_column(DateTime, default=datetime.utcnow)
    updated_at = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    products = relationship("InStoreProduct", back_populates="session", cascade="all, delete-orphan", lazy="select")


class InStoreProduct(Base):
    __tablename__ = "instore_products"
    id = mapped_column(Integer, primary_key=True)
    session_id = mapped_column(Integer, ForeignKey("instore_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = mapped_column(String, nullable=False)
    file_path = mapped_column(String, nullable=False)
    file_type = mapped_column(String, nullable=False)
    status = mapped_column(SAEnum(InStoreStatus, name="instorestatus"), default=InStoreStatus.PENDING, nullable=False)
    error_message = mapped_column(Text, nullable=True)
    product_name = mapped_column(String, nullable=True)
    category = mapped_column(String, nullable=True)
    price = mapped_column(String, nullable=True)
    colours = mapped_column(JSON, nullable=True)
    materials = mapped_column(JSON, nullable=True)
    style_tags = mapped_column(JSON, nullable=True)
    patterns = mapped_column(JSON, nullable=True)
    mood = mapped_column(JSON, nullable=True)
    raw_analysis = mapped_column(JSON, nullable=True)
    created_at = mapped_column(DateTime, default=datetime.utcnow)
    updated_at = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    session = relationship("InStoreSession", back_populates="products")
