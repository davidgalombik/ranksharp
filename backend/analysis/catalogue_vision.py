"""
CatalogueVision — Claude Vision analyser for the In-store Products catalogue.

Takes ONE image (typically a wide shelf shot showing many products) and
returns a list of detected products, each with a name, category, and
style attributes. Category is constrained to one of four fixed values.
"""
import base64
import io
import json
import pathlib
from typing import Optional
import structlog
from anthropic import AsyncAnthropic
from config import settings

log = structlog.get_logger()

CATEGORIES = ["Kitchen & Dining", "Home & Decor", "Candles", "Other"]
PROMINENCE_VALUES = ["hero", "main", "peripheral", "background"]

CATALOGUE_PROMPT = """You are analysing a photograph taken inside a retail store (home, décor, kitchenware).
The photographer framed this shot around a SPECIFIC display, table, aisle end-cap, or product cluster —
that framed area is the ONLY thing you should catalogue as the subject of the photo. The background
typically contains shelving and products from other displays that the photographer was NOT documenting.

For each product visible, extract:
- product_name: a short descriptive name (e.g. "Red lobster-handle ceramic mug", "Sea turtle ceramic plate")
- bbox_norm: REQUIRED for hero and main products — normalized bounding box [x, y, w, h] where each value is between 0.0 and 1.0 (origin is top-left of the image, x/y are the top-left corner of the box, w/h are width/height). Fit the box TIGHTLY around the product — don't include neighbouring products or excessive background. Set to null for peripheral and background products.
- category: exactly one of: "Kitchen & Dining", "Home & Decor", "Candles", "Other"
  - Kitchen & Dining: mugs, plates, bowls, cutlery, cookware, bakeware, glassware, tea towels, placemats, serving ware, food storage, utensils, aprons, lunch boxes
  - Home & Decor: vases, figurines, picture frames, decor objects, throws, cushions, wall art, ornaments, books (decorative/coffee-table)
  - Candles: candles, candle holders, diffusers, wax melts, tea lights
  - Other: anything that doesn't fit the above (tools, electronics, stationery, toys, outdoor, pet, garden)
- prominence: exactly one of "hero" | "main" | "peripheral" | "background" — this is CRITICAL
  - hero: centre-frame, in sharp focus, clearly the primary subject of the photo
  - main: on the same display/table/shelf as the hero items — clearly part of the subject display
  - peripheral: at the edges of the frame, on neighbouring displays that are only partially visible
  - background: on distant shelves, blurred, out of focus, behind the main display, or glimpsed through gaps
- colours: 1-4 dominant colours (e.g. ["red", "cream", "navy"])
- materials: visible materials (e.g. ["ceramic", "cast iron", "wicker"])
- patterns: visible patterns (e.g. ["striped", "solid", "speckled", "floral"]) — empty list if plain
- style_tags: 1-4 style descriptors (e.g. ["coastal", "farmhouse", "modern", "rustic"])
- confidence: "high" | "medium" | "low"

PROMINENCE RULES — BE STRICT:
- The photo was framed around a SPECIFIC display. Everything on that display is hero or main.
- Everything NOT on that display — including items on background shelving, adjacent aisles, boxed
  merchandise stacked behind, distant endcaps, items visible through gaps in the front display,
  cookbooks on a back wall — must be peripheral or background.
- If in doubt between main and peripheral, choose peripheral.
- If in doubt between peripheral and background, choose background.
- Do NOT inflate prominence. It is fine — and expected — for most products to be peripheral/background
  in a wide retail shot.

Return ONLY valid JSON — an array of objects, one per distinct visible product. No prose, no markdown fences.

[
  {
    "product_name": "...",
    "category": "...",
    "prominence": "hero",
    "bbox_norm": [0.12, 0.35, 0.18, 0.42],
    "colours": [...],
    "materials": [...],
    "patterns": [...],
    "style_tags": [...],
    "confidence": "high"
  }
]

Be EXHAUSTIVE — include ALL visible products, but rate each one's prominence honestly so downstream
filtering can separate the subject display from incidental background items.
Do NOT invent duplicates — each entry should correspond to a distinct visible product.
Do NOT include people, store signage, price tags, or empty shelving as products."""


class CatalogueVision:
    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.vision_model

    async def analyse_image_bytes(self, data: bytes, file_type: str) -> Optional[list[dict]]:
        """Analyse an image/PDF's raw bytes and return a list of detected products."""
        content_blocks = self._content_blocks(data, file_type)
        if not content_blocks:
            return None

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=8000,   # many products per image → need the headroom
                messages=[{
                    "role": "user",
                    "content": content_blocks + [{"type": "text", "text": CATALOGUE_PROMPT}],
                }],
            )
            raw = response.content[0].text.strip() if response.content else ""
            raw = _strip_fences(raw)
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                log.warning("catalogue_vision_non_list", sample=raw[:200])
                return None
            return [self._sanitise(item) for item in parsed if isinstance(item, dict)]
        except Exception as exc:
            log.error("catalogue_vision_failed", error=str(exc))
            return None

    @staticmethod
    def _sanitise(item: dict) -> dict:
        cat = (item.get("category") or "").strip()
        if cat not in CATEGORIES:
            cat = "Other"
        prominence = (item.get("prominence") or "").strip().lower()
        if prominence not in PROMINENCE_VALUES:
            prominence = "main"  # sensible default if Claude omits it
        bbox = _coerce_bbox(item.get("bbox_norm") or item.get("bbox"))
        return {
            "product_name": (item.get("product_name") or "Unknown product").strip()[:300],
            "category": cat,
            "prominence": prominence,
            "bbox": bbox,
            "colours": _coerce_list(item.get("colours")),
            "materials": _coerce_list(item.get("materials")),
            "patterns": _coerce_list(item.get("patterns")),
            "style_tags": _coerce_list(item.get("style_tags")),
            "confidence": (item.get("confidence") or "medium").strip()[:10],
        }

    def _content_blocks(self, data: bytes, file_type: str) -> list[dict]:
        ft = (file_type or "").lower().lstrip(".")
        if ft in ("jpeg", "jpg"):
            return [_image_block(data, "image/jpeg")]
        if ft == "png":
            return [_image_block(data, "image/png")]
        if ft in ("heic", "heif"):
            # Convert HEIC to JPEG for Claude Vision
            try:
                from PIL import Image
                try:
                    import pillow_heif
                    pillow_heif.register_heif_opener()
                except ImportError:
                    pass
                img = Image.open(io.BytesIO(data))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=88)
                return [_image_block(buf.getvalue(), "image/jpeg")]
            except Exception as exc:
                log.warning("heic_convert_failed", error=str(exc))
                return []
        if ft == "pdf":
            try:
                from pdf2image import convert_from_bytes
                images = convert_from_bytes(data, first_page=1, last_page=2, dpi=150)
                blocks = []
                for img in images:
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    blocks.append(_image_block(buf.getvalue(), "image/jpeg"))
                return blocks
            except Exception as exc:
                log.warning("pdf_rasterise_failed", error=str(exc))
                return []
        log.warning("catalogue_unsupported_file_type", file_type=file_type)
        return []


def _coerce_bbox(val) -> list[float] | None:
    """Return a normalised [x, y, w, h] list if the value is sensible, else None."""
    if not isinstance(val, (list, tuple)) or len(val) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in val)
    except (TypeError, ValueError):
        return None
    # Require sensible coordinates within [0, 1] with non-zero size
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0):
        return None
    if not (0.0 < w <= 1.0 and 0.0 < h <= 1.0):
        return None
    # Clamp overflow so x+w and y+h stay <= 1
    w = min(w, 1.0 - x)
    h = min(h, 1.0 - y)
    if w < 0.01 or h < 0.01:  # degenerate — reject sub-1% boxes
        return None
    return [round(x, 5), round(y, 5), round(w, 5), round(h, 5)]


def _coerce_list(val) -> list[str]:
    if not val:
        return []
    if isinstance(val, str):
        return [val]
    if isinstance(val, list):
        return [str(v).strip() for v in val if v]
    return []


def _image_block(data: bytes, media_type: str) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode("utf-8"),
        },
    }


def _strip_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.split("\n")
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        return "\n".join(inner)
    return text
