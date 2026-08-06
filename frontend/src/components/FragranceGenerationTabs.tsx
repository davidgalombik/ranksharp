"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import clsx from "clsx";
import { api } from "@/lib/api";

/**
 * Client-side tab bar for the fragrance run's set switcher. Each tab is
 * a compound control: click the label to switch, click the × to hard-delete
 * that Set (with confirm) via the delete-generation endpoint.
 *
 * Delete flow:
 *   - Backend refuses (409) if the delete would leave the run with zero
 *     trends. We also client-side-refuse when only one tab is visible so
 *     the button never even shows the confirm in that state.
 *   - If the ACTIVE set was deleted, we navigate to the highest remaining
 *     set the server returned. Otherwise we router.refresh() so the
 *     server-rendered page re-fetches the run's new state.
 */
export default function FragranceGenerationTabs({
  runId,
  generationList,
  activeGen,
  latestGeneration,
}: {
  runId: number;
  generationList: number[];
  activeGen: number;
  latestGeneration: number;
}) {
  const router = useRouter();

  async function handleDelete(gen: number) {
    if (generationList.length <= 1) {
      alert("Can't delete the only remaining set — run a new analysis first, or clear the whole run.");
      return;
    }
    if (!confirm(
      `Delete Set ${gen}? This removes every trend in that set. Can't be undone.`
    )) return;
    try {
      const result = await api.fragranceTrends.deleteGeneration(runId, gen);
      // If the deleted tab was the active one, switch to the highest remaining
      if (activeGen === gen) {
        const nextGen =
          result.remaining_generations[result.remaining_generations.length - 1]
          ?? latestGeneration;
        router.push(`?generation=${nextGen}`);
      } else {
        // Same tab still valid — re-fetch the server component so the
        // deleted tab disappears from the bar
        router.refresh();
      }
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Delete failed");
    }
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs text-stone-400 font-medium">Set:</span>
      {generationList.map((gen) => {
        const active = activeGen === gen;
        return (
          <div
            key={gen}
            className={clsx(
              "inline-flex items-stretch rounded-lg overflow-hidden border transition-colors",
              active
                ? "border-stone-900 bg-stone-900 text-white"
                : "border-stone-200 bg-white text-stone-600 hover:border-stone-400"
            )}
          >
            <Link href={`?generation=${gen}`} className="px-3 py-1 text-xs font-medium">
              {gen === latestGeneration ? `Set ${gen} ✨` : `Set ${gen}`}
            </Link>
            <button
              onClick={() => handleDelete(gen)}
              title={`Delete Set ${gen}`}
              aria-label={`Delete Set ${gen}`}
              className={clsx(
                "px-1.5 text-xs border-l transition-colors",
                active
                  ? "border-stone-700 hover:bg-stone-800"
                  : "border-stone-200 text-stone-400 hover:bg-red-50 hover:text-red-600"
              )}
            >
              ×
            </button>
          </div>
        );
      })}
    </div>
  );
}
