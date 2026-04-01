"use client";
import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { api, type Report, type TrendSummary } from "@/lib/api";
import clsx from "clsx";

const STATUS_STYLES: Record<string, string> = {
  rising:   "bg-emerald-100 text-emerald-700",
  new:      "bg-blue-100 text-blue-700",
  plateau:  "bg-amber-100 text-amber-700",
  declining:"bg-rose-100 text-rose-700",
};
const STATUS_ICONS: Record<string, string> = {
  rising: "↑", new: "★", plateau: "→", declining: "↓",
};

function formatWeek(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

function TrendPill({ trend }: { trend: TrendSummary }) {
  return (
    <Link
      href={`/trends/${trend.id}`}
      className={clsx(
        "inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium transition-opacity hover:opacity-70",
        STATUS_STYLES[trend.status] ?? "bg-stone-100 text-stone-600"
      )}
    >
      <span>{STATUS_ICONS[trend.status] ?? ""}</span>
      <span className="truncate max-w-[160px]">{trend.name}</span>
    </Link>
  );
}

function StatBadge({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="text-center">
      <p className="text-2xl font-bold text-stone-900">{value}</p>
      <p className="text-xs text-stone-500 mt-0.5">{label}</p>
    </div>
  );
}

function ReportCard({ report }: { report: Report }) {
  const risingCount  = report.rising_trends.length;
  const newCount     = report.new_trends.length;
  const decliningCount = report.declining_trends.length;

  // Prioritise: rising → new → declining → rest
  const highlightTrends = [
    ...report.rising_trends.slice(0, 2),
    ...report.new_trends.slice(0, 3),
    ...report.declining_trends.slice(0, 1),
    ...report.all_trends.filter(
      (t) => !report.rising_trends.find((r) => r.id === t.id) &&
             !report.new_trends.find((n) => n.id === t.id) &&
             !report.declining_trends.find((d) => d.id === t.id)
    ).slice(0, 2),
  ].slice(0, 7);

  return (
    <article className="bg-white rounded-2xl border border-stone-200 overflow-hidden hover:shadow-lg transition-shadow">
      {/* Coloured header stripe */}
      <div className="h-1.5 bg-gradient-to-r from-stone-800 via-stone-600 to-stone-400" />

      <div className="p-6 space-y-5">
        {/* Week + title */}
        <div>
          <p className="text-xs font-medium text-stone-400 uppercase tracking-wider mb-1">
            Week of {formatWeek(report.week_start)}
          </p>
          <h2 className="text-lg font-bold text-stone-900 leading-snug">{report.title}</h2>
          <p className="text-sm text-stone-600 mt-2 line-clamp-2">{report.summary}</p>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-4 gap-3 py-4 border-y border-stone-100">
          <StatBadge value={report.trend_count} label="Trends" />
          <StatBadge value={report.total_products_analysed.toLocaleString()} label="Products" />
          <StatBadge value={report.retailers_covered} label="Retailers" />
          <div className="text-center">
            <p className="text-2xl font-bold text-emerald-600">
              {risingCount > 0 ? `+${risingCount}` : newCount > 0 ? `${newCount}` : "–"}
            </p>
            <p className="text-xs text-stone-500 mt-0.5">
              {risingCount > 0 ? "Rising" : newCount > 0 ? "New" : "Stable"}
            </p>
          </div>
        </div>

        {/* Trend pills */}
        {highlightTrends.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {highlightTrends.map((t) => (
              <TrendPill key={t.id} trend={t} />
            ))}
          </div>
        )}

        {/* Status breakdown */}
        <div className="flex gap-3 text-xs text-stone-500">
          {risingCount > 0 && (
            <span className="text-emerald-600 font-medium">↑ {risingCount} rising</span>
          )}
          {newCount > 0 && (
            <span className="text-blue-600 font-medium">★ {newCount} new</span>
          )}
          {decliningCount > 0 && (
            <span className="text-rose-600 font-medium">↓ {decliningCount} declining</span>
          )}
        </div>

        {/* Footer */}
        <div className="pt-1 flex items-center justify-between">
          <Link
            href={`/trends?week_start=${report.week_start.split("T")[0]}`}
            className="text-sm font-medium text-stone-900 hover:text-stone-600 transition-colors"
          >
            View all trends →
          </Link>
          <span className="text-xs text-stone-400">
            Report #{report.id}
          </span>
        </div>
      </div>
    </article>
  );
}

type GenerateState = "idle" | "loading" | "done" | "error";

export default function ReportsPage() {
  const [reports, setReports]     = useState<Report[]>([]);
  const [loading, setLoading]     = useState(true);
  const [genState, setGenState]   = useState<GenerateState>("idle");
  const [genMsg, setGenMsg]       = useState("");

  const fetchReports = useCallback(async () => {
    try {
      const data = await api.reports.list();
      setReports(data.sort((a, b) => b.id - a.id));
    } catch {
      setReports([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchReports(); }, [fetchReports]);

  async function handleGenerate() {
    setGenState("loading");
    setGenMsg("");
    try {
      const res = await api.reports.generate();
      setGenMsg(`Queued — analysis takes ~60 seconds. This page will refresh automatically.`);
      setGenState("done");
      // Auto-refresh after ~90s once Claude has had time to run
      setTimeout(async () => {
        await fetchReports();
        setGenState("idle");
        setGenMsg("");
      }, 90_000);
    } catch {
      setGenMsg("Failed to queue report. Try again.");
      setGenState("error");
    }
  }

  const latestReport = reports[0];

  return (
    <div className="space-y-8">

      {/* Page header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-stone-900">Trend Reports</h1>
          <p className="text-stone-500 mt-1">Weekly AI-generated analysis of home décor &amp; storage trends</p>
        </div>

        <div className="flex flex-col items-end gap-2 flex-shrink-0">
          <button
            onClick={handleGenerate}
            disabled={genState === "loading"}
            className={clsx(
              "px-4 py-2 rounded-lg text-sm font-medium transition-colors",
              genState === "loading"
                ? "bg-stone-300 text-stone-500 cursor-not-allowed"
                : genState === "done"
                ? "bg-emerald-600 text-white hover:bg-emerald-700"
                : genState === "error"
                ? "bg-rose-600 text-white hover:bg-rose-700"
                : "bg-stone-900 text-white hover:bg-stone-700"
            )}
          >
            {genState === "loading" ? (
              <span className="flex items-center gap-2">
                <span className="w-3.5 h-3.5 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                Queuing…
              </span>
            ) : genState === "done" ? "✓ Queued" : genState === "error" ? "✗ Failed" : "Run Analysis"}
          </button>
          {genMsg && (
            <p className={clsx(
              "text-xs max-w-xs text-right",
              genState === "error" ? "text-rose-600" : "text-stone-500"
            )}>
              {genMsg}
            </p>
          )}
        </div>
      </div>

      {/* Latest report hero — big callout if data exists */}
      {latestReport && (
        <section className="bg-stone-900 text-white rounded-2xl p-8 space-y-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-stone-400 text-sm font-medium uppercase tracking-wider mb-2">
                Latest · Week of {formatWeek(latestReport.week_start)}
              </p>
              <h2 className="text-2xl font-bold leading-snug">{latestReport.title}</h2>
              <p className="text-stone-300 mt-3 max-w-2xl leading-relaxed">{latestReport.summary}</p>
            </div>
          </div>

          {/* Big stats */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-2">
            {[
              { value: latestReport.trend_count,                               label: "Trends identified" },
              { value: latestReport.total_products_analysed.toLocaleString(),  label: "Products analysed" },
              { value: latestReport.retailers_covered,                         label: "Retailers covered" },
              { value: latestReport.rising_trends.length || latestReport.new_trends.length, label: latestReport.rising_trends.length ? "Rising trends" : "New trends" },
            ].map(({ value, label }) => (
              <div key={label} className="bg-white/10 rounded-xl p-4 text-center">
                <p className="text-3xl font-bold">{value}</p>
                <p className="text-stone-400 text-xs mt-1">{label}</p>
              </div>
            ))}
          </div>

          {/* Trend pills on dark */}
          {latestReport.all_trends.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-1">
              {latestReport.all_trends.slice(0, 8).map((t) => (
                <Link
                  key={t.id}
                  href={`/trends/${t.id}`}
                  className="px-3 py-1 bg-white/10 hover:bg-white/20 rounded-full text-sm text-white transition-colors"
                >
                  {STATUS_ICONS[t.status] ?? ""} {t.name}
                </Link>
              ))}
              {latestReport.all_trends.length > 8 && (
                <Link
                  href={`/trends?week_start=${latestReport.week_start.split("T")[0]}`}
                  className="px-3 py-1 bg-white/10 hover:bg-white/20 rounded-full text-sm text-stone-300 transition-colors"
                >
                  +{latestReport.all_trends.length - 8} more →
                </Link>
              )}
            </div>
          )}

          <div className="pt-2">
            <Link
              href={`/trends?week_start=${latestReport.week_start.split("T")[0]}`}
              className="inline-flex items-center gap-2 bg-white text-stone-900 px-5 py-2.5 rounded-lg text-sm font-semibold hover:bg-stone-100 transition-colors"
            >
              Browse this week&apos;s trends →
            </Link>
          </div>
        </section>
      )}

      {/* Archive */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="bg-white rounded-2xl border border-stone-200 h-72 animate-pulse" />
          ))}
        </div>
      ) : reports.length === 0 ? (
        <div className="text-center py-20 text-stone-400">
          <p className="text-5xl mb-4">📊</p>
          <p className="text-lg font-medium text-stone-600">No reports yet</p>
          <p className="text-sm mt-2">
            Click <strong>Run Analysis</strong> above once products have been scraped and analysed.
          </p>
        </div>
      ) : (
        <>
          {reports.length > 1 && (
            <div>
              <h3 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-4">
                Archive ({reports.length} report{reports.length !== 1 ? "s" : ""})
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
                {reports.slice(1).map((r) => (
                  <ReportCard key={r.id} report={r} />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
