"use client";

import { useState } from "react";
import type { FragranceTrend } from "@/lib/api";
import FragranceTrendProductsModal from "./FragranceTrendProductsModal";

/**
 * "View all N products" button, extracted as a tiny client component so
 * the fragrance trends page stays a server component. Click stops
 * propagation so the outer <Link> that wraps the card doesn't navigate.
 *
 * Only rendered when the trend has filter_keywords (new-shape trends).
 * Legacy trends without keywords hide the button entirely — the modal
 * would fall back to stored examples, but on legacy data the total count
 * is meaningless so we just skip it.
 */
export default function FragranceTrendViewAllButton({ trend }: { trend: FragranceTrend }) {
  const [open, setOpen] = useState(false);
  const count = trend.matching_product_count;
  const hasKeywords = (trend.filter_keywords || []).length > 0;
  if (!hasKeywords || count == null || count === 0) return null;
  return (
    <>
      <button
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen(true);
        }}
        className="w-full text-xs font-medium text-amber-800 bg-amber-50 hover:bg-amber-100 border border-amber-200 rounded-lg px-3 py-2 transition-colors"
      >
        View all {count.toLocaleString()} products →
      </button>
      {open && (
        <FragranceTrendProductsModal trend={trend} onClose={() => setOpen(false)} />
      )}
    </>
  );
}
