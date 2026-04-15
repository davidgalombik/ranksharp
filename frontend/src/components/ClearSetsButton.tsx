"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

interface Props {
  target: "trends" | "fragrance";
}

export default function ClearSetsButton({ target }: Props) {
  const router = useRouter();
  const [clearing, setClearing] = useState(false);

  async function handleClick() {
    if (!window.confirm("Clear all sets? This cannot be undone.")) return;
    setClearing(true);
    try {
      await (target === "trends" ? api.reports.clear() : api.fragranceTrends.clear());
      router.refresh();
    } finally {
      setClearing(false);
    }
  }

  return (
    <button
      onClick={handleClick}
      disabled={clearing}
      className="text-xs text-stone-400 hover:text-rose-500 transition-colors disabled:opacity-50 ml-2"
    >
      {clearing ? "Clearing…" : "Clear all sets"}
    </button>
  );
}
