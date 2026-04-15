"""
InStoreProductAnalyser — Claude Vision analysis for in-store product photos.
Extracts product attributes from photos: name, category, colours, materials, style.
"""
import base64
import json
import random
import re
from pathlib import Path
from typing import Optional
from anthropic import AsyncAnthropic
from config import settings
import structlog

log = structlog.get_logger()

# ── Analytical lenses ─────────────────────────────────────────────────────────
# One is chosen at random each run to vary the analytical angle.

INSTORE_LENSES = [
    "Material & Texture Focus — examine what raw materials, surface treatments and tactile qualities are dominant across the products. What material stories are emerging?",
    "Colour & Pattern Story — explore the colour palettes, tonal relationships and pattern approaches connecting these products. What colour narratives stand out?",
    "Form & Silhouette — analyse the shapes, proportions, lines and structural forms recurring across these products. What design language is emerging?",
    "Style & Mood — consider the aesthetic moods, lifestyle narratives and emotional registers these products communicate together. What overarching style movements appear?",
    "Finish & Detail — focus on surface finishes, decorative detailing, hardware and craft quality signals. What finish trends are appearing?",
    "Functional & Category Lens — look at the product categories, use-cases and functional benefits clustering together. What consumer needs and occasions are these products serving?",
]

PRODUCT_PHOTO_PROMPT = """You are analysing a photo of a retail product (home décor, storage, kitchenware, etc.).
Extract the following information and return ONLY valid JSON (no markdown, no explanation):

{
  "product_name": "<infer the product name from what you see, e.g. 'Ceramic Speckled Vase', 'Wicker Storage Basket'>",
  "category": "<product category, e.g. 'Vase', 'Storage Basket', 'Candle Holder', 'Canister', 'Bowl'>",
  "price": "<price if visible in the photo, otherwise null>",
  "colours": ["<list of dominant colours, e.g. 'sage green', 'warm white', 'natural tan'>"],
  "materials": ["<visible materials, e.g. 'ceramic', 'rattan', 'glass', 'stoneware', 'wood'>"],
  "style_tags": ["<style descriptors, e.g. 'minimalist', 'bohemian', 'coastal', 'farmhouse', 'modern'>"],
  "patterns": ["<patterns if any, e.g. 'speckled', 'striped', 'solid', 'textured', 'floral'> — use empty list if none"],
  "mood": ["<mood/feeling words, e.g. 'cosy', 'fresh', 'rustic', 'elegant', 'playful'>"],
  "confidence": <float 0.0 to 1.0 indicating confidence in the analysis>
}

Be specific and descriptive. Focus on what you can directly observe in the image."""

TREND_REPORT_PROMPT = """You are a trend analyst examining a collection of in-store product photos.
Below is data extracted from {n} product photos. Identify the key trends across these products.

ANALYTICAL LENSES — apply ALL of the following simultaneously:
{lens}

PRODUCTS:
{products_json}

{exclusion_block}Identify 3-6 distinct trends. For each trend, group the products that belong to it.
Return ONLY valid JSON (no markdown):

[
  {{
    "name": "<short trend name, e.g. 'Organic Earth Tones', 'Coastal Natural Textures'>",
    "description": "<2-3 sentences describing what defines this trend — colours, materials, style>",
    "colours": ["<key colours in this trend>"],
    "materials": ["<key materials>"],
    "style_tags": ["<style descriptors>"],
    "product_ids": [<list of product IDs (integers) that belong to this trend>]
  }}
]

Every product should appear in at least one trend. Products can appear in multiple trends if relevant."""


class InStoreProductAnalyser:
    def __init__(self):
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def analyse_product_photo(self, file_path: str, file_type: str) -> Optional[dict]:
        """Analyse a single product photo and return extracted attributes."""
        content_blocks = self._load_content_blocks(file_path, file_type)
        if not content_blocks:
            return None

        try:
            response = await self._client.messages.create(
                model=settings.vision_model,
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": content_blocks + [{"type": "text", "text": PRODUCT_PHOTO_PROMPT}],
                }],
            )
            raw = response.content[0].text if response.content else ""
            return json.loads(self._strip_fences(raw))
        except Exception as e:
            log.warning("instore_analysis_failed", file=file_path, error=str(e))
            return None

    async def generate_trend_report(
        self,
        products: list[dict],
        previous_trend_names: list[str] | None = None,
        lens: str | None = None,
    ) -> Optional[dict]:
        """Generate trend report from list of analysed products.

        Returns a dict with keys: 'trends' (list), 'lens' (str used).
        All analytical lenses are applied simultaneously.
        """
        if not products:
            return None

        chosen_lens = "\n".join(f"{i}. {l}" for i, l in enumerate(INSTORE_LENSES, 1))

        exclusion_block = ""
        if previous_trend_names:
            names_str = "\n".join(f"  - {n}" for n in previous_trend_names)
            exclusion_block = (
                f"PREVIOUSLY IDENTIFIED TRENDS (do NOT repeat these — identify meaningfully different trends):\n"
                f"{names_str}\n\n"
            )

        products_json = json.dumps(products, indent=2)
        prompt = TREND_REPORT_PROMPT.format(
            n=len(products),
            lens=chosen_lens,
            products_json=products_json,
            exclusion_block=exclusion_block,
        )
        try:
            response = await self._client.messages.create(
                model=settings.vision_model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text if response.content else ""
            trends = json.loads(self._strip_fences(raw))
            return {"trends": trends, "lens": chosen_lens}
        except Exception as e:
            log.warning("instore_trend_report_failed", error=str(e))
            return None

    def _load_content_blocks(self, file_path: str, file_type: str) -> list:
        try:
            data = Path(file_path).read_bytes()
            if file_type in ("jpeg", "jpg"):
                media = "image/jpeg"
                b64 = base64.standard_b64encode(data).decode()
                return [{"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}}]
            elif file_type == "png":
                media = "image/png"
                b64 = base64.standard_b64encode(data).decode()
                return [{"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}}]
            elif file_type == "heic":
                # Convert HEIC → JPEG for Claude Vision
                import io
                from PIL import Image
                import pillow_heif
                pillow_heif.register_heif_opener()
                img = Image.open(io.BytesIO(data))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=90)
                b64 = base64.standard_b64encode(buf.getvalue()).decode()
                return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}]
            elif file_type == "pdf":
                try:
                    from pdf2image import convert_from_bytes
                    import io
                    pages = convert_from_bytes(data, dpi=150, first_page=1, last_page=2)
                    blocks = []
                    for page in pages[:2]:
                        buf = io.BytesIO()
                        page.save(buf, format="JPEG", quality=85)
                        b64 = base64.standard_b64encode(buf.getvalue()).decode()
                        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
                    return blocks
                except Exception:
                    return []
        except Exception as e:
            log.warning("instore_load_failed", file=file_path, error=str(e))
            return []
        return []

    @staticmethod
    def _strip_fences(text: str) -> str:
        text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
        text = re.sub(r'\s*```$', '', text.strip(), flags=re.MULTILINE)
        return text.strip()
