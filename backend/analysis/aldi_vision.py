"""
Aldi trend mood-board analyser.
Sends uploaded documents (JPEG, PNG, PDF) to Claude Vision to extract
commercial trend intelligence: themes, colour palette, key materials, prints, etc.
"""
import base64
import io
import json
import pathlib
import random
import structlog
from typing import Optional
from anthropic import AsyncAnthropic
from config import settings

log = structlog.get_logger()

MOOD_BOARD_PROMPT = """You are a trend analyst for a global home décor buyer.
You are looking at a trend mood board document which may contain imagery, colour swatches,
fabric samples, Pantone references, text labels, and product references.

Extract the following trend intelligence and return ONLY valid JSON:

{
  "themes": ["<overarching theme or concept name, e.g. 'Country Spring Farmhouse Kitchen'>"],
  "colour_palette": ["<colour name with descriptor, e.g. 'warm sage green', 'dusty rose', 'pale banana yellow'>"],
  "colour_hex": ["<#RRGGBB best estimate for each colour in the same order>"],
  "key_materials": ["<material names visible or implied, e.g. 'linen', 'speckled stoneware', 'reclaimed wood', 'woven seagrass'>"],
  "key_prints": ["<pattern/print descriptions, e.g. 'ditsy florals', 'handpainted gingham', 'fruit salad print', 'cottage floral'>"],
  "product_categories": ["<home product categories visible or implied, e.g. 'table linen', 'ceramic canisters', 'wicker baskets', 'rag rugs'>"],
  "season_occasion": "<season or occasion this trend targets, e.g. 'Spring 2025', 'Easter Entertaining', 'Summer Farmhouse'>",
  "mood_descriptors": ["<adjectives describing the feeling, e.g. 'warm', 'nostalgic', 'relaxed', 'artisan', 'cottage', 'wholesome'>"],
  "confidence": <0.0-1.0>
}

If Pantone colour names appear in the document (e.g. 'Pale Banana', 'Dusty Rose', 'Mistletoe'), include them exactly as written.
If text labels or section headings appear (e.g. 'Key Prints — US Only', 'Key Materials', 'Spring Farmhouse'), use them to inform your extraction.
Be specific and commercially actionable.
Return ONLY the JSON object, no prose, no markdown fences."""

# ---------------------------------------------------------------------------
# Analytical lenses — one chosen at random each generation run
# ---------------------------------------------------------------------------

IDEA_LENSES = [
    (
        "DESIGN & AESTHETICS: Lead with the visual and design story. Focus on products where "
        "the look and feel is the hero — colour, surface treatment, shape, print. Each idea "
        "should feel genuinely on-trend and visually distinctive. Prioritise ideas that would "
        "photograph well and drive impulse purchase based on appearance alone."
    ),
    (
        "FUNCTIONAL & LIFESTYLE FIT: Lead with what each product DOES for the customer and "
        "how it fits into their daily life. Focus on functional benefits, multi-use versatility, "
        "storage solutions, and lifestyle utility. Prioritise ideas that solve a real problem "
        "or make a daily ritual easier, more organised, or more enjoyable."
    ),
    (
        "MATERIALS & COMPOSITION: Lead with material choice and construction. Consider primary "
        "materials (solid wood, MDF, metal, rattan, concrete, resin, recycled materials), "
        "material combinations (e.g. wood + metal, cane + linen), surface finish direction "
        "(matte, gloss, brushed, ribbed, woven, lacquered), and sustainability credentials "
        "(FSC certified, recycled content, natural/biodegradable). Prioritise ideas where "
        "the material story is the key differentiator at Aldi's price point."
    ),
    (
        "COLOUR & PATTERN: Lead with colour palette and surface pattern. Consider dominant "
        "hues and tonal shifts (warm neutrals vs. cool greys, earthy terracottas, deep greens), "
        "mono vs. two-tone vs. pattern, colour blocking or contrast detailing, and pattern "
        "types (geometric, organic, textural, none). Prioritise ideas where a strong colour "
        "or pattern story makes the product stand out on shelf."
    ),
    (
        "TEXTURE & TACTILITY: Lead with how products feel and look up close. Consider surface "
        "texture (smooth, fluted, hammered, woven, embossed, raw), visual texture vs. physical "
        "texture, and grain direction and visibility in natural materials. Prioritise ideas "
        "where tactile richness creates a premium perception at an accessible price."
    ),
    (
        "HARDWARE & DETAILING: Lead with the finishing details that elevate a product. Consider "
        "handle and knob styles (fluted, tab pull, finger pull, integrated, no hardware), hinge "
        "and joint visibility (exposed vs. concealed), decorative vs. functional detailing, and "
        "edge profiles (rounded, chamfered, sharp, lipped). Prioritise ideas where considered "
        "detailing creates a quality feel that punches above Aldi's typical price tier."
    ),
    (
        "FUNCTIONAL FEATURES: Lead with practical innovation and internal functionality. Consider "
        "internal organisation (dividers, inserts, removable trays, adjustable shelving), lid "
        "types (hinged, removable, sliding, open top), ventilation or visibility (open, slatted, "
        "perforated, solid, glazed), and weight and portability (handles, wheels, lightweight "
        "construction). Prioritise ideas where a smart functional feature solves a real problem "
        "and justifies the purchase."
    ),
    (
        "SEASONAL & OCCASION FIT: Lead with timing and occasion relevance. Focus on products "
        "that are perfectly timed for an upcoming season, holiday, or consumer occasion. "
        "Prioritise ideas with strong gifting potential, seasonal shelf appeal, or that tap "
        "into a moment consumers are actively shopping for right now."
    ),
]

IDEAS_PROMPT_TEMPLATE = """You are a product development consultant for Aldi's home and general merchandise buying team.
Aldi's product philosophy: excellent quality at market-beating value prices, private-label focus,
limited SKU range, seasonal "Aldi Finds" format. Aldi customers love value-for-money finds that
feel on-trend without the premium price tag.

ANALYTICAL LENSES — apply ALL of the following simultaneously:
{lens}

TREND ANALYSIS FROM UPLOADED MOOD BOARD:
{trend_json}

SIMILAR PRODUCTS CURRENTLY IN THE MARKET (for inspiration and reference):
{products_summary}

{exclusion_block}Using the trend insights above and drawing inspiration from the real market products,
generate exactly {n} specific product ideas that Aldi could develop as private-label seasonal items.
Honour the analytical focus above by weighting your ideas toward that dimension, while still ensuring
variety across product categories.

Return ONLY valid JSON — an array of exactly {n} objects:
[
  {{
    "position": 1,
    "name": "<specific product name, e.g. 'Country Floral Linen Tea Towel Set of 3'>",
    "description": "<2-3 sentences describing the product including key visual attributes, materials, and dimensions if relevant>",
    "category": "<home category, e.g. 'Kitchen Textiles'>",
    "price_point": "<realistic Aldi price range, e.g. '$6.99–9.99'>",
    "rationale": "<2 sentences explaining why this fits the trend and will appeal to Aldi's customer>",
    "inspired_by_product_ids": [<integer product IDs from the market data above that inspired this idea — include at least 3 IDs, ideally 3–5>]
  }}
]

Ensure variety across product categories. Make names specific and commercial, not generic.
Each idea must have a minimum of 3 inspired_by_product_ids — never fewer.
Each idea must reference DIFFERENT inspired_by_product_ids — never use the same product ID across multiple ideas.
Return ONLY the JSON array, no prose, no markdown fences."""


class MoodBoardAnalyser:
    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.vision_model

    async def analyse_file(self, file_path: str, file_type: str) -> Optional[dict]:
        """
        Analyse a mood board file and return extracted trend attributes as a dict.
        """
        content_blocks = self._load_content_blocks(file_path, file_type)
        if not content_blocks:
            log.error("no_content_blocks_loaded", file_path=file_path)
            return None

        content = content_blocks + [{"type": "text", "text": MOOD_BOARD_PROMPT}]

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=1200,
                messages=[{"role": "user", "content": content}],
            )
            raw = response.content[0].text.strip()
            raw = _strip_fences(raw)
            return json.loads(raw)
        except Exception as exc:
            log.error("mood_board_analysis_failed", file_path=file_path, error=str(exc))
            return None

    async def generate_ideas(
        self,
        trend_data: dict,
        similar_products: list[dict],
        n: int = 10,
        previous_idea_names: list[str] | None = None,
    ) -> Optional[list[dict]]:
        """
        Generate Aldi product ideas based on trend analysis + similar DB products.

        Args:
            trend_data: Extracted trend attributes from mood board analysis.
            similar_products: Pool of similar products from the DB (pass top-50,
                              this method randomly samples 20 for variety).
            n: Number of ideas to generate (default 10).
            previous_idea_names: Names of ideas already generated for this
                                  upload/session — Claude will avoid repeating them.
        """
        # Randomly sample 20 from the provided pool for variety across regenerations
        pool = similar_products[:125]  # cap defensively
        sample = random.sample(pool, min(20, len(pool))) if len(pool) > 20 else pool

        if sample:
            products_summary = "\n".join(
                f"[ID:{p['id']}] {p['name']} | {p['retailer_name']} | "
                f"${p.get('price') or '?'} | "
                f"Colours: {', '.join((p.get('colours') or [])[:3])} | "
                f"Materials: {', '.join((p.get('materials') or [])[:3])}"
                for p in sample
            )
        else:
            products_summary = "No similar products found in database yet — use trend insights only."

        # All analytical lenses applied simultaneously
        lens = "\n".join(f"{i}. {l}" for i, l in enumerate(IDEA_LENSES, 1))

        # Exclusion block — avoid repeating ideas from previous generations
        if previous_idea_names:
            exclusion_lines = [
                "PREVIOUSLY GENERATED IDEAS — DO NOT REPEAT THESE:",
                "The following ideas were already generated for this mood board. "
                "You MUST produce completely different product ideas this time — "
                "different product types, different names, different angles:",
            ]
            for name in previous_idea_names:
                exclusion_lines.append(f"- {name}")
            exclusion_lines.append("")
            exclusion_block = "\n".join(exclusion_lines) + "\n"
        else:
            exclusion_block = ""

        prompt = IDEAS_PROMPT_TEMPLATE.format(
            lens=lens,
            trend_json=json.dumps(trend_data, indent=2),
            products_summary=products_summary,
            exclusion_block=exclusion_block,
            n=n,
        )

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            raw = _strip_fences(raw)
            return json.loads(raw)
        except Exception as exc:
            log.error("idea_generation_failed", error=str(exc))
            return None

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_content_blocks(self, file_path: str, file_type: str) -> list[dict]:
        """Load a document as Claude content blocks (image or text)."""
        path = pathlib.Path(file_path)
        if not path.exists():
            log.error("file_not_found", file_path=file_path)
            return []

        ft = file_type.lower().lstrip(".")
        if ft in ("jpeg", "jpg"):
            return [_image_block(path.read_bytes(), "image/jpeg")]
        if ft == "png":
            return [_image_block(path.read_bytes(), "image/png")]
        if ft == "pdf":
            return self._pdf_blocks(file_path)
        log.warning("unsupported_file_type", file_type=file_type)
        return []

    def _pdf_blocks(self, file_path: str) -> list[dict]:
        """Rasterise first 2 PDF pages → JPEG image blocks. Falls back to text."""
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(file_path, first_page=1, last_page=2, dpi=150)
            blocks = []
            for img in images:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                blocks.append(_image_block(buf.getvalue(), "image/jpeg"))
            if blocks:
                return blocks
        except Exception as exc:
            log.warning("pdf_rasterise_failed", error=str(exc))

        # Fallback: extract text via pypdf
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            text = "\n".join(page.extract_text() or "" for page in reader.pages[:4])
            if text.strip():
                log.info("pdf_text_fallback_used", chars=len(text))
                return [{"type": "text", "text": f"PDF text content:\n{text[:8000]}"}]
        except Exception as exc:
            log.error("pdf_text_fallback_failed", error=str(exc))

        return []


# ── Module-level helpers ──────────────────────────────────────────────────────

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
    """Remove markdown code fences if Claude wraps the JSON in them."""
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop first line (```json or ```) and last line (```)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        return "\n".join(inner)
    return text
