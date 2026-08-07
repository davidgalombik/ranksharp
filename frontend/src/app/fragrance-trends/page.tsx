import { api, type FragranceTrend, type FragranceTrendExample } from "@/lib/api";
import Link from "next/link";
import clsx from "clsx";
import FragranceActionButton from "@/components/FragranceActionButton";
import ClearSetsButton from "@/components/ClearSetsButton";
import FragranceGenerationTabs from "@/components/FragranceGenerationTabs";
import FragranceTrendViewAllButton from "@/components/FragranceTrendViewAllButton";
import FragranceTrendFilters from "@/components/FragranceTrendFilters";

// Fragrance is now run-based (sporadic scrapes → each analysis run
// stands alone). Momentum was removed 2026-08-06 — the "+18% vs last
// week" was fiction when runs happen at arbitrary intervals. Status
// styles below are retained for backward-compat rendering of any
// pre-refactor trends still stored with status=rising/plateau/etc.
const STATUS_STYLES = {
  rising:   "bg-emerald-100 text-emerald-800",
  plateau:  "bg-amber-100 text-amber-800",
  declining:"bg-rose-100 text-rose-800",
  new:      "bg-blue-100 text-blue-800",
} as const;

const STATUS_ICONS = { rising: "↑", plateau: "→", declining: "↓", new: "★" };

const CATEGORY_COLOURS: Record<string, string> = {
  aesthetic:      "bg-purple-100 text-purple-700",
  scent:          "bg-amber-100 text-amber-700",
  market:         "bg-blue-100 text-blue-700",
  sustainability: "bg-emerald-100 text-emerald-700",
  retail:         "bg-rose-100 text-rose-700",
};

const COUNTRY_FLAGS: Record<string, string> = { US: "🇺🇸", AU: "🇦🇺", GB: "🇬🇧" };

function ImageMosaic({ examples }: { examples: FragranceTrendExample[] }) {
  const withImg = examples.filter((e) => e.primary_image_url);
  if (withImg.length === 0) {
    return (
      <div className="h-52 bg-amber-50 flex items-center justify-center text-amber-200 text-5xl">
        🕯
      </div>
    );
  }
  if (withImg.length === 1) {
    return (
      <div className="h-52 bg-stone-100 overflow-hidden">
        <img src={withImg[0].primary_image_url!} alt={withImg[0].name} className="w-full h-full object-cover" />
      </div>
    );
  }
  const [hero, second, third] = withImg;
  const rightSlots = [second, third].filter(Boolean);
  return (
    <div className="h-52 flex gap-0.5 overflow-hidden bg-stone-200">
      <div className="flex-[3] overflow-hidden">
        <img src={hero.primary_image_url!} alt={hero.name} className="w-full h-full object-cover" title={`${hero.retailer_name}: ${hero.name}`} />
      </div>
      <div className="flex-[2] flex flex-col gap-0.5">
        {rightSlots.map((ex) => (
          <div key={ex.product_id} className="flex-1 overflow-hidden">
            <img src={ex.primary_image_url!} alt={ex.name} className="w-full h-full object-cover" title={`${ex.retailer_name}: ${ex.name}`} />
          </div>
        ))}
        {rightSlots.length < 2 && <div className="flex-1 bg-stone-100" />}
      </div>
    </div>
  );
}

function FragranceTrendCard({ trend }: { trend: FragranceTrend }) {
  // Best-seller pill on the mosaic — matches the Product Trends style
  // the buyer approved. Any example being a best-seller flags the whole
  // card (like a section badge).
  const hasBestSeller = (trend.examples ?? []).some((e) => e.is_best_seller);
  return (
    <Link href={`/fragrance-trends/${trend.id}`} className="group block">
      <article className="bg-white rounded-xl border border-stone-200 overflow-hidden hover:shadow-lg transition-shadow flex flex-col">
        <div className="relative">
          <ImageMosaic examples={trend.examples ?? []} />
          {hasBestSeller && (
            <span
              className={clsx(
                "absolute top-2 left-2 px-1.5 py-0.5 rounded-full",
                "text-[10px] font-semibold bg-amber-100 text-amber-800 border border-amber-200",
              )}
            >
              ★ Best Seller
            </span>
          )}
          {/* Legacy status badge — only shown for pre-refactor trends
              where status is anything other than 'new'. New trends drop
              the badge entirely since momentum is gone. */}
          {trend.status && trend.status !== "new" && (
            <span className={clsx("absolute top-2 right-2 px-2 py-0.5 rounded-full text-xs font-semibold shadow-sm", STATUS_STYLES[trend.status])}>
              {STATUS_ICONS[trend.status]}{" "}
              {trend.status.charAt(0).toUpperCase() + trend.status.slice(1)}
              {trend.momentum_pct != null && ` ${trend.momentum_pct > 0 ? "+" : ""}${trend.momentum_pct}%`}
            </span>
          )}
          {trend.markets && trend.markets.length > 0 && (
            <div className="absolute bottom-2 left-2 flex gap-0.5">
              {trend.markets.slice(0, 3).map((m) => (
                <span key={m} className="text-base leading-none" title={m}>{COUNTRY_FLAGS[m] ?? m}</span>
              ))}
            </div>
          )}
        </div>

        <div className="p-4 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={clsx("px-2 py-0.5 rounded-full text-xs font-medium", CATEGORY_COLOURS[trend.category] ?? "bg-stone-100 text-stone-600")}>
              {trend.category}
            </span>
            {trend.price_tier && (
              <span className="text-xs text-stone-400">· {trend.price_tier}</span>
            )}
          </div>

          <div>
            <h3 className="text-base font-semibold text-stone-900 group-hover:text-stone-600 leading-snug">
              {trend.name}
            </h3>
            <p className="text-sm text-stone-600 mt-1 line-clamp-2">{trend.description}</p>
          </div>

          {/* Scent families */}
          {trend.scent_families.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {trend.scent_families.slice(0, 3).map((s) => (
                <span key={s} className="px-2 py-0.5 bg-amber-50 border border-amber-200 rounded-full text-xs text-amber-700">{s}</span>
              ))}
            </div>
          )}

          {/* Container styles */}
          {trend.container_styles.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {trend.container_styles.slice(0, 3).map((c) => (
                <span key={c} className="px-2 py-0.5 bg-stone-100 rounded-full text-xs text-stone-600">{c}</span>
              ))}
            </div>
          )}

          {/* Retailer pills */}
          {trend.retailer_names.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {trend.retailer_names.map((r) => (
                <span key={r} className="px-2 py-0.5 bg-stone-100 rounded-full text-xs text-stone-600 font-medium">{r}</span>
              ))}
            </div>
          )}

          <div className="pt-2 border-t border-stone-100 flex items-center justify-between text-xs text-stone-400">
            <span>{trend.product_count.toLocaleString()} products</span>
            <span>{trend.retailer_names.length} retailers</span>
            {trend.avg_price != null && <span>${trend.avg_price.toFixed(0)} avg</span>}
          </div>

          {/* View all N products — new-shape trends only. Legacy trends
              hide the button (their filter_keywords are empty, so the
              live query has no meaningful total to show). */}
          <FragranceTrendViewAllButton trend={trend} />
        </div>
      </article>
    </Link>
  );
}

interface Props {
  searchParams: { generation?: string; category?: string; value?: string };
}

export default async function FragranceTrendsPage({ searchParams }: Props) {
  const requestedGen = searchParams.generation ? parseInt(searchParams.generation) : undefined;

  const [weeks] = await Promise.all([
    api.fragranceTrends.weeks().catch(() => [] as { week: string; generation_count: number; generations?: number[] }[]),
  ]);

  // Fragrance is now run-based. `weeks[0]` is the LATEST run; its
  // `generations` array is the actual list of set numbers in that run
  // (e.g. [1, 3, 4] after Set 2 was deleted). Fall back to [1..count]
  // for old server responses that don't ship the array yet.
  const latestRun = weeks[0];
  const generationList: number[] = latestRun?.generations
    ?? (latestRun ? Array.from({ length: latestRun.generation_count }, (_, i) => i + 1) : []);
  const latestGeneration = generationList.length ? generationList[generationList.length - 1] : 1;

  // Clamp the requested generation to what actually exists. Otherwise a
  // stale ?generation=2 URL from a prior session hides all the trends
  // after a fresh run only creates Set 1 (`/latest?generation=2` still
  // returns the report row but its trends filter to an empty set).
  const effectiveGen = requestedGen && generationList.includes(requestedGen)
    ? requestedGen
    : latestGeneration;

  let report = null;
  try {
    report = await api.fragranceTrends.latestReport(effectiveGen);
  } catch {
    // No report yet
  }

  // Render the latest run's timestamp. The `week` field is misnamed but
  // holds an ISO datetime string; parse and format for display.
  const latestRunAt = latestRun ? new Date(latestRun.week) : null;
  const latestRunLabel = latestRunAt
    ? latestRunAt.toLocaleString(undefined, {
        day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit",
      })
    : null;

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-stone-900">Fragrance Trends</h1>
          <p className="text-stone-500 text-sm mt-1">
            {latestRunLabel
              ? <>Latest analysis · <span className="text-stone-700 font-medium">{latestRunLabel}</span> · {report?.retailers_covered ?? "—"} retailers</>
              : <>Candle and home fragrance trend analysis. Run an analysis after scraping to get started.</>}
          </p>
        </div>
        <FragranceActionButton initialHasAnalysis={weeks.length > 0} />
      </div>

      {/* Generation tabs — always visible once a run exists so the
          buyer can see "Set 1 ✨" from the very first run rather than
          waiting for a Try Again. The × button on a single-set tab is
          blocked at click time with a friendly alert. */}
      {report && generationList.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          <FragranceGenerationTabs
            runId={report.id}
            generationList={generationList}
            activeGen={effectiveGen}
            latestGeneration={latestGeneration}
          />
          <ClearSetsButton target="fragrance" />
        </div>
      )}


      {/* Report summary */}
      {report && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div>
              <p className="text-sm font-medium text-amber-900">{report.title}</p>
              <p className="text-xs text-amber-700 mt-0.5">{report.summary}</p>
            </div>
            <div className="flex gap-4 text-center">
              {[
                { label: "Products", value: report.total_products_analysed.toLocaleString() },
                { label: "Retailers", value: report.retailers_covered },
                { label: "Trends", value: report.trend_count },
              ].map(({ label, value }) => (
                <div key={label}>
                  <p className="text-lg font-bold text-amber-900">{value}</p>
                  <p className="text-xs text-amber-600">{label}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* No report yet */}
      {!report && (
        <div className="bg-stone-50 border border-stone-200 rounded-xl p-12 text-center">
          <p className="text-4xl mb-3">🕯</p>
          <p className="text-stone-600 font-medium">No fragrance analysis yet</p>
          <p className="text-stone-400 text-sm mt-1">Click "Run Analysis" to analyse candle and fragrance products</p>
        </div>
      )}

      {/* Trend grid — filtered by URL params (?category / ?value).
          Server-side filter, client component owns the dropdowns. */}
      {report && report.trends.length > 0 && (() => {
        const catFilter = searchParams.category || "";
        const valFilter = searchParams.value || "";
        // Value options for the currently selected category — full set of
        // trend names within that category, in whichever order they came.
        const valuesInCategory = catFilter
          ? Array.from(new Set(
              report.trends
                .filter((t) => t.category === catFilter)
                .map((t) => t.name),
            ))
          : [];
        const filtered = report.trends.filter((t) => {
          if (catFilter && t.category !== catFilter) return false;
          if (valFilter && t.name !== valFilter) return false;
          return true;
        });
        return (
          <>
            <FragranceTrendFilters valuesInCategory={valuesInCategory} />
            <p className="text-sm text-stone-500">
              {filtered.length} trend{filtered.length !== 1 ? "s" : ""} found
              {generationList.length > 1 && ` · Set ${effectiveGen} of ${generationList.length}`}
            </p>
            {filtered.length === 0 ? (
              <p className="text-center py-16 text-stone-400">No trends match your filters.</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
                {filtered.map((trend) => (
                  <FragranceTrendCard key={trend.id} trend={trend} />
                ))}
              </div>
            )}
          </>
        );
      })()}
    </div>
  );
}
