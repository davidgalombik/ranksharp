"""
InStoreTrendEngine — analogue of TrendEngine that runs over the In-store
Products catalogue (InStoreCatalogueItem rows) instead of the Online
Products table.

Pipeline:
1. Load hero/main catalogue items that have embeddings.
2. Cluster the embeddings with MiniBatchKMeans.
3. Summarise each cluster (top colours, materials, patterns, style_tags,
   taxonomy buckets).
4. Send the cluster summaries to Claude, ask it to identify named trends
   with rationale + dominant attributes.
5. Persist as InStoreTrendReport + InStoreTrend + InStoreTrendExample,
   linking each trend to a few representative items.

Skipped vs TrendEngine: retailer counts, market/country aggregation,
price tiers, fragrance-keyword exclusion.
"""
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import structlog
from anthropic import AsyncAnthropic
from sklearn.cluster import MiniBatchKMeans
from sklearn.preprocessing import normalize
from sqlalchemy import select, and_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import (
    InStoreCatalogueItem, InStoreCatalogueImage,
    InStoreTrend, InStoreTrendExample, InStoreTrendRecommendation, InStoreTrendReport,
    Product, ProductAttributes, ScrapeStatus,
    TrendStatus,
)

# Minimum cosine similarity required to recommend an online product for a trend.
# The hash-based keyword embedding scheme in analysis/embeddings.py produces
# tiny cosine values even for clearly related items: two 1536-dim vectors with
# 10 shared keyword boosts (+2.0 each) score ~0.025; 50 shared keywords scores
# ~0.13. So this threshold is a noise floor, not a quality gate. Once the
# embedding scheme is upgraded to a real semantic encoder, raise this to 0.5+.
def _month_range(
    months_window: Optional[int],
    now: Optional[datetime] = None,
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Compute the [lower, upper) bounds on InStoreCatalogueImage.created_at
    for a calendar-month horizon. Returns (None, None) for all-time.

    Semantics (see _load_items docstring):
      months_window == 0  → current calendar month only (partial).
                            e.g. Sep 10 → [Sep 1 00:00, Oct 1 00:00).
      months_window == 1  → previous full month only.
                            e.g. Sep 10 → [Aug 1 00:00, Sep 1 00:00).
      months_window >= 2  → trailing N calendar months INCLUDING current.
                            e.g. Sep 10, N=3 → [Jul 1 00:00, Oct 1 00:00).
      months_window is None (or negative) → (None, None), no filter.
    """
    if months_window is None or months_window < 0:
        return (None, None)
    now = now or datetime.utcnow()
    first_of_this_month = datetime(now.year, now.month, 1)
    # First day of NEXT month, used as the exclusive upper bound so
    # today's uploads aren't sliced off by hour-of-day drift.
    if now.month == 12:
        first_of_next_month = datetime(now.year + 1, 1, 1)
    else:
        first_of_next_month = datetime(now.year, now.month + 1, 1)

    if months_window == 0:
        # "This month" — current partial calendar month only.
        return (first_of_this_month, first_of_next_month)

    if months_window == 1:
        # "Last month" — previous calendar month only, excluding current.
        # Walk back one month from first_of_this_month.
        if first_of_this_month.month == 1:
            lo = datetime(first_of_this_month.year - 1, 12, 1)
        else:
            lo = datetime(first_of_this_month.year, first_of_this_month.month - 1, 1)
        return (lo, first_of_this_month)

    # months_window >= 2: current + (months_window - 1) previous
    months_back = months_window - 1
    y, m = first_of_this_month.year, first_of_this_month.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    lo = datetime(y, m, 1)
    return (lo, first_of_next_month)


RECOMMENDATION_THRESHOLD = 0.02
# Max number of online product recommendations stored per trend.
# Tuned 10 -> 50 -> 25 (2026-08-07). Field data: even at 50, each
# pgvector query without the HNSW index takes ~55s (sequential scan
# of ~156k products). 12 trends × 55s = 11 min recommendation phase.
# 25 halves the per-query TOPN sort cost and cuts the total phase
# to ~5-6 min. Real fix is restoring the HNSW index — bump this back
# up once disk headroom is sorted.
RECOMMENDATIONS_PER_TREND = 25

log = structlog.get_logger()


# Buyer-voice momentum (single store walk) → legacy TrendStatus enum, for
# anything still keyed on `status`. The momentum string is the source of
# truth and is what the UI renders.
MOMENTUM_TO_STATUS = {
    "emerging":     TrendStatus.NEW,
    "noted":        TrendStatus.PLATEAU,
    "prominent":    TrendStatus.PLATEAU,
    "strong_focus": TrendStatus.RISING,
    "shifting":     TrendStatus.RISING,
}
VALID_MOMENTUM = frozenset(MOMENTUM_TO_STATUS)
VALID_CATEGORIES = frozenset({
    "colour", "material", "pattern", "motif", "style", "shape", "product_form", "season",
})

# Written from the buying team's own trend boards (2026-09-10): eight
# boards, ~40 callouts, all US Harvest/Halloween season. Every callout
# follows SIGNAL → MOMENTUM → FEEL; the momentum ladder and the example
# sentences below are lifted from those boards, rewritten only where the
# original relied on prior-visit history Claude doesn't have here.
SYSTEM_PROMPT = """You are a retail buyer writing up a store walk. You have just analysed a batch of shelf photos from physical stores and are recording the trends you saw, in the exact voice the buying team uses on their trend boards.

HOW THE TEAM WRITES A TREND
Every callout follows the shape: SIGNAL → MOMENTUM → FEEL.
  - Signal: the thing itself — a material, a pattern, a motif, a colour palette, a season, a style, or a specific product form.
  - Momentum: how present it is right now (see the ladder below).
  - Feel: what it creates for the shopper — "creating a warm, organic and handcrafted aesthetic", "adding a fun graphic touch", "bringing a natural yet elevated feel". "Elevated" is the team's word for premium positioning; use it when the assortment reads as a step up from traditional.

One sentence for a sighting. Up to three sentences only when describing a TRANSITION — a palette moving from one season to the next, or a store shifting its focus.

MOMENTUM LADDER — single store walk. You are seeing one batch with no prior-visit history.
  - emerging      — small but distinct: a new pattern, a new motif, or a faux / "-look" version of a stronger trend appearing at everyday price points
  - noted         — present and worth recording, but not dominant
  - prominent     — repeats widely across products and categories
  - strong_focus  — the store has clearly built around it; it's everywhere ("Strong focus on…", "Heavily focused on…", "Dominant … colour story")
  - shifting      — a palette or seasonal focus is visibly transitioning within this batch (colour and season trends only)
Do NOT write that a trend is "still" here, "continues", "remains", is "growing steadily", or is "fading" — those need visit-to-visit history you do not have. Describe only what is present in this batch.

WHAT COUNTS AS A TREND
  - Group by the UNDERLYING SIGNAL, not by product type. A pumpkin, a storage box and a platter belong together if they're all natural materials. Cross product categories freely.
  - Materials: distinguish real from faux. Real natural materials (rattan, wood, woven, marble, bamboo) are usually prominent or strong focus. Faux or "-look" versions (rattan-look, marble-look, faux fur, faux woven) are usually emerging — and that shift is worth naming on its own.
  - Colour: name 3–5 specific colours ("Warm Rust, Pumpkin, Forest Green"), anchor them to the season, and describe the transition when one is visible ("…while Black, Espresso and Burgundy transition the palette to Halloween. Antique gold and amber connect both.").
  - Season: the season a store is focused on is itself a trend — "Heavily focused on Halloween", "Halloween emerging", or a departing season noted as lingering.
  - Motifs (dogs, fruit, bows, pumpkins) are separate from geometric or textile patterns (plaid, stripes, dots, dash, needlepoint, leopard, tortoiseshell).
  - Product form: a specific buyable format with its construction detail is a trend — "fabric bins with wood handles", "plastic storage bins with embossed designs", "printed bamboo hampers".
  - Name a retailer or brand when it typifies the trend. Put a coined aesthetic in quotes: "Organic Modern", "Fantastical Forest / Enchanted Nature".
  - A trend must repeat across multiple distinct products. Don't invent one off a single photo.
  - Use short, stable trend names so the same trend can be recognised on the next store walk.

EXAMPLES OF THE VOICE (name → description · momentum · category)
  - "Strong focus on natural materials" → "Rattan, wood and woven textures are prominent across the store, creating a warm, organic and handcrafted aesthetic." · strong_focus · material
  - "Faux woven natural materials emerging" → "Faux woven and rattan-look finishes are appearing across storage and home accessories — the natural look reaching everyday price points." · emerging · material
  - "Marble and marble-look finishes" → "Marble and marble-look finishes are prominent across serveware and kitchen prep, bringing a natural yet elevated feel." · prominent · material
  - "Plaid in warm earthy tones" → "Plaid is prominent across drinkware, storage and soft furnishings, in warm earthy Harvest/Fall tones." · prominent · pattern
  - "Polka dots as a playful trend" → "Polka dots are emerging across housewares, adding a fun graphic touch." · emerging · pattern
  - "Pet-inspired motifs" → "Pet-inspired products are prominent, with dog and cat motifs across multiple categories." · prominent · motif
  - "Harvest palette shifting toward Halloween" → "Seasonal colours are becoming deeper, richer and more sophisticated. Warm Rust, Pumpkin and Forest Green establish the Harvest story while Black, Espresso and Burgundy transition the palette to Halloween. Antique gold and amber connect both themes and add an elevated finish." · shifting · colour
  - "Heavily focused on Halloween" → "A dominant black and white colour story represents Halloween through graphic prints, stripes, spots and novelty motifs." · strong_focus · season
  - "Fabric bins with wood handles" → "Fabric bins with wood handles and faux fur bins are standout pieces, with cosy textures, soft finishes and warm neutral tones while still being practical for everyday storage." · prominent · product_form
  - "'Organic Modern' architectural pieces" → "Decorative mountain-range pieces from Hearth & Hand with Magnolia represent the premium and architectural 'Organic Modern' style." · noted · style

OUTPUT
Return ONLY JSON in this shape, no prose, no markdown fences:

{
  "trends": [
    {
      "name": "<the trend as a short buyer headline, 3–8 words, e.g. 'Strong focus on natural materials'>",
      "description": "<the full callout in the team's voice: signal → momentum → feel. 1 sentence; up to 3 for a transition>",
      "rationale": "<1 sentence: the concrete visual evidence — which products, roughly how many, which categories>",
      "momentum": "<one of: emerging | noted | prominent | strong_focus | shifting>",
      "category": "<one of: colour | material | pattern | motif | style | shape | product_form | season>",
      "dominant_colours": ["warm rust", "pumpkin"],
      "dominant_materials": ["rattan", "wood"],
      "dominant_patterns": ["plaid"],
      "dominant_styles": ["elevated", "handcrafted"],
      "dominant_taxonomy": ["Storage & Organization > Baskets", "Tabletop > Serveware"],
      "supporting_cluster_indices": [0, 2]
    }
  ]
}

Aim for 8–14 distinct trends. Materials and patterns usually account for the most; colour palettes and the seasonal focus next; product forms, motifs and styles where the evidence supports them. Skip thin or speculative ones — quality over count."""


class InStoreTrendEngine:
    def __init__(self, db: AsyncSession, task=None):
        self.db = db
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.min_cluster_size = max(5, settings.trend_cluster_min_size)
        self._task = task

    # ── Progress reporting ───────────────────────────────────────────────

    def _progress(self, pct: int, step: str):
        if self._task:
            try:
                self._task.update_state(state="PROGRESS", meta={"pct": pct, "step": step})
            except Exception:
                pass

    # ── Public entry point ────────────────────────────────────────────────

    async def regenerate_analysis(
        self,
        week_start: Optional[datetime] = None,
        months_window: Optional[int] = None,
        country: Optional[str] = None,
    ) -> Optional[InStoreTrendReport]:
        """Run a fresh in-store trend analysis. If a report for `week_start`
        already exists, append a new generation (Try Again) — keeping prior
        trends and their examples intact.

        `months_window` scopes the analysed items to those whose parent
        InStoreCatalogueImage was uploaded within the last N months.
        None (default) = all time. Buyers pick this from a pill selector
        on the trends page. Written back to the report row so the header
        can label the horizon that produced these trends.

        `country` scopes to shelf photos tagged with that country code
        ('US' or 'AU'). None (default) = mixed across every country.
        Buyers don't usually want to conflate different markets so the
        UI defaults to a specific country; None is here for the explicit
        'All countries' pill.
        """
        if week_start is None:
            today = datetime.utcnow().date()
            week_start = datetime.combine(
                today - timedelta(days=today.weekday()),
                datetime.min.time(),
            )

        log.info(
            "instore_trend_run_start",
            week_start=week_start.isoformat(),
            months_window=months_window,
            country=country,
        )
        self._progress(3, "Loading prior trends for exclusion…")

        # Generation numbering is per-week across EVERY country/horizon so
        # Set numbers never collide — /sets groups by generation alone and
        # delete-set targets by generation alone.
        gen_row = await self.db.execute(
            select(func.max(InStoreTrend.generation))
            .where(InStoreTrend.week_start == week_start)
        )
        max_generation = int(gen_row.scalar_one_or_none() or 0)
        next_generation = max_generation + 1

        # The exclusion list is scoped to the SAME country + horizon: a Try
        # Again on US · Last 3 mo should find different angles from the
        # earlier US · Last 3 mo Set, but a run at a different horizon or
        # country is free to re-find the same trends — that's how the buying
        # team tracks a trend from visit to visit.
        def _same_scope(q):
            q = (q.where(InStoreTrend.months_window == months_window)
                 if months_window is not None
                 else q.where(InStoreTrend.months_window.is_(None)))
            q = (q.where(InStoreTrend.country == country)
                 if country
                 else q.where(InStoreTrend.country.is_(None)))
            return q

        prev_result = await self.db.execute(_same_scope(
            select(InStoreTrend.name).where(InStoreTrend.week_start == week_start)
        ))
        previously_found = [r[0] for r in prev_result.all()]

        window_label = (
            f"last {months_window} month{'s' if months_window != 1 else ''}"
            if months_window else "all time"
        )
        country_label = country or "all countries"
        self._progress(8, f"Loading in-store items ({country_label}, {window_label})…")
        items = await self._load_items(months_window=months_window, country=country)
        if len(items) < self.min_cluster_size * 2:
            log.warning(
                "instore_trend_insufficient_items",
                count=len(items),
                months_window=months_window,
                country=country,
            )
            return None

        items_by_id: dict[int, dict] = {it["item"].id: it for it in items}
        self._progress(20, f"Loaded {len(items):,} items — clustering…")
        embeddings, item_ids = self._build_embedding_matrix(items)
        clusters = self._cluster(embeddings, item_ids, items)
        if not clusters:
            log.warning("instore_trend_no_clusters")
            return None

        self._progress(35, f"Found {len(clusters)} clusters — sending to Claude…")
        trend_dicts = await self._holistic_analysis(
            clusters, items_by_id, previously_found,
            months_window=months_window, country=country,
        )
        if not trend_dicts:
            log.warning("instore_trend_no_claude_trends")
            return None

        self._progress(80, f"Claude returned {len(trend_dicts)} trends — saving…")
        new_trends: list[tuple[InStoreTrend, dict]] = []
        for td in trend_dicts:
            trend = self._build_trend_record(td, week_start)
            if not trend:
                continue
            trend.generation = next_generation
            # Stamp the horizon + country this Set was run against so
            # /sets can label each tab and buyers can tell them apart.
            trend.months_window = months_window
            trend.country = country
            self.db.add(trend)
            new_trends.append((trend, td))

        await self.db.flush()

        # Skip example items already used by earlier Sets in the SAME
        # country + horizon (same scoping as the exclusion list — a Set at a
        # different horizon may legitimately re-use the same hero items).
        used_ids: set[int] = set()
        if max_generation > 0:
            prior_ex_result = await self.db.execute(_same_scope(
                select(InStoreTrendExample.item_id)
                .join(InStoreTrend, InStoreTrendExample.trend_id == InStoreTrend.id)
                .where(InStoreTrend.week_start == week_start)
                .where(InStoreTrend.generation < next_generation)
            ))
            used_ids = set(prior_ex_result.scalars().all())

        for trend, td in new_trends:
            await self._create_examples(trend, td, clusters, items_by_id, used_ids)

        # Commit trends + examples BEFORE the slow recommendations phase.
        # If a pgvector query in _create_recommendations fails and
        # rollback() fires to unbreak the session, the already-committed
        # trends + examples survive. Buyer sees trends without recs
        # rather than losing the whole run.
        await self.db.commit()

        self._progress(90, "Finding matching online products…")
        # Per-trend progress log AND progress update. The pgvector query
        # takes ~55s per trend without the HNSW index — bar sitting at
        # 90% for 6+ minutes reads as "stuck" to buyers even though the
        # run is advancing. Ticking the bar per trend makes the reality
        # obvious. 90 -> 95 evenly divided across trends.
        total = len(new_trends)
        for i, (trend, td) in enumerate(new_trends, 1):
            pct = 90 + int(round((i / max(total, 1)) * 5))
            self._progress(pct, f"Finding matching products… trend {i} of {total}")
            log.info("instore_recommendations_step",
                     trend_index=i, total=total,
                     trend_id=trend.id, trend_name=trend.name)
            await self._create_recommendations(trend, td, clusters)

        self._progress(95, "Writing report…")

        # Upsert the report
        report_result = await self.db.execute(
            select(InStoreTrendReport).where(InStoreTrendReport.week_start == week_start)
        )
        report = report_result.scalar_one_or_none()
        committed_ids = [t.id for t, _ in new_trends]

        if report:
            report.trend_ids = (report.trend_ids or []) + committed_ids
            report.generation_count = next_generation
            report.total_items_analysed = len(items)
            report.months_window = months_window
        else:
            report_values = await self._generate_report_meta(
                week_start, [t for t, _ in new_trends], len(items),
            )
            report_values["generation_count"] = next_generation
            report_values["months_window"] = months_window
            upsert_stmt = (
                pg_insert(InStoreTrendReport)
                .values(**report_values)
                .on_conflict_do_update(
                    constraint="instore_trend_reports_week_start_key",
                    set_={k: v for k, v in report_values.items() if k != "week_start"},
                )
            )
            await self.db.execute(upsert_stmt)

        await self.db.commit()

        report_result = await self.db.execute(
            select(InStoreTrendReport).where(InStoreTrendReport.week_start == week_start)
        )
        return report_result.scalar_one_or_none()

    # ── Data loading ──────────────────────────────────────────────────────

    async def _load_items(
        self,
        months_window: Optional[int] = None,
        country: Optional[str] = None,
    ) -> list[dict]:
        """Hero + main items only, with non-null embeddings.

        `months_window` is a CALENDAR-month horizon (2026-09-10) —
        see _month_range() for the mapping. `country` filters
        InStoreCatalogueImage.country ('US' / 'AU'); None = mixed
        (no filter).
        """
        stmt = (
            select(InStoreCatalogueItem, InStoreCatalogueImage)
            .join(InStoreCatalogueImage, InStoreCatalogueItem.image_id == InStoreCatalogueImage.id)
            .where(
                and_(
                    InStoreCatalogueItem.embedding.isnot(None),
                    InStoreCatalogueItem.prominence.in_(["hero", "main"]),
                )
            )
        )
        lo, hi = _month_range(months_window)
        if lo is not None:
            stmt = stmt.where(InStoreCatalogueImage.created_at >= lo)
        if hi is not None:
            stmt = stmt.where(InStoreCatalogueImage.created_at < hi)
        if country:
            stmt = stmt.where(InStoreCatalogueImage.country == country)
        result = await self.db.execute(stmt)
        return [{"item": it, "image": img} for it, img in result.all()]

    # ── Clustering ────────────────────────────────────────────────────────

    def _build_embedding_matrix(self, items: list[dict]) -> tuple[np.ndarray, list[int]]:
        embs, ids = [], []
        for it in items:
            emb = it["item"].embedding
            if emb is not None:
                embs.append(emb)
                ids.append(it["item"].id)
        matrix = np.array(embs, dtype=np.float32)
        matrix = normalize(matrix, norm="l2")
        return matrix, ids

    def _cluster(self, embeddings: np.ndarray, item_ids: list[int], items: list[dict]) -> list[dict]:
        n = len(embeddings)
        k = max(3, min(20, n // self.min_cluster_size))
        kmeans = MiniBatchKMeans(n_clusters=k, random_state=random.randint(0, 99999), n_init=5)
        labels = kmeans.fit_predict(embeddings)

        by_id = {it["item"].id: it for it in items}
        # Group items + their embeddings by cluster label so we can compute centroids.
        items_by_label: dict[int, list[dict]] = defaultdict(list)
        embs_by_label: dict[int, list[np.ndarray]] = defaultdict(list)
        for idx, label in enumerate(labels):
            iid = item_ids[idx]
            items_by_label[label].append(by_id[iid])
            embs_by_label[label].append(embeddings[idx])

        valid = []
        for label, cluster_items in items_by_label.items():
            if len(cluster_items) < self.min_cluster_size:
                continue
            summary = self._summarise_cluster(cluster_items)
            # Cluster centroid as an L2-normalised mean — used downstream to
            # find similar online products via cosine distance.
            cluster_embs = np.array(embs_by_label[label], dtype=np.float32)
            centroid = cluster_embs.mean(axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            summary["centroid"] = centroid.tolist()
            valid.append(summary)
        valid.sort(key=lambda c: c["item_count"], reverse=True)
        return valid[:20]

    def _summarise_cluster(self, items: list[dict]) -> dict:
        colours = Counter()
        materials = Counter()
        patterns = Counter()
        styles = Counter()
        taxonomies = Counter()
        # Retailers + product segments feed the buyer voice: the team names
        # a retailer when it typifies a trend, and calls out specific
        # buyable formats ("fabric bins with wood handles").
        retailers = Counter()
        segments = Counter()
        product_names: list[str] = []

        for it in items:
            i = it["item"]
            for c in i.colours or []:
                colours[c.lower()] += 1
            for m in i.materials or []:
                materials[m.lower()] += 1
            for p in i.patterns or []:
                patterns[p.lower()] += 1
            for s in i.style_tags or []:
                styles[s.lower()] += 1
            if i.category and i.subcategory:
                taxonomies[f"{i.category} > {i.subcategory}"] += 1
            elif i.category:
                taxonomies[i.category] += 1
            if i.product_segment:
                segments[i.product_segment] += 1
            r = getattr(it.get("image"), "retailer", None)
            if r:
                retailers[r] += 1
            product_names.append(i.product_name)

        return {
            "item_count": len(items),
            "item_ids": [it["item"].id for it in items],
            "top_colours": [c for c, _ in colours.most_common(8)],
            "top_materials": [m for m, _ in materials.most_common(8)],
            "top_patterns": [p for p, _ in patterns.most_common(6)],
            "top_styles": [s for s, _ in styles.most_common(6)],
            "top_taxonomies": [t for t, _ in taxonomies.most_common(6)],
            "top_product_segments": [s for s, _ in segments.most_common(6)],
            "top_retailers": [r for r, _ in retailers.most_common(5)],
            "sample_product_names": random.sample(product_names, min(12, len(product_names))),
        }

    # ── Claude synthesis ──────────────────────────────────────────────────

    async def _holistic_analysis(
        self,
        clusters: list[dict],
        items_by_id: dict[int, dict],
        previously_found: list[str],
        months_window: Optional[int] = None,
        country: Optional[str] = None,
    ) -> list[dict]:
        payload = self._build_payload(
            clusters, items_by_id, previously_found,
            months_window=months_window, country=country,
        )

        try:
            response = await self.client.messages.create(
                model=settings.nlp_model,
                # Buyer-voice descriptions run to 3 sentences for palette /
                # seasonal transitions, and we ask for up to 14 trends.
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": payload}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            trends = data.get("trends", [])
            log.info("instore_claude_trends", count=len(trends))
            return trends
        except Exception as exc:
            log.error("instore_claude_failed", error=str(exc))
            return []
        finally:
            try:
                await self.client.close()
            except Exception:
                pass

    def _build_payload(
        self,
        clusters: list[dict],
        items_by_id: dict[int, dict],
        previously_found: list[str],
        months_window: Optional[int] = None,
        country: Optional[str] = None,
    ) -> str:
        lines: list[str] = []

        # Store-walk context — anchors the season ("Harvest/Fall",
        # "Halloween", "Back to School") and the market. Without this Claude
        # has no way to know whether September photos mean autumn or spring.
        lo, hi = _month_range(months_window)
        if lo is not None and hi is not None:
            last_covered = hi - timedelta(days=1)  # hi is exclusive
            first_lbl, last_lbl = lo.strftime("%b %Y"), last_covered.strftime("%b %Y")
            when = first_lbl if first_lbl == last_lbl else f"{first_lbl} – {last_lbl}"
        else:
            when = "all dates on file"
        where = f"{country} stores" if country else "stores across every country on file"
        lines.append(f"STORE WALK: {where}. Shelf photos uploaded {when}.")
        lines.append(f"TOTAL ITEMS ANALYSED: {len(items_by_id):,}")
        lines.append(f"CLUSTERS IDENTIFIED: {len(clusters)}")
        if previously_found:
            lines.append("")
            lines.append("ALREADY WRITTEN UP FROM THIS SAME BATCH — find different angles; do not repeat these or near-duplicates:")
            for n in previously_found:
                lines.append(f"  - {n}")
        lines.append("")
        lines.append("=== CLUSTER SUMMARIES ===")
        for i, c in enumerate(clusters):
            lines.append("")
            lines.append(f"Cluster #{i} ({c['item_count']} items)")
            lines.append(f"  Top colours: {', '.join(c['top_colours']) or '(none)'}")
            lines.append(f"  Top materials: {', '.join(c['top_materials']) or '(none)'}")
            lines.append(f"  Top patterns: {', '.join(c['top_patterns']) or '(none)'}")
            lines.append(f"  Top styles: {', '.join(c['top_styles']) or '(none)'}")
            lines.append(f"  Taxonomy buckets: {', '.join(c['top_taxonomies']) or '(none)'}")
            lines.append(f"  Product formats: {', '.join(c.get('top_product_segments', [])) or '(none)'}")
            lines.append(f"  Retailers: {', '.join(c.get('top_retailers', [])) or '(unknown)'}")
            lines.append(f"  Sample product names:")
            for name in c["sample_product_names"]:
                lines.append(f"    • {name}")
        return "\n".join(lines)

    # ── Record building ───────────────────────────────────────────────────

    def _build_trend_record(self, td: dict, week_start: datetime) -> Optional[InStoreTrend]:
        name = (td.get("name") or "").strip()
        if not name:
            return None
        momentum = (td.get("momentum") or "").strip().lower()
        if momentum not in VALID_MOMENTUM:
            momentum = "noted"
        category = (td.get("category") or "").strip().lower()
        if category not in VALID_CATEGORIES:
            category = "style"
        return InStoreTrend(
            week_start=week_start,
            name=name[:500],
            description=(td.get("description") or "").strip(),
            rationale=(td.get("rationale") or "").strip(),
            category=category,
            status=MOMENTUM_TO_STATUS[momentum],
            momentum=momentum,
            dominant_colours=td.get("dominant_colours") or [],
            dominant_materials=td.get("dominant_materials") or [],
            dominant_patterns=td.get("dominant_patterns") or [],
            dominant_styles=td.get("dominant_styles") or [],
            dominant_taxonomy=td.get("dominant_taxonomy") or [],
        )

    async def _create_examples(
        self,
        trend: InStoreTrend,
        td: dict,
        clusters: list[dict],
        items_by_id: dict[int, dict],
        used_ids: set[int],
        max_examples: int = 12,
    ):
        """Pick up to N representative items from the supporting clusters."""
        supporting = td.get("supporting_cluster_indices") or []
        candidate_ids: list[int] = []
        for idx in supporting:
            if 0 <= idx < len(clusters):
                candidate_ids.extend(clusters[idx]["item_ids"])

        if not candidate_ids:
            return

        random.shuffle(candidate_ids)
        chosen: list[int] = []
        for iid in candidate_ids:
            if iid in used_ids:
                continue
            if iid in chosen:
                continue
            if iid not in items_by_id:
                continue
            chosen.append(iid)
            used_ids.add(iid)
            if len(chosen) >= max_examples:
                break

        # If we couldn't find unique items (re-runs of generation N), fall back
        # to allowing some reuse — better to show *something*.
        if not chosen and candidate_ids:
            chosen = candidate_ids[:max_examples]

        item_count = 0
        for i, iid in enumerate(chosen):
            self.db.add(InStoreTrendExample(
                trend_id=trend.id,
                item_id=iid,
                relevance_score=1.0 - (i * 0.05),
                is_hero=(i == 0),
            ))
            item_count += 1
        trend.item_count = max(item_count, td.get("item_count", item_count))

    # ── Online product recommendations via embedding similarity ───────────

    async def _create_recommendations(
        self,
        trend: InStoreTrend,
        td: dict,
        clusters: list[dict],
    ):
        """For each trend, find the top online Products whose embedding is most
        similar to a synthetic 'trend prototype' embedding built from the trend's
        dominant attributes. Using the dominant attributes (vs the noisy cluster
        centroid) gives a much cleaner signal in this hash-based embedding scheme."""
        search_vec = self._build_trend_search_embedding(td)
        if search_vec is None:
            return

        max_distance = 1.0 - RECOMMENDATION_THRESHOLD
        vec_literal = "[" + ",".join(f"{x:.6f}" for x in search_vec.tolist()) + "]"
        from sqlalchemy import text as sa_text

        # No SET LOCAL statement_timeout here — it was scoping to the
        # session's outer transaction, so when a query timed out the
        # transaction entered aborted state and every subsequent query
        # (including the next trend's) failed with InFailedSqlTransaction,
        # poisoning the whole run.
        #
        # On failure we rollback the session (clearing the aborted
        # transaction) so subsequent trends can continue. This is safe
        # because the caller commits AFTER _create_examples runs, so
        # already-persisted trends + examples aren't lost.
        try:
            result = await self.db.execute(
                sa_text(
                    "SELECT p.id, (pa.embedding <=> CAST(:vec AS vector)) AS distance "
                    "FROM products p "
                    "JOIN product_attributes pa ON pa.product_id = p.id "
                    "WHERE p.is_active = TRUE "
                    "  AND pa.embedding IS NOT NULL "
                    "  AND (pa.embedding <=> CAST(:vec AS vector)) <= :max_dist "
                    "ORDER BY distance ASC "
                    "LIMIT :limit"
                ),
                {"vec": vec_literal, "max_dist": max_distance,
                 "limit": RECOMMENDATIONS_PER_TREND},
            )
            rows = result.all()
        except Exception as exc:
            log.warning("instore_recommendations_query_failed",
                        trend_id=trend.id, trend_name=trend.name,
                        error=str(exc), error_type=type(exc).__name__)
            try:
                await self.db.rollback()
            except Exception:
                pass
            return
        log.info(
            "instore_recommendations_query",
            trend_id=trend.id,
            trend_name=trend.name,
            matches=len(rows),
            top_sim=(1.0 - float(rows[0][1])) if rows else None,
        )
        for rank, (product_id, distance) in enumerate(rows):
            similarity = max(0.0, 1.0 - float(distance))
            self.db.add(InStoreTrendRecommendation(
                trend_id=trend.id,
                product_id=product_id,
                similarity=similarity,
                rank=rank,
            ))

    def _build_trend_search_embedding(self, td: dict) -> Optional[np.ndarray]:
        """Build an L2-normalised search vector from the trend's dominant
        attributes (name + dominant colours/materials/patterns/styles/taxonomy).
        Calls Voyage's sync embed — same model used to embed online products,
        so cosine distance is meaningful across the two."""
        from analysis.embeddings import embed_text_sync
        parts: list[str] = []
        if td.get("name"):
            parts.append(td["name"])
        for key in ("dominant_colours", "dominant_materials",
                    "dominant_patterns", "dominant_styles", "dominant_taxonomy"):
            vals = td.get(key) or []
            if vals:
                parts.append(" ".join(vals))
        text = " | ".join(p for p in parts if p)
        if not text.strip():
            return None
        raw = embed_text_sync(text)
        if raw is None:
            return None
        emb = np.array(raw, dtype=np.float32)
        norm = np.linalg.norm(emb)
        if norm == 0:
            return None
        return emb / norm

    # ── Report metadata via Claude ────────────────────────────────────────

    async def _generate_report_meta(
        self,
        week_start: datetime,
        trends: list[InStoreTrend],
        total_items: int,
    ) -> dict:
        """One short Claude call to produce a title + summary across the
        generated trends. Keeps the report screen readable. Falls back to a
        deterministic title if the call fails."""
        names = ", ".join(t.name for t in trends[:8])
        fallback_title = f"In-store Trend Report — week of {week_start.strftime('%d %b %Y')}"

        try:
            response = await self.client.messages.create(
                model=settings.nlp_model,
                max_tokens=400,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Write a short title (≤80 chars) and a 2-3 sentence summary for a retail "
                        f"trend report covering {total_items:,} in-store products. The identified "
                        f"trends were: {names}.\n\n"
                        f'Return ONLY JSON: {{"title": "...", "summary": "..."}}'
                    ),
                }],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            return {
                "week_start": week_start,
                "title": (data.get("title") or fallback_title)[:500],
                "summary": data.get("summary") or "Trend report generated from the in-store products catalogue.",
                "trend_ids": [t.id for t in trends],
                "total_items_analysed": total_items,
                "generation_count": 1,
            }
        except Exception as exc:
            log.warning("instore_report_meta_failed", error=str(exc))
            return {
                "week_start": week_start,
                "title": fallback_title,
                "summary": "Trend report generated from the in-store products catalogue.",
                "trend_ids": [t.id for t in trends],
                "total_items_analysed": total_items,
                "generation_count": 1,
            }
