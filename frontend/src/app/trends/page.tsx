import { api, type Trend } from "@/lib/api";
import TrendCard from "@/components/TrendCard";

interface Props {
  searchParams: { week_start?: string; category?: string; status?: string };
}

const CATEGORIES = ["colour", "material", "pattern", "style", "shape", "seasonal", "functional"];
const STATUSES = ["rising", "new", "plateau", "declining"];

async function getData(params: Props["searchParams"]): Promise<{ trends: Trend[]; weeks: string[] }> {
  const [trends, weeks] = await Promise.all([
    api.trends.list(params).catch(() => []),
    api.trends.weeks().catch(() => []),
  ]);
  return { trends, weeks };
}

export default async function TrendsPage({ searchParams }: Props) {
  const { trends, weeks } = await getData(searchParams);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-stone-900">Trends</h1>

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
              <option key={w} value={w}>{w}</option>
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

      {/* Trend count */}
      <p className="text-sm text-stone-500">
        {trends.length} trend{trends.length !== 1 ? "s" : ""} found
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
