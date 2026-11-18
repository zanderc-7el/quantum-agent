import type { Workflow } from '../types.ts';

export interface BudgetValues { maxTotalShots: number; maxAttemptsPerNode: number; maxWorkflowAttempts: number; }
export const BUDGET_LIMITS = { maxTotalShots: 100000000, maxAttemptsPerNode: 10, maxWorkflowAttempts: 200 } as const;
export function budgetEditError(values: BudgetValues, workflow: Workflow, reason: string): string | undefined {
  if (workflow.mode !== 'real' || !['draft', 'paused', 'attention'].includes(workflow.status)) return '预算只能在草稿、暂停或待处理检查点调整。';
  if (!reason.trim() || reason.length > 1000) return '请填写不超过 1000 字的调整原因。';
  let increased = false;
  for (const key of Object.keys(BUDGET_LIMITS) as (keyof BudgetValues)[]) {
    const old = workflow[key];
    if (typeof old !== 'number' || !Number.isSafeInteger(old)) return '后端预算信息尚未完整返回。';
    if (!Number.isSafeInteger(values[key]) || values[key] < old || values[key] > BUDGET_LIMITS[key]) return '预算必须为整数，只能提高且不能超过界面标注上限。';
    increased ||= values[key] > old;
  }
  if (!increased) return '至少提高一项预算；保持原值无需保存。';
}
