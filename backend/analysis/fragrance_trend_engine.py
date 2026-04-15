"""
Fragrance Trend Engine.

Analyses only candle and fragrance products from the database.
Covers aesthetic, scent profile, market, sustainability, and retail signals.
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
from sqlalchemy import select, func, and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import (
    Product, ProductAttributes, Retailer,
    FragranceTrend, FragranceTrendExample, FragranceTrendReport, TrendStatus,
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

FRAGRANCE_SYSTEM_PROMPT = """You are a senior fragrance and candle trend analyst for a retail intelligence platform.
Your role is to identify meaningful, actionable trends emerging simultaneously across multiple retailers in the \
candles, home fragrance, and scented products category.

You will receive a structured dataset of candle and fragrance products scraped from retailers across US, AU, and GB markets. \
Products have been pre-clustered by visual and attribute similarity — use clusters as evidence, not as the final answer.

YOUR TASK
Identify 5–10 distinct, meaningful fragrance and candle trends. Each must represent a genuine pattern \
a product designer or retail buyer would find insightful and actionable.

TREND CATEGORIES — use one per trend:
- aesthetic      → Visual design trends: colours, container style (glass/ceramic/tin/pillar/taper/votive), \
shape (sculptural, classic cylinder, novelty), surface pattern (marbled, striped, minimal)
- scent          → Fragrance profile trends: families (fresh/clean, floral, woody, gourmand, earthy/green), \
seasonal scents, multi-note complexity, clean/natural fragrances vs synthetic
- market         → Consumer behaviour: price point shift, gifting vs personal use, brand storytelling, \
lifestyle positioning, celebrity/designer collaborations
- sustainability → Wax type trends (soy, beeswax, coconut, paraffin moving out), eco packaging, \
refillable/reusable designs, non-toxic claims
- retail         → Cultural/platform signals: TikTok/Instagram-friendly aesthetics, occasion-based buying \
(self-care, sleep, meditation/wellness), seasonal buying cycles, crossover with beauty/wellness

WHAT MAKES A STRONG TREND
- Supported by products from at least 2 different retailers (3+ = strong, 5+ = very strong)
- Has a clear, nameable characteristic visible across multiple products
- Backed by real product evidence — do not invent trends not in the data
- 5 strong trends are better than 10 weak ones

EVIDENCE THRESHOLD
Only include a trend if supported by ≥3 products from ≥2 retailers. \
Single-retailer patterns are too narrow — they may reflect one brand's range, not a market trend.

OUTPUT FORMAT
Respond ONLY with valid JSON — no prose before or after.
Each run you will be given a specific analytical focus angle — honour it by weighting your trend selection \
toward that dimension, while still identifying any truly compelling trends outside it.

{
  "fragrance_trends": [
    {
      "name": "<2–5 word evocative title, Title Case>",
      "description": "<1–2 sentences: what this trend IS, what products it covers>",
      "rationale": "<3–5 sentences: WHY this trend is emerging now. Reference specific product names \
and retailers. Be specific, not generic.>",
      "category": "<aesthetic|scent|market|sustainability|retail>",
      "dominant_colours": ["<colour>", ...],
      "dominant_materials": ["<wax type or material>", ...],
      "container_styles": ["<glass|ceramic|tin|pillar|taper|votive|jar|diffuser|etc>", ...],
      "scent_families": ["<fresh|floral|woody|gourmand|earthy|spicy|citrus|etc>", ...],
      "sustainability_signals": ["<soy wax|eco packaging|refillable|non-toxic|etc>", ...],
      "markets": ["US", "AU"],
      "price_tier": "<budget|mid|premium|luxury>",
      "example_product_ids": [<integer product IDs from the data>]
    }
  ]
}"""


# ---------------------------------------------------------------------------
# Analytical lenses — one chosen at random each run
# ---------------------------------------------------------------------------

FRAGRANCE_LENSES = [
    (
        "SCENT PROFILE & OLFACTIVE FAMILIES: Focus especially on what fragrance notes and scent families "
        "are gaining traction. Look for shifts in consumer preference — are woody/earthy notes replacing "
        "florals? Are clean/transparent scents displacing heavy gourmands? Which olfactive directions "
        "are appearing simultaneously across multiple retailers?"
    ),
    (
        "CONTAINER AESTHETICS & VISUAL DESIGN: Focus especially on how candles and fragrance products "
        "LOOK. Look for container shape evolution, colour palette direction, surface treatment (ribbed, "
        "marbled, matte, glossy), and label/branding aesthetic. What visual language is emerging "
        "across the category right now?"
    ),
    (
        "SUSTAINABILITY & INGREDIENT STORY: Focus especially on sustainability signals and ingredient "
        "transparency. Look for wax type shifts (soy, coconut, beeswax, natural blends replacing "
        "paraffin), eco packaging, refillable formats, non-toxic and clean-burn claims, and how "
        "brands are telling the provenance story of their ingredients."
    ),
    (
        "RETAIL & LIFESTYLE POSITIONING: Focus especially on how fragrance products are being "
        "positioned in consumers' lives. Look for occasion-based buying (sleep, self-care, meditation, "
        "entertaining), gifting vs personal use signals, wellness crossover, TikTok/Instagram-friendly "
        "formats, and seasonal buying cycle drivers."
    ),
    (
        "PRICE ARCHITECTURE & GIFTING: Focus especially on how price tiers are shifting and how "
        "products are positioned around gifting. Which price points are growing? Are luxury signals "
        "trickling into mid-market? Are budget formats improving quality cues? How are gift sets, "
        "multi-packs, and discovery formats evolving across retailers?"
    ),
]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class FragranceTrendEngine:
    MIN_CLUSTER_SIZE = 3

    # Keywords used to identify fragrance/candle products
    _FRAGRANCE_KEYWORDS = [
        "candle", "diffuser", "fragrance", "scent", "wax melt", "reed",
        "incense", "aromatherapy", "room spray", "wax", "wick", "votive",
        "taper", "pillar candle", "soy", "beeswax", "home fragrance",
    ]

    def __init__(self, db: AsyncSession, task=None):
        self.db = db
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._task = task

    def _progress(self, pct: int, step: str):
        if self._task:
            try:
                self._task.update_state(
                    state="PROGRESS",
                    meta={"pct": pct, "step": step},
                )
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Main entry point                                                    #
    # ------------------------------------------------------------------ #

    async def regenerate_analysis(self) -> Optional[FragranceTrendReport]:
        """Generate a new set of fragrance trends without deleting previous generations.

        - Keeps all existing FragranceTrend/FragranceTrendExample rows intact.
        - Passes every previously found trend name as an exclusion so Claude finds
          genuinely different trends each time.
        - Saves new trends with generation = max_existing + 1.
        - Example products are never reused across generations.
        - Updates FragranceTrendReport.generation_count.
        """
        today = datetime.utcnow().date()
        week_start = datetime.combine(
            today - timedelta(days=today.weekday()),
            datetime.min.time()
        )

        log.info("fragrance_regenerate_start", week_start=week_start.isoformat())
        self._progress(3, "Loading existing fragrance trends for exclusion…")

        # Collect ALL trend names across ALL generations for this week as exclusions
        prev_trends_result = await self.db.execute(
            select(
                FragranceTrend.name,
                FragranceTrend.category,
                FragranceTrend.dominant_colours,
                FragranceTrend.dominant_materials,
                FragranceTrend.scent_families,
                FragranceTrend.generation,
            ).where(FragranceTrend.week_start == week_start)
        )
        prev_rows = prev_trends_result.all()
        previously_found_trends: list[dict] = [
            {
                "name": row[0],
                "category": row[1] or "",
                "colours": row[2] or [],
                "materials": row[3] or [],
                "scents": row[4] or [],
            }
            for row in prev_rows
        ]
        max_generation = max((row[5] for row in prev_rows), default=0)
        next_generation = max_generation + 1

        log.info(
            "fragrance_regenerate_exclusions",
            excluded_count=len(previously_found_trends),
            next_generation=next_generation,
        )
        self._progress(5, f"Generating set {next_generation} (excluding {len(previously_found_trends)} prior trends)…")

        products = await self._load_fragrance_products()
        if len(products) < self.MIN_CLUSTER_SIZE * 2:
            log.warning("fragrance_insufficient_products", count=len(products))
            return None

        self._progress(15, f"Loaded {len(products):,} fragrance products — clustering…")
        items_by_id = {item["product"].id: item for item in products}

        embeddings, product_ids = self._build_embedding_matrix(products)
        clusters = self._cluster(embeddings, product_ids, products)
        self._progress(30, f"Found {len(clusters)} clusters — sending to Claude…")

        prior_trends = await self._load_prior_trends(week_start)
        self._progress(40, "Analysing with Claude (this takes ~60s)…")

        trend_dicts = await self._holistic_analysis(
            clusters, items_by_id, week_start, prior_trends, previously_found_trends
        )
        if not trend_dicts:
            log.warning("no_fragrance_trends_returned_on_regeneration")
            return None

        self._progress(85, f"Claude returned {len(trend_dicts)} trends — saving…")

        new_trends = []
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
            select(FragranceTrendExample.product_id)
            .join(FragranceTrend, FragranceTrendExample.trend_id == FragranceTrend.id)
            .where(FragranceTrend.week_start == week_start)
            .where(FragranceTrend.generation < next_generation)
        )
        used_product_ids: set[int] = set(prior_example_ids_result.scalars().all())
        used_image_urls: set[str] = set()
        log.info("fragrance_example_products_excluded", count=len(used_product_ids), generation=next_generation)

        for trend, td in new_trends:
            await self._create_examples(trend, td, items_by_id, used_product_ids, used_image_urls)

        self._progress(95, "Updating fragrance report…")

        committed_trends = [t for t, _ in new_trends]
        new_ids = [t.id for t in committed_trends]

        report_result = await self.db.execute(
            select(FragranceTrendReport).where(FragranceTrendReport.week_start == week_start)
        )
        report = report_result.scalar_one_or_none()

        if report:
            report.trend_ids = (report.trend_ids or []) + new_ids
            report.generation_count = next_generation
        else:
            report_values = self._generate_report(week_start, committed_trends, len(products))
            report_values["generation_count"] = next_generation
            upsert_stmt = (
                pg_insert(FragranceTrendReport)
                .values(**report_values)
                .on_conflict_do_update(
                    constraint="fragrance_trend_reports_week_start_key",
                    set_={k: v for k, v in report_values.items() if k != "week_start"},
                )
            )
            await self.db.execute(upsert_stmt)

        await self.db.commit()

        report_result = await self.db.execute(
            select(FragranceTrendReport).where(FragranceTrendReport.week_start == week_start)
        )
        report = report_result.scalar_one_or_none()

        log.info(
            "fragrance_regenerate_complete",
            new_trends=len(committed_trends),
            generation=next_generation,
            week_start=week_start.isoformat(),
        )
        return report

    # ------------------------------------------------------------------ #
    #  Data loading                                                        #
    # ------------------------------------------------------------------ #

    async def _load_fragrance_products(self) -> list[dict]:
        """Load all active analysed candle/fragrance products with embeddings."""
        kw_conditions = []
        for kw in self._FRAGRANCE_KEYWORDS:
            kw_conditions.append(Product.name.ilike(f"%{kw}%"))
            kw_conditions.append(Product.category.ilike(f"%{kw}%"))

        result = await self.db.execute(
            select(Product, ProductAttributes, Retailer)
            .join(ProductAttributes, Product.id == ProductAttributes.product_id)
            .join(Retailer, Product.retailer_id == Retailer.id)
            .where(
                and_(
                    Product.is_active == True,
                    ProductAttributes.embedding.isnot(None),
                    or_(*kw_conditions),
                )
            )
        )
        rows = result.all()
        return [{"product": p, "attrs": a, "retailer": r} for p, a, r in rows]

    async def _load_prior_trends(self, week_start: datetime) -> list[FragranceTrend]:
        prior_week = week_start - timedelta(days=7)
        result = await self.db.execute(
            select(FragranceTrend).where(FragranceTrend.week_start == prior_week)
        )
        return result.scalars().all()

    # ------------------------------------------------------------------ #
    #  Embedding + clustering                                              #
    # ------------------------------------------------------------------ #

    def _build_embedding_matrix(self, items: list[dict]) -> tuple[np.ndarray, list[int]]:
        embeddings, product_ids = [], []
        for item in items:
            emb = item["attrs"].embedding
            if emb is not None:
                embeddings.append(emb)
                product_ids.append(item["product"].id)
        matrix = np.array(embeddings, dtype=np.float32)
        matrix = normalize(matrix, norm="l2")
        return matrix, product_ids

    def _cluster(self, embeddings: np.ndarray, product_ids: list[int], items: list[dict]) -> list[dict]:
        n = len(embeddings)
        k = max(3, min(15, n // self.MIN_CLUSTER_SIZE))

        # Random seed — different cluster groupings each run
        kmeans = MiniBatchKMeans(n_clusters=k, random_state=random.randint(0, 99999), n_init=5)
        labels = kmeans.fit_predict(embeddings)

        clusters_by_label: dict[int, list[dict]] = defaultdict(list)
        for idx, label in enumerate(labels):
            pid = product_ids[idx]
            item = next(x for x in items if x["product"].id == pid)
            clusters_by_label[label].append(item)

        valid = []
        for label, cluster_items in clusters_by_label.items():
            if len(cluster_items) < self.MIN_CLUSTER_SIZE:
                continue
            valid.append(self._summarise_cluster(cluster_items))

        valid.sort(key=lambda c: c["product_count"], reverse=True)
        return valid[:15]

    def _summarise_cluster(self, items: list[dict]) -> dict:
        all_colours = Counter()
        all_materials = Counter()
        all_styles = Counter()
        all_fragrances = Counter()
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
            for s in attrs.style_tags or []:
                all_styles[s.lower()] += 1
            if attrs.fragrance:
                all_fragrances[attrs.fragrance.lower()] += 1
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
            "dominant_styles": [s for s, _ in all_styles.most_common(4)],
            "dominant_fragrances": [f for f, _ in all_fragrances.most_common(4)],
            "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
        }

    # ------------------------------------------------------------------ #
    #  Claude analysis                                                     #
    # ------------------------------------------------------------------ #

    async def _holistic_analysis(
        self,
        clusters: list[dict],
        items_by_id: dict[int, dict],
        week_start: datetime,
        prior_trends: list[FragranceTrend],
        previously_found: list[dict] | None = None,
    ) -> list[dict]:
        payload = self._build_payload(clusters, items_by_id, week_start, prior_trends, previously_found or [])
        log.debug("fragrance_payload_built", chars=len(payload))

        try:
            response = await self.client.messages.create(
                model=settings.nlp_model,
                max_tokens=8192,
                system=FRAGRANCE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": payload}],
            )
            raw = response.content[0].text.strip()

            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("fragrance_partial_json", chars=len(raw))
                bracket = raw.find('"fragrance_trends"')
                if bracket != -1:
                    arr_start = raw.find("[", bracket)
                    if arr_start != -1:
                        truncated = raw[arr_start:]
                        last_brace = truncated.rfind("}")
                        if last_brace != -1:
                            repaired = '{"fragrance_trends": ' + truncated[:last_brace + 1] + "]}"
                            data = json.loads(repaired)
                            log.warning("fragrance_json_repaired", trends=len(data.get("fragrance_trends", [])))
                        else:
                            raise
                    else:
                        raise
                else:
                    raise

            trend_list = data.get("fragrance_trends", [])
            log.info("fragrance_claude_parsed", count=len(trend_list))
            return trend_list

        except Exception as e:
            log.error("fragrance_analysis_error", error=str(e))
            return []

    def _build_payload(
        self,
        clusters: list[dict],
        items_by_id: dict[int, dict],
        week_start: datetime,
        prior_trends: list[FragranceTrend],
        previously_found: list[dict] | None = None,
    ) -> str:
        lines: list[str] = []

        all_retailers = {item["retailer"].name for item in items_by_id.values()}
        all_countries = {item["retailer"].country for item in items_by_id.values()}
        lines.append(f"ANALYSIS PERIOD: Week of {week_start.strftime('%d %b %Y')}")
        lines.append(f"TOTAL FRAGRANCE PRODUCTS: {len(items_by_id):,}")
        lines.append(f"RETAILERS: {len(all_retailers)} ({', '.join(sorted(all_retailers))})")
        lines.append(f"MARKETS: {', '.join(sorted(all_countries))}")
        lines.append("")

        # Apply all analytical lenses — consider every dimension simultaneously
        lines.append("=== ANALYTICAL LENSES — apply ALL of the following simultaneously ===")
        for i, lens in enumerate(FRAGRANCE_LENSES, 1):
            lines.append(f"{i}. {lens}")
        lines.append("")

        lines.append("=== PRODUCT CLUSTERS ===")
        lines.append("")

        sample_ids_per_cluster: list[list[int]] = []
        for i, cluster in enumerate(clusters, 1):
            lines.append(
                f"Cluster {i}: {cluster['product_count']} products | "
                f"{cluster['retailer_count']} retailers: {', '.join(cluster['retailer_names'][:6])} | "
                f"Markets: {', '.join(cluster['countries'])}"
            )
            lines.append(f"  Colours:    {', '.join(cluster['dominant_colours']) or '—'}")
            lines.append(f"  Materials:  {', '.join(cluster['dominant_materials']) or '—'}")
            lines.append(f"  Styles:     {', '.join(cluster['dominant_styles']) or '—'}")
            lines.append(f"  Fragrances: {', '.join(cluster['dominant_fragrances']) or '—'}")
            lines.append(f"  Avg price:  {'${:.2f}'.format(cluster['avg_price']) if cluster['avg_price'] else 'N/A'}")

            # Sort by completeness but sample from the full cluster so every
            # Try Again can surface different products.
            sorted_items = sorted(
                cluster["items"],
                key=lambda x: (bool(x["product"].price), bool(x["product"].image_urls), bool(x["product"].description)),
                reverse=True,
            )
            sampled = random.sample(sorted_items, min(5, len(sorted_items)))
            sample_ids = [item["product"].id for item in sampled]
            sample_ids_per_cluster.append(sample_ids)
            lines.append(f"  Sample product IDs: {sample_ids}")
            lines.append("")

        lines.append("=== PRODUCT SAMPLES ===")
        lines.append("")

        seen_ids: set[int] = set()
        for sample_ids in sample_ids_per_cluster:
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
                fragrance = a.fragrance or "—"
                desc_snippet = (p.description or "")[:120].replace("\n", " ")

                lines.append(
                    f"[ID:{pid}] \"{p.name}\" | {r.name} ({r.country}) | {price_str} | "
                    f"colours: [{colours}] | materials: [{materials}] | fragrance: {fragrance} | "
                    f"desc: {desc_snippet}"
                )

        lines.append("")

        if prior_trends:
            lines.append("=== PRIOR WEEK FRAGRANCE TRENDS (context only) ===")
            for pt in prior_trends[:8]:
                lines.append(f"- \"{pt.name}\" ({pt.category}) | {pt.product_count} products | status: {pt.status}")
            lines.append("")

        # Exclusion list — must not repeat name, theme, scent family, or colour family
        if previously_found:
            lines.append("=== ALREADY IDENTIFIED TRENDS — DO NOT REPEAT ANY OF THESE ===")
            lines.append(
                "These trends were found in a previous run on this SAME product dataset. "
                "You MUST generate COMPLETELY DIFFERENT trends this time. "
                "Avoid not just these exact names but their underlying themes, scent families, "
                "colour families, and aesthetic styles:"
            )
            for t in previously_found:
                parts = []
                if t.get("category"):
                    parts.append(f"type={t['category']}")
                if t.get("colours"):
                    parts.append(f"colours: {', '.join(t['colours'][:4])}")
                if t.get("materials"):
                    parts.append(f"materials: {', '.join(t['materials'][:4])}")
                if t.get("scents"):
                    parts.append(f"scents: {', '.join(t['scents'][:4])}")
                detail = f" [{'; '.join(parts)}]" if parts else ""
                lines.append(f"- \"{t['name']}\"{detail}")
            lines.append(
                "Think of the data from a fresh perspective — what fragrance patterns have NOT yet been named? "
                "Every trend you identify must be meaningfully distinct from the above list."
            )
            lines.append("")

        lines.append("Please identify 5–10 fragrance and candle trends from the above data and respond in the JSON format specified.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  Build DB records                                                    #
    # ------------------------------------------------------------------ #

    def _build_trend_record(
        self,
        td: dict,
        week_start: datetime,
        prior_trends: list[FragranceTrend],
        items_by_id: dict[int, dict],
    ) -> Optional[FragranceTrend]:
        name = (td.get("name") or "").strip()
        description = (td.get("description") or "").strip()
        rationale = (td.get("rationale") or "").strip()

        VALID_CATEGORIES = {"aesthetic", "scent", "market", "sustainability", "retail"}
        category = (td.get("category") or "aesthetic").strip().lower()
        if category not in VALID_CATEGORIES:
            category = "aesthetic"

        if not name or not description:
            log.warning("fragrance_trend_missing_fields", td=td)
            return None

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

        retailer_count = len(retailer_names_set) or 1
        avg_price = round(sum(prices) / len(prices), 2) if prices else None

        if len(retailer_names_set) < 2:
            log.info(
                "fragrance_trend_filtered_insufficient_retailers",
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
            if prior.name.lower() == name.lower():
                delta = len(example_ids) - prior.product_count
                pct = (delta / prior.product_count * 100) if prior.product_count > 0 else 0
                momentum = round(pct, 1)
                prev_id = prior.id
                status = TrendStatus.RISING if pct > 10 else (TrendStatus.DECLINING if pct < -10 else TrendStatus.PLATEAU)
                break

        return FragranceTrend(
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
            container_styles=td.get("container_styles") or [],
            scent_families=td.get("scent_families") or [],
            sustainability_signals=td.get("sustainability_signals") or [],
            markets=td.get("markets") or [],
            price_tier=td.get("price_tier"),
        )

    async def _create_examples(
        self,
        trend: FragranceTrend,
        td: dict,
        items_by_id: dict[int, dict],
        used_product_ids: set[int] | None = None,
        used_image_urls: set[str] | None = None,
    ):
        """Create FragranceTrendExample rows — each product/image appears in at most one trend card."""
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

        def completeness(pid: int) -> float:
            p = items_by_id[pid]["product"]
            score = 0.0
            if p.price:
                score += 1.0
            if p.image_urls:
                score += len(p.image_urls) * 0.5
            if p.description:
                score += 1.0
            return score

        sorted_ids = sorted(example_ids, key=completeness, reverse=True)
        selected = sorted_ids[:10]

        # Ensure at least 2 retailers in the examples
        selected_retailers = {items_by_id[pid]["retailer"].name for pid in selected}
        if len(selected_retailers) < 2 and len(sorted_ids) > 10:
            first_retailer = items_by_id[sorted_ids[0]]["retailer"].name
            for pid in sorted_ids[10:]:
                if items_by_id[pid]["retailer"].name != first_retailer:
                    selected = sorted_ids[:9] + [pid]
                    break

        for rank, pid in enumerate(selected):
            ex = FragranceTrendExample(
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

    def _generate_report(
        self,
        week_start: datetime,
        trends: list[FragranceTrend],
        total_products: int,
    ) -> dict:
        """Build the FragranceTrendReport column values as a plain dict (caller does the upsert)."""
        retailer_count = len({r for t in trends for r in (t.retailer_names or [])})
        rising = [t for t in trends if t.status == TrendStatus.RISING]
        new_trends = [t for t in trends if t.status == TrendStatus.NEW]

        summary = (
            f"Fragrance trend analysis of {total_products:,} candle and fragrance products across "
            f"{retailer_count} retailers identified {len(trends)} distinct trends. "
        )
        if rising:
            names = ", ".join(t.name for t in rising[:3])
            summary += f"{len(rising)} trend{'s are' if len(rising) > 1 else ' is'} rising: {names}. "
        if new_trends:
            summary += f"{len(new_trends)} new trend{'s' if len(new_trends) > 1 else ''} identified."

        return {
            "week_start": week_start,
            "title": f"Candle & Fragrance Trend Report — Week of {week_start.strftime('%d %b %Y')}",
            "summary": summary,
            "trend_ids": [t.id for t in trends],
            "total_products_analysed": total_products,
            "retailers_covered": retailer_count,
        }
