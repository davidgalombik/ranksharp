"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams, usePathname, useRouter } from "next/navigation";
import { api, type Trend } from "@/lib/api";
import TrendCard from "@/components/TrendCard";
import TrendsActionButton from "@/components/TrendsActionButton";
import ClearSetsButton from "@/components/ClearSetsButton";
import CompareTiersView from "@/components/CompareTiersView";
import Link from "next/link";
import clsx from "clsx";

// A week's generation summary. `generations` is the actual list of set
// numbers present that week (e.g. [1, 3, 4] after Set 2 is deleted).
type WeekInfo = { week: string; generation_count: number; generations?: number[] };

const CATEGORIES = ["colour", "material", "pattern", "style", "seasonal"];

// Market segmentation (2026-08-08). Segmented views ONLY — buyer opted
// out of an "All" combined view. First-visit default → Middle (most
// retailers by volume in the buyer's classification).
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

export default function TrendsClient() {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();

  const [trends, setTrends] = useState<Trend[]>([]);
  const [weeks, setWeeks] = useState<WeekInfo[]>([]);
  const [loading, setLoading] = useState(true);

  const [category, setCategory] = useState("");
  const [value, setValue] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);

  // URL-driven state — segment tab + generation both live in the URL so
  // buyers can share deep links.
  const rawSegment = searchParams.get("segment");
  const compareParam = searchParams.get("view") === "compare";
  const activeTab: Tab = compareParam
    ? "compare"
    : (isSegment(rawSegment) ? rawSegment : DEFAULT_SEGMENT);
  const activeSegment: Segment = activeTab === "compare"
    ? DEFAULT_SEGMENT
    : activeTab;
  const generationParam = searchParams.get("generation");

  // First visit → drop a segment into the URL so refreshes stick.
  useEffect(() => {
    if (!rawSegment && !compareParam) {
      const p = new URLSearchParams(searchParams.toString());
      p.set("segment", DEFAULT_SEGMENT);
      router.replace(`${pathname}?${p.toString()}`, { scroll: false });
    }
  }, [rawSegment, compareParam, searchParams, pathname, router]);

  useEffect(() => {
    if (activeTab === "compare") { setLoading(false); return; }
    let cancelled = false;
    setLoading(true);
    const params: Record<string, string> = { market_segment: activeSegment };
    if (generationParam) params.generation = generationParam;
    Promise.all([
      api.trends.list(params).catch(() => [] as Trend[]),
      api.trends.weeks(activeSegment).catch(() => [] as WeekInfo[]),
    ]).then(([t, w]) => {
      if (!cancelled) {
        setTrends(t);
        setWeeks(w);
        setLoading(false);
      }
    });
    return () => { cancelled = true; };
  }, [activeSegment, activeTab, generationParam, refreshTick]);

  useEffect(() => {
    const handler = () => setRefreshTick((n) => n + 1);
    window.addEventListener("trends-refresh", handler);
    return () => window.removeEventListener("trends-refresh", handler);
  }, []);

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

  const latestWeek = weeks[0];
  const generationList: number[] = latestWeek?.generations
    ?? (latestWeek ? Array.from({ length: latestWeek.generation_count }, (_, i) => i + 1) : []);
  const latestGeneration = generationList.length ? generationList[generationList.length - 1] : 1;
  const activeGen = generationParam ? parseInt(generationParam) : latestGeneration;
  const hasMultipleGenerations = generationList.length > 1;

  function switchTab(tab: Tab) {
    const p = new URLSearchParams();
    if (tab === "compare") { p.set("view", "compare"); }
    else { p.set("segment", tab); }
    // Generation tabs are per-segment — clear when switching to avoid
    // landing on Set 3 in a segment that only has Set 1.
    router.push(`${pathname}?${p.toString()}`);
  }

  function genTabHref(gen: number) {
    const p = new URLSearchParams(searchParams.toString());
    p.set("generation", String(gen));
    return `${pathname}?${p.toString()}`;
  }

  async function handleDeleteGeneration(gen: number) {
    if (!latestWeek) return;
    if (generationList.length <= 1) {
      alert("Can't delete the only remaining set — run a new analysis first, or clear all sets.");
      return;
    }
    if (!confirm(`Delete Set ${gen}? This removes every trend in that set. Can't be undone.`)) return;
    try {
      const result = await api.trends.deleteGeneration(latestWeek.week, gen, activeSegment);
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
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-stone-900">Trends</h1>
        <TrendsActionButton initialHasAnalysis={weeks.length > 0} segment={activeSegment} />
      </div>

      {/* Segment pill switcher — replaces the old "All" view. Buyer explicitly
          opted for segmented-only. 'Compare tiers' is the diffusion view. */}
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
          title="Cross-tier diffusion view"
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
        <CompareTiersView />
      ) : (
        <>
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
              <ClearSetsButton target="trends" />
            </div>
          )}
          {weeks.length > 0 && !hasMultipleGenerations && (
            <div className="flex justify-end">
              <ClearSetsButton target="trends" />
            </div>
          )}

          <p className="text-sm text-stone-500 flex items-center gap-2">
            <span className={clsx(
              "px-1.5 py-0.5 rounded-full text-[10px] font-semibold border",
              activeSegmentMeta.badge,
            )}>{activeSegmentMeta.label}</span>
            {loading ? "Loading…" : `${filtered.length} trend${filtered.length !== 1 ? "s" : ""} found`}
            {hasMultipleGenerations && ` · Set ${activeGen} of ${generationList.length}`}
          </p>

          {loading ? null : filtered.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
              {filtered.map((t) => <TrendCard key={t.id} trend={t} />)}
            </div>
          ) : weeks.length === 0 ? (
            <div className="text-center py-20 space-y-3">
              <p className="text-4xl">📊</p>
              <p className="text-stone-600 font-medium">No {activeSegmentMeta.label} trends yet</p>
              <p className="text-stone-400 text-sm">
                Run an analysis to populate this tier. Retailers with a{" "}
                <span className="font-medium">{activeSegmentMeta.label}</span> classification
                will contribute their products.
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
