import type { Attempt, ExperimentNode, MeasurementResult, Workflow } from '../types.ts';

export type ResultComparisonSource = { workflowId: string; nodeId: string; attemptId: string; panel?: 'signals' };

const supported = new Set(['sq_rabi_time', 'sq_t1', 'sq_ramsey', 'sq_echo', 'sq_cpmg']);

/** The displayed result can precede latestAttemptId while another attempt starts. */
function resultAttemptId(result?: MeasurementResult): string | undefined {
  return result?.sourceRunId?.match(/^(attempt-[^/\s]+)\/(?:measure|verify)$/)?.[1];
}

export function hasResultComparisonData(result?: MeasurementResult): boolean {
  return !!result && result.dataKind === 'real' && !!result.acquiredAt && !result.verificationPending
    && result.artifacts?.some(item => item.kind === 'raw' && !!item.id && /^[0-9a-f]{64}$/i.test(item.sha256))
    && !!result.series?.some(series => series.fit !== true && Array.isArray(series.x) && Array.isArray(series.y)
      && series.x.length >= 2 && series.x.length === series.y.length
      && series.x.every(Number.isFinite) && series.y.every(Number.isFinite));
}

export function comparisonSourceMatchesResult(source: ResultComparisonSource, result?: MeasurementResult): boolean {
  return !!source.workflowId && !!source.nodeId && source.attemptId === resultAttemptId(result)
    && hasResultComparisonData(result);
}

export function signalSourceMatchesResult(source: ResultComparisonSource, result?: MeasurementResult): boolean {
  return source.panel === 'signals' && !!source.workflowId && !!source.nodeId
    && result?.dataKind === 'real' && source.attemptId === resultAttemptId(result);
}

function compatibleNode(workflow: Workflow, node: ExperimentNode, panel?: 'signals'): boolean {
  return workflow.mode === 'real' && !!workflow.id && (panel === 'signals' || supported.has(node.procedureId ?? ''))
    && workflow.nodes.some(item => item.id === node.id && item.procedureId === node.procedureId
      && item.resourceId === node.resourceId);
}

export function currentResultComparisonSource(workflow: Workflow, node: ExperimentNode, panel?: 'signals'): ResultComparisonSource | undefined {
  const attemptId = resultAttemptId(node.result);
  if (!compatibleNode(workflow, node, panel) || !attemptId || !(panel === 'signals' ? node.result?.dataKind === 'real' : hasResultComparisonData(node.result))) return;
  return { workflowId: workflow.id, nodeId: node.id, attemptId, ...(panel && {panel}) };
}

export function attemptResultComparisonSource(workflow: Workflow, node: ExperimentNode, attempt: Attempt, panel?: 'signals'): ResultComparisonSource | undefined {
  if (!compatibleNode(workflow, node, panel) || attempt.nodeId !== node.id || attempt.mode !== 'real'
    || attempt.procedureId !== node.procedureId || attempt.id !== resultAttemptId(attempt.result)
    || !(panel === 'signals' ? attempt.result?.dataKind === 'real' : hasResultComparisonData(attempt.result))) return;
  return { workflowId: workflow.id, nodeId: node.id, attemptId: attempt.id, ...(panel && {panel}) };
}
