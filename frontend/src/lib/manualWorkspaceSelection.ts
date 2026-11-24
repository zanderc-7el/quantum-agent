import type { CreateWorkflowInput, Workflow } from '../types.ts';
import { isManualWorkflow } from './workflowControls.ts';

export const manualSelectionKey = (projectId: string) => `qcal:manual-workspace:${projectId}`;

export function selectedManualWorkflow(workflows: Workflow[], selectedId?: string) {
  const manual = workflows.filter(isManualWorkflow);
  return manual.find(workflow => workflow.id === selectedId) ?? manual[0];
}

export function manualDraftInput(input: CreateWorkflowInput): CreateWorkflowInput {
  return { ...input, mode: 'real', policy: 'strict', experiment: 'single', coverage: 'full', qubits: 1 };
}
