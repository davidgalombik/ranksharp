"use client";
import { useState } from "react";
import RunFragranceAnalysisButton from "./RunFragranceAnalysisButton";
import TryAgainFragranceButton from "./TryAgainFragranceButton";

interface Props {
  initialHasAnalysis: boolean;
  // Current segment context ('luxury' / 'middle' / 'mass'). Default
  // button click routes to that tier; the ▾ dropdown offers 'all' or
  // any specific tier.
  segment?: string;
}

export default function FragranceActionButton({ initialHasAnalysis, segment }: Props) {
  const [hasAnalysis, setHasAnalysis] = useState(initialHasAnalysis);

  if (hasAnalysis) return <TryAgainFragranceButton segment={segment} />;
  return <RunFragranceAnalysisButton segment={segment} onSuccess={() => setHasAnalysis(true)} />;
}
