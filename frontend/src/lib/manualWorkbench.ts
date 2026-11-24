import type { RemoteAction, Workflow } from '../types.ts';
import { isManualWorkflow, manualStepControls, precedingDependencies, workflowControls } from './workflowControls.ts';

export interface ManualCreationAttempt { name: string; resourceId: string; knownIds: string[] }
export function manualCreationMatches(workflows: Workflow[], attempt: ManualCreationAttempt): Workflow[] {
  return workflows.filter(workflow => isManualWorkflow(workflow) && !attempt.knownIds.includes(workflow.id)
    && workflow.name === attempt.name && workflow.experiment === 'single' && workflow.coverage === 'full'
    && (workflow.resourceIds?.length === 1 ? workflow.resourceIds[0] === attempt.resourceId : !workflow.resourceIds?.length && workflow.target === attempt.resourceId));
}

/** Validate the same controls again at dispatch, keeping task stops free of node context. */
export function manualWorkbenchAction(workflow: Workflow, selectedId: string, action: RemoteAction, dirty = false, targetId?: string): { action: RemoteAction; nodeId?: string } | undefined {
  if (!isManualWorkflow(workflow)) return undefined;
  const controls = workflowControls(workflow);
  if (action === 'pause') return controls.pause ? { action } : undefined;
  if (action === 'abort') return !controls.terminal && workflow.engineState !== 'stopping_at_checkpoint' ? { action } : undefined;
  if (action === 'reconcile') return controls.reconciliation ? { action } : undefined;
  if (dirty || controls.terminal) return undefined;
  if (action === 'confirm_hardware') return controls.hardware ? { action } : undefined;
  const node = workflow.nodes.find(item => item.id === selectedId);
  if (!node) return undefined;
  const step = manualStepControls(workflow, node);
  if (action === 'run_step' && step.run) return { action, nodeId: node.id };
  if (action === 'approve' && controls.approve && step.approve) return { action, nodeId: node.id };
  if (action === 'backtrack' && controls.backtrack && targetId) {
    const acceptedCurrent = workflow.status === 'paused' && workflow.engineState === 'manual_ready'
      && controls.current?.id === node.id && node.status === 'success' && node.engineState === 'verified'
      && !workflow.nodes.some(item => item.status === 'running' || ['needs_reconciliation', 'unknown', 'awaiting_acceptance'].includes(item.engineState ?? ''));
    if ((targetId === node.id && (step.adjust || acceptedCurrent)) || precedingDependencies(workflow, controls.current).some(item => item.id === targetId)) return { action, nodeId: targetId };
  }
  return undefined;
}
