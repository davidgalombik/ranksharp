import { Suspense } from "react";
import TrendsClient from "./TrendsClient";

// Server wrapper. The actual filter + render logic lives in TrendsClient
// (client component) so filters can auto-apply without a form submit.
// Next.js 14 requires client components that call useSearchParams() to be
// wrapped in a Suspense boundary, otherwise the build fails during static
// prerender ("useSearchParams() should be wrapped in a suspense boundary").
export default function TrendsPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-stone-400">Loading trends…</div>}>
      <TrendsClient />
    </Suspense>
  );
}
