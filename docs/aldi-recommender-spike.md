# ALDI web recommender — Phase 0 spike report

Date: 2026-11-01. Session used: ALDI session 58 (three screenshots: the Coffee Bar
mood board, the Single Themes table, the Single Themes image page). Prompt: Prompt 2
(plastic / glass / ceramic / wood & bamboo) verbatim from `aldi-recommender-prompts.md`.
Model: Opus 5.5 via raw streaming HTTP (the container's anthropic SDK is 0.40.0 and
predates the web fetch block types). Tools: `web_search_20260209` + `web_fetch_20260209`.

## Runs

| | Spike 1 | Spike 2 | Fetch probe |
|---|---|---|---|
| Budget (searches / fetches) | 20 / 25 | 40 / 40 | 0 / 20 |
| Prompt caching | off | on | off |
| Extra instructions | none | explicit budget, open pages early, include mass retailers | fetch 12 known product URLs |
| Elapsed | 163 s | 305 s | 76 s |
| Searches used | 20 (3 duplicates) | 35 | 0 |
| Fetches used | 4 (all Crate & Barrel) | 39 (31 ok, 8 failed) | 20 for 12 URLs (retries) |
| Qualifying picks | 0 of 5 | 2 of 5, plus 10 near misses | n/a |
| Cost | $1.48 (268k uncached input) | $1.59 (1.49M cached reads, 23k output) | ~$0.30 (Sonnet 5.5) |

## Findings

1. **Trend reading is accurate.** Both runs produced a correct theme summary with page
   numbers, image descriptions and the swatch labels, checked against the screenshots.
   The per-session inventory step (Phase 2) is validated.
2. **The single-turn agent does not reach five picks within a sane budget.** Spike 1
   exhausted searches with one page read. Spike 2 doubled the budget and still finished
   with two qualifying products after five minutes. The bar in the prompt is strict
   (4.3★ with 100+ reviews, $15–60, stackability confirmed on the page, novel, elevated),
   and most near misses failed on "rating not visible": several retailers render ratings
   client-side, so the fetched text has no star rating.
3. **Dynamic filtering wastes budget.** In all three runs the model's sandbox scripts
   repeated searches, dropped fetch results through their own bugs and retried, and hit
   the tool cap ("Server tool use limit exceeded").
4. **Each tool round re-bills the whole context.** Without caching, 268k input tokens
   for one run. With caching the same work became 1.49M cache-read tokens, still $0.30,
   because fetched page text (~6k tokens per page) accumulates in the context.
5. **Our own server cannot fetch retailer pages.** 11 of 12 retailers blocked a direct
   browser-style request from the API container (Amazon, Walmart, Target, Container
   Store, Anthropologie, West Elm, Wayfair, Macy's, Crate & Barrel, Williams Sonoma,
   Pottery Barn). Only IKEA served the page. Backend link verification and thumbnail
   scraping are therefore not viable.
6. **Claude's web fetch reaches most retailers.** Walmart, IKEA, Container Store,
   Anthropologie, West Elm, Wayfair, Macy's, Crate & Barrel, Williams Sonoma and Pottery
   Barn fetched with title and price. Some individual URLs still fail
   (`url_not_accessible`), so a fetch failure must be treated as "unverified", not "bad".
7. **Amazon is refused outright** (`url_not_allowed`, Anthropic's fetch policy). Target
   is refused on some URLs and served on others. The existing scrapers reach Amazon,
   Target and Walmart through Apify actors, which is the available fallback.
8. **Fetched pages carry what the card needs.** The fetch result is page text with the
   meta tags intact: `og:image`, `og:title`, `og:price:amount`, canonical URL. So
   thumbnails and prices come from the fetch itself; no second request to the retailer.

## Verdict

Build the recommender as a controlled pipeline, not one agentic turn:

1. **Discovery** — from the stored trend digest and the team's material scope, one call
   writes 12–20 targeted queries. Each query runs as its own small search request
   (Sonnet 5.5, one search, "return candidate product URLs with title, retailer and
   snippet as JSON"), in parallel. Candidates are deduplicated against everything already
   shown and persisted in a per-session, per-team pool.
2. **Triage** — one call ranks the pool against the digest, scope and feedback and picks
   ~15 to verify.
3. **Verification** — each of the ~15 URLs gets its own small fetch request (Sonnet 5.5,
   one fetch) returning structured facts: title, price, in stock, primary material,
   stackability claim with the quoted evidence, best-seller label, rating and review count
   if visible, `og:image`. Amazon and failed Target URLs go through the Apify fallback or
   are marked unverified. All in parallel.
4. **Selection** — one Opus 5.5 call sees only verified candidates plus the digest, the
   rules, the exclusions and the feedback, and returns five picks with the theme and
   screenshot element each one echoes, plus near misses with the failing criterion.
5. **Thumbnails** — downloaded from the `og:image` CDN URL at save time; hotlinked if the
   CDN refuses.

Why: fixed budget and cost per run, parallel (about a minute instead of five), every
candidate verified by construction, the pool is reused by Try Again so repeats are
cheaper and genuinely different, and each step is testable on its own. Rough cost:
$0.80–1.00 for a first run, about half for a Try Again.

The prompt's criteria are applied in the verification and selection steps; the
"proven demand" fallbacks (retailer does not label, rating not visible) are applied
explicitly rather than left to the model to remember.

## Open question for David

Amazon: cover it from day one via the Apify fallback (extra cost per fetch, a few
seconds each), or leave it out of v1 and add it in Phase 5? Walmart, IKEA, Wayfair and
most Target pages already satisfy the mass/value minimum without it.
