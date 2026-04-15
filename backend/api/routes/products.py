"""Product API routes."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_db
from database.models import Product, ProductAttributes, Retailer
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()


class ProductOut(BaseModel):
    id: int
    retailer_name: str
    retailer_slug: str
    name: str
    url: str
    price: Optional[float]
    currency: str
    category: Optional[str]
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
    retailer: Optional[str] = None,
    category: Optional[str] = None,
    colour: Optional[str] = None,
    material: Optional[str] = None,
    style: Optional[str] = None,
    season: Optional[str] = None,
    room: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    best_seller: Optional[bool] = None,
    limit: int = Query(default=48, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Product, ProductAttributes, Retailer)
        .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(Product.is_active == True)
        .order_by(desc(Product.last_seen_at))
    )

    if q:
        stmt = stmt.where(
            or_(
                Product.name.ilike(f"%{q}%"),
                Product.description.ilike(f"%{q}%"),
            )
        )
    if retailer:
        stmt = stmt.where(Retailer.slug == retailer)
    if category:
        stmt = stmt.where(Product.category == category)
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

    # Count query (same filters, no limit/offset)
    count_stmt = (
        select(func.count())
        .select_from(Product)
        .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(Product.is_active == True)
    )
    if q:
        count_stmt = count_stmt.where(or_(Product.name.ilike(f"%{q}%"), Product.description.ilike(f"%{q}%")))
    if retailer:
        count_stmt = count_stmt.where(Retailer.slug == retailer)
    if category:
        count_stmt = count_stmt.where(Product.category == category)
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

    total = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return ProductPage(total=total, items=[_to_out(p, a, r) for p, a, r in result.all()])


@router.get("/historical", response_model=ProductPage)
async def search_historical_products(
    q: Optional[str] = None,
    retailer: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    best_seller: Optional[bool] = None,
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
    if q:
        base = base.where(or_(Product.name.ilike(f"%{q}%"), Product.description.ilike(f"%{q}%")))
    if retailer:
        base = base.where(Retailer.slug == retailer)
    if min_price:
        base = base.where(Product.price >= min_price)
    if max_price:
        base = base.where(Product.price <= max_price)
    if best_seller is True:
        base = base.where(Product.is_best_seller == True)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    items_stmt = base.order_by(desc(Product.last_seen_at)).limit(limit).offset(offset)
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
