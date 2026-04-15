"use client";
import { useState } from "react";
import RunFragranceAnalysisButton from "./RunFragranceAnalysisButton";
import TryAgainFragranceButton from "./TryAgainFragranceButton";

interface Props {
  initialHasAnalysis: boolean;
}

export default function FragranceActionButton({ initialHasAnalysis }: Props) {
  const [hasAnalysis, setHasAnalysis] = useState(initialHasAnalysis);

  if (hasAnalysis) return <TryAgainFragranceButton />;
  return <RunFragranceAnalysisButton onSuccess={() => setHasAnalysis(true)} />;
}
