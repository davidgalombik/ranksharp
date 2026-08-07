"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { useMemo, useEffect } from "react";

// Matches the categories the fragrance engine emits. Kept aligned with
// FRAGRANCE_LENSES on the backend so no dropdown option filters to
// an empty page.
const CATEGORIES = ["aesthetic", "scent", "market", "sustainability", "retail"];

/**
 * Category + Value cascading filter for the Fragrance trends page.
 * Matches the Product Trends filter UX: pick a category, then pick a
 * specific trend name within that category. State lives in the URL
 * (?category=...&value=...) so links share cleanly and the server can
 * pre-filter the trend list before rendering.
 *
 * Value dropdown options are supplied by the parent (server component)
 * as `valuesInCategory` — the parent scopes them to the currently-
 * selected category so we don't re-render on every keystroke.
 */
export default function FragranceTrendFilters({
  valuesInCategory,
}: {
  valuesInCategory: string[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const category = searchParams.get("category") || "";
  const value = searchParams.get("value") || "";

  // If the category changes and the current value isn't in the new
  // category's options, clear it. Prevents showing "Sage Green" as the
  // active filter after switching from Material to Scent.
  useEffect(() => {
    if (value && !valuesInCategory.includes(value)) {
      const params = new URLSearchParams(searchParams.toString());
      params.delete("value");
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    }
  }, [value, valuesInCategory, pathname, router, searchParams]);

  const setParam = (key: string, val: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (val) params.set(key, val); else params.delete(key);
    // Category change clears value — user starts fresh in the new lens.
    if (key === "category") params.delete("value");
    router.push(`${pathname}?${params.toString()}`, { scroll: false });
  };

  const sortedValues = useMemo(
    () => [...valuesInCategory].sort((a, b) => a.localeCompare(b)),
    [valuesInCategory],
  );

  return (
    <div className="flex flex-wrap gap-3 items-end">
      <div>
        <label className="block text-xs font-medium text-stone-500 mb-1">Category</label>
        <select
          value={category}
          onChange={(e) => setParam("category", e.target.value)}
          className="border border-stone-300 rounded-lg px-3 py-1.5 text-sm bg-white"
        >
          <option value="">All categories</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
          ))}
        </select>
      </div>
      {category && sortedValues.length > 0 && (
        <div>
          <label className="block text-xs font-medium text-stone-500 mb-1">
            {category.charAt(0).toUpperCase() + category.slice(1)}
          </label>
          <select
            value={value}
            onChange={(e) => setParam("value", e.target.value)}
            className="border border-stone-300 rounded-lg px-3 py-1.5 text-sm bg-white"
          >
            <option value="">All {category}s</option>
            {sortedValues.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}
