"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams, usePathname, useRouter } from "next/navigation";
import { api, type FragranceTrendReport, type FragranceTrend } from "@/lib/api";
import FragranceTrendCard from "@/components/FragranceTrendCard";
import FragranceActionButton from "@/components/FragranceActionButton";
import ClearSetsButton from "@/components/ClearSetsButton";
import CompareFragranceTiersView from "@/components/CompareFragranceTiersView";
import Link from "next/link";
import clsx from "clsx";

// A run's set summary. `generations` is the actual list of set numbers
// present (e.g. [1, 3, 4] after Set 2 is deleted).
type WeekInfo = { week: string; generation_count: number; generations?: number[] };

// Fragrance categories emitted by the engine (parallel to Product Trends
// but with a fragrance-specific taxonomy).
const CATEGORIES = ["aesthetic", "scent", "market", "sustainability", "retail"];

// Market segmentation — matches Product Trends. Segmented views ONLY.
type Segment = "luxury" | "middle" | "mass";
type Tab = Segment | "compare";
const SEGMENTS: { value: Segment; label: string; accent: string; badge: string }[] = [
  { value: "luxury", label: "Luxury",
    accent: "border-amber-500 bg-amber-500 text-white",
    badge: "bg-amber-100 text-amber-800 border-amber-200" },
  { value: "middle", label: "Middle",
    accent: "border-stone-900 bg-stone-900 text-white",
    badge: "bg-stone-100 text-stone-800 border-stone-200" },
  { value: "mass", label: "Mass",
    accent: "border-sky-600 bg-sky-600 text-white",
    badge: "bg-sky-100 text-sky-800 border-sky-200" },
];
const DEFAULT_SEGMENT: Segment = "middle";

function isSegment(v: string | null): v is Segment {
  return v === "luxury" || v === "middle" || v === "mass";
}

export default function FragranceTrendsClient() {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();

  const [report, setReport] = useState<FragranceTrendReport | null>(null);
  const [weeks, setWeeks] = useState<WeekInfo[]>([]);
  const [loading, setLoading] = useState(true);

  const [category, setCategory] = useState("");
  const [value, setValue] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);

  const rawSegment = searchParams.get("segment");
  const compareParam = searchParams.get("view") === "compare";
  const activeTab: Tab = compareParam
    ? "compare"
    : (isSegment(rawSegment) ? rawSegment : DEFAULT_SEGMENT);
  const activeSegment: Segment = activeTab === "compare" ? DEFAULT_SEGMENT : activeTab;
  const generationParam = searchParams.get("generation");

  // First visit → pin a segment in the URL so refreshes stick.
  useEffect(() => {
    if (!rawSegment && !compareParam) {
      const p = new URLSearchParams(searchParams.toString());
      p.set("segment", DEFAULT_SEGMENT);
      router.replace(`${pathname}?${p.toString()}`, { scroll: false });
    }
  }, [rawSegment, compareParam, searchParams, pathname, router]);

  // Fetch report + runs for the active segment. Fragrance backend keys
  // "week" as the run's ISO timestamp — same shape as trends, different
  // semantics.
  useEffect(() => {
    if (activeTab === "compare") { setLoading(false); return; }
    let cancelled = false;
    setLoading(true);
    const gen = generationParam ? parseInt(generationParam) : undefined;
    Promise.all([
      api.fragranceTrends.latestReport(gen, activeSegment).catch(() => null),
      api.fragranceTrends.weeks(activeSegment).catch(() => [] as WeekInfo[]),
    ]).then(([r, w]) => {
      if (!cancelled) {
        setReport(r);
        setWeeks(w);
        setLoading(false);
      }
    });
    return () => { cancelled = true; };
  }, [activeSegment, activeTab, generationParam, refreshTick]);

  useEffect(() => {
    const handler = () => setRefreshTick((n) => n + 1);
    window.addEventListener("fragrance-refresh", handler);
    return () => window.removeEventListener("fragrance-refresh", handler);
  }, []);

  const trends: FragranceTrend[] = report?.trends ?? [];

  const valuesInCategory = useMemo(() => {
    if (!category) return [];
    const names = new Set<string>();
    for (const t of trends) if (t.category === category) names.add(t.name);
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [trends, category]);

  useEffect(() => { setValue(""); }, [category]);

  const filtered = useMemo(() => trends.filter((t) => {
    if (category && t.category !== category) return false;
    if (value && t.name !== value) return false;
    return true;
  }), [trends, category, value]);

  const latestRun = weeks[0];
  const generationList: number[] = latestRun?.generations
    ?? (latestRun ? Array.from({ length: latestRun.generation_count }, (_, i) => i + 1) : []);
  const latestGeneration = generationList.length ? generationList[generationList.length - 1] : 1;
  const activeGen = generationParam ? parseInt(generationParam) : latestGeneration;
  const hasMultipleGenerations = generationList.length > 1;

  const latestRunAt = latestRun ? new Date(latestRun.week) : null;
  const latestRunLabel = latestRunAt
    ? latestRunAt.toLocaleString(undefined, {
        day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit",
      })
    : null;

  function switchTab(tab: Tab) {
    const p = new URLSearchParams();
    if (tab === "compare") { p.set("view", "compare"); }
    else { p.set("segment", tab); }
    // Generation is per-segment — clear when switching tiers.
    router.push(`${pathname}?${p.toString()}`);
  }

  function genTabHref(gen: number) {
    const p = new URLSearchParams(searchParams.toString());
    p.set("generation", String(gen));
    return `${pathname}?${p.toString()}`;
  }

  async function handleDeleteGeneration(gen: number) {
    if (!report) return;
    if (generationList.length <= 1) {
      alert("Can't delete the only remaining set — run a new analysis first, or clear all sets.");
      return;
    }
    if (!confirm(`Delete Set ${gen}? This removes every fragrance trend in that set. Can't be undone.`)) return;
    try {
      const result = await api.fragranceTrends.deleteGeneration(report.id, gen, activeSegment);
      const nextGen = activeGen === gen
        ? (result.remaining_generations[result.remaining_generations.length - 1] ?? latestGeneration)
        : activeGen;
      if (nextGen !== activeGen) {
        const p = new URLSearchParams(searchParams.toString());
        p.set("generation", String(nextGen));
        router.push(`${pathname}?${p.toString()}`);
      } else {
        setRefreshTick((n) => n + 1);
      }
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Delete failed");
    }
  }

  const activeSegmentMeta = SEGMENTS.find((s) => s.value === activeSegment)!;

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-stone-900">Fragrance Trends</h1>
          <p className="text-stone-500 text-sm mt-1">
            {latestRunLabel
              ? <>Latest analysis · <span className="text-stone-700 font-medium">{latestRunLabel}</span> · {report?.retailers_covered ?? "—"} retailers</>
              : <>Candle and home fragrance trend analysis. Run an analysis after scraping to get started.</>}
          </p>
        </div>
        <FragranceActionButton initialHasAnalysis={weeks.length > 0} segment={activeSegment} />
      </div>

      {/* Segment pill switcher — Luxury / Middle / Mass / Compare tiers */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-stone-400 font-medium uppercase tracking-wider">Tier:</span>
        {SEGMENTS.map((s) => (
          <button
            key={s.value}
            onClick={() => switchTab(s.value)}
            className={clsx(
              "px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors",
              activeTab === s.value
                ? s.accent
                : "border-stone-200 bg-white text-stone-600 hover:border-stone-400"
            )}
          >
            {s.label}
          </button>
        ))}
        <button
          onClick={() => switchTab("compare")}
          title="Cross-tier fragrance diffusion view"
          className={clsx(
            "px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors",
            activeTab === "compare"
              ? "border-purple-600 bg-purple-600 text-white"
              : "border-purple-200 bg-purple-50 text-purple-700 hover:bg-purple-100"
          )}
        >
          Compare tiers ✨
        </button>
      </div>

      {activeTab === "compare" ? (
        <CompareFragranceTiersView />
      ) : (
        <>
          {/* Filters */}
          <div className="flex flex-wrap gap-3 items-end">
            <div>
              <label className="block text-xs font-medium text-stone-500 mb-1">Category</label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="border border-stone-300 rounded-lg px-3 py-1.5 text-sm bg-white"
              >
                <option value="">All categories</option>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
                ))}
              </select>
            </div>
            {category && valuesInCategory.length > 0 && (
              <div>
                <label className="block text-xs font-medium text-stone-500 mb-1">
                  {category.charAt(0).toUpperCase() + category.slice(1)}
                </label>
                <select
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  className="border border-stone-300 rounded-lg px-3 py-1.5 text-sm bg-white"
                >
                  <option value="">All {category}s</option>
                  {valuesInCategory.map((v) => (
                    <option key={v} value={v}>{v}</option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {/* Set tabs */}
          {hasMultipleGenerations && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-stone-400 font-medium">Set:</span>
              {generationList.map((gen) => {
                const active = activeGen === gen;
                return (
                  <div
                    key={gen}
                    className={clsx(
                      "inline-flex items-stretch rounded-lg overflow-hidden border transition-colors",
                      active
                        ? "border-stone-900 bg-stone-900 text-white"
                        : "border-stone-200 bg-white text-stone-600 hover:border-stone-400"
                    )}
                  >
                    <Link href={genTabHref(gen)} className="px-3 py-1 text-xs font-medium">
                      {gen === latestGeneration ? `Set ${gen} ✨` : `Set ${gen}`}
                    </Link>
                    <button
                      onClick={() => handleDeleteGeneration(gen)}
                      title={`Delete Set ${gen}`}
                      aria-label={`Delete Set ${gen}`}
                      className={clsx(
                        "px-1.5 text-xs border-l transition-colors",
                        active ? "border-stone-700 hover:bg-stone-800"
                               : "border-stone-200 text-stone-400 hover:bg-red-50 hover:text-red-600",
                      )}
                    >×</button>
                  </div>
                );
              })}
              <ClearSetsButton target="fragrance" />
            </div>
          )}
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

          {/* Count line */}
          <p className="text-sm text-stone-500 flex items-center gap-2">
            <span className={clsx(
              "px-1.5 py-0.5 rounded-full text-[10px] font-semibold border",
              activeSegmentMeta.badge,
            )}>{activeSegmentMeta.label}</span>
            {loading ? "Loading…" : `${filtered.length} trend${filtered.length !== 1 ? "s" : ""} found`}
            {hasMultipleGenerations && ` · Set ${activeGen} of ${generationList.length}`}
          </p>

          {loading ? null : filtered.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
              {filtered.map((t) => <FragranceTrendCard key={t.id} trend={t} />)}
            </div>
          ) : weeks.length === 0 ? (
            <div className="text-center py-20 space-y-3">
              <p className="text-4xl">🕯</p>
              <p className="text-stone-600 font-medium">No {activeSegmentMeta.label} fragrance trends yet</p>
              <p className="text-stone-400 text-sm">
                Run a fragrance analysis to populate this tier. Retailers with a{" "}
                <span className="font-medium">{activeSegmentMeta.label}</span> classification
                will contribute their fragrance products.
              </p>
            </div>
          ) : (
            <div className="text-center py-20 text-stone-400">
              <p>No trends match your filters.</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
