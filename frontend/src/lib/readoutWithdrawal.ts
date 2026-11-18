import type { ReadoutRevalidation, ReadoutWithdrawalRequest, Workflow } from '../types.ts';

/** Only server-marked candidates at a stopped checkpoint can be withdrawn. */
export function readoutWithdrawalCandidates(workflow: Workflow): ReadoutRevalidation[] {
  if (workflow.mode !== 'real' || !['paused', 'attention'].includes(workflow.status)) return [];
  const markers = Object.values(workflow.readoutRevalidations ?? {}).filter(marker => marker
    && ['active', 'fixed_failed'].includes(marker.state) && marker.origin !== 'operator_withdrawal'
    && typeof marker.id === 'string' && !!marker.id.trim()
    && typeof marker.sourceAttemptId === 'string' && !!marker.sourceAttemptId.trim());
  // Ambiguous marker IDs cannot identify a unique server-side candidate.
  return markers.filter(marker => markers.filter(other => other.id === marker.id).length === 1);
}

export function readoutWithdrawalError(workflow: Workflow, request: ReadoutWithdrawalRequest): string | undefined {
  const marker = readoutWithdrawalCandidates(workflow).find(item => item.id === request.revalidationId);
  if (!marker || marker.sourceAttemptId !== request.sourceAttemptId) return '候选记录或任务状态已变化，请关闭后重新选择。';
  if (workflow.revision !== request.expectedRevision) return '任务版本已更新，请关闭后重新打开撤回入口。';
  if (!request.reason.trim()) return '请填写撤回原因。';
  if (request.reason.trim().length > 1000) return '撤回原因不能超过 1000 字。';
}
