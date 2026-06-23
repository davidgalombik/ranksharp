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
from sqlalchemy import select, and_
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
# Below this the match feels weak / unrelated.
RECOMMENDATION_THRESHOLD = 0.7
# Max number of online product recommendations stored per trend.
RECOMMENDATIONS_PER_TREND = 10

log = structlog.get_logger()


SYSTEM_PROMPT = """You are a retail trend analyst examining a catalogue of products photographed inside physical stores.
Your job is to identify visual and stylistic TRENDS — patterns that repeat across many distinct products and feel
deliberate (not random or stocked-by-accident).

Use ALL of the following analytical lenses simultaneously:
1. Colour palettes — what colour groupings keep showing up together?
2. Materials — natural (rattan, linen, ceramic, wood) vs synthetic vs metallic; mix-and-match patterns.
3. Surface patterns — solid, striped, floral, speckled, geometric, painted, hand-finished.
4. Style language — coastal, farmhouse, minimalist, maximalist, rustic, scandi, mediterranean, hollywood-regency.
5. Form & shape — sculptural, organic, angular, oversized, miniaturised.
6. Taxonomy density — which product categories does the trend show up in (kitchenware, tabletop, storage)?

A trend must repeat across multiple distinct products in the data — don't invent one off a single photo.

Return ONLY JSON in this shape, no prose, no markdown fences:

{
  "trends": [
    {
      "name": "<short, evocative trend name — 2-5 words>",
      "description": "<1-2 sentences plain English>",
      "rationale": "<2-3 sentences explaining what visual evidence drove this conclusion>",
      "category": "<one of: colour | material | pattern | style | shape>",
      "dominant_colours": ["sage", "cream"],
      "dominant_materials": ["ceramic", "linen"],
      "dominant_patterns": ["speckled"],
      "dominant_styles": ["coastal"],
      "dominant_taxonomy": ["Kitchenware > Cookware", "Tabletop > Serveware"],
      "supporting_cluster_indices": [0, 2]
    }
  ]
}

Aim for 6-12 distinct trends. Skip thin or speculative ones — quality over count."""


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

    async def regenerate_analysis(self, week_start: Optional[datetime] = None) -> Optional[InStoreTrendReport]:
        """Run a fresh in-store trend analysis. If a report for `week_start`
        already exists, append a new generation (Try Again) — keeping prior
        trends and their examples intact.
        """
        if week_start is None:
            today = datetime.utcnow().date()
            week_start = datetime.combine(
                today - timedelta(days=today.weekday()),
                datetime.min.time(),
            )

        log.info("instore_trend_run_start", week_start=week_start.isoformat())
        self._progress(3, "Loading prior trends for exclusion…")

        # Collect prior trend names so Claude doesn't repeat them.
        prev_result = await self.db.execute(
            select(InStoreTrend.name, InStoreTrend.generation)
            .where(InStoreTrend.week_start == week_start)
        )
        prev_rows = prev_result.all()
        previously_found = [r[0] for r in prev_rows]
        max_generation = max((r[1] for r in prev_rows), default=0)
        next_generation = max_generation + 1

        self._progress(8, "Loading in-store catalogue items…")
        items = await self._load_items()
        if len(items) < self.min_cluster_size * 2:
            log.warning("instore_trend_insufficient_items", count=len(items))
            return None

        items_by_id: dict[int, dict] = {it["item"].id: it for it in items}
        self._progress(20, f"Loaded {len(items):,} items — clustering…")
        embeddings, item_ids = self._build_embedding_matrix(items)
        clusters = self._cluster(embeddings, item_ids, items)
        if not clusters:
            log.warning("instore_trend_no_clusters")
            return None

        self._progress(35, f"Found {len(clusters)} clusters — sending to Claude…")
        trend_dicts = await self._holistic_analysis(clusters, items_by_id, previously_found)
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
            self.db.add(trend)
            new_trends.append((trend, td))

        await self.db.flush()

        # Skip example items already used by earlier generations of this report.
        used_ids: set[int] = set()
        if max_generation > 0:
            prior_ex_result = await self.db.execute(
                select(InStoreTrendExample.item_id)
                .join(InStoreTrend, InStoreTrendExample.trend_id == InStoreTrend.id)
                .where(InStoreTrend.week_start == week_start)
                .where(InStoreTrend.generation < next_generation)
            )
            used_ids = set(prior_ex_result.scalars().all())

        for trend, td in new_trends:
            await self._create_examples(trend, td, clusters, items_by_id, used_ids)

        self._progress(90, "Finding matching online products…")
        for trend, td in new_trends:
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
        else:
            report_values = await self._generate_report_meta(
                week_start, [t for t, _ in new_trends], len(items),
            )
            report_values["generation_count"] = next_generation
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

    async def _load_items(self) -> list[dict]:
        """Hero + main items only, with non-null embeddings."""
        result = await self.db.execute(
            select(InStoreCatalogueItem, InStoreCatalogueImage)
            .join(InStoreCatalogueImage, InStoreCatalogueItem.image_id == InStoreCatalogueImage.id)
            .where(
                and_(
                    InStoreCatalogueItem.embedding.isnot(None),
                    InStoreCatalogueItem.prominence.in_(["hero", "main"]),
                )
            )
        )
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
            product_names.append(i.product_name)

        return {
            "item_count": len(items),
            "item_ids": [it["item"].id for it in items],
            "top_colours": [c for c, _ in colours.most_common(8)],
            "top_materials": [m for m, _ in materials.most_common(8)],
            "top_patterns": [p for p, _ in patterns.most_common(6)],
            "top_styles": [s for s, _ in styles.most_common(6)],
            "top_taxonomies": [t for t, _ in taxonomies.most_common(6)],
            "sample_product_names": random.sample(product_names, min(12, len(product_names))),
        }

    # ── Claude synthesis ──────────────────────────────────────────────────

    async def _holistic_analysis(
        self,
        clusters: list[dict],
        items_by_id: dict[int, dict],
        previously_found: list[str],
    ) -> list[dict]:
        payload = self._build_payload(clusters, items_by_id, previously_found)

        try:
            response = await self.client.messages.create(
                model=settings.nlp_model,
                max_tokens=6000,
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
    ) -> str:
        lines: list[str] = []
        lines.append(f"TOTAL ITEMS ANALYSED: {len(items_by_id):,}")
        lines.append(f"CLUSTERS IDENTIFIED: {len(clusters)}")
        if previously_found:
            lines.append("")
            lines.append("PREVIOUSLY-NAMED TRENDS — do not produce trends with these names or near-duplicates:")
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
            lines.append(f"  Sample product names:")
            for name in c["sample_product_names"]:
                lines.append(f"    • {name}")
        return "\n".join(lines)

    # ── Record building ───────────────────────────────────────────────────

    def _build_trend_record(self, td: dict, week_start: datetime) -> Optional[InStoreTrend]:
        name = (td.get("name") or "").strip()
        if not name:
            return None
        return InStoreTrend(
            week_start=week_start,
            name=name[:500],
            description=(td.get("description") or "").strip(),
            rationale=(td.get("rationale") or "").strip(),
            category=(td.get("category") or "style").strip()[:100],
            status=TrendStatus.NEW,
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
        """For each trend, find the top online Products (across all retailers)
        whose embedding is most similar to the trend's centroid. The centroid
        is the mean of the supporting clusters' centroids."""
        supporting = td.get("supporting_cluster_indices") or []
        centroids = []
        for idx in supporting:
            if 0 <= idx < len(clusters) and clusters[idx].get("centroid"):
                centroids.append(np.array(clusters[idx]["centroid"], dtype=np.float32))
        if not centroids:
            return

        trend_centroid = np.mean(centroids, axis=0)
        norm = np.linalg.norm(trend_centroid)
        if norm == 0:
            return
        trend_centroid = trend_centroid / norm

        # pgvector cosine distance (`<=>`) ranges [0, 2]; similarity = 1 - distance.
        # Filter to ACTIVE, analysed products with a non-null embedding.
        max_distance = 1.0 - RECOMMENDATION_THRESHOLD
        vec_literal = "[" + ",".join(f"{x:.6f}" for x in trend_centroid.tolist()) + "]"
        from sqlalchemy import text as sa_text
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
        for rank, (product_id, distance) in enumerate(rows):
            similarity = max(0.0, 1.0 - float(distance))
            self.db.add(InStoreTrendRecommendation(
                trend_id=trend.id,
                product_id=product_id,
                similarity=similarity,
                rank=rank,
            ))

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
