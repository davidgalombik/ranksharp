"""Product API routes."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc, or_
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
    last_seen_at: datetime

    class Config:
        from_attributes = True


@router.get("/", response_model=list[ProductOut])
async def search_products(
    q: Optional[str] = None,
    retailer: Optional[str] = None,
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

    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)

    output = []
    for product, attrs, retailer_obj in result.all():
        output.append(ProductOut(
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
            last_seen_at=product.last_seen_at,
        ))
    return output
