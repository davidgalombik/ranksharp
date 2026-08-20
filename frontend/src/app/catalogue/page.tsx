"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import clsx from "clsx";
import { api, type RanksharpProductListItem } from "@/lib/api";
import CatalogueUploadModal from "@/components/CatalogueUploadModal";
import CatalogueImageUploadModal from "@/components/CatalogueImageUploadModal";

const CURRENCIES: Record<string, string> = {
  USD: "$", AUD: "A$", GBP: "£", EUR: "€", NZD: "NZ$", CAD: "C$",
};
const PAGE_SIZE = 48;

/**
 * Ranksharp Catalogue — products Ranksharp has sold to ALDI (and, later,
 * other customers). Grid view + search + category filter + upload flows.
 */
export default function CataloguePage() {
  const [items, setItems] = useState<RanksharpProductListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<string[]>([]);

  const [csvOpen, setCsvOpen] = useState(false);
  const [imageOpen, setImageOpen] = useState(false);
  // Bumped every time an upload commits so the list re-fetches.
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    api.ranksharp.listCategories()
      .then((r) => setCategories(r.categories))
      .catch(() => setCategories([]));
  }, [refreshTick]);

  const loadFirstPage = useCallback(async () => {
    setLoading(true);
    setOffset(0);
    try {
      const res = await api.ranksharp.listProducts({
        q: query || undefined,
        category: category || undefined,
        limit: PAGE_SIZE,
        offset: 0,
      });
      setItems(res.items);
      setTotal(res.total);
    } catch {
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [query, category]);

  // Debounce the search input so we don't hammer the API on every keystroke.
  useEffect(() => {
    const t = setTimeout(loadFirstPage, 250);
    return () => clearTimeout(t);
  }, [loadFirstPage, refreshTick]);

  async function loadMore() {
    if (loadingMore || items.length >= total) return;
    setLoadingMore(true);
    const next = offset + PAGE_SIZE;
    try {
      const res = await api.ranksharp.listProducts({
        q: query || undefined,
        category: category || undefined,
        limit: PAGE_SIZE,
        offset: next,
      });
      setItems((prev) => [...prev, ...res.items]);
      setOffset(next);
    } finally {
      setLoadingMore(false);
    }
  }

  const showLoadMore = items.length < total && !loading;

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-stone-900">Ranksharp Catalogue</h1>
          <p className="text-stone-500 text-sm mt-1">
            Products Ranksharp has sold — sale history, prices, and units purchased by customer
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setImageOpen(true)}
            className="px-3 py-2 rounded-lg bg-white border border-stone-300 hover:bg-stone-50 text-sm font-medium text-stone-700"
          >
            📷 Upload images
          </button>
          <button
            onClick={() => setCsvOpen(true)}
            className="px-3 py-2 rounded-lg bg-stone-900 hover:bg-stone-700 text-white text-sm font-medium"
          >
            📄 Upload CSV
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs font-medium text-stone-500 mb-1">Search</label>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="SKU or product name"
            className="w-full border border-stone-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1">Category</label>
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="border border-stone-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          >
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
      </div>

      <p className="text-sm text-stone-500">
        {loading ? "Loading…" : `${total.toLocaleString()} product${total !== 1 ? "s" : ""} in catalogue`}
      </p>

      {loading ? (
        <p className="text-center text-stone-400 py-16">Loading products…</p>
      ) : items.length === 0 ? (
        <div className="bg-stone-50 border border-stone-200 rounded-xl p-12 text-center">
          <p className="text-4xl mb-3">📦</p>
          <p className="text-stone-600 font-medium">
            {query || category ? "No products match your filters" : "Catalogue is empty"}
          </p>
          {!query && !category && (
            <p className="text-stone-400 text-sm mt-1">
              Upload a CSV to add the products you&apos;ve sold
            </p>
          )}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {items.map((p) => <ProductCard key={p.id} product={p} />)}
          </div>
          {showLoadMore && (
            <div className="pt-6 text-center">
              <button
                onClick={loadMore}
                disabled={loadingMore}
                className={clsx(
                  "px-5 py-2 rounded-lg text-sm font-medium transition-colors",
                  loadingMore
                    ? "bg-stone-100 text-stone-400 cursor-not-allowed"
                    : "bg-stone-900 text-white hover:bg-stone-700",
                )}
              >
                {loadingMore ? "Loading…" : `Load ${Math.min(PAGE_SIZE, total - items.length)} more`}
              </button>
            </div>
          )}
        </>
      )}

      {csvOpen && (
        <CatalogueUploadModal
          onClose={() => setCsvOpen(false)}
          onCommitted={() => { setCsvOpen(false); setRefreshTick((n) => n + 1); }}
        />
      )}
      {imageOpen && (
        <CatalogueImageUploadModal
          onClose={() => setImageOpen(false)}
          onUploaded={() => { setImageOpen(false); setRefreshTick((n) => n + 1); }}
        />
      )}
    </div>
  );
}

function ProductCard({ product }: { product: RanksharpProductListItem }) {
  const symbol = CURRENCIES[product.latest_currency ?? ""] ?? "$";
  const price = product.latest_price_wholesale ?? product.latest_price_retail;
  return (
    <Link
      href={`/catalogue/${product.id}`}
      className="group block bg-white border border-stone-200 hover:border-stone-400 hover:shadow-md rounded-xl overflow-hidden transition-all"
    >
      <div className="aspect-square bg-stone-100 relative">
        {product.has_image ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={api.ranksharp.imageUrl(product.id)}
            alt={product.name}
            loading="lazy"
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-stone-300 text-4xl">📦</div>
        )}
      </div>
      <div className="p-3 space-y-1">
        <p className="text-[10px] uppercase tracking-wide text-stone-400 truncate">
          {product.sku}
        </p>
        <p className="text-sm font-semibold text-stone-900 leading-snug line-clamp-2">
          {product.name}
        </p>
        {product.category && (
          <p className="text-xs text-stone-500 truncate">{product.category}</p>
        )}
        <div className="flex items-center justify-between text-xs text-stone-500 pt-1 border-t border-stone-100">
          <span>
            {product.sale_count > 0
              ? `${product.sale_count} sale${product.sale_count !== 1 ? "s" : ""} · ${product.total_units.toLocaleString()} units`
              : "No sales recorded"}
          </span>
          {price != null && (
            <span className="font-semibold text-stone-900">{symbol}{price.toFixed(2)}</span>
          )}
        </div>
      </div>
    </Link>
  );
}
