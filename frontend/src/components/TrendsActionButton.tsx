"use client";
import { useState } from "react";
import RunAnalysisButton from "./RunAnalysisButton";
import TryAgainTrendsButton from "./TryAgainTrendsButton";

interface Props {
  initialHasAnalysis: boolean;
}

export default function TrendsActionButton({ initialHasAnalysis }: Props) {
  const [hasAnalysis, setHasAnalysis] = useState(initialHasAnalysis);

  if (hasAnalysis) return <TryAgainTrendsButton />;
  return <RunAnalysisButton onSuccess={() => setHasAnalysis(true)} />;
}
