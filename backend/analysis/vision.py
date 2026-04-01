"""
Vision analysis module.
Sends product images to Claude's vision model to extract visual attributes:
colour palette, shape, style, finish, size descriptor.
"""
import base64
import json
import httpx
import structlog
from typing import Optional
from anthropic import AsyncAnthropic
from config import settings

log = structlog.get_logger()

VISION_PROMPT = """You are a product visual-attributes analyst for a home décor and storage trend-tracking system.

Analyse this product image and extract the following attributes as JSON.
Be specific and consistent — use plain English, lowercase, comma-separated values where multiple apply.

Return ONLY valid JSON with these exact keys:

{
  "colours": ["<primary colour name>", "<secondary colour name>"],
  "colour_hex": ["<#RRGGBB for primary>", "<#RRGGBB for secondary>"],
  "shape": "<overall form: round | rectangular | square | cylindrical | irregular | woven | other>",
  "size_descriptor": "<small | medium | large | extra-large | set | unknown>",
  "finish": "<matte | glossy | metallic | natural | textured | woven | painted | raw | other>",
  "style_tags": ["<1-4 style descriptors: e.g. minimalist, coastal, maximalist, rustic, industrial, boho, Scandinavian, cottagecore, mid-century, art deco, japandi>"],
  "season": "<spring | summer | autumn | winter | all-season | unknown>",
  "room": "<kitchen | living room | bedroom | bathroom | dining room | office | outdoor | any>",
  "confidence": <0.0-1.0 float, how confident you are in this analysis>
}

Focus only on what you can actually see. Use "unknown" for anything not visible."""


class VisionAnalyser:
    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.vision_model

    async def analyse(self, image_url: str) -> Optional[dict]:
        """
        Download image and send to Claude vision model.
        Returns parsed attribute dict or None on failure.
        """
        image_data = await self._download_image(image_url)
        if not image_data:
            return None

        media_type, b64 = image_data

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": VISION_PROMPT,
                            },
                        ],
                    }
                ],
            )
            raw = response.content[0].text.strip()
            # Strip any markdown fences
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)
        except (json.JSONDecodeError, IndexError) as e:
            log.warning("vision_parse_error", url=image_url, error=str(e))
            return None
        except Exception as e:
            log.error("vision_api_error", url=image_url, error=str(e))
            return None

    async def analyse_product(self, image_urls: list[str]) -> Optional[dict]:
        """
        Analyse the primary (first) image of a product.
        Falls back to the second image if the first fails.
        """
        for url in image_urls[:2]:
            result = await self.analyse(url)
            if result:
                return result
        return None

    async def _download_image(self, url: str) -> Optional[tuple[str, str]]:
        """Download image and return (media_type, base64_data) or None."""
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                if content_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                    content_type = "image/jpeg"
                b64 = base64.standard_b64encode(resp.content).decode("utf-8")
                return content_type, b64
        except Exception as e:
            log.warning("image_download_error", url=url, error=str(e))
            return None
