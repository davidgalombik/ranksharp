"""
Design Trend Engine.

Produces trends shaped around *design dimensions* — one card per specific
value in one of {colour, material, pattern, style, seasonal} — instead of
the fused product-shaped themes of the original TrendEngine.

Motivation (from the 2026-07-23 user meeting): buyers browse the trends page
looking for specific motifs like "cherry", "tortoise shell", "polka dot" and
specific palette shifts like "sage green" or "dusty pink". The old engine
returned things like *"Ceramic Cottagecore Tableware"* — fused theme + product
type, which was too abstract and never included specific motifs.

Pipeline:

  1. Aggregate structured attributes (colours / materials / style_tags) from
     ProductAttributes. Each distinct value with retailer_count >= 3 becomes
     one trend, sorted by product count.
  2. Ask Claude for a batch of specific visual motifs and seasonal themes
     extracted from product NAMES (since tag values are too generic to
     surface "cherry" or "tortoise" on their own). Each becomes its own
     trend with category=pattern or seasonal.
  3. For every produced trend, compare against last week's same-name trend
     to compute momentum_pct and set status
     (NEW / RISING / PLATEAU / DECLINING).
  4. Persist Trend + TrendExample rows into the existing schema.
     Report generation and per-generation exclusion mirror the old engine.
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import select, and_, or_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import (
    Product, ProductAttributes, Retailer,
    Trend, TrendExample, TrendReport, TrendStatus,
)

log = structlog.get_logger()

# Minimum retailer spread for a design signal to count as a trend.
# Matches the user rule from the transcript: "if noticed in more than three stores".
MIN_RETAILERS = 3
# Maximum trends produced per dimension so the page doesn't explode.
MAX_PER_DIMENSION = 12

# Candle / fragrance products live in the Fragrance tab; exclude here to avoid
# double-counting scent trends across surfaces.
FRAGRANCE_EXCLUSION_KEYWORDS = [
    "candle", "diffuser", "fragrance", "scent", "wax melt", "reed",
    "incense", "aromatherapy", "room spray", "wax", "wick", "votive",
    "taper", "pillar candle", "soy", "beeswax", "home fragrance",
]

# Generic pattern tag values that are too coarse to be a useful trend name
# on their own. If Claude picks these up as motifs, deprioritise them —
# we want *"Cherry Print"*, not *"printed"*.
GENERIC_PATTERN_STOPWORDS = {
    "none", "plain", "solid", "no pattern", "unpatterned", "printed",
}


MOTIF_SYSTEM_PROMPT = """You are a retail-trend analyst. You will read a list of home décor and \
storage product NAMES with IDs, and extract three kinds of recurring design signals.

CRITICAL: what to extract vs. what to IGNORE

MOTIFS = decorative subject or surface pattern applied ACROSS multiple product types.
YES: cherry, tortoise shell, mushroom, gingham, polka dot, floral, stripe, checkerboard, \
scalloped, ribbed, dinosaur, unicorn, palm leaf, botanical, gnome, ghost, pumpkin, harvest wheat, \
american flag, hearts, stars, animal print, evil eye, retro flowers.

NO — these are NOT motifs, do not include them:
- Product types: "stainless steel cookware", "muffin cupcake pan", "baking sheet", \
"food storage container", "trash can", "coffee mug", "salad bowl"
- Materials on their own: "wood", "glass", "ceramic", "resin", "plastic"
- Generic descriptors: "textured", "printed", "solid", "plain", "modern", "large"
- Functional features: "with lid", "collapsible", "portable", "waterproof"

PALETTE_COLOURS = specific colour or colour combination named in the product name itself.
YES: sage green, dusty pink, terracotta, warm white, charcoal, ochre, matte black, \
navy, coral, sky blue, olive, blush, cream, taupe, mustard, forest green.

NO: bare "black" / "white" / "brown" without qualifier; product names that just describe the \
material colour incidentally.

SEASONAL = Christmas, Halloween, back-to-school, Valentine's Day, Easter, autumn/harvest, \
spring florals, summer coastal, winter cosy, thanksgiving. Only include if clearly seasonal.

HARD RULES (apply strictly — reject any group failing these)

1. Every group MUST have >= 3 product_ids from the input list. Groups with 1-2 products are FORBIDDEN.
2. Every product_id you include must be an ID that literally appeared in the input; do not invent IDs.
3. Group names are short (1-3 Title Case words): "Cherry", "Sage Green", "Halloween", not \
long descriptions.
4. Do not repeat a product_id across more than one motif in the same category.

Aim for 6-12 MOTIFS, 6-10 PALETTE_COLOURS, 3-5 SEASONAL. Quality > quantity — output fewer if \
the signal isn't strong.

Output ONLY valid JSON (no prose, no code fences):

{
  "motifs": [
    {"name": "<1-3 word Title Case>", "product_ids": [<int>, <int>, <int>, ...]}
  ],
  "palette_colours": [
    {"name": "<1-3 word Title Case>", "product_ids": [<int>, ...]}
  ],
  "seasonal": [
    {"name": "<1-3 word Title Case>", "product_ids": [<int>, ...]}
  ]
}
"""


class DesignTrendEngine:
    """Produces design-dimension-first trends. Interface-compatible with
    TrendEngine.regenerate_analysis so the Celery task can drop it in."""

    def __init__(self, db: AsyncSession, task=None):
        self.db = db
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._task = task

    # -- Progress -------------------------------------------------------------

    def _progress(self, pct: int, step: str):
        if self._task:
            try:
                self._task.update_state(state="PROGRESS", meta={"pct": pct, "step": step})
            except Exception:
                pass

    # -- Public entry point ---------------------------------------------------

    async def regenerate_analysis(self, week_start: Optional[datetime] = None) -> Optional[TrendReport]:
        """Generate a fresh set of design-shape trends for `week_start`.
        Appends a new generation if one already exists for the same week
        (mirrors TrendEngine so the "Try Again" button in the UI still works).
        """
        if week_start is None:
            today = datetime.utcnow().date()
            week_start = datetime.combine(
                today - timedelta(days=today.weekday()),
                datetime.min.time(),
            )

        log.info("design_trend_run_start", week_start=week_start.isoformat())
        self._progress(3, "Loading prior generations for exclusion…")

        prev_rows = (await self.db.execute(
            select(Trend.name, Trend.generation).where(Trend.week_start == week_start)
        )).all()
        excluded_names = {r[0].strip().lower() for r in prev_rows}
        max_generation = max((r[1] for r in prev_rows), default=0)
        next_generation = max_generation + 1

        self._progress(10, "Loading products with attributes…")
        products = await self._load_products()
        if len(products) < 30:
            log.warning("design_trend_insufficient_products", count=len(products))
            return None

        # index products for retailer + example lookups later
        by_id = {p["product"].id: p for p in products}

        self._progress(25, "Aggregating colour / material / style trends…")
        raw_trends: list[dict] = []
        raw_trends.extend(self._aggregate_attribute("colour", products, "colours"))
        raw_trends.extend(self._aggregate_attribute("material", products, "materials"))
        raw_trends.extend(self._aggregate_attribute("style", products, "style_tags"))

        self._progress(55, "Extracting motifs & seasonal themes from product names…")
        motif_trends = await self._extract_motif_trends(products)
        raw_trends.extend(motif_trends)

        # Filter out names already used in a prior generation this week.
        raw_trends = [t for t in raw_trends if t["name"].strip().lower() not in excluded_names]
        if not raw_trends:
            log.warning("design_trend_no_new_signals")
            return None

        self._progress(75, "Momentum vs last week…")
        prior_by_name = await self._load_prior_trends_map(week_start)

        self._progress(85, f"Persisting {len(raw_trends)} trends…")
        committed: list[Trend] = []
        for rt in raw_trends:
            trend = self._build_trend_record(rt, week_start, prior_by_name)
            if trend is None:
                continue
            trend.generation = next_generation
            self.db.add(trend)
            committed.append((trend, rt))

        await self.db.flush()

        # Create TrendExamples — each trend gets up to 10 example products
        used_product_ids: set[int] = set()
        used_image_urls: set[str] = set()
        if max_generation > 0:
            prior_ex_ids = (await self.db.execute(
                select(TrendExample.product_id)
                .join(Trend, TrendExample.trend_id == Trend.id)
                .where(Trend.week_start == week_start)
                .where(Trend.generation < next_generation)
            )).scalars().all()
            used_product_ids.update(prior_ex_ids)

        for trend, rt in committed:
            self._create_examples(trend, rt, by_id, used_product_ids, used_image_urls)

        self._progress(95, "Writing report…")

        report_result = await self.db.execute(
            select(TrendReport).where(TrendReport.week_start == week_start)
        )
        report = report_result.scalar_one_or_none()
        committed_trends = [t for t, _ in committed]
        new_ids = [t.id for t in committed_trends]

        if report:
            report.trend_ids = (report.trend_ids or []) + new_ids
            report.generation_count = next_generation
        else:
            values = self._build_report_meta(week_start, committed_trends, len(products))
            values["generation_count"] = next_generation
            await self.db.execute(
                pg_insert(TrendReport).values(**values).on_conflict_do_update(
                    constraint="trend_reports_week_start_key",
                    set_={k: v for k, v in values.items() if k != "week_start"},
                )
            )

        await self.db.commit()

        result = await self.db.execute(
            select(TrendReport).where(TrendReport.week_start == week_start)
        )
        return result.scalar_one_or_none()

    # -- Data loading ---------------------------------------------------------

    async def _load_products(self) -> list[dict]:
        fragrance_match = or_(*[
            cond
            for kw in FRAGRANCE_EXCLUSION_KEYWORDS
            for cond in (Product.name.ilike(f"%{kw}%"), Product.category.ilike(f"%{kw}%"))
        ])
        result = await self.db.execute(
            select(Product, ProductAttributes, Retailer)
            .join(ProductAttributes, Product.id == ProductAttributes.product_id)
            .join(Retailer, Product.retailer_id == Retailer.id)
            .where(and_(
                Product.is_active == True,
                ~fragrance_match,
            ))
        )
        return [{"product": p, "attrs": a, "retailer": r} for p, a, r in result.all()]

    async def _load_prior_trends_map(self, week_start: datetime) -> dict[str, Trend]:
        prior_week = week_start - timedelta(days=7)
        rows = (await self.db.execute(
            select(Trend).where(Trend.week_start == prior_week)
        )).scalars().all()
        # Case-insensitive key so momentum matches "Sage Green" -> "sage green"
        return {t.name.strip().lower(): t for t in rows}

    # -- Structured aggregation (colour / material / style) -------------------

    def _aggregate_attribute(
        self, category: str, products: list[dict], attr_name: str,
    ) -> list[dict]:
        """Count how often each value appears in `attrs.<attr_name>` and
        emit one trend per distinct value that clears MIN_RETAILERS."""
        # NB: product_ids is a *set* — a product whose tag list contains
        # the same value twice (e.g. colours=['sage', 'sage']) must not
        # produce two TrendExample rows for the same (trend, product) pair.
        buckets: dict[str, dict] = defaultdict(lambda: {
            "product_ids": set(),
            "retailers": set(),
            "markets": set(),
            "prices": [],
        })
        for p in products:
            vals = getattr(p["attrs"], attr_name, None) or []
            # Dedupe values inside a single product too, so one product
            # only ever adds itself once to any given bucket.
            seen_here: set[str] = set()
            for v in vals:
                v = (v or "").strip().lower()
                if not v or v in GENERIC_PATTERN_STOPWORDS or v in seen_here:
                    continue
                seen_here.add(v)
                b = buckets[v]
                b["product_ids"].add(p["product"].id)
                b["retailers"].add(p["retailer"].slug)
                b["markets"].add(p["retailer"].country)
                if p["product"].price:
                    b["prices"].append(p["product"].price)

        trends: list[dict] = []
        for value, b in buckets.items():
            if len(b["retailers"]) < MIN_RETAILERS:
                continue
            trends.append({
                "category": category,
                "name": _titlecase(value),
                "product_ids": sorted(b["product_ids"]),
                "retailers": sorted(b["retailers"]),
                "markets": sorted(b["markets"]),
                "avg_price": round(sum(b["prices"]) / len(b["prices"]), 2) if b["prices"] else None,
                "dominant_colours": [value] if category == "colour" else [],
                "dominant_materials": [value] if category == "material" else [],
                "dominant_styles": [value] if category == "style" else [],
            })

        # Rank by product count, cap per dimension so the page stays scannable
        trends.sort(key=lambda t: len(t["product_ids"]), reverse=True)
        return trends[:MAX_PER_DIMENSION]

    # -- Motif + seasonal extraction via Claude -------------------------------

    async def _extract_motif_trends(self, products: list[dict]) -> list[dict]:
        """Send a sample of product NAMES to Claude and ask it to identify
        specific visual motifs (cherry, tortoise, mushroom, polka dot…) and
        seasonal themes (Halloween, back-to-school…). Each returned motif
        becomes one trend."""
        # Sample ~600 names — enough signal for Claude, small enough context.
        # Weight sampling toward newer products so seasonal drift shows up.
        sorted_products = sorted(
            products, key=lambda p: p["product"].last_seen_at, reverse=True,
        )
        pool = sorted_products[:800]
        sampled = random.sample(pool, min(600, len(pool)))
        lines = [f"[ID:{p['product'].id}] {p['product'].name}" for p in sampled]
        payload = "PRODUCT NAMES:\n" + "\n".join(lines) + "\n\nIdentify recurring motifs and seasonal themes."

        try:
            response = await self.client.messages.create(
                model=settings.nlp_model,
                max_tokens=8000,
                system=MOTIF_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": payload}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            # Some models emit prose or multiple objects around the JSON.
            # Extract only the first well-formed top-level {...} block.
            json_str = _extract_first_json_object(raw)
            data = json.loads(json_str)
        except Exception as exc:
            log.error(
                "motif_extract_failed",
                error=str(exc),
                preview=(raw[:400] if 'raw' in locals() else 'no response'),
            )
            return []

        # Index the sampled products so we can turn Claude's product IDs
        # into retailers / markets / prices without a second query
        sampled_by_id = {p["product"].id: p for p in sampled}
        trends: list[dict] = []
        raw_motif_count = len(data.get("motifs", []) or [])
        raw_seasonal_count = len(data.get("seasonal", []) or [])
        rejected_thin: list[dict] = []

        # New: also consume palette_colours from Claude, since the DB
        # colours column is mostly empty (vision didn't populate reliably)
        # so aggregation alone produces zero colour trends.
        raw_palette_count = len(data.get("palette_colours", []) or [])

        for group_key, category in (
            ("motifs", "pattern"),
            ("palette_colours", "colour"),
            ("seasonal", "seasonal"),
        ):
            for item in data.get(group_key, []) or []:
                name = (item.get("name") or "").strip()
                if not name:
                    continue
                # Dedupe + filter to sampled IDs (Claude occasionally hallucinates)
                pids = list(dict.fromkeys(
                    pid for pid in (item.get("product_ids") or []) if pid in sampled_by_id
                ))
                if len(pids) < 3:
                    rejected_thin.append({"name": name, "reason": "products<3", "n": len(pids)})
                    continue
                retailers = {sampled_by_id[pid]["retailer"].slug for pid in pids}
                markets = {sampled_by_id[pid]["retailer"].country for pid in pids}
                prices = [
                    sampled_by_id[pid]["product"].price
                    for pid in pids
                    if sampled_by_id[pid]["product"].price
                ]
                if len(retailers) < MIN_RETAILERS:
                    rejected_thin.append({"name": name, "reason": "retailers<3", "n": len(retailers)})
                    continue
                trends.append({
                    "category": category,
                    "name": _titlecase(name),
                    "product_ids": pids,
                    "retailers": sorted(retailers),
                    "markets": sorted(markets),
                    "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
                    "dominant_colours": [name.lower()] if category == "colour" else [],
                    "dominant_materials": [],
                    "dominant_styles": [],
                    "dominant_patterns": [name.lower()] if category == "pattern" else [],
                })

        log.info(
            "motif_extract_done",
            raw_motifs=raw_motif_count,
            raw_palette=raw_palette_count,
            raw_seasonal=raw_seasonal_count,
            kept_motifs=sum(1 for t in trends if t["category"] == "pattern"),
            kept_colour=sum(1 for t in trends if t["category"] == "colour"),
            kept_seasonal=sum(1 for t in trends if t["category"] == "seasonal"),
            rejected=len(rejected_thin),
            rejected_sample=rejected_thin[:5],
        )
        return trends

    # -- Trend record construction --------------------------------------------

    def _build_trend_record(
        self,
        rt: dict,
        week_start: datetime,
        prior_by_name: dict[str, Trend],
    ) -> Optional[Trend]:
        name = rt["name"].strip()
        if not name:
            return None
        pids = rt["product_ids"]
        retailer_slugs = rt["retailers"]
        markets = rt["markets"]

        # Momentum vs same-named trend last week
        prior = prior_by_name.get(name.lower())
        status = TrendStatus.NEW
        momentum_pct: Optional[float] = None
        prev_id: Optional[int] = None
        if prior:
            prev_id = prior.id
            prior_count = prior.product_count or 0
            if prior_count > 0:
                delta = len(pids) - prior_count
                momentum_pct = round((delta / prior_count) * 100, 1)
                if momentum_pct > 10:
                    status = TrendStatus.RISING
                elif momentum_pct < -10:
                    status = TrendStatus.DECLINING
                else:
                    status = TrendStatus.PLATEAU

        description = _templated_description(rt, momentum_pct)
        rationale = (
            f"Aggregated from {len(pids)} products across "
            f"{len(retailer_slugs)} retailers in {', '.join(markets) or 'the catalogue'}. "
            f"Ranks in the top design signals for the {rt['category']} dimension this week."
        )

        return Trend(
            week_start=week_start,
            name=name[:500],
            description=description[:1000],
            rationale=rationale[:2000],
            category=rt["category"],
            status=status,
            product_count=len(pids),
            retailer_count=len(retailer_slugs),
            retailer_names=retailer_slugs,
            avg_price=rt.get("avg_price"),
            momentum_pct=momentum_pct,
            prev_trend_id=prev_id,
            dominant_colours=rt.get("dominant_colours") or [],
            dominant_materials=rt.get("dominant_materials") or [],
            dominant_patterns=rt.get("dominant_patterns") or [],
            dominant_styles=rt.get("dominant_styles") or [],
            markets=markets,
            price_tier=None,
        )

    def _create_examples(
        self,
        trend: Trend,
        rt: dict,
        by_id: dict[int, dict],
        used_product_ids: set[int],
        used_image_urls: set[str],
    ):
        """Pick up to 10 example products for this trend. Prefer complete
        (priced + image) products, and prefer retailer diversity.
        Dedupes defensively — the DB has a unique (trend_id, product_id)
        constraint so any repeat here would blow the whole transaction."""
        # dict.fromkeys preserves order while removing duplicate PIDs
        candidates = list(dict.fromkeys(
            pid for pid in rt["product_ids"]
            if pid in by_id and pid not in used_product_ids
        ))
        if not candidates:
            candidates = list(dict.fromkeys(rt["product_ids"]))[:10]

        def completeness(pid: int) -> tuple[float, int]:
            item = by_id.get(pid)
            if not item:
                return (0.0, 0)
            p = item["product"]
            score = 0.0
            if p.price:
                score += 1.0
            if p.image_urls:
                score += 0.5 * len(p.image_urls)
            if p.description:
                score += 1.0
            return (score, hash(pid) % 1000)  # tiebreak by stable-ish hash

        # Diverse: take 10 top-scoring, spreading across retailers
        sorted_ids = sorted(candidates, key=completeness, reverse=True)
        selected: list[int] = []
        retailer_seen: set[str] = set()
        for pid in sorted_ids:
            item = by_id.get(pid)
            if item is None:
                continue
            r_slug = item["retailer"].slug
            if len(selected) < 6 and r_slug in retailer_seen:
                continue  # first 6 slots enforce retailer diversity
            selected.append(pid)
            retailer_seen.add(r_slug)
            if len(selected) >= 10:
                break
        if not selected:
            selected = sorted_ids[:10]

        for rank, pid in enumerate(selected):
            self.db.add(TrendExample(
                trend_id=trend.id,
                product_id=pid,
                relevance_score=max(0.1, 1.0 - rank * 0.08),
                is_hero=(rank == 0),
            ))
            used_product_ids.add(pid)
            item = by_id.get(pid)
            if item and item["product"].image_urls:
                used_image_urls.add(item["product"].image_urls[0])

    # -- Report meta ----------------------------------------------------------

    def _build_report_meta(
        self, week_start: datetime, trends: list[Trend], total_products: int,
    ) -> dict:
        retailer_count = len({r for t in trends for r in (t.retailer_names or [])})
        rising = [t for t in trends if t.status == TrendStatus.RISING]
        by_cat = Counter(t.category for t in trends)
        cat_summary = ", ".join(f"{n} {c}" for c, n in by_cat.most_common())
        summary = (
            f"Design-first analysis of {total_products:,} products across "
            f"{retailer_count} retailers. {len(trends)} trends: {cat_summary}. "
        )
        if rising:
            names = ", ".join(t.name for t in rising[:3])
            summary += f"{len(rising)} rising: {names}."
        return {
            "week_start": week_start,
            "title": f"Home Décor & Storage Design Trends — Week of {week_start.strftime('%d %b %Y')}",
            "summary": summary,
            "trend_ids": [t.id for t in trends],
            "total_products_analysed": total_products,
            "retailers_covered": retailer_count,
        }


# -- Module helpers -----------------------------------------------------------

def _extract_first_json_object(text: str) -> str:
    """Return the first well-formed top-level {...} block in `text`.

    Models sometimes emit prose or multiple JSON objects around the target,
    e.g. `{...}\\n\\n{...}` or `Sure, here's the JSON:\\n{...}`. A plain
    json.loads on that raises "Extra data" or "Expecting value". This
    walks the string with a bracket counter that ignores braces inside
    strings + honours backslash escapes, so it picks out exactly the first
    balanced object regardless of what surrounds it.

    Falls back to the input verbatim if no balanced object is found —
    json.loads will then raise a clear error the caller already logs.
    """
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if start == -1:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    return text[start:i + 1]
    return text


def _titlecase(s: str) -> str:
    """Simple title case that preserves & and doesn't lowercase acronyms."""
    return " ".join(w[0].upper() + w[1:] if w else w for w in s.strip().split())


def _templated_description(rt: dict, momentum_pct: Optional[float]) -> str:
    """Human-readable one-liner for the trend card. Kept deterministic
    (no Claude call per trend) to keep the pipeline cheap."""
    n = len(rt["product_ids"])
    r = len(rt["retailers"])
    cat_word = {
        "colour": "colour",
        "material": "material",
        "pattern": "motif",
        "style": "aesthetic",
        "seasonal": "seasonal theme",
    }.get(rt["category"], rt["category"])
    base = f"{rt['name']} is trending as a {cat_word}: {n} products across {r} retailers."
    if momentum_pct is not None:
        if momentum_pct > 10:
            base += f" ↑ {momentum_pct:+.0f}% vs last week."
        elif momentum_pct < -10:
            base += f" ↓ {momentum_pct:+.0f}% vs last week."
        else:
            base += " Stable vs last week."
    return base
