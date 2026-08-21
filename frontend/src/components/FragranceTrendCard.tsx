"use client";

import Link from "next/link";
import clsx from "clsx";
import type { FragranceTrend, FragranceTrendExample } from "@/lib/api";
import FragranceTrendViewAllButton from "./FragranceTrendViewAllButton";

// Kept for backward-compat rendering of pre-refactor trends that still
// carry a status/momentum. New trends drop the badge entirely.
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

export default function FragranceTrendCard({ trend }: { trend: FragranceTrend }) {
  const hasBestSeller = (trend.examples ?? []).some((e) => e.is_best_seller);
  return (
    <Link href={`/fragrance-trends/${trend.id}`} className="group block">
      <article className="bg-white rounded-xl border border-stone-200 overflow-hidden hover:shadow-lg transition-shadow flex flex-col">
        <div className="relative">
          <ImageMosaic examples={trend.examples ?? []} />
          {hasBestSeller && (
            <span className="absolute top-2 left-2 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-amber-100 text-amber-800 border border-amber-200">
              ★ Best Seller
            </span>
          )}
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

          {trend.scent_families.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {trend.scent_families.slice(0, 3).map((s) => (
                <span key={s} className="px-2 py-0.5 bg-amber-50 border border-amber-200 rounded-full text-xs text-amber-700">{s}</span>
              ))}
            </div>
          )}

          {trend.container_styles.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {trend.container_styles.slice(0, 3).map((c) => (
                <span key={c} className="px-2 py-0.5 bg-stone-100 rounded-full text-xs text-stone-600">{c}</span>
              ))}
            </div>
          )}

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

          <FragranceTrendViewAllButton trend={trend} />
        </div>
      </article>
    </Link>
  );
}
