"""
Trend Engine.

Runs weekly after all products have been analysed. Steps:
1. Load all ProductAttributes for the current week
2. Cluster by embedding similarity (k-means) for structure
3. Build a holistic data payload (all cluster summaries + product samples)
4. Single Claude call with the full picture → 5-10 identified trends as JSON
5. Detect momentum vs prior week (rising / plateau / declining)
6. Persist Trend + TrendExample records
"""
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional
import numpy as np
from sklearn.cluster import MiniBatchKMeans
from sklearn.preprocessing import normalize
import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import select, and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import (
    Product, ProductAttributes, Retailer, Trend, TrendExample,
    TrendReport, TrendStatus
)

log = structlog.get_logger()

# Keywords that identify candle/fragrance products — these belong exclusively
# to the Fragrance tab and must be excluded from the general Trends analysis.
FRAGRANCE_EXCLUSION_KEYWORDS = [
    "candle", "diffuser", "fragrance", "scent", "wax melt", "reed",
    "incense", "aromatherapy", "room spray", "wax", "wick", "votive",
    "taper", "pillar candle", "soy", "beeswax", "home fragrance",
]

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

HOLISTIC_SYSTEM_PROMPT = """You are a senior home décor and lifestyle trends analyst for a retail intelligence platform. \
Your role is to identify meaningful, actionable trends emerging simultaneously across multiple retailers.

You will receive a structured dataset of home décor and storage products scraped from retailers across US, AU, and GB markets. \
Products have been pre-clustered by visual and attribute similarity to help you see patterns — use the clusters as evidence, \
not as the final answer. Trends may cut across clusters or merge several small ones.

YOUR TASK
Identify 5–10 distinct, meaningful trends from the data provided. Each trend must represent a genuine pattern — not a random \
cluster — that a retail buyer or product designer would find insightful and actionable.

WHAT MAKES A STRONG TREND
- Supported by products from at least 3 different retailers (5+ = strong, 7+ = very strong)
- Has a clear, nameable characteristic: colour palette, material, surface pattern, aesthetic style, \
  form/silhouette, functional concept, or seasonal theme
- Distinguishable from the other trends you identify in the same analysis
- Backed by real product evidence — do not invent trends not visible in the data

TREND CATEGORIES
Use the most specific category that fits. Multiple sub-signals within a category can be combined into one trend if they tell a coherent story.

- colour       → Dominant hues and tonal shifts (e.g. warm neutrals vs. cool greys, earthy terracottas, deep greens); \
mono vs. two-tone vs. pattern; colour blocking or contrast detailing
- pattern      → Pattern types across surfaces — geometric, organic, textural, hand-painted marks, tonal stripe, none
- material     → Primary material gaining traction (solid wood, MDF, metal, rattan, concrete, resin, recycled); \
material combinations (e.g. wood + metal, cane + linen); sustainability credentials (FSC, recycled content, biodegradable)
- finish       → Surface finish direction — matte, gloss, brushed, ribbed, woven, lacquered, raw; \
visual texture vs. physical texture; grain direction and visibility in natural materials; \
fluted, hammered, embossed, woven, smooth
- shape        → Silhouette evolution (curved, angular, minimal, sculptural); proportions — squat vs. tall, \
wide vs. narrow, oversized vs. compact; modular vs. fixed/monolithic; stackability and nestability
- hardware     → Handle and knob styles (fluted, tab pull, finger pull, integrated, no hardware); \
hinge and joint visibility (exposed vs. concealed); decorative vs. functional detailing; \
edge profiles — rounded, chamfered, sharp, lipped
- functional   → Internal organisation (dividers, inserts, removable trays, adjustable shelving); \
lid types (hinged, removable, sliding, open top); ventilation or visibility (open, slatted, perforated, solid, glazed); \
broader usage/behavioural shifts (visible kitchen organisation, layered scent rituals)
- style        → Aesthetic movement or design language (e.g. quiet luxury minimalism, coastal grandmother, japandi, \
maximalist revival, organic modernism)
- seasonal     → Upcoming season driver (e.g. autumnal harvest warmth, summer coastal refresh)

CROSS-MARKET INTELLIGENCE
Where a trend appears in both US and AU/GB markets, that signals strong global momentum. \
Where it appears in only one market, note it — it may be leading or lagging. \
Price tier helps identify whether a trend is mass-market or premium-first.

SCOPE RESTRICTION — CRITICAL
This dataset contains ONLY home décor, storage, and lifestyle products. \
Candles, fragrance, diffusers, wax melts, incense, room sprays, and all scented products are handled \
by a separate dedicated analysis and are NOT present in this dataset. \
Do NOT identify any fragrance, scent, candle, or aromatherapy trends. \
If you encounter any such products, ignore them entirely.

EVIDENCE THRESHOLD
Only include a trend if supported by ≥3 products from ≥3 retailers. \
5 strong trends are better than 10 weak ones. Skip a pattern if the evidence is thin. \
Trends seen in only 1–2 retailers are too narrow — they may be retailer-specific promotions, not true cross-market trends.

OUTPUT FORMAT
Respond ONLY with valid JSON — no prose before or after the JSON block.
Each run you will be given a specific analytical focus angle — honour it by weighting your trend selection toward that dimension, \
while still identifying any truly compelling trends outside it.

{
  "trends": [
    {
      "name": "<2–5 word evocative title, Title Case>",
      "description": "<1–2 sentences for a retail buyer: what this trend IS, what products it covers>",
      "rationale": "<3–5 sentences: WHY this trend is emerging now — cultural driver, consumer behaviour, \
season, or macro shift. Reference specific product names and retailers by name. Be specific, not generic.>",
      "category": "<colour | pattern | material | finish | shape | hardware | functional | style | seasonal>",
      "dominant_colours": ["<top 3–5 colours>"],
      "dominant_materials": ["<top 3–5 materials>"],
      "dominant_patterns": ["<top 3 patterns — empty list if not applicable>"],
      "dominant_styles": ["<top 3–5 style tags>"],
      "markets": ["<subset of US, AU, GB — markets with strongest evidence>"],
      "price_tier": "<budget | mid | premium | luxury>",
      "example_product_ids": [<5–10 integer product IDs from the input data that best represent this trend>],
      "retailer_spread": <integer — number of distinct retailers with products in this trend>,
      "confidence": "<high | medium | low>"
    }
  ]
}"""


# ---------------------------------------------------------------------------
# Analytical lenses — one is chosen at random each run to steer Claude
# toward a different dimension of the same product dataset.
# ---------------------------------------------------------------------------

ANALYSIS_LENSES = [
    (
        "COLOUR & MATERIAL INNOVATION: Focus especially on colour palettes and material stories. "
        "Look for emerging colour combinations, novel finishes, and material pairings gaining traction "
        "across multiple retailers. Prioritise trends defined primarily by HOW things look and feel."
    ),
    (
        "FUNCTIONAL & LIFESTYLE SHIFTS: Focus especially on HOW consumers use and organise their homes. "
        "Look for behavioural trends — visible storage, multi-room flexibility, ritual-led products, "
        "workspace integration. Prioritise trends defined by what products DO rather than how they look."
    ),
    (
        "STYLE AESTHETICS & FORM: Focus especially on aesthetic movements and silhouette/shape shifts. "
        "Look for design language evolution — new minimalism, maximalist revival, cultural fusions, "
        "organic vs geometric forms. Prioritise trends defined by the overall visual language and form."
    ),
    (
        "CROSS-MARKET & GLOBAL MOMENTUM: Focus especially on signals appearing simultaneously across "
        "US, AU, and GB markets. Trends confirmed in multiple geographies signal genuine global momentum. "
        "Also flag any market-specific emerging signal that could spread — leading indicators matter."
    ),
    (
        "NICHE & COUNTERINTUITIVE SIGNALS: Look BEYOND the most dominant clusters. Find the smaller, "
        "more specific, or counterintuitive patterns that could indicate the next big thing before it peaks. "
        "Challenge obvious interpretations. Prioritise specificity over breadth — narrow and real beats broad and vague."
    ),
    (
        "PRICE TIER & ACCESSIBILITY: Focus on how design trends move across price tiers. Which aesthetics "
        "are premium-first this cycle? Are any luxury signals trickling down to mid-market? Are there "
        "budget-segment design innovations that punch above their tier? Price tier context is key."
    ),
]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class TrendEngine:
    def __init__(self, db: AsyncSession, task=None):
        self.db = db
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.min_cluster_size = settings.trend_cluster_min_size
        self._task = task  # optional Celery task for progress reporting

    def _progress(self, pct: int, step: str):
        """Report progress back to Celery if a task handle is available."""
        if self._task:
            try:
                self._task.update_state(
                    state="PROGRESS",
                    meta={"pct": pct, "step": step},
                )
            except Exception:
                pass  # never let progress reporting break the pipeline

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    async def regenerate_analysis(self, week_start: Optional[datetime] = None) -> Optional[TrendReport]:
        """Generate a fresh set of trends without deleting the previous generation.

        - Keeps all existing Trend/TrendExample rows intact.
        - Passes every previously found trend name as an exclusion so Claude
          identifies genuinely different trends.
        - Saves new trends with generation = max_existing + 1.
        - Updates TrendReport.generation_count.
        """
        if week_start is None:
            today = datetime.utcnow().date()
            week_start = datetime.combine(
                today - timedelta(days=today.weekday()),
                datetime.min.time()
            )

        log.info("trend_regenerate_start", week_start=week_start.isoformat())
        self._progress(3, "Loading existing trends for exclusion…")

        # Collect ALL trend names across ALL generations for this week as exclusions
        prev_trends_result = await self.db.execute(
            select(
                Trend.name,
                Trend.category,
                Trend.dominant_colours,
                Trend.dominant_materials,
                Trend.dominant_styles,
                Trend.generation,
            ).where(Trend.week_start == week_start)
        )
        prev_rows = prev_trends_result.all()
        previously_found_trends: list[dict] = [
            {
                "name": row[0],
                "category": row[1] or "",
                "colours": row[2] or [],
                "materials": row[3] or [],
                "styles": row[4] or [],
            }
            for row in prev_rows
        ]
        max_generation = max((row[5] for row in prev_rows), default=0)
        next_generation = max_generation + 1

        log.info(
            "trend_regenerate_exclusions",
            excluded_count=len(previously_found_trends),
            next_generation=next_generation,
        )
        self._progress(5, f"Generating set {next_generation} (excluding {len(previously_found_trends)} prior trends)…")

        # Run the same clustering + Claude pipeline
        products_with_attrs = await self._load_products_with_attributes(week_start)
        if len(products_with_attrs) < self.min_cluster_size * 2:
            log.warning("insufficient_products", count=len(products_with_attrs))
            return None

        self._progress(15, f"Loaded {len(products_with_attrs):,} products — clustering…")
        items_by_id: dict[int, dict] = {item["product"].id: item for item in products_with_attrs}
        embeddings, product_ids = self._build_embedding_matrix(products_with_attrs)
        clusters = self._cluster(embeddings, product_ids, products_with_attrs)
        self._progress(30, f"Found {len(clusters)} clusters — sending to Claude…")

        prior_trends = await self._load_prior_trends(week_start)
        self._progress(40, "Analysing with Claude (this takes ~60s)…")

        trend_dicts = await self._holistic_analysis(
            clusters, items_by_id, week_start, prior_trends, previously_found_trends
        )
        if not trend_dicts:
            log.warning("no_trends_returned_on_regeneration")
            return None

        self._progress(85, f"Claude returned {len(trend_dicts)} trends — saving…")

        # Save new trends with next_generation
        new_trends: list[Trend] = []
        for td in trend_dicts:
            trend = self._build_trend_record(td, week_start, prior_trends, items_by_id)
            if trend:
                trend.generation = next_generation
                self.db.add(trend)
                new_trends.append((trend, td))

        await self.db.flush()

        # Pre-populate used_product_ids from ALL previous generations so no
        # example product is reused across sets.
        prior_example_ids_result = await self.db.execute(
            select(TrendExample.product_id)
            .join(Trend, TrendExample.trend_id == Trend.id)
            .where(Trend.week_start == week_start)
            .where(Trend.generation < next_generation)
        )
        used_product_ids: set[int] = set(prior_example_ids_result.scalars().all())
        used_image_urls: set[str] = set()
        log.info("example_products_excluded", count=len(used_product_ids), generation=next_generation)

        for trend, td in new_trends:
            await self._create_examples(trend, td, items_by_id, used_product_ids, used_image_urls)

        self._progress(95, "Updating report…")

        # Update TrendReport: append new trend IDs, bump generation_count
        report_result = await self.db.execute(
            select(TrendReport).where(TrendReport.week_start == week_start)
        )
        report = report_result.scalar_one_or_none()
        committed_trends = [t for t, _ in new_trends]
        new_ids = [t.id for t in committed_trends]

        if report:
            report.trend_ids = (report.trend_ids or []) + new_ids
            report.generation_count = next_generation
        else:
            # No report yet — generate one fresh
            report_values = await self._generate_report(week_start, committed_trends, len(products_with_attrs))
            report_values["generation_count"] = next_generation
            upsert_stmt = (
                pg_insert(TrendReport)
                .values(**report_values)
                .on_conflict_do_update(
                    constraint="trend_reports_week_start_key",
                    set_={k: v for k, v in report_values.items() if k != "week_start"},
                )
            )
            await self.db.execute(upsert_stmt)

        await self.db.commit()

        report_result = await self.db.execute(
            select(TrendReport).where(TrendReport.week_start == week_start)
        )
        report = report_result.scalar_one_or_none()

        log.info(
            "trend_regenerate_complete",
            new_trends=len(committed_trends),
            generation=next_generation,
            week_start=week_start.isoformat(),
        )
        return report

    # ------------------------------------------------------------------ #
    #  Data loading                                                        #
    # ------------------------------------------------------------------ #

    async def _load_products_with_attributes(self, week_start: datetime) -> list[dict]:
        week_end = week_start + timedelta(days=7)

        # Build exclusion filter: skip any product whose name or category
        # matches a fragrance/candle keyword — those belong in the Fragrance tab.
        fragrance_matches = [
            cond
            for kw in FRAGRANCE_EXCLUSION_KEYWORDS
            for cond in (Product.name.ilike(f"%{kw}%"), Product.category.ilike(f"%{kw}%"))
        ]
        not_fragrance = ~or_(*fragrance_matches)

        result = await self.db.execute(
            select(Product, ProductAttributes, Retailer)
            .join(ProductAttributes, Product.id == ProductAttributes.product_id)
            .join(Retailer, Product.retailer_id == Retailer.id)
            .where(
                and_(
                    Product.is_active == True,
                    ProductAttributes.embedding.isnot(None),
                    not_fragrance,
                )
            )
        )
        rows = result.all()
        return [{"product": p, "attrs": a, "retailer": r} for p, a, r in rows]

    async def _load_prior_trends(self, week_start: datetime) -> list[Trend]:
        prior_week = week_start - timedelta(days=7)
        result = await self.db.execute(
            select(Trend).where(Trend.week_start == prior_week)
        )
        return result.scalars().all()

    # ------------------------------------------------------------------ #
    #  Embedding + clustering                                              #
    # ------------------------------------------------------------------ #

    def _build_embedding_matrix(
        self, items: list[dict]
    ) -> tuple[np.ndarray, list[int]]:
        embeddings, product_ids = [], []
        for item in items:
            emb = item["attrs"].embedding
            if emb is not None:
                embeddings.append(emb)
                product_ids.append(item["product"].id)
        matrix = np.array(embeddings, dtype=np.float32)
        matrix = normalize(matrix, norm="l2")
        return matrix, product_ids

    def _cluster(
        self,
        embeddings: np.ndarray,
        product_ids: list[int],
        items: list[dict],
    ) -> list[dict]:
        n = len(embeddings)
        k = max(3, min(20, n // self.min_cluster_size))

        kmeans = MiniBatchKMeans(n_clusters=k, random_state=random.randint(0, 99999), n_init=5)
        labels = kmeans.fit_predict(embeddings)

        clusters_by_label: dict[int, list[dict]] = defaultdict(list)
        for idx, label in enumerate(labels):
            pid = product_ids[idx]
            item = next(x for x in items if x["product"].id == pid)
            clusters_by_label[label].append(item)

        valid = []
        for label, cluster_items in clusters_by_label.items():
            if len(cluster_items) < self.min_cluster_size:
                continue
            valid.append(self._summarise_cluster(cluster_items))

        valid.sort(key=lambda c: c["product_count"], reverse=True)
        return valid[:20]  # cap at 20 clusters to keep payload manageable

    def _summarise_cluster(self, items: list[dict]) -> dict:
        all_colours = Counter()
        all_materials = Counter()
        all_patterns = Counter()
        all_styles = Counter()
        all_functions = Counter()
        seasons: Counter = Counter()
        rooms: Counter = Counter()
        prices: list[float] = []
        retailer_slugs: set[str] = set()
        countries: set[str] = set()

        for item in items:
            attrs = item["attrs"]
            retailer = item["retailer"]
            retailer_slugs.add(retailer.slug)
            countries.add(retailer.country)

            for c in attrs.colours or []:
                all_colours[c.lower()] += 1
            for m in attrs.materials or []:
                all_materials[m.lower()] += 1
            for p in attrs.patterns or []:
                all_patterns[p.lower()] += 1
            for s in attrs.style_tags or []:
                all_styles[s.lower()] += 1
            for f in attrs.function_tags or []:
                all_functions[f.lower()] += 1
            if attrs.season:
                seasons[attrs.season] += 1
            if attrs.room:
                rooms[attrs.room] += 1
            if item["product"].price:
                prices.append(item["product"].price)

        return {
            "items": items,
            "product_count": len(items),
            "retailer_count": len(retailer_slugs),
            "retailer_names": list({item["retailer"].name for item in items}),
            "countries": sorted(countries),
            "dominant_colours": [c for c, _ in all_colours.most_common(5)],
            "dominant_materials": [m for m, _ in all_materials.most_common(5)],
            "dominant_patterns": [p for p, _ in all_patterns.most_common(3)],
            "dominant_styles": [s for s, _ in all_styles.most_common(4)],
            "dominant_functions": [f for f, _ in all_functions.most_common(4)],
            "dominant_season": seasons.most_common(1)[0][0] if seasons else "all-season",
            "dominant_room": rooms.most_common(1)[0][0] if rooms else "any",
            "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
        }

    # ------------------------------------------------------------------ #
    #  Holistic Claude analysis                                            #
    # ------------------------------------------------------------------ #

    async def _holistic_analysis(
        self,
        clusters: list[dict],
        items_by_id: dict[int, dict],
        week_start: datetime,
        prior_trends: list[Trend],
        previously_found: list[dict] | None = None,
    ) -> list[dict]:
        """Build a single structured payload and ask Claude to identify trends."""
        payload = self._build_analysis_payload(clusters, items_by_id, week_start, prior_trends, previously_found or [])
        log.debug("trend_payload_built", chars=len(payload))

        try:
            response = await self.client.messages.create(
                model=settings.nlp_model,
                max_tokens=8192,
                system=HOLISTIC_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": payload}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

            # Attempt full parse first
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Response may have been truncated — try to recover any complete
                # trend objects by extracting everything up to the last valid "},"
                # pattern inside the trends array.
                log.warning("holistic_analysis_partial_json", chars=len(raw))
                bracket = raw.find('"trends"')
                if bracket != -1:
                    # Find the opening "[" of the trends array
                    arr_start = raw.find("[", bracket)
                    if arr_start != -1:
                        # Walk backwards from the end to find the last complete "}"
                        truncated = raw[arr_start:]
                        last_brace = truncated.rfind("}")
                        if last_brace != -1:
                            repaired = '{"trends": ' + truncated[:last_brace + 1] + "]}"
                            try:
                                data = json.loads(repaired)
                                log.warning("holistic_analysis_repaired", trends=len(data.get("trends", [])))
                            except json.JSONDecodeError:
                                raise
                        else:
                            raise
                    else:
                        raise
                else:
                    raise

            trend_list = data.get("trends", [])
            log.info("claude_trends_parsed", count=len(trend_list))
            return trend_list

        except Exception as e:
            log.error("holistic_analysis_error", error=str(e))
            return []

    def _build_analysis_payload(
        self,
        clusters: list[dict],
        items_by_id: dict[int, dict],
        week_start: datetime,
        prior_trends: list[Trend],
        previously_found: list[dict] | None = None,
    ) -> str:
        """Assemble the structured text payload sent to Claude."""
        lines: list[str] = []

        # Header
        all_retailers = {item["retailer"].name for item in items_by_id.values()}
        all_countries = {item["retailer"].country for item in items_by_id.values()}
        lines.append(f"ANALYSIS PERIOD: Week of {week_start.strftime('%d %b %Y')}")
        lines.append(f"TOTAL PRODUCTS: {len(items_by_id):,}")
        lines.append(f"RETAILERS: {len(all_retailers)} ({', '.join(sorted(all_retailers))})")
        lines.append(f"MARKETS: {', '.join(sorted(all_countries))}")
        lines.append(f"PRIOR WEEK TRENDS AVAILABLE: {'Yes — ' + str(len(prior_trends)) + ' trends' if prior_trends else 'No (first run or no prior data)'}")
        lines.append("")

        # Apply all analytical lenses — consider every dimension simultaneously
        lines.append("=== ANALYTICAL LENSES — apply ALL of the following simultaneously ===")
        for i, lens in enumerate(ANALYSIS_LENSES, 1):
            lines.append(f"{i}. {lens}")
        lines.append("")

        # Cluster summaries
        lines.append("=== PRODUCT CLUSTERS ===")
        lines.append("(Clusters are computed by embedding similarity — use them as evidence, not hard boundaries.)")
        lines.append("")

        # Collect all sample product IDs so we know which ones to include below
        sample_ids_per_cluster: list[list[int]] = []

        for i, cluster in enumerate(clusters, 1):
            lines.append(f"Cluster {i}: {cluster['product_count']} products | "
                         f"{cluster['retailer_count']} retailers: {', '.join(cluster['retailer_names'][:6])} | "
                         f"Markets: {', '.join(cluster['countries'])}")
            lines.append(f"  Colours:   {', '.join(cluster['dominant_colours']) or '—'}")
            lines.append(f"  Materials: {', '.join(cluster['dominant_materials']) or '—'}")
            lines.append(f"  Patterns:  {', '.join(cluster['dominant_patterns']) or '—'}")
            lines.append(f"  Styles:    {', '.join(cluster['dominant_styles']) or '—'}")
            lines.append(f"  Functions: {', '.join(cluster['dominant_functions']) or '—'}")
            lines.append(f"  Season: {cluster['dominant_season']} | Room: {cluster['dominant_room']} | "
                         f"Avg price: {'${:.2f}'.format(cluster['avg_price']) if cluster['avg_price'] else 'N/A'}")

            # Pick 5 samples from this cluster: use the full cluster as the pool
            # so each Try Again genuinely surfaces different products. Weight toward
            # data-complete items by sorting, but sample from the entire cluster.
            sorted_items = sorted(
                cluster["items"],
                key=lambda x: (
                    bool(x["product"].price),
                    bool(x["product"].image_urls),
                    bool(x["product"].description),
                ),
                reverse=True,
            )
            sampled = random.sample(sorted_items, min(5, len(sorted_items)))
            sample_ids = [item["product"].id for item in sampled]
            sample_ids_per_cluster.append(sample_ids)
            lines.append(f"  Sample product IDs: {sample_ids}")
            lines.append("")

        # Detailed product samples
        lines.append("=== PRODUCT SAMPLES ===")
        lines.append("(These are the representative products whose IDs you should reference in example_product_ids.)")
        lines.append("")

        seen_ids: set[int] = set()
        for cluster_idx, sample_ids in enumerate(sample_ids_per_cluster, 1):
            for pid in sample_ids:
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                item = items_by_id.get(pid)
                if not item:
                    continue
                p = item["product"]
                a = item["attrs"]
                r = item["retailer"]

                price_str = f"${p.price:.2f}" if p.price else "N/A"
                colours = ", ".join((a.colours or [])[:4]) or "—"
                materials = ", ".join((a.materials or [])[:4]) or "—"
                styles = ", ".join((a.style_tags or [])[:3]) or "—"
                patterns = ", ".join((a.patterns or [])[:2]) or "—"

                lines.append(
                    f"[ID:{pid}] \"{p.name}\" | {r.name} ({r.country}) | {price_str} | "
                    f"colours: [{colours}] | materials: [{materials}] | "
                    f"styles: [{styles}] | patterns: [{patterns}]"
                )

        lines.append("")

        # Prior week context for momentum
        if prior_trends:
            lines.append("=== PRIOR WEEK TRENDS (for context only — do not just repeat these) ===")
            for pt in prior_trends[:10]:
                lines.append(
                    f"- \"{pt.name}\" ({pt.category}) | {pt.product_count} products | "
                    f"status: {pt.status}"
                )
            lines.append("")

        # Trends already identified in previous runs — must not repeat name, theme, OR attributes
        if previously_found:
            lines.append("=== ALREADY IDENTIFIED TRENDS — DO NOT REPEAT ANY OF THESE ===")
            lines.append(
                "These trends were found in a previous run on this SAME product dataset. "
                "You MUST generate COMPLETELY DIFFERENT trends this time. "
                "Avoid not just these exact names but their underlying themes, colour families, "
                "material categories, and aesthetic styles:"
            )
            for t in previously_found:
                parts = []
                if t.get("category"):
                    parts.append(f"type={t['category']}")
                if t.get("colours"):
                    parts.append(f"colours: {', '.join(t['colours'][:4])}")
                if t.get("materials"):
                    parts.append(f"materials: {', '.join(t['materials'][:4])}")
                if t.get("styles"):
                    parts.append(f"styles: {', '.join(t['styles'][:3])}")
                detail = f" [{'; '.join(parts)}]" if parts else ""
                lines.append(f"- \"{t['name']}\"{detail}")
            lines.append(
                "Think of the data from a fresh perspective — what patterns have NOT yet been named? "
                "Every trend you identify must be meaningfully distinct from the above list."
            )
            lines.append("")

        lines.append("Please identify 5–10 trends from the above data and respond in the JSON format specified.")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Build Trend DB records from Claude output                           #
    # ------------------------------------------------------------------ #

    def _build_trend_record(
        self,
        td: dict,
        week_start: datetime,
        prior_trends: list[Trend],
        items_by_id: dict[int, dict],
    ) -> Optional[Trend]:
        """Convert a Claude trend dict into a Trend ORM object."""
        name = (td.get("name") or "").strip()
        description = (td.get("description") or "").strip()
        rationale = (td.get("rationale") or "").strip()
        VALID_CATEGORIES = {
            "colour", "pattern", "material", "finish", "shape",
            "hardware", "functional", "style", "seasonal",
        }
        category = (td.get("category") or "style").strip().lower()
        if category not in VALID_CATEGORIES:
            category = "style"  # safe fallback

        if not name or not description:
            log.warning("trend_missing_required_fields", td=td)
            return None

        # Derive retailer info from example product IDs
        example_ids: list[int] = [
            pid for pid in td.get("example_product_ids", [])
            if isinstance(pid, int) and pid in items_by_id
        ]

        retailer_names_set: set[str] = set()
        prices: list[float] = []
        for pid in example_ids:
            item = items_by_id[pid]
            retailer_names_set.add(item["retailer"].name)
            if item["product"].price:
                prices.append(item["product"].price)

        # Fall back to Claude-reported retailer spread if we can't infer
        # Always derive retailer_count from the actual example products, not Claude's
        # self-reported retailer_spread (which is often inflated/guessed).
        retailer_count = len(retailer_names_set) or 1
        avg_price = round(sum(prices) / len(prices), 2) if prices else None

        # Hard minimum: discard trends spanning fewer than 3 retailers
        if len(retailer_names_set) < 3:
            log.info(
                "trend_filtered_insufficient_retailers",
                name=name,
                retailer_count=len(retailer_names_set),
                retailers=sorted(retailer_names_set),
            )
            return None

        # Momentum vs prior week
        status = TrendStatus.NEW
        momentum = None
        prev_id = None

        for prior in prior_trends:
            if self._is_same_theme(td, prior):
                delta = len(example_ids) - prior.product_count
                pct = (delta / prior.product_count * 100) if prior.product_count > 0 else 0
                momentum = round(pct, 1)
                prev_id = prior.id
                if pct > 10:
                    status = TrendStatus.RISING
                elif pct < -10:
                    status = TrendStatus.DECLINING
                else:
                    status = TrendStatus.PLATEAU
                break

        return Trend(
            week_start=week_start,
            name=name,
            description=description,
            rationale=rationale,
            category=category,
            status=status,
            product_count=len(example_ids) or retailer_count,
            retailer_count=retailer_count,
            retailer_names=sorted(retailer_names_set),
            avg_price=avg_price,
            momentum_pct=momentum,
            prev_trend_id=prev_id,
            dominant_colours=td.get("dominant_colours") or [],
            dominant_materials=td.get("dominant_materials") or [],
            dominant_patterns=td.get("dominant_patterns") or [],
            dominant_styles=td.get("dominant_styles") or [],
            markets=td.get("markets") or [],
            price_tier=td.get("price_tier"),
        )

    def _is_same_theme(self, td: dict, prior: Trend) -> bool:
        """Heuristic match: overlapping colours or materials with a prior trend."""
        new_colours = set(c.lower() for c in (td.get("dominant_colours") or [])[:3])
        prior_colours = set(c.lower() for c in (prior.dominant_colours or [])[:3])
        new_materials = set(m.lower() for m in (td.get("dominant_materials") or [])[:3])
        prior_materials = set(m.lower() for m in (prior.dominant_materials or [])[:3])

        colour_overlap = len(new_colours & prior_colours) >= 2
        material_overlap = len(new_materials & prior_materials) >= 2
        return colour_overlap or material_overlap

    # ------------------------------------------------------------------ #
    #  Trend examples                                                      #
    # ------------------------------------------------------------------ #

    async def _create_examples(
        self,
        trend: Trend,
        td: dict,
        items_by_id: dict[int, dict],
        used_product_ids: set[int] | None = None,
        used_image_urls: set[str] | None = None,
    ):
        """Create TrendExample rows for the products Claude cited.

        Products already assigned to an earlier trend are excluded (by product ID
        and by primary image URL) so the same image never appears on two trend cards.
        """
        if used_product_ids is None:
            used_product_ids = set()
        if used_image_urls is None:
            used_image_urls = set()

        def _primary_image(pid: int) -> str | None:
            p = items_by_id[pid]["product"]
            urls = p.image_urls or []
            return urls[0] if urls else p.primary_image_url

        example_ids: list[int] = list(dict.fromkeys(
            pid for pid in td.get("example_product_ids", [])
            if isinstance(pid, int)
            and pid in items_by_id
            and pid not in used_product_ids
            and (_primary_image(pid) is None or _primary_image(pid) not in used_image_urls)
        ))

        # Score each by data completeness for hero selection
        def completeness(pid: int) -> float:
            item = items_by_id[pid]
            p = item["product"]
            score = 0.0
            if p.price:
                score += 1.0
            if p.image_urls:
                score += len(p.image_urls) * 0.5
            if p.description:
                score += 1.0
            return score

        sorted_ids = sorted(example_ids, key=completeness, reverse=True)

        # Select top 10 by completeness, but ensure at least 2 retailers represented
        selected = sorted_ids[:10]
        selected_retailer_names = {
            items_by_id[pid]["retailer"].name for pid in selected
        }
        if len(selected_retailer_names) < 2 and len(sorted_ids) > 10:
            # Inject the best-scored product from a different retailer
            first_retailer = items_by_id[sorted_ids[0]]["retailer"].name
            for pid in sorted_ids[10:]:
                if items_by_id[pid]["retailer"].name != first_retailer:
                    selected = sorted_ids[:9] + [pid]
                    break

        for rank, pid in enumerate(selected):
            ex = TrendExample(
                trend_id=trend.id,
                product_id=pid,
                relevance_score=max(0.1, 1.0 - rank * 0.08),
                is_hero=(rank == 0),
            )
            self.db.add(ex)
            used_product_ids.add(pid)
            img = _primary_image(pid)
            if img:
                used_image_urls.add(img)

    # ------------------------------------------------------------------ #
    #  Weekly report                                                       #
    # ------------------------------------------------------------------ #

    async def _generate_report(
        self,
        week_start: datetime,
        trends: list[Trend],
        total_products: int,
    ) -> dict:
        """Build the TrendReport column values as a plain dict (caller does the upsert)."""
        retailer_count = len({r for t in trends for r in (t.retailer_names or [])})
        rising = [t for t in trends if t.status == TrendStatus.RISING]
        new_trends = [t for t in trends if t.status == TrendStatus.NEW]

        summary = (
            f"This week's analysis of {total_products:,} products across "
            f"{retailer_count} retailers identified {len(trends)} distinct trends. "
        )
        if rising:
            names = ", ".join(t.name for t in rising[:3])
            summary += f"{len(rising)} trend{'s are' if len(rising) > 1 else ' is'} rising: {names}. "
        if new_trends:
            summary += (
                f"{len(new_trends)} new trend{'s' if len(new_trends) > 1 else ''} emerged this week."
            )

        return {
            "week_start": week_start,
            "title": f"Home Décor & Storage Trend Report — Week of {week_start.strftime('%d %b %Y')}",
            "summary": summary,
            "trend_ids": [t.id for t in trends],
            "total_products_analysed": total_products,
            "retailers_covered": retailer_count,
        }
