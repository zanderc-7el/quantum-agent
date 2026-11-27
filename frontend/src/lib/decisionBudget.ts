import type { SelectionVerificationBudgetLookahead } from '../types';

const bases = new Set(['same_sampling_counts_after_calibration_only', 'arclight_single_setting_same_shots_captures_blocks']);
const count = (value: unknown, zero = false): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= (zero ? 0 : 1);

/** Only present the controller's recorded v1 lookahead. Never derive a new budget or reservation. */
export function decisionBudgetRows(value: unknown): { label: string; value: string }[] | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const record = value as Partial<SelectionVerificationBudgetLookahead>;
  const unlimited = record.budgetMode === 'accounting_only' && record.remainingShotsAtCheck === null;
  if (record.version !== 'selection-verification-budget/1' || record.reservation !== false
      || record.candidateKnown !== false || record.actualVerificationPlanRechecked !== true
      || !bases.has(record.verificationBasis ?? '')
      || !count(record.selectionShots) || !count(record.verificationShotsUpperBound)
      || !count(record.jointShots) || (!unlimited && !count(record.remainingShotsAtCheck, true))
      || record.selectionShots + record.verificationShotsUpperBound !== record.jointShots
      || (typeof record.remainingShotsAtCheck === 'number' && record.jointShots > record.remainingShotsAtCheck)) return null;
  const rows: [string, number][] = [
    ['首测', record.selectionShots],
    ['独立复测上限', record.verificationShotsUpperBound],
    ['首测与独立复测合计', record.jointShots],
  ];
  if (typeof record.remainingShotsAtCheck === 'number') rows.push(['核算时剩余额度', record.remainingShotsAtCheck]);
  return rows.map(([label, amount]) => ({ label, value: amount.toLocaleString('en-US') }));
}
