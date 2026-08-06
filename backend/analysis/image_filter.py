"""Image-quality SQL filter for Aldi Trends.

The Aldi buyer surface must never render a product without a usable
product image — a broken tile in front of a buyer erodes trust in the
entire recommendation. This module encodes the boolean predicate as a
raw SQL fragment (for use inside text() queries) and as a SQLAlchemy
ORM expression (for use in select().where()).

The gate is deliberately permissive on well-formed URLs and strict only
on the known-bad patterns we've seen from adapters. If a retailer's
placeholder pattern slips through in production, add it below rather
than tightening the whitelist — retailer URL shapes vary widely and
a stricter allowlist would drop legitimate CDN paths.
"""
from sqlalchemy import and_, or_, literal
from database.models import Product


# Known placeholder / broken-image URL patterns. Case-insensitive match
# via ILIKE. Extend when a bad pattern surfaces in production.
_PLACEHOLDER_PATTERNS: list[str] = [
    "%placeholder%",
    "%no-image%",
    "%no_image%",
    "%noimage%",
    "%coming-soon%",
    "%comingsoon%",
    "%default.jpg%",
    "%default.png%",
    "%404%",
]

# Positive signals that the URL points at real image content. A URL only
# has to match ONE of these; most legitimate product images do.
_VALID_URL_HINTS: list[str] = [
    "%.jpg%",
    "%.jpeg%",
    "%.png%",
    "%.webp%",
    "%.avif%",
    "%images.%",     # images.example.com — very common CDN pattern
    "%/media/%",
    "%/img/%",
    "%cdn.%",
    "%/product%",    # /product-images/, /products/, etc.
    "%scene7%",      # Adobe Scene7 (Target, Nordstrom, etc.)
]


def image_ok_sql(column: str = "p.primary_image_url") -> str:
    """Return a raw SQL fragment safe to embed in a text() WHERE clause.

    Escapes: none needed — the patterns are compile-time constants and
    the column name is caller-supplied (usually a qualified column like
    'p.primary_image_url'). Do not pass user input as `column`.
    """
    not_placeholder = " AND ".join(
        f"{column} NOT ILIKE '{p}'" for p in _PLACEHOLDER_PATTERNS
    )
    is_image_like = " OR ".join(
        f"{column} ILIKE '{h}'" for h in _VALID_URL_HINTS
    )
    return (
        f"({column} IS NOT NULL "
        f"AND {column} != '' "
        f"AND {not_placeholder} "
        f"AND ({is_image_like}))"
    )


def image_ok_orm():
    """ORM equivalent of image_ok_sql for use with select().where()."""
    col = Product.primary_image_url
    conditions = [col.isnot(None), col != ""]
    for p in _PLACEHOLDER_PATTERNS:
        conditions.append(~col.ilike(p))
    hint_or = or_(*[col.ilike(h) for h in _VALID_URL_HINTS])
    conditions.append(hint_or)
    return and_(*conditions)
