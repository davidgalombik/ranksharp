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
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional
import numpy as np
from sklearn.cluster import MiniBatchKMeans
from sklearn.preprocessing import normalize
import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import (
    Product, ProductAttributes, Retailer, Trend, TrendExample,
    TrendReport, TrendStatus
)

log = structlog.get_logger()

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
- Supported by products from at least 2 different retailers (3+ = strong, 5+ = very strong)
- Has a clear, nameable characteristic: colour palette, material, surface pattern, aesthetic style, \
  form/silhouette, functional concept, or seasonal theme
- Distinguishable from the other trends you identify in the same analysis
- Backed by real product evidence — do not invent trends not visible in the data

TREND CATEGORIES
- colour    → A specific palette appearing across product categories (e.g. "warm terracotta across ceramics and textiles")
- material  → A material gaining traction (e.g. "ribbed stoneware", "woven rattan in storage")
- pattern   → Surface/visual pattern (e.g. "organic hand-painted marks", "tonal stripe")
- style     → Aesthetic movement (e.g. "quiet luxury minimalism", "coastal grandmother", "japandi")
- shape     → Silhouette or form (e.g. "curved organic forms replacing angular", "oversized statement vessels")
- seasonal  → Upcoming season driver (e.g. "autumnal harvest warmth")
- functional→ Usage/behavioural shift (e.g. "visible kitchen organisation", "layered scent rituals")

CROSS-MARKET INTELLIGENCE
Where a trend appears in both US and AU/GB markets, that signals strong global momentum. \
Where it appears in only one market, note it — it may be leading or lagging. \
Price tier helps identify whether a trend is mass-market or premium-first.

EVIDENCE THRESHOLD
Only include a trend if supported by ≥3 products from ≥2 retailers. \
5 strong trends are better than 10 weak ones. Skip a pattern if the evidence is thin.

FIRST-RUN NOTE
If no prior week context is provided, that is normal — treat all trends as NEW. \
Momentum detection requires at least two weeks of data.

OUTPUT FORMAT
Respond ONLY with valid JSON — no prose before or after the JSON block.

{
  "trends": [
    {
      "name": "<2–5 word evocative title, Title Case>",
      "description": "<1–2 sentences for a retail buyer: what this trend IS, what products it covers>",
      "rationale": "<3–5 sentences: WHY this trend is emerging now — cultural driver, consumer behaviour, \
season, or macro shift. Reference specific product names and retailers by name. Be specific, not generic.>",
      "category": "<colour | material | pattern | style | shape | seasonal | functional>",
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
# Engine
# ---------------------------------------------------------------------------

class TrendEngine:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.min_cluster_size = settings.trend_cluster_min_size

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    async def run_weekly_analysis(self, week_start: Optional[datetime] = None) -> Optional[TrendReport]:
        """Full weekly trend analysis pipeline."""
        if week_start is None:
            today = datetime.utcnow().date()
            week_start = datetime.combine(
                today - timedelta(days=today.weekday()),
                datetime.min.time()
            )

        log.info("trend_analysis_start", week_start=week_start.isoformat())

        # 1. Load all analysed products from this week
        products_with_attrs = await self._load_products_with_attributes(week_start)
        log.info("products_loaded", count=len(products_with_attrs))

        if len(products_with_attrs) < self.min_cluster_size * 2:
            log.warning("insufficient_products", count=len(products_with_attrs))
            return None

        # 2. Build a fast lookup by product ID
        items_by_id: dict[int, dict] = {
            item["product"].id: item for item in products_with_attrs
        }

        # 3. Build embedding matrix + cluster (for structure, not final truth)
        embeddings, product_ids = self._build_embedding_matrix(products_with_attrs)
        clusters = self._cluster(embeddings, product_ids, products_with_attrs)
        log.info("clusters_identified", count=len(clusters))

        # 4. Load prior week trends for momentum detection
        prior_trends = await self._load_prior_trends(week_start)

        # 5. Single holistic Claude call → list of trend dicts
        trend_dicts = await self._holistic_analysis(
            clusters, items_by_id, week_start, prior_trends
        )
        log.info("trends_from_claude", count=len(trend_dicts))

        if not trend_dicts:
            log.warning("no_trends_returned")
            return None

        # 6. Persist Trend records
        trends: list[Trend] = []
        for td in trend_dicts:
            trend = self._build_trend_record(td, week_start, prior_trends, items_by_id)
            if trend:
                self.db.add(trend)
                trends.append((trend, td))

        await self.db.flush()  # populate trend.id

        # 7. Create TrendExample records
        for trend, td in trends:
            await self._create_examples(trend, td, items_by_id)

        # 8. Generate weekly report
        committed_trends = [t for t, _ in trends]
        report = await self._generate_report(week_start, committed_trends, len(products_with_attrs))
        self.db.add(report)
        await self.db.commit()

        log.info(
            "trend_analysis_complete",
            trends=len(committed_trends),
            week_start=week_start.isoformat(),
        )
        return report

    # ------------------------------------------------------------------ #
    #  Data loading                                                        #
    # ------------------------------------------------------------------ #

    async def _load_products_with_attributes(self, week_start: datetime) -> list[dict]:
        week_end = week_start + timedelta(days=7)

        result = await self.db.execute(
            select(Product, ProductAttributes, Retailer)
            .join(ProductAttributes, Product.id == ProductAttributes.product_id)
            .join(Retailer, Product.retailer_id == Retailer.id)
            .where(
                and_(
                    Product.analysed_at >= week_start,
                    Product.analysed_at < week_end,
                    Product.last_seen_at >= week_start - timedelta(days=30),
                    ProductAttributes.embedding.isnot(None),
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

        kmeans = MiniBatchKMeans(n_clusters=k, random_state=42, n_init=5)
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
    ) -> list[dict]:
        """Build a single structured payload and ask Claude to identify trends."""
        payload = self._build_analysis_payload(clusters, items_by_id, week_start, prior_trends)
        log.debug("trend_payload_built", chars=len(payload))

        try:
            response = await self.client.messages.create(
                model=settings.nlp_model,
                max_tokens=4096,
                system=HOLISTIC_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": payload}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

            data = json.loads(raw)
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

            # Pick best 5 samples from this cluster (prefer price + image + description)
            sorted_items = sorted(
                cluster["items"],
                key=lambda x: (
                    bool(x["product"].price),
                    bool(x["product"].image_urls),
                    bool(x["product"].description),
                ),
                reverse=True,
            )
            sample_ids = [item["product"].id for item in sorted_items[:5]]
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
        category = (td.get("category") or "style").strip().lower()

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
        retailer_count = td.get("retailer_spread") or len(retailer_names_set) or 1
        avg_price = round(sum(prices) / len(prices), 2) if prices else None

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
    ):
        """Create TrendExample rows for the products Claude cited."""
        example_ids: list[int] = [
            pid for pid in td.get("example_product_ids", [])
            if isinstance(pid, int) and pid in items_by_id
        ]

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

        for rank, pid in enumerate(sorted_ids[:10]):
            ex = TrendExample(
                trend_id=trend.id,
                product_id=pid,
                relevance_score=max(0.1, 1.0 - rank * 0.08),
                is_hero=(rank == 0),
            )
            self.db.add(ex)

    # ------------------------------------------------------------------ #
    #  Weekly report                                                       #
    # ------------------------------------------------------------------ #

    async def _generate_report(
        self,
        week_start: datetime,
        trends: list[Trend],
        total_products: int,
    ) -> TrendReport:
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

        return TrendReport(
            week_start=week_start,
            title=f"Home Décor & Storage Trend Report — Week of {week_start.strftime('%d %b %Y')}",
            summary=summary,
            trend_ids=[t.id for t in trends],
            total_products_analysed=total_products,
            retailers_covered=retailer_count,
        )
