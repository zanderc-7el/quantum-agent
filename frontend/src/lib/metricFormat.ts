/**
 * Display formatting shared by every view that shows a backend reference.
 *
 * The backend stores fidelity as a 0..1 ratio and time metrics in their native
 * unit. The comparison cards, the Skill/audit settings table and the audit
 * timeline must agree on both unit and precision, so those rules live here
 * instead of being re-derived per component.
 *
 * The module is pure and has no imports: `metricComparison.ts` builds on it,
 * and the read-only remote views (`remoteInsights.ts`) can use it without
 * pulling in the measurement pipeline.
 */

export interface WireTargetLike {
  metric: string;
  ideal: number;
  unit: string;
  direction: string;
  tolerance?: number | null;
}

export interface WireCriterionLike {
  metric: string;
  comparator: string;
  threshold: number;
  unit: string;
}

/**
 * Convert a backend value into the display unit. Fidelity is stored as a
 * 0..1 ratio and shown as a percentage; every other unit is shown as-is. An
 * incompatible unit yields `null`, which the caller treats as "this reference
 * cannot be shown" rather than guessing.
 */
export function displayValue(
  value: number,
  from: string,
  to: string,
): number | null {
  if (!Number.isFinite(value)) return null;
  if (from === to) return value;
  if (to === "%" && (from === "ratio" || from === "")) {
    return value <= 1.5 ? value * 100 : value;
  }
  return null;
}

/** Percent for a ratio wire unit; everything else is already a display unit. */
export function wireUnit(unit: string): string {
  return unit === "ratio" ? "%" : unit;
}

/**
 * Display precision by metric family, matching the parameter panel:
 * T1 keeps one decimal, fidelity and T2 keep two.
 */
export function wireDecimals(metric: string): number {
  return metric.endsWith("t1Us") ? 1 : 2;
}

export function formatMetricValue(
  value: number | null,
  unit: string,
  decimals = 2,
): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const text = value.toFixed(decimals);
  return unit === "%" || unit === "" ? `${text}${unit}` : `${text} ${unit}`;
}

const COMPARATOR_SYMBOLS: Readonly<Record<string, string>> = Object.freeze({
  ">=": "≥",
  "<=": "≤",
  ">": ">",
  "<": "<",
  "==": "=",
});

/** Backend comparators are ASCII (`>=`); the UI uses typographic symbols. */
export function formatComparator(comparator: string): string {
  return COMPARATOR_SYMBOLS[comparator] ?? comparator;
}

/** A recorded measurement in its display unit and precision. */
export function formatWireValue(
  value: number | null,
  metric: string,
  unit: string,
): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const displayUnit = wireUnit(unit);
  const converted = displayValue(value, unit, displayUnit);
  return formatMetricValue(converted, displayUnit, wireDecimals(metric));
}

/** A design target, e.g. `≥ 99.50%` or `≈ 4.00 GHz ± 0.05 GHz`. */
export function formatWireTarget(target: WireTargetLike): string {
  const unit = wireUnit(target.unit);
  const decimals = wireDecimals(target.metric);
  const operator =
    target.direction === "lower"
      ? "≤"
      : target.direction === "band"
        ? "≈"
        : "≥";
  const ideal = displayValue(target.ideal, target.unit, unit);
  if (ideal === null) return `${operator} ${target.ideal} ${target.unit}`;
  const tolerance =
    target.tolerance === null || target.tolerance === undefined
      ? null
      : displayValue(target.tolerance, target.unit, unit);
  const suffix =
    target.direction === "band" && tolerance !== null
      ? ` ± ${formatMetricValue(tolerance, unit, decimals)}`
      : "";
  return `${operator} ${formatMetricValue(ideal, unit, decimals)}${suffix}`;
}

/** An acceptance floor, e.g. `≥ 98.00%`. */
export function formatWireCriterion(criterion: WireCriterionLike): string {
  const unit = wireUnit(criterion.unit);
  const decimals = wireDecimals(criterion.metric);
  const threshold = displayValue(criterion.threshold, criterion.unit, unit);
  const operator = formatComparator(criterion.comparator);
  return threshold === null
    ? `${operator} ${criterion.threshold} ${criterion.unit}`
    : `${operator} ${formatMetricValue(threshold, unit, decimals)}`;
}
