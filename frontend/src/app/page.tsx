import { api, type Report, type Trend } from "@/lib/api";
import Link from "next/link";
import TrendCard from "@/components/TrendCard";

async function getData(): Promise<{ report: Report | null; trends: Trend[] }> {
  try {
    const [report, trends] = await Promise.all([
      api.reports.latest().catch(() => null),
      api.trends.latest().catch(() => []),
    ]);
    return { report, trends };
  } catch {
    return { report: null, trends: [] };
  }
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-white rounded-xl border border-stone-200 p-5">
      <p className="text-xs font-medium text-stone-400 uppercase tracking-wider">{label}</p>
      <p className="text-3xl font-bold text-stone-900 mt-1">{value}</p>
      {sub && <p className="text-sm text-stone-500 mt-0.5">{sub}</p>}
    </div>
  );
}

export default async function Dashboard() {
  const { report, trends } = await getData();

  const risingTrends  = trends.filter((t) => t.status === "rising");
  const newTrends     = trends.filter((t) => t.status === "new");
  const hasReport     = !!report;

  return (
    <div className="space-y-8">

      {/* Hero banner — dark if we have a report */}
      {hasReport ? (
        <div className="bg-stone-900 text-white rounded-2xl px-8 py-7 flex items-start justify-between gap-6">
          <div className="min-w-0">
            <p className="text-stone-400 text-xs font-medium uppercase tracking-wider mb-1">
              Latest report
            </p>
            <h1 className="text-2xl font-bold leading-snug">{report!.title}</h1>
            <p className="text-stone-300 mt-2 text-sm max-w-2xl leading-relaxed line-clamp-2">
              {report!.summary}
            </p>
          </div>
          <Link
            href="/reports"
            className="flex-shrink-0 bg-white text-stone-900 px-4 py-2 rounded-lg text-sm font-semibold hover:bg-stone-100 transition-colors"
          >
            Full report →
          </Link>
        </div>
      ) : (
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-stone-900">Home Décor &amp; Storage Trend Tracker</h1>
            <p className="text-stone-500 mt-1">Scrape retailers, analyse products, discover trends.</p>
          </div>
          <div className="flex gap-3">
            <Link
              href="/retailers"
              className="bg-stone-900 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-stone-700 transition-colors"
            >
              Start first scrape →
            </Link>
          </div>
        </div>
      )}

      {/* Stats row */}
      {hasReport && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard label="Trends identified" value={report!.trend_count} />
          <StatCard label="Products analysed" value={report!.total_products_analysed.toLocaleString()} />
          <StatCard label="Retailers covered" value={report!.retailers_covered} />
          <StatCard
            label="Rising trends"
            value={report!.rising_trends.length || report!.new_trends.length}
            sub={
              report!.rising_trends.length
                ? report!.rising_trends.slice(0, 2).map((t) => t.name).join(", ")
                : report!.new_trends.slice(0, 2).map((t) => t.name).join(", ")
            }
          />
        </div>
      )}

      {/* Rising trends */}
      {risingTrends.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-stone-900">↑ Rising This Week</h2>
            <Link href="/trends?status=rising" className="text-sm text-stone-500 hover:text-stone-900">
              View all →
            </Link>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {risingTrends.slice(0, 3).map((t) => (
              <TrendCard key={t.id} trend={t} />
            ))}
          </div>
        </section>
      )}

      {/* New this week */}
      {newTrends.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-stone-900">★ New This Week</h2>
            <Link href="/trends?status=new" className="text-sm text-stone-500 hover:text-stone-900">
              View all →
            </Link>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {newTrends.slice(0, 6).map((t) => (
              <TrendCard key={t.id} trend={t} />
            ))}
          </div>
        </section>
      )}

      {/* All trends — shown when no rising/new segments */}
      {trends.length > 0 && risingTrends.length === 0 && newTrends.length === 0 && (
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-stone-900">All Trends</h2>
            <Link href="/trends" className="text-sm text-stone-500 hover:text-stone-900">
              View all →
            </Link>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {trends.slice(0, 9).map((t) => (
              <TrendCard key={t.id} trend={t} />
            ))}
          </div>
        </section>
      )}

      {/* Show a "See all trends" link when we have both rising + new sections */}
      {trends.length > 0 && (risingTrends.length > 0 || newTrends.length > 0) && (
        <div className="text-center pt-2">
          <Link
            href="/trends"
            className="inline-flex items-center gap-2 text-stone-600 hover:text-stone-900 text-sm font-medium transition-colors"
          >
            Browse all {trends.length} trends →
          </Link>
        </div>
      )}

      {/* Empty state */}
      {trends.length === 0 && (
        <div className="text-center py-20 text-stone-400">
          <p className="text-5xl mb-4">⌂</p>
          <p className="text-lg font-medium text-stone-600">No trend data yet</p>
          <p className="mt-2 text-sm">
            Go to{" "}
            <Link href="/retailers" className="text-stone-700 underline">Retailers</Link>
            {" "}to trigger a scrape, then{" "}
            <Link href="/reports" className="text-stone-700 underline">Reports</Link>
            {" "}to run the analysis.
          </p>
        </div>
      )}
    </div>
  );
}
