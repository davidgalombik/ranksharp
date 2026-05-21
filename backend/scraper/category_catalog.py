"""Per-retailer category taxonomy catalog (3-level: category -> subcategory -> product_segment).

Single source of truth for the (category, subcategory, product_segment, URL)
tuples that drive both scraping (which URLs to visit) and filtering (what
dropdowns to show).

Each retailer has a CSV at `catalogs/<retailer-slug>.csv`. Headers are
case-insensitive and tolerant of common variants ("Category" / "category" /
"Sub Category" / "subcategory" / "Product Segment" / "product_segment" / "URL").

Display labels (e.g. "Candles & Aromatherapy") are kept as-is in the DB so
they render directly in dropdowns. Slugs are derived automatically for use
in URLs/CSV imports.

If a retailer has no CSV in catalogs/, `has_catalog(slug)` returns False and
callers fall back to legacy behavior — i.e. nothing breaks for retailers not
yet onboarded.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_CATALOGS_DIR = Path(__file__).parent / "catalogs"


@dataclass(frozen=True)
class CatalogEntry:
    category: str          # Display label, e.g. "Candles & Aromatherapy"
    subcategory: str       # Display label, e.g. "Diffusers"
    product_segment: str   # Display label, e.g. "Liquid"
    url: str               # Full URL to scrape


def slugify(label: str) -> str:
    """Convert a display label into a URL/CSV-safe slug.

    "Candles & Aromatherapy" -> "candles-and-aromatherapy"
    "Food Preparation"       -> "food-preparation"
    """
    s = label.strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# Map of normalised header forms -> our canonical column name
_HEADER_ALIASES = {
    "category": "category",
    "sub category": "subcategory",
    "subcategory": "subcategory",
    "sub-category": "subcategory",
    "product segment": "product_segment",
    "product_segment": "product_segment",
    "product-segment": "product_segment",
    "segment": "product_segment",
    "url": "url",
}


def _norm_header(h: str) -> str:
    return _HEADER_ALIASES.get((h or "").strip().lower(), "")


def _load() -> dict[str, list[CatalogEntry]]:
    """Load every CSV under catalogs/ once at import time."""
    catalog: dict[str, list[CatalogEntry]] = {}
    if not _CATALOGS_DIR.exists():
        return catalog
    for csv_path in sorted(_CATALOGS_DIR.glob("*.csv")):
        retailer_slug = csv_path.stem
        entries: list[CatalogEntry] = []
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            try:
                raw_headers = next(reader)
            except StopIteration:
                continue
            # Build {col_name: index} from tolerant header matching
            idx: dict[str, int] = {}
            for i, h in enumerate(raw_headers):
                canon = _norm_header(h)
                if canon and canon not in idx:
                    idx[canon] = i
            # Need at least the four columns
            if not all(k in idx for k in ("category", "subcategory", "product_segment", "url")):
                continue
            ci, si, pi, ui = idx["category"], idx["subcategory"], idx["product_segment"], idx["url"]
            for row in reader:
                if max(ci, si, pi, ui) >= len(row):
                    continue
                cat = (row[ci] or "").strip()
                sub = (row[si] or "").strip()
                seg = (row[pi] or "").strip()
                url = (row[ui] or "").strip()
                # URL is optional — CSV-only retailers (no scraper) keep it
                # blank but still drive UI dropdowns + upload validation.
                if not (cat and sub and seg):
                    continue
                entries.append(CatalogEntry(
                    category=cat, subcategory=sub, product_segment=seg, url=url,
                ))
        if entries:
            catalog[retailer_slug] = entries
    return catalog


_CATALOG = _load()


def has_catalog(retailer_slug: str) -> bool:
    return retailer_slug in _CATALOG


def all_entries(retailer_slug: str) -> list[CatalogEntry]:
    return list(_CATALOG.get(retailer_slug, []))


def get_categories(retailer_slug: str) -> list[str]:
    """Distinct category display labels, preserving first-seen order."""
    seen: list[str] = []
    for e in _CATALOG.get(retailer_slug, []):
        if e.category not in seen:
            seen.append(e.category)
    return seen


def get_subcategories(retailer_slug: str, category: str) -> list[str]:
    """Distinct subcategory labels under a given category."""
    seen: list[str] = []
    for e in _CATALOG.get(retailer_slug, []):
        if e.category == category and e.subcategory not in seen:
            seen.append(e.subcategory)
    return seen


def get_product_segments(retailer_slug: str, category: str, subcategory: str) -> list[str]:
    """Distinct product-segment labels under a given (category, subcategory)."""
    seen: list[str] = []
    for e in _CATALOG.get(retailer_slug, []):
        if e.category == category and e.subcategory == subcategory and e.product_segment not in seen:
            seen.append(e.product_segment)
    return seen


def is_valid(
    retailer_slug: str,
    category: str,
    subcategory: Optional[str] = None,
    product_segment: Optional[str] = None,
) -> bool:
    """True when the given (category[, subcategory[, product_segment]]) path
    exists in the catalog for this retailer. Each deeper level is only checked
    if the previous one is provided."""
    entries = _CATALOG.get(retailer_slug, [])
    if not entries:
        return False
    if subcategory is None:
        return any(e.category == category for e in entries)
    if product_segment is None:
        return any(e.category == category and e.subcategory == subcategory for e in entries)
    return any(
        e.category == category
        and e.subcategory == subcategory
        and e.product_segment == product_segment
        for e in entries
    )


def lookup_for_url(retailer_slug: str, url: str) -> Optional[tuple[str, str, str]]:
    """Reverse lookup: which (category, subcategory, product_segment) does this
    URL belong to? Returns the first match, or None if not in the catalog."""
    for e in _CATALOG.get(retailer_slug, []):
        if e.url == url:
            return (e.category, e.subcategory, e.product_segment)
    return None


def get_tree(retailer_slug: str) -> list[dict]:
    """Return the catalog shaped as a tree for the /taxonomy API endpoint:
    [
      {"category": "...", "category_slug": "...",
       "subcategories": [
         {"label": "...", "slug": "...",
          "product_segments": [{"label": "...", "slug": "..."}, ...]},
         ...
       ]},
      ...
    ]
    """
    tree: list[dict] = []
    for cat in get_categories(retailer_slug):
        subs: list[dict] = []
        for sub in get_subcategories(retailer_slug, cat):
            segs = [
                {"label": s, "slug": slugify(s)}
                for s in get_product_segments(retailer_slug, cat, sub)
            ]
            subs.append({"label": sub, "slug": slugify(sub), "product_segments": segs})
        tree.append({"category": cat, "category_slug": slugify(cat), "subcategories": subs})
    return tree


def resolve_label(retailer_slug: str, value: str, *, kind: str) -> Optional[str]:
    """Accept either a display label or a slug and return the canonical
    display label (or None if not found). `kind` is "category", "subcategory",
    or "product_segment". Used by CSV upload to accept either form."""
    entries = _CATALOG.get(retailer_slug, [])
    if not entries:
        return None
    value_norm = value.strip()
    value_slug = slugify(value_norm)
    for e in entries:
        target = getattr(e, kind, None)
        if target is None:
            continue
        if target == value_norm or slugify(target) == value_slug:
            return target
    return None
