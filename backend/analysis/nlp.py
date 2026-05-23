"""
NLP attribute extraction module.
Sends product title + description to Claude to extract structured attributes:
materials, patterns, fragrance, occasion, function tags.
"""
import json
import structlog
from typing import Optional
from anthropic import AsyncAnthropic
from config import settings

log = structlog.get_logger()

NLP_PROMPT = """You are a product attribute extraction specialist for a home décor and food/home storage trend-tracking system.

Extract structured attributes from the product title and description below.
Focus on Home & Food Storage Organisation and Home Decor categories.

Return ONLY valid JSON with these exact keys (use empty lists/null for unknown):

{
  "materials": ["<material 1>", "<material 2>"],
  "patterns": ["<pattern: solid | striped | geometric | floral | abstract | animal print | checkered | plain | printed | textured | embroidered | none>"],
  "fragrance": "<scent description or null>",
  "season": "<spring | summer | autumn | winter | all-season | null>",
  "occasion": "<everyday | gifting | entertaining | seasonal | null>",
  "room": "<kitchen | living room | bedroom | bathroom | dining room | office | outdoor | any>",
  "function_tags": ["<functional descriptor: organiser | container | bin | basket | box | jar | candle | vase | tray | shelf | rack | hook | dispenser | set | decorative>"],
  "style_tags": ["<style: minimalist | coastal | maximalist | rustic | industrial | boho | Scandinavian | cottagecore | mid-century | art deco | japandi | eclectic | classic | contemporary | tropical | farmhouse>"],
  "size_mentions": ["<any size/dimension mentioned, e.g. '3L', 'medium', '30cm'>"],
  "eco_mentions": ["<any sustainability claims: recycled | bamboo | organic | BPA-free | sustainably sourced | etc>"],
  "confidence": <0.0-1.0>
}

Product title: {title}
Product description: {description}"""


class NLPExtractor:
    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.nlp_model

    async def aclose(self):
        """Close the underlying Anthropic httpx client so it doesn't try to
        clean up after the event loop has been torn down (which raises
        'RuntimeError: Event loop is closed' under Celery's asyncio.run)."""
        try:
            await self.client.close()
        except Exception:
            pass

    async def extract(self, name: str, description: str, raw_attributes: dict = None) -> Optional[dict]:
        """
        Extract structured attributes from product text.
        Also uses any raw_attributes already scraped from the page.
        """
        # Incorporate scraped page attributes to improve extraction
        extra = ""
        if raw_attributes:
            page_attrs = [
                f"- {k}: {v}"
                for k, v in raw_attributes.items()
                if v and k in ("materials", "color", "style", "tags", "care_instructions")
            ]
            if page_attrs:
                extra = "\n\nAdditional page attributes:\n" + "\n".join(page_attrs)

        prompt = (
            NLP_PROMPT
            .replace("{title}", name or "(no title)")
            .replace("{description}", (description or "(no description)")[:2000] + extra)
        )

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)
        except (json.JSONDecodeError, IndexError) as e:
            log.warning("nlp_parse_error", name=name, error=str(e))
            return None
        except Exception as e:
            log.error("nlp_api_error", name=name, error=str(e))
            return None
