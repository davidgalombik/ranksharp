"""Fragrance product identification — used to scope both the trend engine
and the live "View all N products" modal to actual fragrance/candle
products regardless of retailer.

Content-based rather than retailer-allowlisted: any product whose NAME
matches one of these keywords (word-boundary regex — not substring) is
considered fragrance-adjacent and eligible for Fragrance Trends. This
lets fragrance products from generalist retailers (Target, Walmart, etc.)
appear alongside those from specialists.

Word-boundary matching is important — the old `ILIKE '%wax%'` sub-string
approach pulled in "wax paper" and "waxed wood" (kitchen + furniture).
Postgres \\y matches transitions between word characters and non-word
characters, so `\\ywax\\y` catches "wax" and "wax melt" but not "beeswax"
(we list "beeswax" explicitly for that).
"""
import re


# Every keyword a fragrance product name might reasonably contain. Kept
# comprehensive rather than tight — false negatives (missing a product)
# hurt more than false positives (a curious candle-holder-adjacent
# product). Add missing terms as they surface in production.
FRAGRANCE_KEYWORDS: list[str] = [
    # Candles + candle types
    "candle", "candles",
    "tealight", "tealights", "tea light", "tea lights",
    "votive", "votives",
    "pillar candle", "pillar candles",
    "taper candle", "taper candles",
    "jar candle", "jar candles",
    "soy candle", "soy candles",
    "beeswax candle",
    "container candle", "container candles",

    # Wax products
    "wax melt", "wax melts",
    "wax cube", "wax cubes",
    "wax tart", "wax tarts",
    "wax warmer", "wax warmers",
    "beeswax", "soy wax", "coconut wax",

    # Diffusers
    "diffuser", "diffusers",
    "reed diffuser", "reed diffusers",
    "oil diffuser", "oil diffusers",

    # Sprays / mists
    "room spray", "room sprays",
    "room mist", "room mists",
    "linen spray", "linen mist",
    "pillow spray", "pillow mist",

    # Aromatherapy
    "essential oil", "essential oils",
    "aromatherapy",
    "incense",

    # Fragrance general
    "fragrance", "fragrances", "fragranced",
    "scented", "scent",

    # Potpourri + sachets
    "potpourri",
    "sachet", "sachets", "fragrance sachet",

    # Candle-making + accessories
    "wick", "wicks",
    "candle holder", "candle holders",
    "candle snuffer",

    # Home fragrance category
    "home fragrance",
]


def fragrance_regex_pattern() -> str:
    """Return the Postgres word-boundary regex that matches any
    fragrance keyword. Suitable for `column ~* :pattern` binds."""
    return r"\y(?:" + "|".join(re.escape(k) for k in FRAGRANCE_KEYWORDS) + r")\y"


def fragrance_where_sql(column: str = "p.name") -> str:
    """SQL fragment for `text()` queries. Uses a bind parameter so we
    don't have to escape the pattern; the caller must provide
    `:frag_pattern` in the bind dict via `fragrance_regex_pattern()`."""
    return f"{column} ~* :frag_pattern"
