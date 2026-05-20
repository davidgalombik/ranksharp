"""Per-retailer category taxonomy catalog.

Single source of truth for the (category, subcategory, URL) tuples that drive
both scraping (which URLs to visit) and filtering (what dropdowns to show).

Each retailer has a CSV at `catalogs/<retailer-slug>.csv` with columns:
    category, subcategory, url

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
    category: str        # Display label, e.g. "Candles & Aromatherapy"
    subcategory: str     # Display label, e.g. "Diffusers"
    url: str             # Full URL to scrape


def slugify(label: str) -> str:
    """Convert a display label into a URL/CSV-safe slug.

    "Candles & Aromatherapy" -> "candles-and-aromatherapy"
    "Food Preparation"       -> "food-preparation"
    """
    s = label.strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _load() -> dict[str, list[CatalogEntry]]:
    """Load every CSV under catalogs/ once at import time."""
    catalog: dict[str, list[CatalogEntry]] = {}
    if not _CATALOGS_DIR.exists():
        return catalog
    for csv_path in sorted(_CATALOGS_DIR.glob("*.csv")):
        retailer_slug = csv_path.stem
        entries: list[CatalogEntry] = []
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cat = (row.get("category") or "").strip()
                sub = (row.get("subcategory") or "").strip()
                url = (row.get("url") or "").strip()
                if not (cat and sub and url):
                    continue
                entries.append(CatalogEntry(category=cat, subcategory=sub, url=url))
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
    """Distinct subcategory labels under a given category, preserving order."""
    seen: list[str] = []
    for e in _CATALOG.get(retailer_slug, []):
        if e.category == category and e.subcategory not in seen:
            seen.append(e.subcategory)
    return seen


def is_valid(retailer_slug: str, category: str, subcategory: Optional[str] = None) -> bool:
    """True when `category` (and optionally `subcategory`) exists in the catalog
    for this retailer. Used by CSV upload validation."""
    entries = _CATALOG.get(retailer_slug, [])
    if not entries:
        return False
    if subcategory is None:
        return any(e.category == category for e in entries)
    return any(e.category == category and e.subcategory == subcategory for e in entries)


def lookup_for_url(retailer_slug: str, url: str) -> Optional[tuple[str, str]]:
    """Reverse lookup: which (category, subcategory) does this URL belong to?
    Returns the first match, or None if the URL is not in the catalog."""
    for e in _CATALOG.get(retailer_slug, []):
        if e.url == url:
            return (e.category, e.subcategory)
    return None


def get_tree(retailer_slug: str) -> list[dict]:
    """Return the catalog shaped as a tree for the /taxonomy API endpoint:
    [{"category": "...", "category_slug": "...",
      "subcategories": [{"label": "...", "slug": "..."}, ...]}, ...]
    """
    tree: list[dict] = []
    for cat in get_categories(retailer_slug):
        subs = [{"label": s, "slug": slugify(s)} for s in get_subcategories(retailer_slug, cat)]
        tree.append({"category": cat, "category_slug": slugify(cat), "subcategories": subs})
    return tree


def resolve_label(retailer_slug: str, value: str, *, kind: str) -> Optional[str]:
    """Accept either a display label or a slug and return the canonical
    display label (or None if not found). `kind` is "category" or "subcategory".
    Used by CSV upload to accept either form."""
    entries = _CATALOG.get(retailer_slug, [])
    if not entries:
        return None
    value_norm = value.strip()
    value_slug = slugify(value_norm)
    if kind == "category":
        for e in entries:
            if e.category == value_norm or slugify(e.category) == value_slug:
                return e.category
    elif kind == "subcategory":
        for e in entries:
            if e.subcategory == value_norm or slugify(e.subcategory) == value_slug:
                return e.subcategory
    return None
