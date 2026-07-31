"use client";

import { useState } from "react";
import type { Trend } from "@/lib/api";
import TrendProductsModal from "./TrendProductsModal";

/**
 * Small button that lives inside a TrendCard and, when clicked, opens
 * TrendProductsModal for that trend. Extracted into its own client
 * component so TrendCard itself can stay server-rendered.
 */
export default function ViewAllProductsButton({ trend }: { trend: Trend }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={(e) => {
          // TrendCard wraps content in a <Link>; without this, clicking
          // the button navigates to the detail page instead of opening
          // the modal.
          e.preventDefault();
          e.stopPropagation();
          setOpen(true);
        }}
        className="mt-2 text-xs font-medium text-stone-600 hover:text-stone-900 underline underline-offset-2 decoration-stone-300 hover:decoration-stone-700"
      >
        View all {trend.product_count.toLocaleString()} products →
      </button>
      {open && <TrendProductsModal trend={trend} onClose={() => setOpen(false)} />}
    </>
  );
}
