import type { MeasurementResult } from '../types';
import { comparisonSourceMatchesResult } from '../lib/resultComparisonSource';
import type { ResultComparisonSource } from '../lib/resultComparisonSource';

export default function ResultCompareButton({ result, source, onCompareResult }: {
  result?: MeasurementResult;
  source?: ResultComparisonSource;
  onCompareResult?: (source: ResultComparisonSource) => void;
}) {
  if (!source || !onCompareResult || !comparisonSourceMatchesResult(source, result)) return null;
  return <button type="button" className="secondary" onClick={() => onCompareResult({ ...source })}>与仿真对照</button>;
}
