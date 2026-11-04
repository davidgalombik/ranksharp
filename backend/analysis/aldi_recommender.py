"""ALDI web product recommender — the pipeline (Phase 3, 2026-11-01).

One run turns a session's trend digest into five product links from the
web, each tied to the screenshot element it echoes, for one team:

  plan      one fast call writes the search queries (themes × materials ×
            retailer tiers), avoiding queries earlier runs already made
  discover  one search per query, in parallel; the server's result list
            is harvested directly and the model marks which results are
            product pages worth opening
  rank      one fast call orders the pool and picks what to verify
  verify    one fetch per candidate, in parallel; structured facts are
            extracted (price, material, stacks/nests, demand evidence,
            image); og: meta tags are read from the page text as backup
  select    one strong-model call chooses the picks from VERIFIED
            candidates only, citing digest elements; rules are then
            enforced in code (price band, material scope, retailer cap,
            tier minimums, not shown before) with one bounded repair round
  save      recommendations + near misses + thumbnails + cost

Why a pipeline and not one agentic turn: see docs/aldi-recommender-spike.md
(budget exhaustion, serial sandbox retries, $1.6 for 2 of 5 picks).

Candidates persist per session + team, so Try Again re-plans around what
was already searched, verifies only new pages, and never repeats a shown
product. Every model call is in the cost ledger under ref_type 'aldi_run'.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx
import structlog

from analysis import material_families as mf
from analysis.claude_raw import (
    ClaudeRawError, fetch_results, messages_create, page_meta, parse_json, response_text,
    search_results,
)
from config import settings

log = structlog.get_logger()

# ── Teams ────────────────────────────────────────────────────────────────────

TEAMS: dict[str, dict] = {
    "adrah": {
        "label": "Adrah",
        "families": ["fabric", "metal"],
        "material_block": (
            "Only select products whose PRIMARY material is one of the following:\n"
            "* Fabrics: e.g. canvas, linen, cotton, felt, woven textile, faux leather\n"
            "* Metals: e.g. stainless steel, powder-coated steel, iron, aluminium, brass, metal wire\n"
            "Exclude any product whose primary material is outside these two groups."
        ),
        "finish_examples": (
            "quality finishes, e.g. brushed, matte or powder-coated metal, textured or heavyweight fabric; "
            "premium trims or hardware, e.g. leather tabs or handles, wooden lids, brass or gold-tone accents, "
            "contrast stitching. Exclude thin flimsy fabric bins, plain chrome wire racks, generic mesh organisers."
        ),
    },
    "sapana": {
        "label": "Sapana",
        "families": ["ceramic", "plastic", "glass", "wood"],
        "material_block": (
            "Only select products whose PRIMARY material is one of the following:\n"
            "* Plastic: high-quality plastics only, e.g. PP, PET, acrylic, Tritan, recycled plastic\n"
            "* Glass: e.g. borosilicate, tempered or soda-lime glass\n"
            "* Ceramic: e.g. stoneware, porcelain, earthenware\n"
            "* Wood & Bamboo: e.g. acacia, mango wood, oak, walnut, paulownia, bamboo\n"
            "Exclude any product whose primary material is outside these four groups."
        ),
        "finish_examples": (
            "quality finishes, e.g. reactive or matte glazes, frosted or tinted glass, oiled or natural wood grain, "
            "crystal-clear or tinted acrylic; premium details, e.g. wooden or cork lids, airtight seals, gold-tone or "
            "brass accents. Exclude cloudy thin-walled plastic tubs, generic food containers with coloured snap lids, "
            "unfinished bamboo dividers."
        ),
    },
}

# ── Preferred retailers (seeded into aldi_preferred_retailers) ───────────────
# Tiers follow the prompt; priority steers the search budget — kids, pet,
# apparel, appliance and Germany rarely sell storage, so they are low.

TIER_LABEL = {
    "mass_value": "Mass and value", "marketplace": "Online marketplaces", "off_price": "Off-price",
    "mid_market": "Mid-market and specialty", "premium": "Premium home", "brand": "Home, kitchen and decor brands",
    "wholesale": "Wholesale decor brands", "kids": "Kids, baby and toy", "pet": "Pet",
    "lifestyle": "Lifestyle, travel, stationery and drinkware", "apparel": "Apparel, beauty and fitness",
    "appliance": "Appliance brands", "germany": "Germany",
}

MID_OR_PREMIUM = {"mid_market", "premium"}


def _r(name: str, tier: str, domain: Optional[str] = None, priority: str = "normal", country: str = "US") -> dict:
    return {"name": name, "domain": domain, "tier": tier, "priority": priority, "country": country}


PREFERRED_RETAILERS: list[dict] = [
    _r("Walmart", "mass_value", "walmart.com", "high"), _r("Target", "mass_value", "target.com", "high"),
    _r("Costco", "mass_value", "costco.com", "high"), _r("Amazon", "mass_value", "amazon.com", "high"),
    _r("Temu", "mass_value", "temu.com", "high"), _r("Five Below", "mass_value", "fivebelow.com", "high"),
    _r("Wayfair", "mass_value", "wayfair.com", "high"), _r("IKEA", "mass_value", "ikea.com", "high"),
    _r("Etsy", "marketplace", "etsy.com", "high"), _r("eBay", "marketplace", "ebay.com", "high"),
    _r("Uncommon Goods", "marketplace", "uncommongoods.com", "high"),
    _r("Not On The High Street", "marketplace", "notonthehighstreet.com", "high", "GB"),
    _r("HomeGoods", "off_price", "homegoods.com"), _r("Marshalls", "off_price", "marshalls.com"),
    _r("The Container Store", "mid_market", "containerstore.com", "high"), _r("World Market", "mid_market", "worldmarket.com", "high"),
    _r("Kirkland's", "mid_market", "kirklands.com", "high"), _r("Macy's", "mid_market", "macys.com", "high"),
    _r("Belk", "mid_market", "belk.com", "high"), _r("Bed Bath & Beyond", "mid_market", "bedbathandbeyond.com", "high"),
    _r("Urban Outfitters", "mid_market", "urbanoutfitters.com", "high"), _r("H&M Home", "mid_market", "hm.com", "high"),
    _r("Michaels", "mid_market", "michaels.com", "high"), _r("Hobby Lobby", "mid_market", "hobbylobby.com", "high"),
    _r("Paper Source", "mid_market", "papersource.com"), _r("Barnes & Noble", "mid_market", "barnesandnoble.com"),
    _r("Bath & Body Works", "mid_market", "bathandbodyworks.com"), _r("Altar'd State", "mid_market", "altardstate.com"),
    _r("Coldwater Creek", "mid_market", "coldwatercreek.com"), _r("Whole Foods", "mid_market", "wholefoodsmarket.com"),
    _r("Edward's Orchard", "mid_market", None), _r("Uncharted", "mid_market", "uncharted.com"),
    _r("Some Design Store", "mid_market", None),
    _r("Crate & Barrel", "premium", "crateandbarrel.com", "high"), _r("CB2", "premium", "cb2.com", "high"),
    _r("West Elm", "premium", "westelm.com", "high"), _r("Anthropologie", "premium", "anthropologie.com", "high"),
    _r("Lulu and Georgia", "premium", "luluandgeorgia.com", "high"), _r("Pottery Barn", "premium", "potterybarn.com", "high"),
    _r("Pottery Barn Kids", "premium", "potterybarnkids.com"), _r("Pottery Barn Teen", "premium", "pbteen.com"),
    _r("Pottery Barn Baby", "premium", None), _r("Williams Sonoma", "premium", "williams-sonoma.com", "high"),
    _r("Zara Home", "premium", "zarahome.com", "high"), _r("Birch Lane", "premium", "birchlane.com"),
    _r("Frontgate", "premium", "frontgate.com"), _r("Garnet Hill", "premium", "garnethill.com"),
    _r("Grandin Road", "premium", "grandinroad.com"), _r("Balsam Hill", "premium", "balsamhill.com"),
    _r("Article", "premium", "article.com"), _r("Magnolia", "premium", "magnolia.com"),
    _r("MacKenzie-Childs", "premium", "mackenzie-childs.com"), _r("Lenox", "premium", "lenox.com"),
    _r("Kate Spade New York", "premium", "katespade.com"), _r("Le Creuset", "premium", "lecreuset.com"),
    _r("STAUB", "premium", "zwilling.com"),
    _r("Joseph Joseph", "brand", "josephjoseph.com"), _r("The Cross Living", "brand", None),
    _r("Aesthetic Living", "brand", None), _r("Cotton & Crete", "brand", None), _r("Green Bubble Gorgeous", "brand", None),
    _r("Desenio", "brand", "desenio.com"), _r("Ruggable", "brand", "ruggable.com"), _r("Meri Meri", "brand", "merimeri.com"),
    _r("Packed Party", "brand", "packedparty.com"),
    _r("Mud Pie", "wholesale", "mud-pie.com"), _r("TAG", "wholesale", "tagltd.com"), _r("RAZ Imports", "wholesale", "razimports.com"),
    _r("Accent Decor", "wholesale", "accentdecor.com"), _r("My Mind's Eye", "wholesale", "mymindseye.com"),
    _r("S. Feldman Housewares", "wholesale", None), _r("Black Forest Decor", "wholesale", "blackforestdecor.com"),
    _r("Little Dutch", "kids", "littledutch.com", "low"), _r("Liewood", "kids", "liewood.com", "low"),
    _r("Hape", "kids", "hapetoys.com", "low"), _r("Melissa & Doug", "kids", "melissaanddoug.com", "low"),
    _r("Tender Leaf Toys", "kids", "tenderleaftoys.com", "low"), _r("Tooky Toy", "kids", None, "low"),
    _r("Wooden Toy Story", "kids", None, "low"), _r("waytoplay", "kids", "waytoplay.com", "low"),
    _r("Fairplay", "kids", None, "low"), _r("Petite Amelie", "kids", "petiteamelie.com", "low"),
    _r("PolarB", "kids", None, "low"), _r("Bauspiel", "kids", "bauspiel.com", "low"),
    _r("Baby Stork", "kids", None, "low"), _r("Little Oz Kid", "kids", None, "low"),
    _r("Chewy", "pet", "chewy.com", "low"), _r("Cocopup London", "pet", "cocopuplondon.com", "low"),
    _r("Lambwolf Collective", "pet", "lambwolf.com", "low"),
    _r("CALPAK", "lifestyle", "calpaktravel.com", "low"), _r("Bird in Bag", "lifestyle", None, "low"),
    _r("Kitsch", "lifestyle", "mykitsch.com", "low"), _r("Nodpod", "lifestyle", "nodpod.com", "low"),
    _r("Hydro Flask", "lifestyle", "hydroflask.com", "low"), _r("Stanley 1913", "lifestyle", "stanley1913.com", "low"),
    _r("The Darling Effect", "lifestyle", None, "low"), _r("Ello", "lifestyle", "elloproducts.com", "low"),
    _r("Rifle Paper Co.", "lifestyle", "riflepaperco.com", "low"), _r("Penco", "lifestyle", None, "low"),
    _r("Zebra Pen", "lifestyle", "zebrapen.com", "low"), _r("Ohuhu", "lifestyle", "ohuhu.com", "low"),
    _r("PRINTWORKS", "lifestyle", "printworksmarket.com", "low"), _r("Piecework Puzzles", "lifestyle", "pieceworkpuzzles.com", "low"),
    _r("LazyOne", "lifestyle", "lazyone.com", "low"), _r("Pixie Woo", "lifestyle", None, "low"),
    _r("Alo Yoga", "apparel", "aloyoga.com", "low"), _r("Gymshark", "apparel", "gymshark.com", "low"),
    _r("NEOLYMP", "apparel", None, "low"), _r("Bombas", "apparel", "bombas.com", "low"),
    _r("Beach Riot", "apparel", "beachriot.com", "low"), _r("FUNBOY", "apparel", "funboy.com", "low"),
    _r("Skin Gym", "apparel", "skingymco.com", "low"),
    _r("KitchenAid", "appliance", "kitchenaid.com", "low"), _r("Ninja", "appliance", "ninjakitchen.com", "low"),
    _r("Panasonic", "appliance", "panasonic.com", "low"),
    _r("BAUR", "germany", "baur.de", "low", "DE"), _r("car moebel", "germany", "car-moebel.de", "low", "DE"),
    _r("Segmüller", "germany", "segmueller.de", "low", "DE"), _r("Thalia", "germany", "thalia.de", "low", "DE"),
]

# Domains Claude's web fetch refuses outright (url_not_allowed, policy).
FETCH_BLOCKED_DOMAINS = {"amazon.com"}

# ── Prompts ──────────────────────────────────────────────────────────────────

ROLE = (
    "You are a senior Buying Manager and Merchandise Expert specialising in home storage and organisation. "
    "You have deep knowledge of materials, construction quality, price architecture and what sells at US mass "
    "and mid-to-premium retail. You have a strong eye for design and know what looks premium on shelf and wins "
    "a buyer's attention."
)

CRITERIA = """Product criteria (all must be met)
* Functional: solves a real storage or organisation need. Not purely decorative.
* Elevated and visually appealing: the product must look premium and present well on shelf, the kind of item a buyer would put in a feature display. Look for considered design details, such as {finish_examples} At mass and value retailers, look for their elevated or design-led lines rather than core basics.
* Stackable or nesting: the product must ship and display efficiently. It must stack securely on identical units, nest inside identical units when empty, or collapse / fold flat. Exclude bulky or irregular items that can't stack or nest (freestanding units, assembled shelving, carts, baskets whose handles or shape stop them stacking). Stackability must be confirmed from the product page, never assumed.
* Novel: a distinctive design, material, mechanism or format. Exclude commodity basics such as plain clear bins, standard wire baskets and generic drawer dividers.
* Proven demand: prefer products carrying a label such as Best Seller, Bestseller, Top Rated, Popular Pick, Customer Favourite or #1 in Category. An unlabelled product may still qualify if rated 4.3 stars or higher with at least 100 reviews. If the retailer uses neither labels nor ratings, use another visible popularity signal (Best Sellers / Most Popular placement, selling fast / low-stock messaging). Do not exclude a product from a retailer that doesn't label or rate for lacking a label.
* Selection preference: when choosing between qualifying products, prefer (1) the more elevated and visually appealing product, then (2) the product with a Best Seller or equivalent label (never held against retailers that don't label).
* Current: in stock and currently listed. Only products whose page was actually opened and verified can be chosen.
* Price: US${price_min:.0f}–{price_max:.0f} retail. For non-US retailers apply the band to the USD equivalent.
* No licensed characters or branded IP.
Spread: use no more than 3 products from any one retailer; include at least 2 from mass/value retailers and at least 2 from mid-market or premium retailers when the candidates allow it."""

PLAN_PROMPT = """{role}

TASK: write web search queries that will find elevated, visually appealing, stackable or nesting HOME STORAGE AND ORGANISATION products that express the trend themes below, for the US market. {material_scope}

The products must be storage or organisation items — canisters, jars, lidded containers, boxes, bins, baskets, caddies, trays, organisers, racks, holders, crocks. Translate each theme's colours, finishes, prints, forms and materials onto those product types. Do NOT search for the report's own product types unless they are storage items (a mixing bowl, pie dish or mug is not storage; a lidded canister in the same glaze is).

TREND DIGEST
{digest_block}

RETAILERS TO SPREAD ACROSS (search these by name, e.g. "Target stackable glass canister wood lid")
{retailer_block}

{avoid_block}{feedback_block}{hints_block}{rotation_block}RULES
- Return exactly {n} queries as a JSON array: [{{"query": "...", "theme_id": "T1", "tier": "mass_value|marketplace|mid_market|premium|brand|open", "material": "..."}}]
- Every theme gets at least one query; weight queries toward themes with product imagery.
- Each query names a concrete product type + a material from the scope + a distinctive attribute from the theme (colour, finish, print, form), and is 4–12 words. Include "stackable", "nesting" or "collapsible" where natural.
- About half the queries name a retailer (spread across the tiers, at least 3 mass/value and 3 mid-market/premium); the rest are open-web ("tier": "open").
- Do not target amazon.com{amazon_note}.
- No two queries may be near-duplicates, and none may repeat a query listed under AVOID.
Return ONLY the JSON array."""

DISCOVER_PROMPT = """Run exactly ONE web search for this query, then stop searching:
{query}

From the results, return ONLY a JSON array of the URLs that are individual PRODUCT pages (one product you could buy — not category, search, listicle, blog or brand-home pages) for a home storage or organisation item, each as:
{{"url": "<exact result url>", "title": "<result title>", "retailer": "<site name>", "fit": <0.0-1.0 how well it matches the query's product type, material and look>, "why": "<under 10 words>"}}
Skip furniture units, carts, purely decorative items, and anything with licensed characters. If no result is a product page, return []."""

RANK_PROMPT = """{role}

{material_scope} Price band US${price_min:.0f}–{price_max:.0f}.

TREND DIGEST (short)
{digest_short}

CANDIDATE PRODUCT PAGES found by search (not yet opened):
{candidate_block}

{feedback_block}Choose up to {k} candidates to open and verify. Maximise: fit to a theme (material, colour, finish, form), likelihood of being stackable/nesting storage, elevated look, and spread across retailers and tiers (aim for several mass/value AND several mid-market/premium). Skip obvious duplicates of the same product.
Return ONLY a JSON array, best first: [{{"id": <candidate id>, "theme_id": "T1", "fit": <0.0-1.0>}}]"""

VERIFY_PROMPT = """Open this product page with web_fetch (one fetch only): {url}

Then return ONLY a JSON object with these facts, read from the page. Use null where the page does not say; never guess.
{{
  "is_product_page": true/false,
  "product_name": "...",
  "retailer": "...",
  "price_usd": <number or null — the current price of the single product as shown; if a set, the set price>,
  "price_text": "<price exactly as shown, with currency>",
  "currency": "USD" | "...",
  "in_stock": true/false/null,
  "primary_material": "<the main material, e.g. 'stoneware', 'powder-coated steel', 'cotton canvas'>",
  "materials": ["<all materials named>"],
  "stacks_nests_collapses": {{"claim": "stacks" | "nests" | "collapses" | "none" | "unclear", "evidence": "<short quote from the page, or ''>"}},
  "bestseller_label": "<exact label wording such as 'Best Seller', or null>",
  "rating": <number or null>,
  "review_count": <integer or null — count any 'N reviews' / 'N ratings' text even when no star value is shown>,
  "demand_signals": ["<other popularity text on the page, e.g. 'Popular pick', 'Selling fast', '500+ bought in past month', 'Only 3 left', 'Top rated' — [] if none>"],
  "stock_text": "<exact stock wording if any, e.g. 'In stock', 'Sold out', or null>",
  "licensed_ip": true/false,
  "dimensions": "<as listed, or null>",
  "image_url": "<main product image URL if present, else null>",
  "description": "<one or two sentences: what it is, finish, colour, notable details>"
}}
If the fetch fails or the page is not a product page, return {{"is_product_page": false, "error": "<why>"}}."""

SELECT_SYSTEM = """{role}

{material_block}

{criteria}

Rules
* Only use facts from the verified candidate facts given to you. If a detail is missing there, treat it as not listed.
* The facts come from the page's text, not its scripts. Many retailers render star ratings and stock widgets in scripts, so: (a) treat a product as in stock unless the page says sold out, out of stock, unavailable or discontinued; (b) a missing star rating is NOT a demand failure by itself — demand is met by ANY one of: a Best Seller-type label, 100+ reviews mentioned, a popularity or low-stock signal, or a retailer that does not show ratings (then judge on label or placement). Demand fails only when the page gives no signal of any kind.
* Stackable / nesting / collapsing must still be stated on the page (the stack_nest claim and its evidence). "Unclear" is a failure; do not assume.
* Only cite trend elements that appear in the digest. Do not invent pages, elements or themes.
* If fewer than {n} candidates meet every criterion, choose only those that do and list the closest misses separately with the criterion each one fails. Do not lower the bar."""

SELECT_USER = """TREND DIGEST (themes with the screenshot elements that show them)
{digest_block}

VERIFIED CANDIDATES (each page was opened; facts are from the page)
{candidate_block}

{exclusion_block}{feedback_block}{approved_block}{fixed_block}TASK: choose {n} products{gap_note}. Return ONLY JSON:
{{
  "picks": [
    {{
      "candidate_id": <id>,
      "theme_id": "T1",
      "element_refs": [{{"upload_id": <int>, "element_id": "E4"}}],   // 1–3 elements from the digest this product echoes, images first
      "how_it_matches": "<1–2 sentences on the specific shared attributes: material, colour, finish, form, function>",
      "demand_evidence": "<the label / rating / other signal, in a few words>",
      "material_family": "fabric" | "metal" | "ceramic" | "glass" | "wood" | "plastic"
    }}
  ],
  "near_misses": [ {{"candidate_id": <id>, "fails": "<criterion>", "note": "<under 20 words>"}} ]
}}
Order picks best first (visual appeal and trend fit). Respect the spread rules. Never pick a candidate listed under DO NOT REPEAT."""


# ── URL helpers ──────────────────────────────────────────────────────────────

_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "ref_", "tag",
                    "gclid", "fbclid", "mc_cid", "mc_eid", "srsltid", "cm_mmc", "clickid", "irclickid"}


def normalise_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        host = (parts.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if k.lower() not in _TRACKING_PARAMS]
        path = re.sub(r"/+$", "", parts.path) or "/"
        return urlunsplit((parts.scheme.lower() or "https", host, path, urlencode(query), ""))
    except Exception:  # noqa: BLE001
        return url.strip()


def url_hash(url: str) -> str:
    return hashlib.sha256(normalise_url(url).encode("utf-8")).hexdigest()


_LISTING_RE = re.compile(
    r"(/search\b|/s\?|[?&]q=|[?&]query=|[?&]keyword=|[?&]searchTerm=|/keyword\.php|/category/|"
    r"/categories/|/browse/|/collections?/(?![^/]+/products/)|/sch/|/market/)",
    re.I,
)


def looks_like_listing(url: str) -> bool:
    """Search and category pages: the first run spent 3 of 15 fetches on
    them (Wayfair keyword pages, an Etsy search). Deliberately narrow —
    Anthropologie and Urban Outfitters sell at /shop/<product>, Container
    Store at /s/<path>?productId=, so path shape alone is not enough and
    anything unclear is left for verification to reject."""
    try:
        parts = urlsplit(url)
        path_q = parts.path + ("?" + parts.query if parts.query else "")
    except Exception:  # noqa: BLE001
        return False
    return bool(_LISTING_RE.search(path_q))


def domain_of(url: str) -> str:
    try:
        host = (urlsplit(url).netloc or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:  # noqa: BLE001
        return ""


def retailer_for_domain(domain: str, retailers: list[dict]) -> tuple[Optional[str], str]:
    """(retailer name, tier) for a domain; ('other' tier when unknown)."""
    d = domain.lower()
    for r in retailers:
        rd = (r.get("domain") or "").lower()
        if rd and (d == rd or d.endswith("." + rd)):
            return r["name"], r["tier"]
    return None, "other"


# ── Context ──────────────────────────────────────────────────────────────────

class RunContext:
    """Everything the stages need, loaded once by the orchestrator."""

    def __init__(self, *, run_id: int, session_id: int, team: str, digest: dict, prefs: dict,
                 retailers: list[dict], shown: list[dict], feedback: list[dict], previous_queries: list[str],
                 approved: Optional[list[dict]] = None, rejected: Optional[list[dict]] = None,
                 searched_retailers: Optional[dict] = None):
        self.run_id = run_id
        self.session_id = session_id
        self.team = team
        self.team_cfg = TEAMS[team]
        self.digest = digest
        self.prefs = prefs                     # {memo, price_min, price_max}
        self.retailers = [r for r in retailers if r.get("active", True)]
        self.shown = shown                     # [{product_name, url, url_hash, retailer}] already shown to this team
        self.feedback = feedback               # [{verdict, note, product_name, retailer, kind, fails}]
        # Near misses the buyer marked Good (pre-approved despite the failed
        # criterion) and Bad (excluded). (2026-11-04)
        self.approved = approved or []         # [{candidate_id, product_name, retailer, fails, note}]
        self.rejected = rejected or []         # [{product_name, retailer, url, url_hash}]
        self.approved_overrides = {a["candidate_id"]: a["fails"] for a in self.approved}
        # Retailer name -> how many earlier queries for this report targeted it
        # (Phase 5 rotation: Try Again widens the search instead of circling).
        self.searched_retailers = searched_retailers or {}
        self.previous_queries = previous_queries
        self.fast_model = settings.aldi_recommend_fast_model
        self.search_model = settings.aldi_recommend_search_model
        self.strong_model = settings.aldi_recommend_model

    # text blocks ------------------------------------------------------------

    @property
    def material_scope(self) -> str:
        fams = ", ".join(self.team_cfg["families"])
        return f"Material scope for this team: {fams} as the PRIMARY material only."

    def digest_block(self, with_elements: bool = True) -> str:
        lines = []
        season = self.digest.get("season_occasion")
        if season:
            lines.append(f"Season / occasion: {season}")
        if self.digest.get("applies_to"):
            lines.append(f"Applies to: {', '.join(self.digest['applies_to'])}")
        if self.digest.get("notes"):
            lines.append(f"Notes: {self.digest['notes']}")
        elements = self._elements_by_key()
        for t in self.digest.get("themes") or []:
            lines.append(f"\n{t['id']} — {t['name']} (pages {', '.join(str(p) for p in t.get('pages') or []) or '?'})")
            lines.append(f"  {t.get('description', '')}")
            if t.get("colours"):
                lines.append(f"  colours: {', '.join(t['colours'])}")
            if t.get("materials"):
                lines.append(f"  materials: {', '.join(t['materials'])}")
            if t.get("product_types"):
                lines.append(f"  product types: {', '.join(t['product_types'])}")
            if with_elements:
                for ref in t.get("element_refs") or []:
                    e = elements.get((ref["upload_id"], ref["element_id"]))
                    if e:
                        lines.append(f"  - upload_id {ref['upload_id']} {ref['element_id']} [{e.get('kind')}] page {ref.get('page')}: {e.get('description', '')[:220]}")
        return "\n".join(lines)

    def digest_short(self) -> str:
        return "\n".join(
            f"{t['id']} {t['name']}: {t.get('description', '')[:200]} | colours: {', '.join(t.get('colours') or [])[:80]}"
            for t in self.digest.get("themes") or []
        )

    def _elements_by_key(self) -> dict:
        out = {}
        for p in self.digest.get("pages") or []:
            for e in p.get("elements") or []:
                out[(p["upload_id"], e["id"])] = e
        return out

    def retailer_block(self) -> str:
        by_tier: dict[str, list[str]] = {}
        for r in self.retailers:
            if r.get("priority") == "low":
                continue
            by_tier.setdefault(r["tier"], []).append(r["name"])
        return "\n".join(f"* {TIER_LABEL.get(t, t)}: {', '.join(names)}" for t, names in by_tier.items())

    def feedback_block(self) -> str:
        parts = []
        memo = (self.prefs.get("memo") or "").strip()
        if memo:
            parts.append(f"TEAM PREFERENCES (written by the team):\n{memo}")
        learned = (self.prefs.get("learned_rules") or "").strip()
        if learned:
            parts.append("LEARNED FROM THIS TEAM'S FEEDBACK ACROSS REPORTS (standing rules, distilled from every Good / Bad "
                         "they have given — follow these unless the feedback below for this report says otherwise):\n" + learned)
        if self.feedback:
            lines = []
            for f in self.feedback[-40:]:
                tag = "GOOD" if f["verdict"] == "good" else "BAD"
                note = f" — {f['note']}" if f.get("note") else ""
                if f.get("kind") == "near_miss":
                    lines.append(f"- {tag} on a near miss that failed '{f.get('fails') or '?'}': "
                                 f"{f.get('product_name')} ({f.get('retailer') or '?'}){note}")
                else:
                    lines.append(f"- {tag}: {f.get('product_name')} ({f.get('retailer') or '?'}){note}")
            parts.append("BUYER FEEDBACK ON EARLIER PICKS AND NEAR MISSES for this report (BAD = do not repeat the mistake; "
                         "GOOD = more like this; GOOD on a near miss = the buyer would accept it despite that criterion, "
                         "so weigh that criterion more softly for this team):\n" + "\n".join(lines))
        return ("\n\n".join(parts) + "\n\n") if parts else ""

    def exclusion_block(self) -> str:
        if not self.shown and not self.rejected:
            return ""
        lines = [f"- {s.get('product_name')} ({s.get('retailer') or '?'}) {s.get('url')}" for s in self.shown[-60:]]
        lines += [f"- {r.get('product_name')} ({r.get('retailer') or '?'}) {r.get('url')} — buyer rejected it" for r in self.rejected[-30:]]
        return "DO NOT REPEAT — already shown to this team for this report, or rejected by the buyer:\n" + "\n".join(lines) + "\n\n"

    def approved_block(self) -> str:
        if not self.approved:
            return ""
        lines = [f"- [{a['candidate_id']}] {a.get('product_name')} ({a.get('retailer') or '?'}) — failed: {a.get('fails')}"
                 + (f" — buyer: {a['note']}" if a.get("note") else "") for a in self.approved[-20:]]
        return ("PRE-APPROVED BY THE BUYER — near misses they said they would accept. You may pick these; treat the "
                "named criterion as satisfied for this team:\n" + "\n".join(lines) + "\n\n")


# ── Stage 1: plan ────────────────────────────────────────────────────────────

async def plan_queries(ctx: RunContext, n: int, hints: Optional[list[str]] = None) -> list[dict]:
    """`hints` (second round) are the reasons the first round's candidates
    failed, so the planner can steer toward products that will qualify."""
    avoid = ""
    if ctx.previous_queries:
        avoid = "AVOID (queries already run for this report and team — find different angles):\n" + \
                "\n".join(f"- {q}" for q in ctx.previous_queries[-60:]) + "\n\n"
    hints_block = ""
    if hints:
        hints_block = ("WHAT FAILED IN THE PREVIOUS ROUND (steer the new queries away from these problems — e.g. add "
                       "'stackable' / 'set of' / 'lidded', target the price band, prefer retailers that show ratings):\n"
                       + "\n".join(f"- {h}" for h in hints[:12]) + "\n\n")
    amazon_note = "" if settings.aldi_recommend_amazon_fallback else " (its product pages cannot be opened)"
    rotation_block = ""
    if ctx.searched_retailers:
        top = sorted(ctx.searched_retailers.items(), key=lambda kv: -kv[1])[:20]
        rotation_block = ("RETAILERS ALREADY SEARCHED for this report and team (query counts): "
                          + ", ".join(f"{name} ×{count}" for name, count in top)
                          + ". For the retailer-named queries this time, prefer retailers NOT in this list while "
                            "keeping the tier spread, so the pool widens instead of circling.\n\n")
    prompt = PLAN_PROMPT.format(
        role=ROLE, material_scope=ctx.material_scope, digest_block=ctx.digest_block(with_elements=False),
        retailer_block=ctx.retailer_block(), avoid_block=avoid, feedback_block=ctx.feedback_block(),
        hints_block=hints_block, rotation_block=rotation_block, n=n, amazon_note=amazon_note,
    )
    data = await messages_create(
        {"model": ctx.fast_model, "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]},
        feature="aldi_recommend_plan", ref_type="aldi_run", ref_id=ctx.run_id,
    )
    raw = parse_json(response_text(data))
    out: list[dict] = []
    seen: set[str] = set()
    for q in raw if isinstance(raw, list) else []:
        if not isinstance(q, dict) or not (q.get("query") or "").strip():
            continue
        text = " ".join(str(q["query"]).split())[:160]
        key = text.lower()
        if key in seen or key in {p.lower() for p in ctx.previous_queries}:
            continue
        seen.add(key)
        out.append({"query": text, "theme_id": str(q.get("theme_id") or "")[:8] or None,
                    "tier": str(q.get("tier") or "open")[:20], "material": str(q.get("material") or "")[:60]})
    return out[:n]


# ── Stage 2: discover ────────────────────────────────────────────────────────

async def discover(ctx: RunContext, queries: list[dict]) -> list[dict]:
    sem = asyncio.Semaphore(settings.aldi_recommend_concurrency)

    async def one(q: dict) -> list[dict]:
        async with sem:
            body = {
                "model": ctx.search_model, "max_tokens": 2500,
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1,
                           "user_location": {"type": "approximate", "country": "US"}}],
                "messages": [{"role": "user", "content": DISCOVER_PROMPT.format(query=q["query"])}],
            }
            try:
                data = await messages_create(body, feature="aldi_recommend_search", ref_type="aldi_run", ref_id=ctx.run_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("aldi_recommend_search_failed", run_id=ctx.run_id, query=q["query"], error=str(exc))
                return []
            results = search_results(data)
            by_url = {normalise_url(r["url"]): r for r in results}
            picked: list[dict] = []
            try:
                listed = parse_json(response_text(data))
            except Exception:  # noqa: BLE001
                listed = []
            if isinstance(listed, list) and listed:
                for item in listed:
                    if not isinstance(item, dict) or not item.get("url"):
                        continue
                    nu = normalise_url(str(item["url"]))
                    base = by_url.get(nu)
                    picked.append({
                        "url": base["url"] if base else str(item["url"]),
                        "title": (item.get("title") or (base or {}).get("title") or "")[:300],
                        "snippet": (item.get("why") or "")[:200],
                        "retailer_hint": (item.get("retailer") or "")[:120],
                        "fit": _clamp(item.get("fit"), 0.4),
                        "source_query": q["query"], "theme_id": q.get("theme_id"),
                    })
            else:
                # Model gave nothing usable — keep the raw results at low confidence.
                picked = [{"url": r["url"], "title": r["title"][:300], "snippet": "", "retailer_hint": "",
                           "fit": 0.25, "source_query": q["query"], "theme_id": q.get("theme_id")} for r in results]
            return picked

    batches = await asyncio.gather(*(one(q) for q in queries))
    merged: dict[str, dict] = {}
    for batch in batches:
        for c in batch:
            d = domain_of(c["url"])
            if not d or d in FETCH_BLOCKED_DOMAINS and not settings.aldi_recommend_amazon_fallback:
                continue
            if looks_like_listing(c["url"]) or (c.get("fit") or 0) < 0.3:
                continue
            key = url_hash(c["url"])
            if key not in merged or c["fit"] > merged[key]["fit"]:
                c["url_hash"] = key
                c["domain"] = d
                merged[key] = c
    return list(merged.values())


# ── Stage 3: rank ────────────────────────────────────────────────────────────

async def rank(ctx: RunContext, candidates: list[dict], k: int) -> list[dict]:
    """candidates carry 'id' (db id), 'title', 'domain', 'snippet', 'theme_id', 'fit'."""
    if not candidates:
        return []
    lines = []
    for c in candidates:
        name, tier = retailer_for_domain(c["domain"], ctx.retailers)
        lines.append(f"[{c['id']}] {c['title'][:120]} | {name or c['domain']} ({tier}) | theme {c.get('theme_id') or '?'} | {c.get('snippet') or ''}")
    prompt = RANK_PROMPT.format(
        role=ROLE, material_scope=ctx.material_scope, price_min=ctx.prefs["price_min"], price_max=ctx.prefs["price_max"],
        digest_short=ctx.digest_short(), candidate_block="\n".join(lines), feedback_block=ctx.feedback_block(), k=k,
    )
    data = await messages_create(
        {"model": ctx.fast_model, "max_tokens": 3000, "messages": [{"role": "user", "content": prompt}]},
        feature="aldi_recommend_rank", ref_type="aldi_run", ref_id=ctx.run_id,
    )
    try:
        order = parse_json(response_text(data))
    except Exception:  # noqa: BLE001
        order = []
    by_id = {c["id"]: c for c in candidates}
    chosen: list[dict] = []
    seen: set[int] = set()
    for item in order if isinstance(order, list) else []:
        try:
            cid = int(item.get("id"))
        except Exception:  # noqa: BLE001
            continue
        if cid in by_id and cid not in seen:
            seen.add(cid)
            c = by_id[cid]
            c["fit"] = _clamp(item.get("fit"), c.get("fit") or 0.4)
            if item.get("theme_id"):
                c["theme_id"] = str(item["theme_id"])[:8]
            chosen.append(c)
    if not chosen:  # fall back to discovery fit order
        chosen = sorted(candidates, key=lambda c: -(c.get("fit") or 0))
    return chosen[:k]


# ── Stage 4: verify ──────────────────────────────────────────────────────────

async def verify(ctx: RunContext, candidates: list[dict]) -> dict[int, dict]:
    """{candidate id: {status, facts, og_image}} — status: verified | blocked | dead | not_product | error."""
    sem = asyncio.Semaphore(settings.aldi_recommend_concurrency)

    async def one(c: dict) -> tuple[int, dict]:
        async with sem:
            data = None
            for tool_type in ("web_fetch_20250910", "web_fetch_20260209"):
                body = {
                    "model": ctx.fast_model, "max_tokens": 2500,
                    "tools": [{"type": tool_type, "name": "web_fetch", "max_uses": 1}],
                    "messages": [{"role": "user", "content": VERIFY_PROMPT.format(url=c["url"])}],
                }
                try:
                    data = await messages_create(body, feature="aldi_recommend_fetch", ref_type="aldi_run", ref_id=ctx.run_id)
                    break
                except ClaudeRawError as exc:
                    if exc.status == 400 and "web_fetch" in exc.body:
                        continue        # tool version not accepted — try the other
                    log.warning("aldi_recommend_fetch_failed", run_id=ctx.run_id, url=c["url"], error=str(exc))
                    return c["id"], {"status": "error", "facts": {"error": str(exc)[:200]}, "og_image": None}
                except Exception as exc:  # noqa: BLE001
                    log.warning("aldi_recommend_fetch_failed", run_id=ctx.run_id, url=c["url"], error=str(exc))
                    return c["id"], {"status": "error", "facts": {"error": str(exc)[:200]}, "og_image": None}
            if data is None:
                return c["id"], {"status": "error", "facts": {"error": "no fetch tool accepted"}, "og_image": None}
            fetched = fetch_results(data)
            ok = [f for f in fetched if f["ok"]]
            if not ok:
                code = (fetched[0]["error_code"] if fetched else "no_fetch") or "unknown"
                status = "blocked" if code in ("url_not_allowed", "url_not_accessible", "too_many_requests") else "dead" if code in ("not_found",) else "blocked"
                return c["id"], {"status": status, "facts": {"error": code}, "og_image": None}
            meta = page_meta(ok[0]["text"])
            try:
                facts = parse_json(response_text(data))
                if not isinstance(facts, dict):
                    facts = {}
            except Exception:  # noqa: BLE001
                facts = {}
            facts = _sanitise_facts(facts, meta)
            og = facts.get("image_url") or meta.get("og:image")
            status = "verified" if facts.get("is_product_page") else "not_product"
            return c["id"], {"status": status, "facts": facts, "og_image": og}

    pairs = await asyncio.gather(*(one(c) for c in candidates))
    return dict(pairs)


def _sanitise_facts(facts: dict, meta: dict) -> dict:
    out = dict(facts)
    out["is_product_page"] = bool(facts.get("is_product_page"))
    price = _to_float(facts.get("price_usd"))
    if price is None and (facts.get("currency") in (None, "USD")):
        price = _to_float(meta.get("og:price:amount") or meta.get("product:price:amount"))
    out["price_usd"] = price
    out["materials"] = [str(m).strip() for m in (facts.get("materials") or []) if str(m).strip()][:10]
    out["primary_material"] = (str(facts.get("primary_material")).strip()[:80] if facts.get("primary_material") else None)
    snc = facts.get("stacks_nests_collapses") or {}
    if not isinstance(snc, dict):
        snc = {"claim": "unclear", "evidence": ""}
    claim = str(snc.get("claim") or "unclear").lower()
    out["stacks_nests_collapses"] = {"claim": claim if claim in ("stacks", "nests", "collapses", "none", "unclear") else "unclear",
                                     "evidence": str(snc.get("evidence") or "")[:300]}
    out["rating"] = _to_float(facts.get("rating"))
    rc = _to_float(facts.get("review_count"))
    out["review_count"] = int(rc) if rc is not None else None
    out["bestseller_label"] = (str(facts.get("bestseller_label")).strip()[:80] if facts.get("bestseller_label") else None)
    out["demand_signals"] = [str(s).strip()[:80] for s in (facts.get("demand_signals") or []) if str(s).strip()][:6]
    out["stock_text"] = (str(facts.get("stock_text")).strip()[:80] if facts.get("stock_text") else None)
    out["licensed_ip"] = bool(facts.get("licensed_ip"))
    out["product_name"] = (str(facts.get("product_name") or meta.get("og:title") or "").strip()[:300]) or None
    out["retailer"] = (str(facts.get("retailer") or meta.get("og:site_name") or "").strip()[:120]) or None
    out["image_url"] = (str(facts.get("image_url")).strip() if facts.get("image_url") and str(facts["image_url"]).startswith("http") else None)
    out["description"] = (str(facts.get("description") or "").strip()[:600]) or None
    out["in_stock"] = facts.get("in_stock") if isinstance(facts.get("in_stock"), bool) else None
    out["families"] = mf.families_for(out["materials"] + ([out["primary_material"]] if out["primary_material"] else []))
    out["primary_family"] = mf.family_of(out["primary_material"]) if out["primary_material"] else None
    return out


# ── Stage 5: select + enforce ────────────────────────────────────────────────

def _candidate_block(ctx: RunContext, verified: list[dict]) -> str:
    lines = []
    for c in verified:
        f = c["facts"]
        name, tier = retailer_for_domain(c["domain"], ctx.retailers)
        snc = f.get("stacks_nests_collapses") or {}
        demand_bits = []
        if f.get("bestseller_label"):
            demand_bits.append(f"label '{f['bestseller_label']}'")
        if f.get("rating") is not None:
            demand_bits.append(f"{f['rating']}★")
        if f.get("review_count") is not None:
            demand_bits.append(f"{f['review_count']} reviews")
        demand_bits += f.get("demand_signals") or []
        demand = "; ".join(demand_bits) or "no signal visible in page text"
        stock = f.get("stock_text") or ("in stock" if f.get("in_stock") else "sold out" if f.get("in_stock") is False else "not stated (assume in stock)")
        lines.append(
            f"[{c['id']}] {f.get('product_name') or c['title']} | {name or f.get('retailer') or c['domain']} ({tier}) | "
            f"price {f.get('price_text') or f.get('price_usd') or 'not listed'} (USD {f.get('price_usd') if f.get('price_usd') is not None else '?'}) | "
            f"primary material: {f.get('primary_material') or '?'}; materials: {', '.join(f.get('materials') or []) or '?'} | "
            f"stack/nest: {snc.get('claim')} — \"{(snc.get('evidence') or '')[:120]}\" | demand: {demand} | "
            f"stock: {stock} | licensed IP: {f.get('licensed_ip')} | {f.get('description') or ''}"
        )
    return "\n".join(lines)


async def select(ctx: RunContext, verified: list[dict], n: int, fixed: Optional[list[dict]] = None, gap_note: str = "") -> dict:
    system = SELECT_SYSTEM.format(
        role=ROLE, material_block=ctx.team_cfg["material_block"],
        criteria=CRITERIA.format(finish_examples=ctx.team_cfg["finish_examples"],
                                 price_min=ctx.prefs["price_min"], price_max=ctx.prefs["price_max"]),
        n=n,
    )
    fixed_block = ""
    if fixed:
        fixed_block = "ALREADY CHOSEN (keep; do not repeat): " + ", ".join(f"[{p['candidate_id']}]" for p in fixed) + "\n\n"
    user = SELECT_USER.format(
        digest_block=ctx.digest_block(with_elements=True), candidate_block=_candidate_block(ctx, verified),
        exclusion_block=ctx.exclusion_block(), feedback_block=ctx.feedback_block(), approved_block=ctx.approved_block(),
        fixed_block=fixed_block,
        n=n, gap_note=gap_note,
    )
    data = await messages_create(
        {"model": ctx.strong_model, "max_tokens": 6000, "system": system,
         "messages": [{"role": "user", "content": user}]},
        feature="aldi_recommend_select", ref_type="aldi_run", ref_id=ctx.run_id,
    )
    try:
        out = parse_json(response_text(data))
    except Exception as exc:  # noqa: BLE001
        log.error("aldi_recommend_select_unparseable", run_id=ctx.run_id, error=str(exc))
        return {"picks": [], "near_misses": []}
    if not isinstance(out, dict):
        return {"picks": [], "near_misses": []}
    return {"picks": [p for p in (out.get("picks") or []) if isinstance(p, dict)],
            "near_misses": [m for m in (out.get("near_misses") or []) if isinstance(m, dict)]}


def enforce_rules(ctx: RunContext, picks: list[dict], verified_by_id: dict[int, dict], shown_hashes: set[str]) -> tuple[list[dict], list[dict]]:
    """Apply the hard rules in code. Returns (accepted, rejected) where each
    rejected pick carries 'fails'."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    per_retailer: dict[str, int] = {}
    pmin, pmax = float(ctx.prefs["price_min"]), float(ctx.prefs["price_max"])
    team_fams = set(ctx.team_cfg["families"])
    seen_ids: set[int] = set()
    for p in picks:
        try:
            cid = int(p.get("candidate_id"))
        except Exception:  # noqa: BLE001
            continue
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        c = verified_by_id.get(cid)
        if not c:
            rejected.append({**p, "fails": "unknown candidate"})
            continue
        f = c["facts"]
        name, tier = retailer_for_domain(c["domain"], ctx.retailers)
        rkey = name or c["domain"]
        # A near miss the buyer marked Good is exempt from the price and
        # stacking rules it failed on (never from IP, stock or scope).
        override = ctx.approved_overrides.get(cid)
        reason = None
        if c.get("status") != "verified" or not f.get("is_product_page"):
            reason = "page could not be verified"
        elif c["url_hash"] in shown_hashes:
            reason = "already shown"
        elif f.get("licensed_ip"):
            reason = "licensed character / branded IP"
        elif f.get("price_usd") is None and not override:
            reason = "price not listed"
        elif f.get("price_usd") is not None and not (pmin <= float(f["price_usd"]) <= pmax) and not override:
            reason = f"price ${f['price_usd']:.2f} outside ${pmin:.0f}–{pmax:.0f}"
        elif f.get("in_stock") is False:
            reason = "out of stock"
        elif (f.get("stacks_nests_collapses") or {}).get("claim") in ("none",) and not override:
            reason = "does not stack, nest or collapse"
        else:
            pf = f.get("primary_family")
            fams = set(f.get("families") or [])
            model_fam = str(p.get("material_family") or "").lower()
            if pf and pf not in team_fams:
                reason = f"primary material ({f.get('primary_material')}) outside team scope"
            elif not pf and not (fams & team_fams) and model_fam not in team_fams:
                reason = "material outside team scope"
        if reason is None and per_retailer.get(rkey, 0) >= 3:
            reason = f"more than 3 picks from {rkey}"
        if reason:
            rejected.append({**p, "fails": reason, "candidate_id": cid})
            continue
        per_retailer[rkey] = per_retailer.get(rkey, 0) + 1
        accepted.append({**p, "candidate_id": cid, "retailer": rkey, "tier": tier})
    return accepted, rejected


def spread_gap(ctx: RunContext, accepted: list[dict], n: int) -> str:
    """Describe what a repair round should add, or '' when nothing is needed."""
    tiers = [a.get("tier") for a in accepted]
    need = []
    if len(accepted) < n:
        need.append(f"{n - len(accepted)} more qualifying product(s)")
    if sum(1 for t in tiers if t == "mass_value") < 2 and len(accepted) >= n:
        need.append("at least 2 from mass/value retailers")
    if sum(1 for t in tiers if t in MID_OR_PREMIUM) < 2 and len(accepted) >= n:
        need.append("at least 2 from mid-market or premium retailers")
    return "; ".join(need)


# ── Phase 5: distillation and link re-check ──────────────────────────────────

DISTILL_PROMPT = """You maintain the standing preferences of {team_label}'s buying team for a product recommender. 
The recommender finds stackable / nesting home storage and organisation products on the web that express a trend report, 
restricted to these primary materials: {families}. The team marks each recommended product Good or Bad, often with a note, 
and also marks "near misses" (products that failed one criterion) — a Good on a near miss means they would accept it despite 
that criterion.

THE TEAM'S OWN WRITTEN PREFERENCES (do not restate these):
{memo}

CURRENT DISTILLED RULES (revise: keep what the log still supports, drop what newer feedback contradicts):
{current_rules}

FEEDBACK LOG (oldest first; {n} items):
{log}

Write the standing rules this team's recommender should follow from now on. Rules must generalise beyond a single product 
(what to prefer, what to avoid, which criteria the team treats as soft and when, retailers or looks they favour, price 
signals). Phrase each as an instruction ("Prefer …", "Avoid …", "Treat … as acceptable when …"). Do not invent preferences 
the log does not support; where the log is thin, say less. At most 12 rules, each under 30 words.
Return ONLY JSON: {{"rules": ["…", "…"], "summary": "<one sentence on what changed since the current rules, or 'no change'>"}}"""


async def distill_rules(team: str, items: list[dict], current_rules: str, memo: str, *, ref_id: Optional[int] = None) -> Optional[dict]:
    """One fast call that turns a team's whole feedback log into standing rules."""
    cfg = TEAMS[team]
    lines = []
    for it in items[-200:]:
        tag = "GOOD" if it.get("verdict") == "good" else "BAD"
        kind = f"near miss (failed: {it.get('fails')})" if it.get("kind") == "near_miss" else "pick"
        price = f" ${it['price_usd']:.0f}" if it.get("price_usd") is not None else ""
        fam = f" [{it['material_family']}]" if it.get("material_family") else ""
        theme = f" theme: {it['theme_name']};" if it.get("theme_name") else ""
        note = f" — \"{it['note']}\"" if it.get("note") else ""
        lines.append(f"- {tag} {kind}: {it.get('product_name')} ({it.get('retailer') or '?'}{price}){fam};{theme}{note}")
    prompt = DISTILL_PROMPT.format(
        team_label=cfg["label"], families=", ".join(cfg["families"]), memo=memo.strip() or "(none)",
        current_rules=current_rules.strip() or "(none yet)", n=len(lines), log="\n".join(lines) or "(empty)",
    )
    try:
        data = await messages_create(
            {"model": settings.aldi_distill_model, "max_tokens": 3000, "messages": [{"role": "user", "content": prompt}]},
            feature="aldi_distill", ref_type="aldi_team", ref_id=ref_id,
        )
        out = parse_json(response_text(data))
    except Exception as exc:  # noqa: BLE001
        log.error("aldi_distill_failed", team=team, error=str(exc))
        return None
    if not isinstance(out, dict):
        return None
    rules = [str(r).strip()[:240] for r in (out.get("rules") or []) if str(r).strip()][:12]
    return {"rules": rules, "summary": str(out.get("summary") or "")[:300]}


LINKCHECK_PROMPT = """Open this URL once with web_fetch (one fetch only): {url}
Return ONLY JSON: {{"reachable": true/false, "is_product_page": true/false, "in_stock": true/false/null, "title": "<page title or null>"}}.
If the fetch fails return {{"reachable": false}}."""


async def check_link(url: str, *, ref_id: Optional[int] = None) -> dict:
    """{status: ok | gone | blocked, title, in_stock}. 'blocked' means the
    fetch was refused, which says nothing about the product."""
    data = None
    for tool_type in ("web_fetch_20250910", "web_fetch_20260209"):
        body = {"model": settings.aldi_linkcheck_model, "max_tokens": 400,
                "tools": [{"type": tool_type, "name": "web_fetch", "max_uses": 1}],
                "messages": [{"role": "user", "content": LINKCHECK_PROMPT.format(url=url)}]}
        try:
            data = await messages_create(body, feature="aldi_linkcheck", ref_type="aldi_recommendation", ref_id=ref_id)
            break
        except ClaudeRawError as exc:
            if exc.status == 400 and "web_fetch" in exc.body:
                continue
            return {"status": "blocked", "error": str(exc)[:120]}
        except Exception as exc:  # noqa: BLE001
            return {"status": "blocked", "error": str(exc)[:120]}
    if data is None:
        return {"status": "blocked", "error": "no fetch tool accepted"}
    fetched = fetch_results(data)
    ok = [f for f in fetched if f["ok"]]
    if not ok:
        code = (fetched[0]["error_code"] if fetched else None) or "unknown"
        return {"status": "gone" if code in ("not_found",) else "blocked", "error": code}
    try:
        facts = parse_json(response_text(data))
    except Exception:  # noqa: BLE001
        facts = {}
    if isinstance(facts, dict) and facts.get("reachable") is False:
        return {"status": "gone"}
    if isinstance(facts, dict) and facts.get("is_product_page") is False:
        return {"status": "gone", "title": facts.get("title")}
    return {"status": "ok", "title": (facts or {}).get("title") if isinstance(facts, dict) else None,
            "in_stock": (facts or {}).get("in_stock") if isinstance(facts, dict) else None}


# ── Thumbnails ───────────────────────────────────────────────────────────────

# No avif / webp in Accept: Pillow 10 cannot decode AVIF, and CDNs honour the
# header (Walmart served AVIF on the first run and the thumbnail was lost).
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
       "Accept": "image/jpeg,image/png,image/gif;q=0.9,*/*;q=0.5"}


async def fetch_thumbnail(url: Optional[str], max_edge: int = 480) -> Optional[bytes]:
    """Download a product image and downsize it to a JPEG thumbnail. None on
    any failure (the card then hotlinks the URL or shows no image)."""
    if not url or not url.startswith("http"):
        return None
    try:
        async with httpx.AsyncClient(headers=_UA, follow_redirects=True, timeout=20) as client:
            r = await client.get(url)
            if r.status_code != 200 or len(r.content) < 500:
                return None
            data = r.content
    except Exception:  # noqa: BLE001
        return None
    try:
        from PIL import Image
        from analysis.catalogue_payload import encode_jpeg
        img = Image.open(io.BytesIO(data))
        img.thumbnail((max_edge, max_edge))
        return encode_jpeg(img.convert("RGB"), quality=82, optimize=True)
    except Exception:  # noqa: BLE001
        return None


# ── Small helpers ────────────────────────────────────────────────────────────

def _clamp(v, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, x))


def _to_float(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def element_lookup(digest: dict) -> dict:
    out = {}
    for p in digest.get("pages") or []:
        for e in p.get("elements") or []:
            out[(int(p["upload_id"]), e["id"])] = {**e, "page": p.get("page")}
    return out


def validate_refs(refs: Any, elements: dict, theme: Optional[dict]) -> list[dict]:
    out: list[dict] = []
    seen = set()
    for r in refs if isinstance(refs, list) else []:
        try:
            uid, eid = int(r.get("upload_id")), str(r.get("element_id") or "").upper()
        except Exception:  # noqa: BLE001
            continue
        e = elements.get((uid, eid))
        if e and (uid, eid) not in seen:
            seen.add((uid, eid))
            out.append({"upload_id": uid, "element_id": eid, "page": e.get("page"), "description": e.get("description", "")[:200]})
    if not out and theme:
        for r in theme.get("element_refs") or []:
            e = elements.get((r["upload_id"], r["element_id"]))
            if e and e.get("kind") == "image":
                out.append({"upload_id": r["upload_id"], "element_id": r["element_id"], "page": e.get("page"), "description": e.get("description", "")[:200]})
                break
    return out[:3]
