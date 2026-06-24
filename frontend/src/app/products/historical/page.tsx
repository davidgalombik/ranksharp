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
  subcategory: string | null;
  product_segment: string | null;
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
  has_patent: boolean;
  is_new: boolean;
  is_active: boolean;
  last_seen_at: string;
}

const SEASONS = ["spring", "summer", "autumn", "winter", "all-season"];
const ROOMS = ["kitchen", "living room", "bedroom", "bathroom", "dining room", "office", "outdoor", "multiple"];
const CURRENCIES: Record<string, string> = { USD: "$", AUD: "A$", GBP: "£", EUR: "€" };

// Mirrors COUNTRY_BUCKETS in backend/api/routes/products.py.
const COUNTRY_OPTIONS: Array<{ key: string; label: string }> = [
  { key: "AU", label: "Australia" },
  { key: "US", label: "USA" },
  { key: "UK", label: "United Kingdom" },
  { key: "EU", label: "Europe" },
];

function retailerInBucket(retailerCountry: string, bucket: string): boolean {
  if (bucket === "AU") return retailerCountry === "AU";
  if (bucket === "US") return retailerCountry === "US";
  if (bucket === "UK") return retailerCountry === "GB";
  if (bucket === "EU") return !["AU", "US", "GB"].includes(retailerCountry);
  return true;
}

function ProductCard({ product }: { product: Product }) {
  const symbol = CURRENCIES[product.currency] || product.currency;
  return (
    <a
      href={product.url}
      target="_blank"
      rel="noopener noreferrer"
      className={clsx(
        "group bg-white rounded-xl border overflow-hidden hover:shadow-md transition-shadow flex flex-col",
        product.is_active ? "border-stone-200" : "border-stone-200 opacity-60"
      )}
    >
      {/* Image */}
      <div className="relative aspect-square bg-stone-100 overflow-hidden">
        {product.primary_image_url ? (
          <img
            src={product.primary_image_url}
            alt={product.name}
            className={clsx(
              "w-full h-full object-cover group-hover:scale-105 transition-transform duration-300",
              !product.is_active && "grayscale"
            )}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-stone-300 text-4xl">⌂</div>
        )}
        <div className="absolute top-2 left-2 flex flex-col gap-1">
          {!product.is_active && (
            <span className="px-2 py-0.5 bg-stone-700 text-white rounded-full text-xs font-semibold shadow-sm">
              No longer listed
            </span>
          )}
          {product.is_best_seller && (
            <span className="px-2 py-0.5 bg-amber-400 text-amber-900 rounded-full text-xs font-semibold shadow-sm">
              ★ Best Seller
            </span>
          )}
          {product.has_patent && (
            <span className="px-2 py-0.5 bg-sky-100 text-sky-800 rounded-full text-xs font-semibold shadow-sm border border-sky-200">
              ⚙ Patented
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
        {product.colours.length > 0 && (
          <div className="flex gap-1 flex-wrap">
            {product.colours.slice(0, 4).map((c) => (
              <span key={c} className="text-xs text-stone-500 bg-stone-50 px-1.5 py-0.5 rounded">{c}</span>
            ))}
          </div>
        )}
        <p className="text-xs text-stone-400">
          Last seen: {new Date(product.last_seen_at).toLocaleDateString()}
        </p>
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

export default function HistoricalProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 48;

  // Filters — mirrors Online Products plus a "No longer listed" toggle
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [country, setCountry] = useState("");
  const [retailer, setRetailer] = useState("");
  const [category, setCategory] = useState("");
  const [subcategory, setSubcategory] = useState("");
  const [productSegment, setProductSegment] = useState("");
  const [season, setSeason] = useState("");
  const [room, setRoom] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [bestSellerOnly, setBestSellerOnly] = useState(false);
  const [patentOnly, setPatentOnly] = useState(false);
  const [newOnly, setNewOnly] = useState(false);
  const [inactiveOnly, setInactiveOnly] = useState(false);

  const [retailers, setRetailers] = useState<{ slug: string; name: string; country: string }[]>([]);
  const [availableCategories, setAvailableCategories] = useState<string[]>([]);
  const [taxonomy, setTaxonomy] = useState<{
    has_catalog: boolean;
    tree: {
      category: string;
      category_slug: string;
      subcategories: {
        label: string;
        slug: string;
        product_segments: { label: string; slug: string }[];
      }[];
    }[];
  } | null>(null);
  const [facets, setFacets] = useState<{
    categories: Record<string, number>;
    subcategories: Record<string, number>;
    product_segments: Record<string, number>;
    seasons: Record<string, number>;
    rooms: Record<string, number>;
    best_seller: number;
    has_patent: number;
    is_new: number;
    inactive: number;
  } | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 400);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    fetch(`${API_BASE}/api/retailers/`)
      .then((r) => r.json())
      .then((d) => setRetailers(d))
      .catch(() => {});
  }, []);

  // Load taxonomy + legacy category list when retailer changes; reset cascade
  useEffect(() => {
    setCategory("");
    setSubcategory("");
    setProductSegment("");
    if (!retailer) {
      setAvailableCategories([]);
      setTaxonomy(null);
      return;
    }
    fetch(`${API_BASE}/api/retailers/${retailer}/taxonomy`)
      .then((r) => r.json())
      .then((t) => setTaxonomy(t))
      .catch(() => setTaxonomy(null));
    fetch(`${API_BASE}/api/retailers/${retailer}/categories`)
      .then((r) => r.json())
      .then((cats: string[]) => setAvailableCategories(cats))
      .catch(() => setAvailableCategories([]));
  }, [retailer]);

  // Cascading clears: changing a level clears every deeper level
  useEffect(() => {
    setSubcategory("");
    setProductSegment("");
  }, [category]);
  useEffect(() => {
    setProductSegment("");
  }, [subcategory]);

  // Country change resets the retailer (dropdown narrows to that country)
  useEffect(() => {
    setRetailer("");
  }, [country]);

  const fetchProducts = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams();
    if (debouncedSearch) params.set("q", debouncedSearch);
    if (country) params.set("country", country);
    if (retailer) params.set("retailer", retailer);
    if (category) params.set("category", category);
    if (subcategory) params.set("subcategory", subcategory);
    if (productSegment) params.set("product_segment", productSegment);
    if (season) params.set("season", season);
    if (room) params.set("room", room);
    if (minPrice) params.set("min_price", minPrice);
    if (maxPrice) params.set("max_price", maxPrice);
    if (bestSellerOnly) params.set("best_seller", "true");
    if (patentOnly) params.set("has_patent", "true");
    if (newOnly) params.set("is_new", "true");
    if (inactiveOnly) params.set("inactive_only", "true");
    params.set("limit", String(PAGE_SIZE));
    params.set("offset", String(page * PAGE_SIZE));

    try {
      const res = await fetch(`${API_BASE}/api/products/historical?${params}`);
      const json = await res.json();
      setProducts(json.items ?? []);
      setTotal(json.total ?? null);
    } catch {
      setProducts([]);
      setTotal(null);
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, country, retailer, category, subcategory, productSegment, season, room, minPrice, maxPrice, bestSellerOnly, patentOnly, newOnly, inactiveOnly, page]);

  useEffect(() => { setPage(0); }, [debouncedSearch, country, retailer, category, subcategory, productSegment, season, room, minPrice, maxPrice, bestSellerOnly, patentOnly, newOnly, inactiveOnly]);
  useEffect(() => { fetchProducts(); }, [fetchProducts]);

  // Fetch facet counts so zero-reach options/toggles can be hidden.
  useEffect(() => {
    const params = new URLSearchParams();
    if (debouncedSearch) params.set("q", debouncedSearch);
    if (country) params.set("country", country);
    if (retailer) params.set("retailer", retailer);
    if (category) params.set("category", category);
    if (subcategory) params.set("subcategory", subcategory);
    if (productSegment) params.set("product_segment", productSegment);
    if (season) params.set("season", season);
    if (room) params.set("room", room);
    if (minPrice) params.set("min_price", minPrice);
    if (maxPrice) params.set("max_price", maxPrice);
    if (bestSellerOnly) params.set("best_seller", "true");
    if (patentOnly) params.set("has_patent", "true");
    if (newOnly) params.set("is_new", "true");
    if (inactiveOnly) params.set("inactive_only", "true");
    fetch(`${API_BASE}/api/products/historical/facets?${params}`)
      .then((r) => r.json())
      .then((f) => setFacets(f))
      .catch(() => setFacets(null));
  }, [debouncedSearch, country, retailer, category, subcategory, productSegment, season, room, minPrice, maxPrice, bestSellerOnly, patentOnly, newOnly, inactiveOnly]);

  const hasFilters = debouncedSearch || country || retailer || category || subcategory || productSegment || season || room || minPrice || maxPrice || bestSellerOnly || patentOnly || newOnly || inactiveOnly;
  const clearAll = () => {
    setSearch(""); setCountry(""); setRetailer(""); setCategory(""); setSubcategory(""); setProductSegment("");
    setSeason(""); setRoom(""); setMinPrice(""); setMaxPrice("");
    setBestSellerOnly(false); setPatentOnly(false); setNewOnly(false); setInactiveOnly(false);
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-stone-900">Historical Products</h1>
          <p className="text-sm text-stone-500 mt-0.5">All products ever scraped or uploaded, including those no longer listed</p>
        </div>
        <p className="text-sm text-stone-500">
          {loading ? "Loading…" : (
            total !== null
              ? `Showing ${page * PAGE_SIZE + 1}–${Math.min(page * PAGE_SIZE + products.length, total)} of ${total.toLocaleString()} products`
              : `${products.length} shown`
          )}
        </p>
      </div>

      {/* Filter bar — mirrors Online Products */}
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

          {/* Country */}
          <select
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
          >
            <option value="">All countries</option>
            {COUNTRY_OPTIONS.map((c) => (
              <option key={c.key} value={c.key}>{c.label}</option>
            ))}
          </select>

          {/* Retailer — narrows to selected country when set */}
          <select
            value={retailer}
            onChange={(e) => setRetailer(e.target.value)}
            className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
          >
            <option value="">All retailers</option>
            {retailers
              .filter((r) => !country || retailerInBucket(r.country, country))
              .map((r) => (
                <option key={r.slug} value={r.slug}>{r.name}</option>
              ))}
          </select>

          {/* Category + Subcategory + Product Segment — cascading. Uses the
              retailer's taxonomy catalog when available, falls back to legacy
              single dropdown. Same logic as Online Products. */}
          {taxonomy?.has_catalog ? (
            <>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
              >
                <option value="">All categories</option>
                {taxonomy.tree
                  .filter((node) => !facets || node.category === category || (facets.categories[node.category] ?? 0) > 0)
                  .map((node) => (
                    <option key={node.category_slug} value={node.category}>{node.category}</option>
                  ))}
              </select>
              {category && (() => {
                const node = taxonomy.tree.find((n) => n.category === category);
                const subs = (node?.subcategories ?? [])
                  .filter((s) => !facets || s.label === subcategory || (facets.subcategories[s.label] ?? 0) > 0);
                if (subs.length === 0) return null;
                return (
                  <select
                    value={subcategory}
                    onChange={(e) => setSubcategory(e.target.value)}
                    className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
                  >
                    <option value="">All subcategories</option>
                    {subs.map((s) => (
                      <option key={s.slug} value={s.label}>{s.label}</option>
                    ))}
                  </select>
                );
              })()}
              {category && subcategory && (() => {
                const catNode = taxonomy.tree.find((n) => n.category === category);
                const subNode = catNode?.subcategories.find((s) => s.label === subcategory);
                const segs = (subNode?.product_segments ?? [])
                  .filter((s) => !facets || s.label === productSegment || (facets.product_segments[s.label] ?? 0) > 0);
                if (segs.length === 0) return null;
                return (
                  <select
                    value={productSegment}
                    onChange={(e) => setProductSegment(e.target.value)}
                    className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
                  >
                    <option value="">All product segments</option>
                    {segs.map((s) => (
                      <option key={s.slug} value={s.label}>{s.label}</option>
                    ))}
                  </select>
                );
              })()}
            </>
          ) : (
            availableCategories.length > 1 && (
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
              >
                <option value="">All categories</option>
                {availableCategories
                  .filter((c) => !facets || c === category || (facets.categories[c] ?? 0) > 0)
                  .map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
              </select>
            )
          )}

          {/* Season */}
          <select
            value={season}
            onChange={(e) => setSeason(e.target.value)}
            className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
          >
            <option value="">All seasons</option>
            {SEASONS
              .filter((s) => !facets || s === season || (facets.seasons[s] ?? 0) > 0)
              .map((s) => (
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
            {ROOMS
              .filter((r) => !facets || r === room || (facets.rooms[r] ?? 0) > 0)
              .map((r) => (
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

          {/* Best Sellers toggle */}
          {(!facets || bestSellerOnly || facets.best_seller > 0) && (
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
          )}

          {/* Patent toggle */}
          {(!facets || patentOnly || facets.has_patent > 0) && (
            <button
              onClick={() => setPatentOnly((v) => !v)}
              className={clsx(
                "flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm font-medium transition-colors",
                patentOnly
                  ? "bg-sky-100 border-sky-300 text-sky-800"
                  : "bg-white border-stone-200 text-stone-600 hover:border-sky-300 hover:text-sky-700"
              )}
            >
              <span>⚙</span>
              <span>Patent</span>
            </button>
          )}

          {/* New toggle */}
          {(!facets || newOnly || facets.is_new > 0) && (
            <button
              onClick={() => setNewOnly((v) => !v)}
              className={clsx(
                "flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm font-medium transition-colors",
                newOnly
                  ? "bg-emerald-500 border-emerald-500 text-white"
                  : "bg-white border-stone-200 text-stone-600 hover:border-emerald-300 hover:text-emerald-700"
              )}
            >
              <span>✦</span>
              <span>New</span>
            </button>
          )}

          {/* No longer listed toggle — Historical-only quick filter for inactive */}
          {(!facets || inactiveOnly || facets.inactive > 0) && (
            <button
              onClick={() => setInactiveOnly((v) => !v)}
              className={clsx(
                "flex items-center gap-1.5 px-3 py-2 rounded-lg border text-sm font-medium transition-colors",
                inactiveOnly
                  ? "bg-stone-700 border-stone-700 text-white"
                  : "bg-white border-stone-200 text-stone-600 hover:border-stone-400 hover:text-stone-900"
              )}
            >
              <span>⊘</span>
              <span>No longer listed</span>
            </button>
          )}
        </div>

        {hasFilters && (
          <button
            onClick={clearAll}
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
          <div className="flex items-center justify-center gap-3 pt-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-4 py-2 rounded-lg border border-stone-200 text-sm font-medium disabled:opacity-40 hover:bg-stone-50 transition-colors"
            >
              ← Previous
            </button>
            <span className="text-sm text-stone-500">
              Page {page + 1}{total !== null ? ` of ${Math.ceil(total / PAGE_SIZE).toLocaleString()}` : ""}
            </span>
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
          <p className="font-medium">No products found</p>
          {hasFilters && (
            <button onClick={clearAll} className="mt-2 text-sm text-stone-600 underline hover:text-stone-900">
              Clear filters
            </button>
          )}
        </div>
      )}
    </div>
  );
}
