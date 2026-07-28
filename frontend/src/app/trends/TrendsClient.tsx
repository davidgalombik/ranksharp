"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams, usePathname } from "next/navigation";
import { api, type Trend } from "@/lib/api";
import TrendCard from "@/components/TrendCard";
import TrendsActionButton from "@/components/TrendsActionButton";
import ClearSetsButton from "@/components/ClearSetsButton";
import Link from "next/link";
import clsx from "clsx";

// Matches the categories the DesignTrendEngine actually produces so no
// dropdown option filters to an empty page. Legacy trends with other
// categories still show under "All categories" but aren't filterable.
const CATEGORIES = ["colour", "material", "pattern", "style", "seasonal"];

export default function TrendsClient() {
  const searchParams = useSearchParams();
  const pathname = usePathname();

  const [trends, setTrends] = useState<Trend[]>([]);
  const [weeks, setWeeks] = useState<{ week: string; generation_count: number }[]>([]);
  const [loading, setLoading] = useState(true);

  // Local filter state — no submit button, applies on change
  const [category, setCategory] = useState("");
  const [value, setValue] = useState("");

  // Bumped when a "Try Again" / "Run" completes elsewhere on the page,
  // to force this component to re-fetch trends. Custom event dispatched
  // by RunAnalysisButton + TryAgainTrendsButton on state === SUCCESS.
  const [refreshTick, setRefreshTick] = useState(0);

  // Server-driven params (generation tab lives in URL so links share cleanly)
  const generationParam = searchParams.get("generation");

  // Load trends + weeks whenever generation changes (Set 1 / Set 2 tabs)
  // or when the action buttons signal a re-run completed.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const params: Record<string, string> = {};
    if (generationParam) params.generation = generationParam;
    Promise.all([
      api.trends.list(params).catch(() => [] as Trend[]),
      api.trends.weeks().catch(() => [] as { week: string; generation_count: number }[]),
    ]).then(([t, w]) => {
      if (!cancelled) {
        setTrends(t);
        setWeeks(w);
        setLoading(false);
      }
    });
    return () => { cancelled = true; };
  }, [generationParam, refreshTick]);

  // Listen for the analysis-complete signal from the action buttons
  useEffect(() => {
    const handler = () => setRefreshTick((n) => n + 1);
    window.addEventListener("trends-refresh", handler);
    return () => window.removeEventListener("trends-refresh", handler);
  }, []);

  // Value dropdown options — trend names within the currently-selected
  // category. Sorted alphabetically for scanability.
  const valuesInCategory = useMemo(() => {
    if (!category) return [];
    const names = new Set<string>();
    for (const t of trends) {
      if (t.category === category) names.add(t.name);
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [trends, category]);

  // Reset the value dropdown whenever category changes so stale "green"
  // doesn't carry over into "Material"
  useEffect(() => {
    setValue("");
  }, [category]);

  // Client-side filtered view — no page reload / no Filter button
  const filtered = useMemo(() => {
    return trends.filter((t) => {
      if (category && t.category !== category) return false;
      if (value && t.name !== value) return false;
      return true;
    });
  }, [trends, category, value]);

  const activeGen = generationParam ? parseInt(generationParam) : (weeks[0]?.generation_count ?? 1);
  const generationCount = weeks[0]?.generation_count ?? 1;
  const hasMultipleGenerations = generationCount > 1;

  function genTabHref(gen: number) {
    const p = new URLSearchParams();
    p.set("generation", String(gen));
    return `${pathname}?${p.toString()}`;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-stone-900">Trends</h1>
        <TrendsActionButton initialHasAnalysis={weeks.length > 0} />
      </div>

      {/* Filters — auto-apply on change, no submit button. Week + Status
          are intentionally hidden per user request (2026-07-28); the
          engine is single-week and status is "New" for everything until
          we have two consecutive weekly reports. */}
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

        {/* Cascading value dropdown — only appears once a category is picked */}
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

      {/* Generation tabs — shown when multiple sets exist */}
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

      {weeks.length > 0 && !hasMultipleGenerations && (
        <div className="flex justify-end">
          <ClearSetsButton target="trends" />
        </div>
      )}

      <p className="text-sm text-stone-500">
        {loading ? "Loading…" : `${filtered.length} trend${filtered.length !== 1 ? "s" : ""} found`}
        {hasMultipleGenerations && ` · Set ${activeGen} of ${generationCount}`}
      </p>

      {loading ? null : filtered.length > 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {filtered.map((t) => (
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
