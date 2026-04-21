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

CATALOGUE_PROMPT = """You are analysing a photograph taken inside a retail store (home, décor, kitchenware).
The image may contain ANYWHERE FROM 1 TO 80+ distinct products on shelves, tables, or displays.
Identify EVERY visible product — small items, background items, and items partially in frame count too.

For each product, extract:
- product_name: a short descriptive name (e.g. "Red lobster-handle ceramic mug", "Sea turtle ceramic plate", "Blue striped canister")
- category: exactly one of: "Kitchen & Dining", "Home & Decor", "Candles", "Other"
  - Kitchen & Dining: mugs, plates, bowls, cutlery, cookware, bakeware, glassware, tea towels, placemats, serving ware, food storage, utensils, aprons, lunch boxes
  - Home & Decor: vases, figurines, picture frames, decor objects, throws, cushions, wall art, ornaments, books (decorative/coffee-table)
  - Candles: candles, candle holders, diffusers, wax melts, tea lights
  - Other: anything that doesn't fit the above (tools, electronics, stationery, toys, outdoor, pet, garden)
- colours: 1-4 dominant colours (e.g. ["red", "cream", "navy"])
- materials: visible materials (e.g. ["ceramic", "cast iron", "wicker"])
- patterns: visible patterns (e.g. ["striped", "solid", "speckled", "floral"]) — empty list if plain
- style_tags: 1-4 style descriptors (e.g. ["coastal", "farmhouse", "modern", "rustic"])
- confidence: "high" | "medium" | "low"

Return ONLY valid JSON — an array of objects, one per detected product. No prose, no markdown fences.

[
  {
    "product_name": "...",
    "category": "...",
    "colours": [...],
    "materials": [...],
    "patterns": [...],
    "style_tags": [...],
    "confidence": "high"
  }
]

Be EXHAUSTIVE. If you see 40 products, return 40 entries. If you see 2, return 2.
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
        return {
            "product_name": (item.get("product_name") or "Unknown product").strip()[:300],
            "category": cat,
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
