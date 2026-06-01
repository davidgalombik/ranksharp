"""
Text-only re-classification for existing InStoreCatalogueItems.

Used by the one-shot backfill task to migrate items that were classified
under the old 4-category set ({Kitchen & Dining, Home & Decor, Candles, Other})
to the new shared 3-level taxonomy (9 cats / 26 subs / 105 segs).

Cheaper than re-running CatalogueVision because we never look at the image —
Claude gets the product name + previously-extracted attributes (colours,
materials, patterns, style_tags, prior category) as text, and picks the
best (category, subcategory, product_segment) triple from the taxonomy.
"""
import json
from typing import Optional
import structlog
from anthropic import AsyncAnthropic
from config import settings
from scraper import category_catalog as cc

log = structlog.get_logger()


def _build_taxonomy_section() -> str:
    lines: list[str] = []
    for cat in cc.get_shared_categories():
        lines.append(f"- {cat}")
        for sub in cc.get_shared_subcategories(cat):
            lines.append(f"  - {sub}")
            for seg in cc.get_shared_product_segments(cat, sub):
                lines.append(f"    - {seg}")
    return "\n".join(lines)


_TAXONOMY_TREE = _build_taxonomy_section()


_PROMPT_TEMPLATE = f"""You are classifying retail products into a fixed 3-level taxonomy.
Pick the best (category, subcategory, product_segment) for the product below,
using EXACT spelling from the taxonomy.

TAXONOMY (Category > Subcategory > Product Segment):

{_TAXONOMY_TREE}

Rules:
- If the product clearly fits a leaf product_segment, set all three.
- If only the category and subcategory are confident, set product_segment to null.
- If only the category is confident, set subcategory and product_segment to null.
- If the product doesn't fit ANY category in the taxonomy (e.g. clothing, food,
  electronics), set all three to null.

Return ONLY valid JSON, no prose, no markdown fences:
{{"category": "...", "subcategory": "...", "product_segment": "..."}}

PRODUCT TO CLASSIFY:
"""


class CatalogueReclassifier:
    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        # Use the cheaper NLP model — vision isn't needed here
        self.model = settings.nlp_model

    async def aclose(self):
        try:
            await self.client.close()
        except Exception:
            pass

    async def classify(self, item_attrs: dict) -> Optional[dict]:
        """item_attrs may include any of: product_name, category, subcategory,
        product_segment, colours, materials, patterns, style_tags. Returns
        a dict with the three taxonomy levels (each can be None) or None on
        failure."""
        product_lines = []
        if item_attrs.get("product_name"):
            product_lines.append(f"Name: {item_attrs['product_name']}")
        if item_attrs.get("category"):
            product_lines.append(f"Previously classified as category: {item_attrs['category']}")
        if item_attrs.get("colours"):
            product_lines.append(f"Colours: {', '.join(item_attrs['colours'])}")
        if item_attrs.get("materials"):
            product_lines.append(f"Materials: {', '.join(item_attrs['materials'])}")
        if item_attrs.get("patterns"):
            product_lines.append(f"Patterns: {', '.join(item_attrs['patterns'])}")
        if item_attrs.get("style_tags"):
            product_lines.append(f"Style: {', '.join(item_attrs['style_tags'])}")
        prompt = _PROMPT_TEMPLATE + "\n".join(product_lines)

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip() if response.content else ""
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            parsed = json.loads(raw)
            return _sanitise(parsed)
        except Exception as exc:
            log.warning("reclassify_failed",
                        name=item_attrs.get("product_name", "?")[:60], error=str(exc))
            return None


def _sanitise(parsed: dict) -> dict:
    cat = (parsed.get("category") or "").strip() or None
    sub = (parsed.get("subcategory") or "").strip() or None
    seg = (parsed.get("product_segment") or "").strip() or None
    cat = cc.resolve_shared_label(cat, kind="category") if cat else None
    sub = cc.resolve_shared_label(sub, kind="subcategory") if sub else None
    seg = cc.resolve_shared_label(seg, kind="product_segment") if seg else None
    if cat is None:
        sub = None
        seg = None
    elif sub and not cc.is_valid_shared(cat, sub):
        sub = None
        seg = None
    elif sub and seg and not cc.is_valid_shared(cat, sub, seg):
        seg = None
    return {"category": cat, "subcategory": sub, "product_segment": seg}
