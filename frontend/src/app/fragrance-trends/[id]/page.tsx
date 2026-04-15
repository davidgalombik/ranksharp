import { api, type FragranceTrend, type FragranceTrendExample } from "@/lib/api";
import { notFound } from "next/navigation";
import Link from "next/link";
import clsx from "clsx";

const STATUS_STYLES = {
  rising:   "bg-emerald-100 text-emerald-800",
  plateau:  "bg-amber-100 text-amber-800",
  declining:"bg-rose-100 text-rose-800",
  new:      "bg-blue-100 text-blue-800",
} as const;

const CATEGORY_COLOURS: Record<string, string> = {
  aesthetic:      "bg-purple-100 text-purple-700",
  scent:          "bg-amber-100 text-amber-700",
  market:         "bg-blue-100 text-blue-700",
  sustainability: "bg-emerald-100 text-emerald-700",
  retail:         "bg-rose-100 text-rose-700",
};

const COUNTRY_FLAGS: Record<string, string> = { US: "🇺🇸", AU: "🇦🇺", GB: "🇬🇧" };

const PRICE_TIER_LABELS: Record<string, string> = {
  budget: "Budget", mid: "Mid-range", premium: "Premium", luxury: "Luxury",
};

function formatPrice(price: number, currency: string) {
  const symbols: Record<string, string> = { USD: "$", AUD: "A$", GBP: "£", EUR: "€" };
  return `${symbols[currency] ?? currency}${price.toFixed(2)}`;
}

function HeroMosaic({ examples }: { examples: FragranceTrendExample[] }) {
  const withImg = examples.filter((e) => e.primary_image_url).slice(0, 5);
  if (withImg.length === 0) {
    return (
      <div className="h-64 bg-amber-50 flex items-center justify-center text-amber-200 text-7xl rounded-xl">🕯</div>
    );
  }
  if (withImg.length === 1) {
    return (
      <div className="h-72 rounded-xl overflow-hidden">
        <img src={withImg[0].primary_image_url!} alt={withImg[0].name} className="w-full h-full object-cover" />
      </div>
    );
  }
  if (withImg.length === 2) {
    return (
      <div className="h-72 grid grid-cols-2 gap-1 rounded-xl overflow-hidden">
        {withImg.map((ex) => (
          <div key={ex.product_id} className="overflow-hidden">
            <img src={ex.primary_image_url!} alt={ex.name} className="w-full h-full object-cover" />
          </div>
        ))}
      </div>
    );
  }
  const [hero, ...rest] = withImg;
  return (
    <div className="h-80 flex gap-1 rounded-xl overflow-hidden">
      <div className="flex-[11] overflow-hidden">
        <img src={hero.primary_image_url!} alt={hero.name} className="w-full h-full object-cover" title={`${hero.retailer_name}: ${hero.name}`} />
      </div>
      <div className="flex-[9] flex flex-col gap-1">
        {rest.map((ex) => (
          <div key={ex.product_id} className="flex-1 overflow-hidden">
            <img src={ex.primary_image_url!} alt={ex.name} className="w-full h-full object-cover" title={`${ex.retailer_name}: ${ex.name}`} />
          </div>
        ))}
      </div>
    </div>
  );
}

function ProductCard({ example }: { example: FragranceTrendExample }) {
  return (
    <a href={example.url} target="_blank" rel="noopener noreferrer"
      className="group bg-white rounded-xl border border-stone-200 overflow-hidden hover:shadow-md transition-shadow flex flex-col">
      <div className="relative h-48 bg-stone-100 flex-shrink-0 overflow-hidden">
        {example.primary_image_url ? (
          <img src={example.primary_image_url} alt={example.name}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-stone-300 text-4xl">🕯</div>
        )}
        <span className="absolute top-2 left-2 text-base leading-none" title={example.retailer_country}>
          {COUNTRY_FLAGS[example.retailer_country] ?? example.retailer_country}
        </span>
        {example.is_hero && (
          <span className="absolute top-2 right-2 text-xs bg-amber-400 text-white px-1.5 py-0.5 rounded font-medium">Hero</span>
        )}
      </div>
      <div className="p-3 flex flex-col gap-1 flex-1">
        <p className="text-xs font-medium text-stone-400 uppercase tracking-wide">{example.retailer_name}</p>
        <p className="text-sm font-medium text-stone-900 line-clamp-2 flex-1">{example.name}</p>
        {example.price != null && (
          <p className="text-sm font-semibold text-stone-700">{formatPrice(example.price, example.currency)}</p>
        )}
        {example.colours.length > 0 && (
          <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-0.5">
            {example.colours.slice(0, 3).map((c) => (
              <span key={c} className="text-xs text-stone-500">{c}</span>
            ))}
          </div>
        )}
      </div>
    </a>
  );
}

export default async function FragranceTrendDetailPage({ params }: { params: { id: string } }) {
  let trend: FragranceTrend;
  try {
    trend = await api.fragranceTrends.getTrend(parseInt(params.id));
  } catch {
    notFound();
  }

  const attributeSections = [
    { label: "Colours",          items: trend.dominant_colours },
    { label: "Wax & Materials",  items: trend.dominant_materials },
    { label: "Container Styles", items: trend.container_styles },
    { label: "Scent Families",   items: trend.scent_families },
  ].filter(({ items }) => items.length > 0);

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      {/* Breadcrumb */}
      <nav className="text-sm text-stone-500">
        <Link href="/fragrance-trends" className="hover:text-stone-900">Fragrance Trends</Link>
        {" / "}
        <span className="text-stone-900">{trend.name}</span>
      </nav>

      {/* Hero mosaic */}
      <HeroMosaic examples={trend.examples.slice(0, 5)} />

      {/* Title + badges */}
      <div className="space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={clsx("px-2.5 py-0.5 rounded-full text-sm font-medium", STATUS_STYLES[trend.status])}>
            {trend.status.charAt(0).toUpperCase() + trend.status.slice(1)}
            {trend.momentum_pct != null && ` (${trend.momentum_pct > 0 ? "+" : ""}${trend.momentum_pct}% vs last week)`}
          </span>
          <span className={clsx("px-2.5 py-0.5 rounded-full text-sm font-medium", CATEGORY_COLOURS[trend.category] ?? "bg-stone-100 text-stone-600")}>
            {trend.category}
          </span>
          {trend.price_tier && (
            <span className="px-2.5 py-0.5 bg-stone-100 text-stone-600 rounded-full text-sm">
              {PRICE_TIER_LABELS[trend.price_tier] ?? trend.price_tier}
            </span>
          )}
          {trend.markets && trend.markets.length > 0 && (
            <div className="flex gap-1.5 items-center">
              {trend.markets.map((m) => (
                <span key={m} className="flex items-center gap-1 text-sm text-stone-600">
                  <span title={m}>{COUNTRY_FLAGS[m] ?? m}</span>
                  <span>{m}</span>
                </span>
              ))}
            </div>
          )}
        </div>
        <h1 className="text-3xl font-bold text-stone-900">{trend.name}</h1>
        <p className="text-lg text-stone-600 leading-relaxed">{trend.description}</p>
      </div>

      {/* Metrics row */}
      <div className="grid grid-cols-3 sm:grid-cols-4 gap-4">
        {[
          { label: "Products",  value: trend.product_count.toLocaleString() },
          { label: "Retailers", value: trend.retailer_names.length },
          { label: "Avg price", value: trend.avg_price ? `$${trend.avg_price.toFixed(0)}` : "–" },
          { label: "Markets",   value: trend.markets?.length ? trend.markets.join(", ") : "–" },
        ].map(({ label, value }) => (
          <div key={label} className="bg-stone-50 rounded-xl p-4 text-center">
            <p className="text-xl font-bold text-stone-900">{value}</p>
            <p className="text-xs text-stone-500 mt-0.5">{label}</p>
          </div>
        ))}
      </div>

      {/* Why this is trending */}
      <section className="bg-white rounded-xl border border-stone-200 p-6">
        <h2 className="text-base font-semibold text-stone-900 mb-3">Why this is trending</h2>
        <p className="text-stone-700 leading-relaxed">{trend.rationale}</p>
      </section>

      {/* Attribute grid */}
      {attributeSections.length > 0 && (
        <section className="grid grid-cols-2 gap-4">
          {attributeSections.map(({ label, items }) => (
            <div key={label} className="bg-white rounded-xl border border-stone-200 p-4">
              <p className="text-xs font-medium text-stone-400 uppercase tracking-wider mb-2">{label}</p>
              <div className="flex flex-wrap gap-1.5">
                {items.map((item) => (
                  <span key={item} className="px-2 py-0.5 bg-stone-100 rounded-full text-sm text-stone-700">{item}</span>
                ))}
              </div>
            </div>
          ))}
        </section>
      )}

      {/* Sustainability signals */}
      {trend.sustainability_signals.length > 0 && (
        <section className="bg-emerald-50 rounded-xl border border-emerald-200 p-4">
          <p className="text-xs font-medium text-emerald-600 uppercase tracking-wider mb-2">Sustainability Signals</p>
          <div className="flex flex-wrap gap-1.5">
            {trend.sustainability_signals.map((s) => (
              <span key={s} className="px-2 py-0.5 bg-emerald-100 rounded-full text-sm text-emerald-800">{s}</span>
            ))}
          </div>
        </section>
      )}

      {/* Seen at retailers */}
      <section className="bg-white rounded-xl border border-stone-200 p-6">
        <h2 className="text-base font-semibold text-stone-900 mb-3">Seen at these retailers</h2>
        <div className="flex flex-wrap gap-2">
          {trend.retailer_names.map((name) => (
            <span key={name} className="px-3 py-1 bg-stone-50 border border-stone-200 rounded-full text-sm text-stone-700">{name}</span>
          ))}
        </div>
      </section>

      {/* Example products */}
      {trend.examples.length > 0 && (
        <section>
          <h2 className="text-base font-semibold text-stone-900 mb-4">
            Example products ({trend.examples.length})
          </h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
            {trend.examples.map((example) => (
              <ProductCard key={example.product_id} example={example} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
