/** Keep a narrow scan around a large carrier distinguishable on its axis. */
export function formatAxisTick(value: number, span: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  const magnitude = Math.abs(value);
  const width = Number.isFinite(span) && span !== 0 ? Math.abs(span) : magnitude;
  const significant = Math.min(15, Math.max(5, Math.ceil(Math.log10(magnitude / width)) + 3));
  if (magnitude >= 1e6 || magnitude < 0.001) return value.toExponential(significant - 1);
  return Number(value.toPrecision(significant)).toString();
}
