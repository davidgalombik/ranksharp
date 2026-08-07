"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const CURRENCIES: Record<string, string> = { USD: "$", AUD: "A$", GBP: "£", EUR: "€" };
const PAGE_SIZE = 48;

type Recommendation = {
  product_id: number;
  name: string;
  retailer_name: string | null;
  retailer_slug?: string | null;
  url: string;
  price: number | null;
  currency: string;
  primary_image_url: string | null;
  similarity: number;
  is_best_seller: boolean;
};

/**
 * "View all N recommended products" modal for an in-store trend.
 * Paginated fetch against the stored InStoreTrendRecommendation rows
 * for this trend — best-sellers first, image-gate applied.
 *
 * Same UX as TrendProductsModal (Online) and FragranceTrendProductsModal.
 */
export default function InStoreRecommendationsModal({
  trendId,
  trendName,
  trendCategory,
  onClose,
}: {
  trendId: number;
  trendName: string;
  trendCategory: string;
  onClose: () => void;
}) {
  const [items, setItems] = useState<Recommendation[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [onlyBestSellers, setOnlyBestSellers] = useState(false);

  const loadFirstPage = useCallback(async () => {
    setLoading(true);
    setOffset(0);
    try {
      const qs = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: "0",
        ...(onlyBestSellers ? { only_best_sellers: "true" } : {}),
      });
      const res = await fetch(
        `${API_BASE}/api/instore-trends/trend/${trendId}/recommendations?${qs}`,
      );
      const data = await res.json();
      setItems(data.items || []);
      setTotal(data.total ?? 0);
    } catch {
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [trendId, onlyBestSellers]);

  useEffect(() => { loadFirstPage(); }, [loadFirstPage]);

  async function loadMore() {
    if (loadingMore || total === null || items.length >= total) return;
    setLoadingMore(true);
    const next = offset + PAGE_SIZE;
    try {
      const qs = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(next),
        ...(onlyBestSellers ? { only_best_sellers: "true" } : {}),
      });
      const res = await fetch(
        `${API_BASE}/api/instore-trends/trend/${trendId}/recommendations?${qs}`,
      );
      const data = await res.json();
      setItems((prev) => [...prev, ...(data.items || [])]);
      setOffset(next);
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const showLoadMore = total !== null && items.length < total && !loading;

  return (
    <div
      className="fixed inset-0 z-50 bg-stone-900/60 flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`${trendName} recommended products`}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-6xl max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between p-6 border-b border-stone-200 gap-4">
          <div className="min-w-0">
            <p className="text-xs text-stone-400 uppercase tracking-wider">
              {trendCategory} in-store trend
            </p>
            <h2 className="text-xl font-bold text-stone-900 mt-0.5">{trendName}</h2>
            <p className="text-sm text-stone-500 mt-1">
              {loading
                ? "Loading recommended products…"
                : total === null
                  ? ""
                  : `Showing ${items.length.toLocaleString()} of ${total.toLocaleString()} recommended online products for this in-store trend`}
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
              Note: not every retailer publishes best-seller flags.
            </p>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <p className="text-center text-stone-400 py-16">Loading products…</p>
          ) : items.length === 0 ? (
            <p className="text-center text-stone-400 py-16">
              {onlyBestSellers
                ? "No best-seller recommendations for this trend."
                : "No recommendations for this trend."}
            </p>
          ) : (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {items.map((r) => (
                  <ProductTile key={r.product_id} r={r} />
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
                        : "bg-stone-900 text-white hover:bg-stone-700",
                    )}
                  >
                    {loadingMore
                      ? "Loading…"
                      : `Load ${Math.min(PAGE_SIZE, (total ?? 0) - items.length)} more`}
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

function ProductTile({ r }: { r: Recommendation }) {
  const symbol = CURRENCIES[r.currency] ?? "$";
  return (
    <a
      href={r.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group block bg-white border border-stone-200 hover:border-amber-300 rounded-lg overflow-hidden transition-colors"
    >
      <div className="aspect-square bg-stone-100 relative">
        {r.primary_image_url ? (
          <img
            src={r.primary_image_url}
            alt={r.name}
            className="w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-stone-300 text-3xl">
            ⌂
          </div>
        )}
        {r.is_best_seller && (
          <span
            className={clsx(
              "absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded-full",
              "text-[10px] font-semibold bg-amber-100 text-amber-800 border border-amber-200",
            )}
          >
            ★ Best Seller
          </span>
        )}
        <span className="absolute bottom-1 right-1 text-[10px] font-semibold bg-white/85 text-stone-700 px-1 rounded">
          {(r.similarity * 100).toFixed(0)}%
        </span>
      </div>
      <div className="p-2 flex flex-col gap-0.5">
        <p className="text-[11px] text-stone-400 truncate">{r.retailer_name ?? "—"}</p>
        <p className="text-xs text-stone-800 line-clamp-2 leading-tight">{r.name}</p>
        {r.price != null && (
          <p className="text-xs font-semibold text-stone-900 mt-0.5">
            {symbol}{r.price.toFixed(2)}
          </p>
        )}
      </div>
    </a>
  );
}
