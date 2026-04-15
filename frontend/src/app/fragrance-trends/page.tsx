import { api, type FragranceTrend, type FragranceTrendExample } from "@/lib/api";
import Link from "next/link";
import clsx from "clsx";
import FragranceActionButton from "@/components/FragranceActionButton";
import ClearSetsButton from "@/components/ClearSetsButton";

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
  return (
    <Link href={`/fragrance-trends/${trend.id}`} className="group block">
      <article className="bg-white rounded-xl border border-stone-200 overflow-hidden hover:shadow-lg transition-shadow">
        <div className="relative">
          <ImageMosaic examples={trend.examples ?? []} />
          <span className={clsx("absolute top-2 right-2 px-2 py-0.5 rounded-full text-xs font-semibold shadow-sm", STATUS_STYLES[trend.status])}>
            {STATUS_ICONS[trend.status]}{" "}
            {trend.status.charAt(0).toUpperCase() + trend.status.slice(1)}
            {trend.momentum_pct != null && ` ${trend.momentum_pct > 0 ? "+" : ""}${trend.momentum_pct}%`}
          </span>
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
        </div>
      </article>
    </Link>
  );
}

interface Props {
  searchParams: { generation?: string };
}

export default async function FragranceTrendsPage({ searchParams }: Props) {
  const activeGen = searchParams.generation ? parseInt(searchParams.generation) : undefined;

  const [weeks] = await Promise.all([
    api.fragranceTrends.weeks().catch(() => [] as { week: string; generation_count: number }[]),
  ]);

  const generationCount = weeks[0]?.generation_count ?? 1;
  const effectiveGen = activeGen ?? generationCount;

  let report = null;
  try {
    report = await api.fragranceTrends.latestReport(effectiveGen);
  } catch {
    // No report yet
  }

  function genTabHref(gen: number) {
    return `?generation=${gen}`;
  }

  const hasMultipleGenerations = generationCount > 1;

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-stone-900">Fragrance Trends</h1>
          <p className="text-stone-500 text-sm mt-1">
            Candle and home fragrance trend analysis across {report?.retailers_covered ?? "—"} retailers
          </p>
        </div>
        <FragranceActionButton initialHasAnalysis={weeks.length > 0} />
      </div>

      {/* Generation tabs */}
      {hasMultipleGenerations && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-stone-400 font-medium">Set:</span>
          {Array.from({ length: generationCount }, (_, i) => i + 1).map((gen) => (
            <Link
              key={gen}
              href={genTabHref(gen)}
              className={clsx(
                "px-3 py-1 rounded-lg text-xs font-medium transition-colors border",
                effectiveGen === gen
                  ? "bg-stone-900 border-stone-900 text-white"
                  : "bg-white border-stone-200 text-stone-600 hover:border-stone-400"
              )}
            >
              {gen === generationCount ? `Set ${gen} ✨` : `Set ${gen}`}
            </Link>
          ))}
          <ClearSetsButton target="fragrance" />
        </div>
      )}

      {/* Clear button when only one set exists */}
      {weeks.length > 0 && !hasMultipleGenerations && (
        <div className="flex justify-end">
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

      {/* Trend grid */}
      {report && report.trends.length > 0 && (
        <>
          <p className="text-sm text-stone-500">
            {report.trend_count} trend{report.trend_count !== 1 ? "s" : ""} found
            {hasMultipleGenerations && ` · Set ${effectiveGen} of ${generationCount}`}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {report.trends.map((trend) => (
              <FragranceTrendCard key={trend.id} trend={trend} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
