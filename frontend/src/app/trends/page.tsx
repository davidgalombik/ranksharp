import { api, type Trend } from "@/lib/api";
import TrendCard from "@/components/TrendCard";
import TrendsActionButton from "@/components/TrendsActionButton";
import ClearSetsButton from "@/components/ClearSetsButton";
import Link from "next/link";
import clsx from "clsx";

interface Props {
  searchParams: { week_start?: string; category?: string; status?: string; generation?: string };
}

const CATEGORIES = ["colour", "material", "pattern", "style", "shape", "seasonal", "functional"];
const STATUSES = ["rising", "new", "plateau", "declining"];

async function getData(params: Props["searchParams"]): Promise<{
  trends: Trend[];
  weeks: { week: string; generation_count: number }[];
}> {
  const [trends, weeks] = await Promise.all([
    api.trends.list(params).catch(() => []),
    api.trends.weeks().catch(() => []),
  ]);
  return { trends, weeks };
}

export default async function TrendsPage({ searchParams }: Props) {
  const { trends, weeks } = await getData(searchParams);

  // Determine active week and its generation count
  const activeWeek = searchParams.week_start || weeks[0]?.week || null;
  const weekInfo = weeks.find((w) => w.week === activeWeek);
  const generationCount = weekInfo?.generation_count ?? 1;
  const activeGen = searchParams.generation ? parseInt(searchParams.generation) : generationCount;

  // Build generation tab URLs (preserve other filters, only swap generation)
  function genTabHref(gen: number) {
    const p = new URLSearchParams();
    if (searchParams.week_start) p.set("week_start", searchParams.week_start);
    if (searchParams.category) p.set("category", searchParams.category);
    if (searchParams.status) p.set("status", searchParams.status);
    p.set("generation", String(gen));
    return `?${p.toString()}`;
  }

  const hasMultipleGenerations = generationCount > 1;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-stone-900">Trends</h1>
        <TrendsActionButton initialHasAnalysis={weeks.length > 0} />
      </div>

      {/* Filters */}
      <form className="flex flex-wrap gap-3 items-end">
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1">Week</label>
          <select
            name="week_start"
            defaultValue={searchParams.week_start || ""}
            className="border border-stone-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          >
            <option value="">All weeks</option>
            {weeks.map((w) => (
              <option key={w.week} value={w.week}>{w.week}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1">Category</label>
          <select
            name="category"
            defaultValue={searchParams.category || ""}
            className="border border-stone-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          >
            <option value="">All categories</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1">Status</label>
          <select
            name="status"
            defaultValue={searchParams.status || ""}
            className="border border-stone-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          >
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
            ))}
          </select>
        </div>

        <button
          type="submit"
          className="bg-stone-900 text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-stone-700 transition-colors"
        >
          Filter
        </button>
      </form>

      {/* Generation tabs — shown when multiple sets exist for this week */}
      {hasMultipleGenerations && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-stone-400 font-medium">Set:</span>
          {Array.from({ length: generationCount }, (_, i) => i + 1).map((gen) => (
            <Link
              key={gen}
              href={genTabHref(gen)}
              className={clsx(
                "px-3 py-1 rounded-lg text-xs font-medium transition-colors border",
                activeGen === gen
                  ? "bg-stone-900 border-stone-900 text-white"
                  : "bg-white border-stone-200 text-stone-600 hover:border-stone-400"
              )}
            >
              {gen === generationCount ? `Set ${gen} ✨` : `Set ${gen}`}
            </Link>
          ))}
          <ClearSetsButton target="trends" />
        </div>
      )}

      {/* Clear button when only one set exists */}
      {weeks.length > 0 && !hasMultipleGenerations && (
        <div className="flex justify-end">
          <ClearSetsButton target="trends" />
        </div>
      )}

      {/* Trend count */}
      <p className="text-sm text-stone-500">
        {trends.length} trend{trends.length !== 1 ? "s" : ""} found
        {hasMultipleGenerations && ` · Set ${activeGen} of ${generationCount}`}
      </p>

      {/* Grid */}
      {trends.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {trends.map((t) => (
            <TrendCard key={t.id} trend={t} />
          ))}
        </div>
      ) : (
        <div className="text-center py-20 text-stone-400">
          <p>No trends match your filters.</p>
        </div>
      )}
    </div>
  );
}
