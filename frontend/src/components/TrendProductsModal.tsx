"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Trend, type TrendExample } from "@/lib/api";
import clsx from "clsx";

const CURRENCIES: Record<string, string> = { USD: "$", AUD: "A$", GBP: "£", EUR: "€" };
const PAGE_SIZE = 48;

interface Props {
  trend: Trend;
  onClose: () => void;
}

/**
 * "View all N products" modal for a trend card. Paginated live query
 * against the products table — so a trend with 36k matching products
 * is fully browsable, not capped at the ~100 stored TrendExample rows.
 *
 * "Only best sellers" toggle re-fetches with the filter applied
 * (so the total count reflects the toggle state).
 */
export default function TrendProductsModal({ trend, onClose }: Props) {
  const [products, setProducts] = useState<TrendExample[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [onlyBestSellers, setOnlyBestSellers] = useState(false);

  // Initial page + reset when the toggle changes
  const loadFirstPage = useCallback(async () => {
    setLoading(true);
    setOffset(0);
    try {
      const res = await api.trends.products(trend.id, {
        limit: PAGE_SIZE,
        offset: 0,
        only_best_sellers: onlyBestSellers,
      });
      setProducts(res.items);
      setTotal(res.total);
    } catch {
      setProducts([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [trend.id, onlyBestSellers]);

  useEffect(() => {
    loadFirstPage();
  }, [loadFirstPage]);

  async function loadMore() {
    if (loadingMore || total === null || products.length >= total) return;
    setLoadingMore(true);
    const nextOffset = offset + PAGE_SIZE;
    try {
      const res = await api.trends.products(trend.id, {
        limit: PAGE_SIZE,
        offset: nextOffset,
        only_best_sellers: onlyBestSellers,
      });
      setProducts((prev) => [...prev, ...res.items]);
      setOffset(nextOffset);
    } finally {
      setLoadingMore(false);
    }
  }

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const showLoadMore = total !== null && products.length < total && !loading;

  return (
    <div
      className="fixed inset-0 z-50 bg-stone-900/60 flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`${trend.name} matching products`}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-6xl max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between p-6 border-b border-stone-200 gap-4">
          <div className="min-w-0">
            <p className="text-xs text-stone-400 uppercase tracking-wider">
              {trend.category} trend
            </p>
            <h2 className="text-xl font-bold text-stone-900 mt-0.5">{trend.name}</h2>
            <p className="text-sm text-stone-500 mt-1">
              {loading
                ? "Loading matching products…"
                : total === null
                  ? ""
                  : `Showing ${products.length.toLocaleString()} of ${total.toLocaleString()} matching products across ${trend.retailer_count} retailers`}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-stone-400 hover:text-stone-900 text-2xl leading-none px-2"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Filter bar */}
        <div className="px-6 py-3 border-b border-stone-100 flex items-center gap-4 flex-wrap">
          <label className="flex items-center gap-2 text-sm text-stone-700 cursor-pointer">
            <input
              type="checkbox"
              checked={onlyBestSellers}
              onChange={(e) => setOnlyBestSellers(e.target.checked)}
              className="rounded"
            />
            Only best sellers
          </label>
          {onlyBestSellers && (
            <p className="text-xs text-stone-400">
              Note: some retailers don&apos;t publish best-seller data — their
              products won&apos;t appear when this filter is on.
            </p>
          )}
        </div>

        {/* Product grid */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <p className="text-center text-stone-400 py-16">Loading products…</p>
          ) : products.length === 0 ? (
            <p className="text-center text-stone-400 py-16">
              {onlyBestSellers
                ? "No best-seller products in this trend."
                : "No products match this trend."}
            </p>
          ) : (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {products.map((p) => (
                  <ProductTile key={p.product_id} p={p} />
                ))}
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
                        : "bg-stone-900 text-white hover:bg-stone-700"
                    )}
                  >
                    {loadingMore
                      ? "Loading…"
                      : `Load ${Math.min(PAGE_SIZE, (total ?? 0) - products.length)} more`}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function ProductTile({ p }: { p: TrendExample }) {
  const symbol = CURRENCIES[p.currency] || p.currency;
  return (
    <a
      href={p.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex flex-col bg-white rounded-lg border border-stone-200 overflow-hidden hover:shadow-md hover:border-stone-300 transition-all"
    >
      <div className="relative aspect-square bg-stone-100 overflow-hidden">
        {p.primary_image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={p.primary_image_url}
            alt={p.name}
            className="w-full h-full object-cover group-hover:scale-[1.02] transition-transform"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-stone-300 text-3xl">
            ⌂
          </div>
        )}
        {p.is_best_seller && (
          <span
            className={clsx(
              "absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded-full",
              "text-[10px] font-semibold bg-amber-100 text-amber-800 border border-amber-200"
            )}
          >
            ★ Best Seller
          </span>
        )}
      </div>
      <div className="p-2 flex flex-col gap-0.5">
        <p className="text-[11px] text-stone-400 truncate">{p.retailer_name}</p>
        <p className="text-xs text-stone-800 line-clamp-2 leading-tight">{p.name}</p>
        {p.price != null && (
          <p className="text-xs font-semibold text-stone-900 mt-0.5">
            {symbol}
            {p.price.toFixed(2)}
          </p>
        )}
      </div>
    </a>
  );
}
