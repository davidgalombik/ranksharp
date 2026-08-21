import { Suspense } from "react";
import FragranceTrendsClient from "./FragranceTrendsClient";

// Server wrapper. The actual filter + render logic lives in
// FragranceTrendsClient (client component) so tab / segment switches
// don't need full navigations. Next.js 14 requires client components
// that call useSearchParams() to be wrapped in a Suspense boundary,
// otherwise the build fails during static prerender
// ("useSearchParams() should be wrapped in a suspense boundary").
export default function FragranceTrendsPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-stone-400">Loading fragrance trends…</div>}>
      <FragranceTrendsClient />
    </Suspense>
  );
}
