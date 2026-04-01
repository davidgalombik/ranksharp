"""Trend API routes."""
from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from database.db import get_db
from database.models import Trend, TrendExample, Product, ProductAttributes, Retailer, TrendStatus
from pydantic import BaseModel

router = APIRouter()


class TrendExampleOut(BaseModel):
    product_id: int
    name: str
    url: str
    price: Optional[float]
    currency: str
    primary_image_url: Optional[str]
    retailer_name: str
    retailer_slug: str
    retailer_country: str
    colours: list[str]
    materials: list[str]
    style_tags: list[str]
    is_hero: bool

    class Config:
        from_attributes = True


class TrendOut(BaseModel):
    id: int
    week_start: datetime
    name: str
    description: str
    rationale: str
    category: str
    status: TrendStatus
    product_count: int
    retailer_count: int
    retailer_names: list[str]
    avg_price: Optional[float]
    momentum_pct: Optional[float]
    dominant_colours: list[str]
    dominant_materials: list[str]
    dominant_patterns: list[str]
    dominant_styles: list[str]
    markets: list[str]
    price_tier: Optional[str]
    examples: list[TrendExampleOut] = []

    class Config:
        from_attributes = True


@router.get("/", response_model=list[TrendOut])
async def list_trends(
    week_start: Optional[date] = None,
    category: Optional[str] = None,
    status: Optional[TrendStatus] = None,
    limit: int = Query(default=20, le=100),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List trends, optionally filtered by week, category, or status."""
    q = select(Trend).order_by(desc(Trend.week_start), desc(Trend.product_count))

    if week_start:
        q = q.where(Trend.week_start == datetime.combine(week_start, datetime.min.time()))
    if category:
        q = q.where(Trend.category == category)
    if status:
        q = q.where(Trend.status == status)

    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    trends = result.scalars().all()

    # Load examples for each trend
    output = []
    for trend in trends:
        trend_out = await _build_trend_out(trend, db)
        output.append(trend_out)

    return output


@router.get("/latest", response_model=list[TrendOut])
async def latest_trends(
    limit: int = Query(default=10, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent week's trends."""
    # Find the most recent week
    latest_result = await db.execute(
        select(Trend.week_start).order_by(desc(Trend.week_start)).limit(1)
    )
    latest_week = latest_result.scalar_one_or_none()
    if not latest_week:
        return []

    result = await db.execute(
        select(Trend)
        .where(Trend.week_start == latest_week)
        .order_by(desc(Trend.product_count))
        .limit(limit)
    )
    trends = result.scalars().all()
    return [await _build_trend_out(t, db) for t in trends]


@router.get("/{trend_id}", response_model=TrendOut)
async def get_trend(trend_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single trend with all examples."""
    result = await db.execute(select(Trend).where(Trend.id == trend_id))
    trend = result.scalar_one_or_none()
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    return await _build_trend_out(trend, db, max_examples=20)


@router.get("/weeks/", response_model=list[str])
async def list_weeks(db: AsyncSession = Depends(get_db)):
    """List all weeks with trend data."""
    result = await db.execute(
        select(Trend.week_start).distinct().order_by(desc(Trend.week_start))
    )
    return [str(r.date()) for r in result.scalars().all()]


async def _build_trend_out(
    trend: Trend, db: AsyncSession, max_examples: int = 6
) -> TrendOut:
    """Build a TrendOut including example products."""
    examples_result = await db.execute(
        select(TrendExample, Product, ProductAttributes, Retailer)
        .join(Product, TrendExample.product_id == Product.id)
        .outerjoin(ProductAttributes, Product.id == ProductAttributes.product_id)
        .join(Retailer, Product.retailer_id == Retailer.id)
        .where(TrendExample.trend_id == trend.id)
        .order_by(desc(TrendExample.is_hero), desc(TrendExample.relevance_score))
        .limit(max_examples)
    )

    examples = []
    for ex, product, attrs, retailer in examples_result.all():
        examples.append(TrendExampleOut(
            product_id=product.id,
            name=product.name,
            url=product.url,
            price=product.price,
            currency=product.currency,
            primary_image_url=product.primary_image_url,
            retailer_name=retailer.name,
            retailer_slug=retailer.slug,
            retailer_country=retailer.country,
            colours=attrs.colours if attrs else [],
            materials=attrs.materials if attrs else [],
            style_tags=attrs.style_tags if attrs else [],
            is_hero=ex.is_hero,
        ))

    return TrendOut(
        id=trend.id,
        week_start=trend.week_start,
        name=trend.name,
        description=trend.description,
        rationale=trend.rationale,
        category=trend.category,
        status=trend.status,
        product_count=trend.product_count,
        retailer_count=trend.retailer_count,
        retailer_names=trend.retailer_names or [],
        avg_price=trend.avg_price,
        momentum_pct=trend.momentum_pct,
        dominant_colours=trend.dominant_colours or [],
        dominant_materials=trend.dominant_materials or [],
        dominant_patterns=trend.dominant_patterns or [],
        dominant_styles=trend.dominant_styles or [],
        markets=trend.markets or [],
        price_tier=trend.price_tier,
        examples=examples,
    )
