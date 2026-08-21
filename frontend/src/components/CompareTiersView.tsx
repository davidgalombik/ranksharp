"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import Link from "next/link";
import { api } from "@/lib/api";

type TierCell = {
  trend_id: number;
  product_count: number;
  retailer_count: number;
  momentum_pct: number | null;
} | null;

type Row = {
  name: string;
  category: string;
  luxury: TierCell;
  middle: TierCell;
  mass: TierCell;
};

type Signal = "luxury-only" | "middle-only" | "mass-only"
            | "luxury-middle" | "middle-mass" | "luxury-mass"
            | "all-three";

function classify(row: Row): Signal {
  const l = !!row.luxury, m = !!row.middle, x = !!row.mass;
  if (l && m && x) return "all-three";
  if (l && m) return "luxury-middle";
  if (m && x) return "middle-mass";
  if (l && x) return "luxury-mass";
  if (l) return "luxury-only";
  if (m) return "middle-only";
  return "mass-only";
}

const SIGNAL_LABELS: Record<Signal, { label: string; blurb: string; badge: string }> = {
  "luxury-only":   { label: "🌱 Emerging in Luxury",  blurb: "Early signal — may diffuse to Middle / Mass in coming weeks",
                     badge: "bg-amber-100 text-amber-800 border-amber-200" },
  "middle-only":   { label: "Middle-only",            blurb: "Present in Middle but not Luxury or Mass — worth watching",
                     badge: "bg-stone-100 text-stone-700 border-stone-200" },
  "mass-only":     { label: "Mass-only",              blurb: "Present in Mass without Luxury upstream — may be commoditised or Mass-native",
                     badge: "bg-sky-100 text-sky-800 border-sky-200" },
  "luxury-middle": { label: "Diffusing (Lux → Mid)",  blurb: "Working its way down — Mass likely next",
                     badge: "bg-amber-50 text-amber-700 border-amber-200" },
  "middle-mass":   { label: "Widely adopted",         blurb: "Middle + Mass — mainstream. May be past peak in Luxury.",
                     badge: "bg-sky-50 text-sky-700 border-sky-200" },
  "luxury-mass":   { label: "Skipped the middle",     blurb: "Luxury and Mass without Middle — unusual, worth investigating",
                     badge: "bg-purple-50 text-purple-700 border-purple-200" },
  "all-three":     { label: "Fully diffused",         blurb: "Present in all three tiers — mature trend",
                     badge: "bg-emerald-50 text-emerald-700 border-emerald-200" },
};

/**
 * Diffusion view — same-name trends compared across Luxury / Middle / Mass.
 *
 * Reads /api/trends/compare which merges trends by (name, category).
 * Rows are ranked by "how many tiers is it present in" so fully-diffused
 * trends surface first and Luxury-only early signals get a badge for
 * quick visual scanning.
 */
export default function CompareTiersView() {
  const [rows, setRows] = useState<Row[]>([]);
  const [week, setWeek] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Signal | "">("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.trends.compare()
      .then((r) => { if (!cancelled) { setRows(r.rows as Row[]); setWeek(r.week); } })
      .catch(() => { if (!cancelled) { setRows([]); setWeek(null); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const filtered = filter ? rows.filter((r) => classify(r) === filter) : rows;

  return (
    <div className="space-y-4">
      <div className="bg-purple-50 border border-purple-200 rounded-xl p-4">
        <p className="text-sm font-semibold text-purple-900">Diffusion view · Compare tiers</p>
        <p className="text-xs text-purple-700 mt-1 leading-relaxed">
          Same-name trends compared across Luxury, Middle, and Mass. A trend
          in Luxury only is often a leading indicator — Mass adoption may
          follow 6-12 months later. Rows fully diffused across all three
          tiers rank first.
        </p>
        {week && (
          <p className="text-xs text-purple-500 mt-1">Week of {week}</p>
        )}
      </div>

      {/* Signal filter chips */}
      <div className="flex flex-wrap gap-1.5">
        <button
          onClick={() => setFilter("")}
          className={clsx(
            "px-2.5 py-1 rounded-full text-xs font-medium border transition-colors",
            !filter ? "bg-stone-900 text-white border-stone-900" : "bg-white text-stone-600 border-stone-200 hover:border-stone-400",
          )}
        >All rows</button>
        {(Object.keys(SIGNAL_LABELS) as Signal[]).map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s === filter ? "" : s)}
            className={clsx(
              "px-2.5 py-1 rounded-full text-xs font-medium border transition-colors",
              filter === s
                ? SIGNAL_LABELS[s].badge + " border"
                : "bg-white text-stone-600 border-stone-200 hover:border-stone-400",
            )}
          >
            {SIGNAL_LABELS[s].label}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-center text-stone-400 py-16">Loading comparison…</p>
      ) : filtered.length === 0 ? (
        <div className="bg-stone-50 border border-stone-200 rounded-xl p-12 text-center">
          <p className="text-4xl mb-3">📊</p>
          <p className="text-stone-600 font-medium">
            {rows.length === 0
              ? "No segmented trends yet"
              : "No trends match this signal filter"}
          </p>
          {rows.length === 0 && (
            <p className="text-stone-400 text-sm mt-1">
              Run analysis for at least one tier — Luxury, Middle, or Mass — to populate this view.
            </p>
          )}
        </div>
      ) : (
        <div className="bg-white border border-stone-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-stone-50 text-xs uppercase text-stone-500">
                <tr>
                  <th className="text-left px-4 py-2">Trend</th>
                  <th className="text-left px-4 py-2">Category</th>
                  <th className="text-left px-4 py-2">Signal</th>
                  <th className="text-right px-4 py-2 bg-amber-50/50">Luxury</th>
                  <th className="text-right px-4 py-2">Middle</th>
                  <th className="text-right px-4 py-2 bg-sky-50/50">Mass</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => {
                  const signal = classify(r);
                  const meta = SIGNAL_LABELS[signal];
                  return (
                    <tr key={`${r.name}-${r.category}-${i}`} className="border-t border-stone-100 hover:bg-stone-50/50">
                      <td className="px-4 py-2 font-medium text-stone-900">{r.name}</td>
                      <td className="px-4 py-2 text-stone-500 capitalize">{r.category}</td>
                      <td className="px-4 py-2">
                        <span className={clsx("px-2 py-0.5 rounded-full text-[10px] font-semibold border", meta.badge)}
                              title={meta.blurb}>
                          {meta.label}
                        </span>
                      </td>
                      <TierCol cell={r.luxury} bg="bg-amber-50/50" />
                      <TierCol cell={r.middle} />
                      <TierCol cell={r.mass} bg="bg-sky-50/50" />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function TierCol({ cell, bg }: { cell: TierCell; bg?: string }) {
  if (!cell) {
    return <td className={clsx("px-4 py-2 text-right text-stone-300", bg)}>—</td>;
  }
  return (
    <td className={clsx("px-4 py-2 text-right tabular-nums", bg)}>
      <Link href={`/trends/${cell.trend_id}`}
            className="text-stone-800 hover:text-stone-500">
        <span className="font-medium">{cell.product_count.toLocaleString()}</span>
        <span className="text-stone-400 text-xs ml-1">/ {cell.retailer_count}r</span>
        {cell.momentum_pct != null && (
          <span className={clsx(
            "block text-[10px]",
            cell.momentum_pct > 10 ? "text-emerald-600" :
            cell.momentum_pct < -10 ? "text-rose-600" : "text-stone-400"
          )}>
            {cell.momentum_pct > 0 ? "+" : ""}{cell.momentum_pct}%
          </span>
        )}
      </Link>
    </td>
  );
}
