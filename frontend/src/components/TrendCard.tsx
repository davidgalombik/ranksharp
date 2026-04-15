import Link from "next/link";
import type { Trend, TrendExample } from "@/lib/api";
import clsx from "clsx";

const STATUS_STYLES = {
  rising:   "bg-emerald-100 text-emerald-800",
  plateau:  "bg-amber-100 text-amber-800",
  declining:"bg-rose-100 text-rose-800",
  new:      "bg-blue-100 text-blue-800",
} as const;

const STATUS_ICONS = {
  rising:   "↑",
  plateau:  "→",
  declining:"↓",
  new:      "★",
};

const PRICE_TIER_LABELS: Record<string, string> = {
  budget:  "Budget",
  mid:     "Mid-range",
  premium: "Premium",
  luxury:  "Luxury",
};

const COUNTRY_FLAGS: Record<string, string> = {
  US: "🇺🇸", AU: "🇦🇺", GB: "🇬🇧", NL: "🇳🇱",
};

/** 1-large + 2-stacked editorial mosaic, or hero-only fallback */
function ImageMosaic({ examples }: { examples: TrendExample[] }) {
  const withImg = examples.filter((e) => e.primary_image_url);

  if (withImg.length === 0) {
    return (
      <div className="h-52 bg-stone-100 flex items-center justify-center text-stone-300 text-5xl">
        ⌂
      </div>
    );
  }

  if (withImg.length === 1) {
    return (
      <div className="h-52 bg-stone-100 overflow-hidden">
        <img
          src={withImg[0].primary_image_url!}
          alt={withImg[0].name}
          className="w-full h-full object-cover"
        />
      </div>
    );
  }

  // 2-col: left = hero (full height), right = two stacked images
  const [hero, second, third, fourth] = withImg;
  const rightSlots = [second, third, fourth].filter(Boolean).slice(0, 2);

  return (
    <div className="h-52 flex gap-0.5 overflow-hidden bg-stone-200">
      {/* Left: hero — takes 60% width */}
      <div className="flex-[3] overflow-hidden">
        <img
          src={hero.primary_image_url!}
          alt={hero.name}
          className="w-full h-full object-cover"
          title={`${hero.retailer_name}: ${hero.name}`}
        />
      </div>
      {/* Right: 1 or 2 stacked — takes 40% width */}
      <div className="flex-[2] flex flex-col gap-0.5">
        {rightSlots.map((ex) => (
          <div
            key={ex.product_id}
            className="flex-1 overflow-hidden relative"
          >
            <img
              src={ex.primary_image_url!}
              alt={ex.name}
              className="w-full h-full object-cover"
              title={`${ex.retailer_name}: ${ex.name}`}
            />
          </div>
        ))}
        {/* Fill empty slot with grey if only 1 right image */}
        {rightSlots.length < 2 && (
          <div className="flex-1 bg-stone-100" />
        )}
      </div>
    </div>
  );
}

export default function TrendCard({ trend }: { trend: Trend }) {
  const examples = trend.examples ?? [];

  return (
    <Link href={`/trends/${trend.id}`} className="group block">
      <article className="bg-white rounded-xl border border-stone-200 overflow-hidden hover:shadow-lg transition-shadow">

        {/* Image mosaic */}
        <div className="relative">
          <ImageMosaic examples={examples} />

          {/* Status badge */}
          <span
            className={clsx(
              "absolute top-2 right-2 px-2 py-0.5 rounded-full text-xs font-semibold shadow-sm",
              STATUS_STYLES[trend.status]
            )}
          >
            {STATUS_ICONS[trend.status]}{" "}
            {trend.status.charAt(0).toUpperCase() + trend.status.slice(1)}
            {trend.momentum_pct != null &&
              ` ${trend.momentum_pct > 0 ? "+" : ""}${trend.momentum_pct}%`}
          </span>

          {/* Market flags */}
          {trend.markets && trend.markets.length > 0 && (
            <div className="absolute bottom-2 left-2 flex gap-0.5">
              {trend.markets.slice(0, 3).map((m) => (
                <span key={m} className="text-base leading-none" title={m}>
                  {COUNTRY_FLAGS[m] ?? m}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Content */}
        <div className="p-4 space-y-3">
          {/* Category + price tier */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-medium text-stone-400 uppercase tracking-wider">
              {trend.category}
            </span>
            {trend.price_tier && (
              <span className="text-xs text-stone-400">
                · {PRICE_TIER_LABELS[trend.price_tier] ?? trend.price_tier}
              </span>
            )}
          </div>

          <div>
            <h3 className="text-base font-semibold text-stone-900 group-hover:text-stone-600 leading-snug">
              {trend.name}
            </h3>
            <p className="text-sm text-stone-600 mt-1 line-clamp-2">
              {trend.description}
            </p>
          </div>

          {/* Colour swatches */}
          {trend.dominant_colours.length > 0 && (
            <div className="flex flex-wrap gap-x-2.5 gap-y-1">
              {trend.dominant_colours.slice(0, 4).map((c) => (
                <span key={c} className="flex items-center gap-1 text-xs text-stone-600">
                  <span
                    className="inline-block w-3 h-3 rounded-full border border-stone-200 flex-shrink-0"
                    style={{ backgroundColor: c }}
                    title={c}
                  />
                  {c}
                </span>
              ))}
            </div>
          )}

          {/* Material chips */}
          {trend.dominant_materials.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {trend.dominant_materials.slice(0, 3).map((m) => (
                <span
                  key={m}
                  className="px-2 py-0.5 bg-stone-100 rounded-full text-xs text-stone-600"
                >
                  {m}
                </span>
              ))}
            </div>
          )}

          {/* Footer stats */}
          <div className="pt-2 border-t border-stone-100 space-y-1.5">
            {/* Retailer names */}
            {trend.retailer_names && trend.retailer_names.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {trend.retailer_names.map((r) => (
                  <span
                    key={r}
                    className="px-2 py-0.5 bg-stone-100 rounded-full text-xs text-stone-600 font-medium"
                  >
                    {r}
                  </span>
                ))}
              </div>
            )}
            <div className="flex items-center justify-between text-xs text-stone-400">
              <span>{trend.product_count.toLocaleString()} products</span>
              <span>{trend.retailer_names.length} retailers</span>
              {trend.avg_price != null && (
                <span>${trend.avg_price.toFixed(0)} avg</span>
              )}
            </div>
          </div>
        </div>
      </article>
    </Link>
  );
}
