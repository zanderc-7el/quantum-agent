import type { Catalog, CreateWorkflowInput, Workflow } from '../types.ts';
import type { WorkspaceGateway } from './workspaceGateway.ts';

const profile = 'arclight-private-f8-q11-v3';
const pendingKey = 'qcal.pending-smart-q11-start.v1';

export function offersSmartStart(input: CreateWorkflowInput, catalog?: Catalog, published = false) {
  const adapter = catalog?.adapter?.id ?? (catalog?.capabilities as { id?: string } | undefined)?.id;
  return adapter === profile && !published && !input.chipVersionId && !input.deploymentVersionId
    && input.mode === 'real' && input.policy === 'autopilot' && input.experiment === 'single'
    && (input.coverage ?? 'full') === 'full' && input.resourceIds?.length === 1 && input.resourceIds[0] === 'Q11';
}

export async function launchSmartQ11(api: Pick<WorkspaceGateway, 'write'>, name: string, storage: Storage = localStorage) {
  // Keep unresolved start intents across a response loss or a desktop reload.
  const previous = storage.getItem(pendingKey);
  const pending: { name: string; operationId: string } | null = previous ? JSON.parse(previous) : null;
  if (pending && pending.name !== name) throw new Error('上次启动的回执待确认，请使用原任务名「' + pending.name + '」重试。');
  const operationId = pending?.operationId ?? crypto.randomUUID();
  storage.setItem(pendingKey, JSON.stringify({ name, operationId }));
  const request = { operationId, name, resourceId: 'Q11', adapterId: profile, scope: 'q11-full-same-gate/1', stationConfirmed: true };
  try {
    const wf = await api.write<Workflow>('/workflows/smart-start', request);
    storage.removeItem(pendingKey);
    return wf;
  } catch (error) {
    const status = (error as { status?: number } | null)?.status;
    if (status && status >= 400 && status < 500 && status !== 408) storage.removeItem(pendingKey);
    throw error;
  }
}
