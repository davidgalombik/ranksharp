"use client";

import { useEffect, useState, useCallback } from "react";
import clsx from "clsx";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Product {
  id: number;
  retailer_name: string;
  retailer_slug: string;
  name: string;
  url: string;
  price: number | null;
  currency: string;
  category: string | null;
  primary_image_url: string | null;
  colours: string[];
  materials: string[];
  style_tags: string[];
  patterns: string[];
  shape: string | null;
  finish: string | null;
  season: string | null;
  room: string | null;
  is_best_seller: boolean;
  last_seen_at: string;
}

const SEASONS = ["spring", "summer", "autumn", "winter", "all-season"];
const ROOMS = ["kitchen", "living room", "bedroom", "bathroom", "dining room", "office", "outdoor"];
const CURRENCIES: Record<string, string> = { USD: "$", AUD: "A$", GBP: "£", EUR: "€" };

function ProductCard({ product }: { product: Product }) {
  const symbol = CURRENCIES[product.currency] || product.currency;
  return (
    <a
      href={product.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group bg-white rounded-xl border border-stone-200 overflow-hidden hover:shadow-md transition-shadow flex flex-col"
    >
      {/* Image */}
      <div className="relative aspect-square bg-stone-100 overflow-hidden">
        {product.primary_image_url ? (
          <img
            src={product.primary_image_url}
            alt={product.name}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-stone-300 text-4xl">⌂</div>
        )}
        <div className="absolute top-2 left-2 flex flex-col gap-1">
          {product.is_best_seller && (
            <span className="px-2 py-0.5 bg-amber-400 text-amber-900 rounded-full text-xs font-semibold shadow-sm">
              ★ Best Seller
            </span>
          )}
          {product.season && product.season !== "all-season" && (
            <span className="px-2 py-0.5 bg-white/90 backdrop-blur-sm rounded-full text-xs font-medium text-stone-600 capitalize">
              {product.season}
            </span>
          )}
        </div>
      </div>

      {/* Info */}
      <div className="p-3 flex flex-col gap-1.5 flex-1">
        <p className="text-xs font-medium text-stone-400 truncate">{product.retailer_name}</p>
        <p className="text-sm font-medium text-stone-900 line-clamp-2 leading-snug">{product.name}</p>

        {product.price != null && (
          <p className="text-sm font-semibold text-stone-800 mt-auto pt-1">
            {symbol}{product.price.toFixed(2)}
          </p>
        )}

        {/* Colour swatches */}
        {product.colours.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {product.colours.slice(0, 4).map((c) => (
              <span key={c} className="text-xs text-stone-500 bg-stone-50 px-1.5 py-0.5 rounded">{c}</span>
            ))}
          </div>
        )}

        {/* Materials */}
        {product.materials.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {product.materials.slice(0, 3).map((m) => (
              <span key={m} className="text-xs text-stone-400">{m}</span>
            ))}
          </div>
        )}
      </div>
    </a>
  );
}

function Skeleton() {
  return (
    <div className="bg-white rounded-xl border border-stone-200 overflow-hidden animate-pulse">
      <div className="aspect-square bg-stone-100" />
      <div className="p-3 space-y-2">
        <div className="h-3 bg-stone-100 rounded w-1/2" />
        <div className="h-4 bg-stone-100 rounded w-3/4" />
        <div className="h-4 bg-stone-100 rounded w-1/2" />
      </div>
    </div>
  );
}

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 48;

  // Filters
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [retailer, setRetailer] = useState("");
  const [season, setSeason] = useState("");
  const [room, setRoom] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [bestSellerOnly, setBestSellerOnly] = useState(false);
  const [retailers, setRetailers] = useState<{ slug: string; name: string }[]>([]);

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 400);
    return () => clearTimeout(t);
  }, [search]);

  // Load retailer list for filter dropdown
  useEffect(() => {
    fetch(`${API_BASE}/api/retailers/`)
      .then((r) => r.json())
      .then((d) => setRetailers(d.filter((r: any) => r.product_count > 0)))
      .catch(() => {});
  }, []);

  const fetchProducts = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams();
    if (debouncedSearch) params.set("q", debouncedSearch);
    if (retailer) params.set("retailer", retailer);
    if (season) params.set("season", season);
    if (room) params.set("room", room);
    if (minPrice) params.set("min_price", minPrice);
    if (maxPrice) params.set("max_price", maxPrice);
    if (bestSellerOnly) params.set("best_seller", "true");
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(page * PAGE_SIZE));

    try {
      const res = await fetch(`${API_BASE}/api/products/?${params}`);
      const data: Product[] = await res.json();
      setProducts(data);
      setTotal(data.length === PAGE_SIZE ? (page + 2) * PAGE_SIZE : page * PAGE_SIZE + data.length);
    } catch {
      setProducts([]);
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, retailer, season, room, minPrice, maxPrice, bestSellerOnly, page]);

  useEffect(() => {
    setPage(0);
  }, [debouncedSearch, retailer, season, room, minPrice, maxPrice, bestSellerOnly]);

  useEffect(() => {
    fetchProducts();
  }, [fetchProducts]);

  const hasFilters = debouncedSearch || retailer || season || room || minPrice || maxPrice || bestSellerOnly;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-stone-900">Products</h1>
        <p className="text-sm text-stone-500">
          {loading ? "Loading…" : `${products.length} shown`}
          {total > PAGE_SIZE && ` of ${total}+`}
        </p>
      </div>

      {/* Filter bar */}
      <div className="bg-white rounded-xl border border-stone-200 p-4">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 items-center">
          {/* Search */}
          <div className="col-span-2 sm:col-span-3 lg:col-span-2">
            <input
              type="search"
              placeholder="Search products…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full border border-stone-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-stone-300"
            />
          </div>

          {/* Retailer */}
          <select
            value={retailer}
            onChange={(e) => setRetailer(e.target.value)}
            className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
          >
            <option value="">All retailers</option>
            {retailers.map((r) => (
              <option key={r.slug} value={r.slug}>{r.name}</option>
            ))}
          </select>

          {/* Season */}
          <select
            value={season}
            onChange={(e) => setSeason(e.target.value)}
            className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
          >
            <option value="">All seasons</option>
            {SEASONS.map((s) => (
              <option key={s} value={s} className="capitalize">{s.charAt(0).toUpperCase() + s.slice(1)}</option>
            ))}
          </select>

          {/* Room */}
          <select
            value={room}
            onChange={(e) => setRoom(e.target.value)}
            className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
          >
            <option value="">All rooms</option>
            {ROOMS.map((r) => (
              <option key={r} value={r} className="capitalize">{r.charAt(0).toUpperCase() + r.slice(1)}</option>
            ))}
          </select>

          {/* Price range */}
          <div className="flex gap-1.5 items-center">
            <input
              type="number"
              placeholder="$ min"
              value={minPrice}
              onChange={(e) => setMinPrice(e.target.value)}
              className="w-full border border-stone-200 rounded-lg px-2 py-2 text-sm focus:outline-none"
            />
            <span className="text-stone-300 flex-shrink-0">–</span>
            <input
              type="number"
              placeholder="max"
              value={maxPrice}
              onChange={(e) => setMaxPrice(e.target.value)}
              className="w-full border border-stone-200 rounded-lg px-2 py-2 text-sm focus:outline-none"
            />
          </div>

          {/* Best Seller toggle */}
          <button
            onClick={() => setBestSellerOnly((v) => !v)}
            className={clsx(
              "flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm font-medium transition-colors",
              bestSellerOnly
                ? "bg-amber-400 border-amber-400 text-amber-900"
                : "bg-white border-stone-200 text-stone-600 hover:border-amber-300 hover:text-amber-700"
            )}
          >
            <span>★</span>
            <span>Best Sellers</span>
          </button>
        </div>

        {hasFilters && (
          <button
            onClick={() => { setSearch(""); setRetailer(""); setSeason(""); setRoom(""); setMinPrice(""); setMaxPrice(""); setBestSellerOnly(false); }}
            className="mt-2 text-xs text-stone-500 hover:text-stone-900 underline"
          >
            Clear all filters
          </button>
        )}
      </div>

      {/* Grid */}
      {loading ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-4">
          {Array.from({ length: 24 }).map((_, i) => <Skeleton key={i} />)}
        </div>
      ) : products.length > 0 ? (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-4">
            {products.map((p) => <ProductCard key={p.id} product={p} />)}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-center gap-3 pt-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-4 py-2 rounded-lg border border-stone-200 text-sm font-medium disabled:opacity-40 hover:bg-stone-50 transition-colors"
            >
              ← Previous
            </button>
            <span className="text-sm text-stone-500">Page {page + 1}</span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={products.length < PAGE_SIZE}
              className="px-4 py-2 rounded-lg border border-stone-200 text-sm font-medium disabled:opacity-40 hover:bg-stone-50 transition-colors"
            >
              Next →
            </button>
          </div>
        </>
      ) : (
        <div className="text-center py-24 text-stone-400">
          <p className="text-4xl mb-3">⌂</p>
          {hasFilters ? (
            <>
              <p className="font-medium">No products match your filters</p>
              <button
                onClick={() => { setSearch(""); setRetailer(""); setSeason(""); setRoom(""); setMinPrice(""); setMaxPrice(""); setBestSellerOnly(false); }}
                className="mt-2 text-sm text-stone-600 underline hover:text-stone-900"
              >
                Clear filters
              </button>
            </>
          ) : (
            <>
              <p className="font-medium">No products yet</p>
              <p className="text-sm mt-1">Go to <a href="/progress" className="underline text-stone-600">Scrape Progress</a> to start collecting products</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
