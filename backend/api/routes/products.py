"""Product API routes."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc, or_, func, not_, case, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from database.models import Product, ProductAttributes, Retailer
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

# Hybrid-search tuning. Semantic tier includes any product whose voyage-3
# embedding is within this cosine distance of the query embedding. Products
# scoring worse are excluded from the semantic tier — exact-substring matches
# (name/description ILIKE) still surface separately regardless.
# Empirically voyage-3 puts genuinely related retail items at similarity >= 0.5.
SEMANTIC_MAX_DISTANCE = 0.60  # similarity = 1 - distance, so >= 0.40 kept

# Country bucket -> ISO country codes that fall under it. "EU" is everything
# not in AU/US/GB so any future European retailer (DE/FR/IT/ES/…) is
# auto-bucketed under Europe without code changes.
COUNTRY_BUCKETS: dict[str, list[str]] = {
    "AU": ["AU"],
    "US": ["US"],
    "UK": ["GB"],
}
COUNTRY_LABELS: dict[str, str] = {
    "AU": "Australia",
    "US": "USA",
    "UK": "United Kingdom",
    "EU": "Europe",
}


def _query_tokens(q: str) -> list[str]:
    """Split a search query into individual word tokens. Handles multiple
    spaces, punctuation, ignores 1-char tokens as too noisy."""
    import re
    parts = re.split(r"[^\w]+", q or "")
    return [p for p in parts if len(p) >= 2]


def apply_hybrid_search(stmt, q: str, vec_literal: Optional[str]):
    """Apply search to a Product query.

    Historical note: an earlier version widened the WHERE with a pgvector
    cosine distance clause to catch semantic (synonym / theme) matches.
    On Railway's Postgres that clause pushed shared memory past its limit
    for the 156k * 1024-dim table and returned DiskFullError. Disabled
    until we can add an IVFFlat/HNSW index safely — vec_literal parameter
    kept for signature compatibility with facets callers.

    Now:
      * Exact-phrase match: rows containing the full query as a substring
        of name or description.
      * Tokenised OR match: rows containing ANY individual word from the
        query in name or description. So "acacia wood bowl" also returns
        rows matching "acacia" only or "bowl" only — hybrid_search_order
        ranks by how many tokens matched so full-phrase hits float top.
    """
    like_q = f"%{q}%"
    conds = [Product.name.ilike(like_q), Product.description.ilike(like_q)]
    for tok in _query_tokens(q):
        like_tok = f"%{tok}%"
        conds.append(Product.name.ilike(like_tok))
        conds.append(Product.description.ilike(like_tok))
    stmt = stmt.where(or_(*conds))
    return stmt


def hybrid_search_order(q: str, vec_literal: Optional[str]) -> list:
    """ORDER BY clauses for the paginated select.

    Tier 1 = full query is a substring of the product name.
    Tier 2 = full query is a substring of the description.
    Tier 3 = at least one token matches (ranked by how many tokens hit).
    Within tier 3, rows matching more tokens rank first.
    """
    like_q = f"%{q}%"
    tier = case(
        (Product.name.ilike(like_q), 1),
        (Product.description.ilike(like_q), 2),
        else_=3,
    )
    # Token-match count — sum of CASE WHEN name ILIKE '%tok%' THEN 1 ELSE 0
    # across every token, plus the same for description. Higher = better.
    tokens = _query_tokens(q)
    token_score_cols = []
    for tok in tokens:
        like_tok = f"%{tok}%"
        token_score_cols.append(case((Product.name.ilike(like_tok), 1), else_=0))
        token_score_cols.append(case((Product.description.ilike(like_tok), 1), else_=0))
    if token_score_cols:
        token_score = sum(token_score_cols[1:], token_score_cols[0])
        return [tier.asc(), token_score.desc()]
    return [tier.asc()]


def country_filter_clause(bucket: Optional[str]):
    """Build a SQLAlchemy WHERE clause for filtering Retailer rows by country
    bucket. Returns None when bucket is empty/unknown (caller skips filter)."""
    if not bucket:
        return None
    bucket = bucket.upper()
    if bucket == "EU":
        explicit = [c for codes in COUNTRY_BUCKETS.values() for c in codes]
        return not_(Retailer.country.in_(explicit))
    codes = COUNTRY_BUCKETS.get(bucket)
    if not codes:
        return None
    return Retailer.country.in_(codes)


class ProductOut(BaseModel):
    id: int
    retailer_name: str
    retailer_slug: str
    name: str
    url: str
    price: Optional[float]
    currency: str
    category: Optional[str]
    subcategory: Optional[str] = None
    product_segment: Optional[str] = None
    primary_image_url: Optional[str]
    colours: list[str] = []
    materials: list[str] = []
    style_tags: list[str] = []
    patterns: list[str] = []
    shape: Optional[str] = None
    finish: Optional[str] = None
    season: Optional[str] = None
    room: Optional[str] = None
    is_best_seller: bool = False
    has_patent: bool = False
    is_active: bool = True
    is_new: bool = False
    last_seen_at: datetime
    first_seen_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ProductPage(BaseModel):
    total: int
    items: list[ProductOut]


@router.get("/", response_model=ProductPage)
async def search_products(
    q: Optional[str] = None,
    country: Optional[str] = None,
    retailer: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    product_segment: Optional[str] = None,
    colour: Optional[str] = None,
    material: Optional[str] = None,
    style: Optional[str] = None,
    season: Optional[str] = None,
    room: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    best_seller: Optional[bool] = None,
    has_patent: Optional[bool] = None,
    is_new: Optional[bool] = None,
    limit: int = Query(default=48, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    # When q is set, hybrid search (exact-first, tokenised OR fallback)
    # replaces the default recency ordering. Semantic tier is currently
    # disabled — see apply_hybrid_search() for context — so vec_literal
    # stays None here.
    vec_literal: Optional[str] = None

    stmt = (
        select(Product, ProductAttributes, Retailer)
        .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(Product.is_active == True)
    )
    if q:
        stmt = apply_hybrid_search(stmt, q, vec_literal)
        stmt = stmt.order_by(*hybrid_search_order(q, vec_literal))
    else:
        stmt = stmt.order_by(desc(Product.last_seen_at))

    country_clause = country_filter_clause(country)
    if country_clause is not None:
        stmt = stmt.where(country_clause)
    if retailer:
        stmt = stmt.where(Retailer.slug == retailer)
    if category:
        stmt = stmt.where(Product.category == category)
    if subcategory:
        stmt = stmt.where(Product.subcategory == subcategory)
    if product_segment:
        stmt = stmt.where(Product.product_segment == product_segment)
    if min_price:
        stmt = stmt.where(Product.price >= min_price)
    if max_price:
        stmt = stmt.where(Product.price <= max_price)
    if season:
        stmt = stmt.where(ProductAttributes.season == season)
    if room:
        stmt = stmt.where(ProductAttributes.room == room)
    if best_seller is True:
        stmt = stmt.where(Product.is_best_seller == True)
    if has_patent is True:
        stmt = stmt.where(Product.has_patent == True)
    if is_new is True:
        stmt = stmt.where(Product.is_new == True)

    # Count query (same filters, no limit/offset)
    count_stmt = (
        select(func.count())
        .select_from(Product)
        .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(Product.is_active == True)
    )
    if q:
        count_stmt = apply_hybrid_search(count_stmt, q, vec_literal)
    if country_clause is not None:
        count_stmt = count_stmt.where(country_clause)
    if retailer:
        count_stmt = count_stmt.where(Retailer.slug == retailer)
    if category:
        count_stmt = count_stmt.where(Product.category == category)
    if subcategory:
        count_stmt = count_stmt.where(Product.subcategory == subcategory)
    if product_segment:
        count_stmt = count_stmt.where(Product.product_segment == product_segment)
    if min_price:
        count_stmt = count_stmt.where(Product.price >= min_price)
    if max_price:
        count_stmt = count_stmt.where(Product.price <= max_price)
    if season:
        count_stmt = count_stmt.where(ProductAttributes.season == season)
    if room:
        count_stmt = count_stmt.where(ProductAttributes.room == room)
    if best_seller is True:
        count_stmt = count_stmt.where(Product.is_best_seller == True)
    if has_patent is True:
        count_stmt = count_stmt.where(Product.has_patent == True)
    if is_new is True:
        count_stmt = count_stmt.where(Product.is_new == True)

    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return ProductPage(total=total, items=[_to_out(p, a, r) for p, a, r in result.all()])


class FacetsOut(BaseModel):
    categories: dict[str, int] = {}
    subcategories: dict[str, int] = {}
    product_segments: dict[str, int] = {}
    seasons: dict[str, int] = {}
    rooms: dict[str, int] = {}
    best_seller: int = 0
    has_patent: int = 0
    is_new: int = 0


def _apply_current_filters(
    stmt,
    *,
    q: Optional[str],
    country: Optional[str],
    retailer: Optional[str],
    category: Optional[str],
    subcategory: Optional[str],
    product_segment: Optional[str],
    season: Optional[str],
    room: Optional[str],
    min_price: Optional[float],
    max_price: Optional[float],
    best_seller: Optional[bool],
    has_patent: Optional[bool],
    is_new: Optional[bool],
    exclude: Optional[set[str]] = None,
):
    """Apply the current-product search filters, optionally omitting one.

    Used by the facets endpoint so each facet's count reflects what is
    reachable given all *other* active filters.
    """
    exclude = exclude or set()
    if q:
        # Facets don't paginate, so we don't need the ORDER BY — just the
        # widened WHERE. Semantic tier is currently disabled (see
        # apply_hybrid_search) so pass vec_literal=None.
        stmt = apply_hybrid_search(stmt, q, None)
    if country and "country" not in exclude:
        country_clause = country_filter_clause(country)
        if country_clause is not None:
            stmt = stmt.where(country_clause)
    if retailer and "retailer" not in exclude:
        stmt = stmt.where(Retailer.slug == retailer)
    if category and "category" not in exclude:
        stmt = stmt.where(Product.category == category)
    if subcategory and "subcategory" not in exclude:
        stmt = stmt.where(Product.subcategory == subcategory)
    if product_segment and "product_segment" not in exclude:
        stmt = stmt.where(Product.product_segment == product_segment)
    if min_price is not None:
        stmt = stmt.where(Product.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Product.price <= max_price)
    if season and "season" not in exclude:
        stmt = stmt.where(ProductAttributes.season == season)
    if room and "room" not in exclude:
        stmt = stmt.where(ProductAttributes.room == room)
    if best_seller is True and "best_seller" not in exclude:
        stmt = stmt.where(Product.is_best_seller == True)
    if has_patent is True and "has_patent" not in exclude:
        stmt = stmt.where(Product.has_patent == True)
    if is_new is True and "is_new" not in exclude:
        stmt = stmt.where(Product.is_new == True)
    return stmt


@router.get("/facets", response_model=FacetsOut)
async def current_product_facets(
    q: Optional[str] = None,
    country: Optional[str] = None,
    retailer: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    product_segment: Optional[str] = None,
    season: Optional[str] = None,
    room: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    best_seller: Optional[bool] = None,
    has_patent: Optional[bool] = None,
    is_new: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    """Count per filter value for the Current Products page, so zero-reach
    options can be hidden in the UI."""
    kwargs = dict(
        q=q, country=country, retailer=retailer, category=category, subcategory=subcategory,
        product_segment=product_segment,
        season=season, room=room,
        min_price=min_price, max_price=max_price,
        best_seller=best_seller, has_patent=has_patent, is_new=is_new,
    )

    def _base_grouped(group_col, exclude: set[str]):
        stmt = (
            select(group_col, func.count(Product.id))
            .select_from(Product)
            .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
            .join(Retailer, Product.retailer_id == Retailer.id)
            .where(Product.is_active == True)
            .group_by(group_col)
        )
        return _apply_current_filters(stmt, **kwargs, exclude=exclude)

    def _base_bool(col, exclude: set[str]):
        stmt = (
            select(func.count(Product.id))
            .select_from(Product)
            .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
            .join(Retailer, Product.retailer_id == Retailer.id)
            .where(Product.is_active == True)
            .where(col == True)
        )
        return _apply_current_filters(stmt, **kwargs, exclude=exclude)

    # Categories + subcategories — retailer-gated in the UI, but we compute
    # them when a retailer is selected so the dropdowns can hide zero-count
    # options. Subcategory count uses all-other-filters (incl. current category)
    # so picking a category narrows the subcategory options.
    categories: dict[str, int] = {}
    subcategories: dict[str, int] = {}
    product_segments: dict[str, int] = {}
    if retailer:
        # Category facet ignores its own filter AND deeper-level filters, so
        # picking a deeper filter doesn't shrink the category list.
        cat_rows = await db.execute(_base_grouped(
            Product.category, {"category", "subcategory", "product_segment"},
        ))
        for cat, cnt in cat_rows.all():
            if cat:
                categories[cat] = cnt
        # Subcategory facet keeps current category filter but ignores
        # subcategory + product_segment, so picking a category narrows
        # the subcategory list to those reachable under it.
        sub_rows = await db.execute(_base_grouped(
            Product.subcategory, {"subcategory", "product_segment"},
        ))
        for sub, cnt in sub_rows.all():
            if sub:
                subcategories[sub] = cnt
        # Product-segment facet keeps category + subcategory filters but
        # ignores its own, so segments narrow as you drill down.
        seg_rows = await db.execute(_base_grouped(
            Product.product_segment, {"product_segment"},
        ))
        for seg, cnt in seg_rows.all():
            if seg:
                product_segments[seg] = cnt

    season_rows = await db.execute(_base_grouped(ProductAttributes.season, {"season"}))
    seasons = {s: c for s, c in season_rows.all() if s}

    room_rows = await db.execute(_base_grouped(ProductAttributes.room, {"room"}))
    rooms = {r: c for r, c in room_rows.all() if r}

    best_seller_ct = (await db.execute(_base_bool(Product.is_best_seller, {"best_seller"}))).scalar_one()
    has_patent_ct = (await db.execute(_base_bool(Product.has_patent, {"has_patent"}))).scalar_one()
    is_new_ct = (await db.execute(_base_bool(Product.is_new, {"is_new"}))).scalar_one()

    return FacetsOut(
        categories=categories,
        subcategories=subcategories,
        product_segments=product_segments,
        seasons=seasons,
        rooms=rooms,
        best_seller=best_seller_ct,
        has_patent=has_patent_ct,
        is_new=is_new_ct,
    )


class HistoricalFacetsOut(BaseModel):
    categories: dict[str, int] = {}
    subcategories: dict[str, int] = {}
    product_segments: dict[str, int] = {}
    seasons: dict[str, int] = {}
    rooms: dict[str, int] = {}
    best_seller: int = 0
    has_patent: int = 0
    is_new: int = 0
    inactive: int = 0


def _apply_historical_filters(
    stmt,
    *,
    q: Optional[str],
    country: Optional[str],
    retailer: Optional[str],
    category: Optional[str],
    subcategory: Optional[str],
    product_segment: Optional[str],
    season: Optional[str],
    room: Optional[str],
    min_price: Optional[float],
    max_price: Optional[float],
    best_seller: Optional[bool],
    has_patent: Optional[bool],
    is_new: Optional[bool],
    inactive_only: Optional[bool],
    exclude: Optional[set[str]] = None,
):
    """Apply the historical-products filter set, optionally omitting one.
    Same shape as _apply_current_filters but does NOT default to is_active=True
    (Historical includes inactive rows)."""
    exclude = exclude or set()
    if q:
        from analysis.embeddings import embed_text_sync
        vec = embed_text_sync(q, input_type="query")
        vec_literal = (
            "[" + ",".join(f"{x:.6f}" for x in vec) + "]" if vec else None
        )
        stmt = apply_hybrid_search(stmt, q, vec_literal)
    if country and "country" not in exclude:
        country_clause = country_filter_clause(country)
        if country_clause is not None:
            stmt = stmt.where(country_clause)
    if retailer and "retailer" not in exclude:
        stmt = stmt.where(Retailer.slug == retailer)
    if category and "category" not in exclude:
        stmt = stmt.where(Product.category == category)
    if subcategory and "subcategory" not in exclude:
        stmt = stmt.where(Product.subcategory == subcategory)
    if product_segment and "product_segment" not in exclude:
        stmt = stmt.where(Product.product_segment == product_segment)
    if min_price is not None:
        stmt = stmt.where(Product.price >= min_price)
    if max_price is not None:
        stmt = stmt.where(Product.price <= max_price)
    if season and "season" not in exclude:
        stmt = stmt.where(ProductAttributes.season == season)
    if room and "room" not in exclude:
        stmt = stmt.where(ProductAttributes.room == room)
    if best_seller is True and "best_seller" not in exclude:
        stmt = stmt.where(Product.is_best_seller == True)
    if has_patent is True and "has_patent" not in exclude:
        stmt = stmt.where(Product.has_patent == True)
    if is_new is True and "is_new" not in exclude:
        stmt = stmt.where(Product.is_new == True)
    if inactive_only is True and "inactive_only" not in exclude:
        stmt = stmt.where(Product.is_active == False)
    return stmt


@router.get("/historical/facets", response_model=HistoricalFacetsOut)
async def historical_product_facets(
    q: Optional[str] = None,
    country: Optional[str] = None,
    retailer: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    product_segment: Optional[str] = None,
    season: Optional[str] = None,
    room: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    best_seller: Optional[bool] = None,
    has_patent: Optional[bool] = None,
    is_new: Optional[bool] = None,
    inactive_only: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    """Count per filter value for the Historical Products page so zero-reach
    options can be hidden in the UI (mirrors the Online Products facets)."""
    kwargs = dict(
        q=q, country=country, retailer=retailer, category=category, subcategory=subcategory,
        product_segment=product_segment, season=season, room=room,
        min_price=min_price, max_price=max_price,
        best_seller=best_seller, has_patent=has_patent, is_new=is_new,
        inactive_only=inactive_only,
    )

    def _base_grouped(group_col, exclude: set[str]):
        stmt = (
            select(group_col, func.count(Product.id))
            .select_from(Product)
            .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
            .join(Retailer, Product.retailer_id == Retailer.id)
            .group_by(group_col)
        )
        return _apply_historical_filters(stmt, **kwargs, exclude=exclude)

    def _base_bool(col, exclude: set[str]):
        stmt = (
            select(func.count(Product.id))
            .select_from(Product)
            .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
            .join(Retailer, Product.retailer_id == Retailer.id)
            .where(col == True)
        )
        return _apply_historical_filters(stmt, **kwargs, exclude=exclude)

    categories: dict[str, int] = {}
    subcategories: dict[str, int] = {}
    product_segments: dict[str, int] = {}
    if retailer:
        cat_rows = await db.execute(_base_grouped(
            Product.category, {"category", "subcategory", "product_segment"},
        ))
        for cat, cnt in cat_rows.all():
            if cat:
                categories[cat] = cnt
        sub_rows = await db.execute(_base_grouped(
            Product.subcategory, {"subcategory", "product_segment"},
        ))
        for sub, cnt in sub_rows.all():
            if sub:
                subcategories[sub] = cnt
        seg_rows = await db.execute(_base_grouped(
            Product.product_segment, {"product_segment"},
        ))
        for seg, cnt in seg_rows.all():
            if seg:
                product_segments[seg] = cnt

    season_rows = await db.execute(_base_grouped(ProductAttributes.season, {"season"}))
    seasons = {s: c for s, c in season_rows.all() if s}

    room_rows = await db.execute(_base_grouped(ProductAttributes.room, {"room"}))
    rooms = {r: c for r, c in room_rows.all() if r}

    best_seller_ct = (await db.execute(_base_bool(Product.is_best_seller, {"best_seller"}))).scalar_one()
    has_patent_ct = (await db.execute(_base_bool(Product.has_patent, {"has_patent"}))).scalar_one()
    is_new_ct = (await db.execute(_base_bool(Product.is_new, {"is_new"}))).scalar_one()

    # Count of inactive rows reachable under the other filters (for the
    # "No longer listed" toggle visibility logic).
    inactive_stmt = (
        select(func.count(Product.id))
        .select_from(Product)
        .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(Product.is_active == False)
    )
    inactive_ct = (await db.execute(
        _apply_historical_filters(inactive_stmt, **kwargs, exclude={"inactive_only"})
    )).scalar_one()

    return HistoricalFacetsOut(
        categories=categories,
        subcategories=subcategories,
        product_segments=product_segments,
        seasons=seasons,
        rooms=rooms,
        best_seller=best_seller_ct,
        has_patent=has_patent_ct,
        is_new=is_new_ct,
        inactive=inactive_ct,
    )


@router.get("/historical", response_model=ProductPage)
async def search_historical_products(
    q: Optional[str] = None,
    country: Optional[str] = None,
    retailer: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    product_segment: Optional[str] = None,
    season: Optional[str] = None,
    room: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    best_seller: Optional[bool] = None,
    has_patent: Optional[bool] = None,
    is_new: Optional[bool] = None,
    inactive_only: Optional[bool] = None,
    limit: int = Query(default=48, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """All products ever scraped, including those no longer on retailer sites."""
    base = (
        select(Product, ProductAttributes, Retailer)
        .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
        .join(Retailer, Product.retailer_id == Retailer.id)
    )
    base = _apply_historical_filters(
        base,
        q=q, country=country, retailer=retailer, category=category, subcategory=subcategory,
        product_segment=product_segment, season=season, room=room,
        min_price=min_price, max_price=max_price,
        best_seller=best_seller, has_patent=has_patent, is_new=is_new,
        inactive_only=inactive_only,
    )

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # When q is set, sort by hybrid-search tier (exact-first, then
    # tokenised OR). Otherwise fall back to the historical recency ordering.
    if q:
        items_stmt = base.order_by(*hybrid_search_order(q, None))
    else:
        items_stmt = base.order_by(desc(Product.last_seen_at))
    items_stmt = items_stmt.limit(limit).offset(offset)
    result = await db.execute(items_stmt)
    return ProductPage(total=total, items=[_to_out(p, a, r) for p, a, r in result.all()])


def _to_out(product, attrs, retailer_obj) -> ProductOut:
    return ProductOut(
        id=product.id,
        retailer_name=retailer_obj.name,
        retailer_slug=retailer_obj.slug,
        name=product.name,
        url=product.url,
        price=product.price,
        currency=product.currency,
        category=product.category,
        subcategory=product.subcategory,
        product_segment=product.product_segment,
        primary_image_url=product.primary_image_url,
        colours=attrs.colours if attrs else [],
        materials=attrs.materials if attrs else [],
        style_tags=attrs.style_tags if attrs else [],
        patterns=attrs.patterns if attrs else [],
        shape=attrs.shape if attrs else None,
        finish=attrs.finish if attrs else None,
        season=attrs.season if attrs else None,
        room=attrs.room if attrs else None,
        is_best_seller=product.is_best_seller,
        has_patent=product.has_patent,
        is_new=product.is_new,
        last_seen_at=product.last_seen_at,
        first_seen_at=product.first_seen_at,
        is_active=product.is_active,
    )
