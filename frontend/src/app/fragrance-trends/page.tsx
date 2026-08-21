// The fragrance-trends page is fully client-driven since the 2026-08-22
// segmentation parity work — the URL owns segment + view + generation
// and the client fetches per-segment data. Server-rendering doesn't help
// here (there's no shared "latest" — each tier lands on its own latest
// report) and it lets pill/set clicks avoid full page navigations.
import FragranceTrendsClient from "./FragranceTrendsClient";

export default function FragranceTrendsPage() {
  return <FragranceTrendsClient />;
}
