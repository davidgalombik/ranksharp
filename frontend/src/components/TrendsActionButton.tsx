"use client";
import { useState } from "react";
import RunAnalysisButton from "./RunAnalysisButton";
import TryAgainTrendsButton from "./TryAgainTrendsButton";

interface Props {
  initialHasAnalysis: boolean;
  // Current segment context ('luxury' / 'middle' / 'mass'). Default
  // button click routes to that tier; the ▾ dropdown offers 'all' or
  // any specific tier.
  segment?: string;
}

export default function TrendsActionButton({ initialHasAnalysis, segment }: Props) {
  const [hasAnalysis, setHasAnalysis] = useState(initialHasAnalysis);

  if (hasAnalysis) return <TryAgainTrendsButton segment={segment} />;
  return <RunAnalysisButton onSuccess={() => setHasAnalysis(true)} />;
}
