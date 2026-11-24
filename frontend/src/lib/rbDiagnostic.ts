import type { RbDiagnosticInspection, RbDiagnosticMarker, RbDiagnosticRequest, Workflow } from '../types.ts';
import { ApiError } from './remoteGateway.ts';
import type { RemoteGateway } from './remoteGateway.ts';

export const RB_DIAGNOSTIC_POLICY = 'rb-after-cpmg-diagnostic/1';
const text = (value: unknown): value is string => typeof value === 'string' && !!value.trim();
const proof = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const states = new Set(['queued', 'running', 'completed', 'failed', 'interrupted', 'invalidated']);

export function rbDiagnosticWorkflow(workflow: Workflow): boolean {
  return workflow.mode === 'real' && workflow.coverage === 'working_point'
    && workflow.adapterId === 'arclight-private-f8-q11-v2';
}

export function rbDiagnosticMarker(workflow: Workflow): RbDiagnosticMarker | undefined {
  const marker = workflow.rbDiagnostic;
  if (!rbDiagnosticWorkflow(workflow) || !marker || marker.policyVersion !== RB_DIAGNOSTIC_POLICY
      || marker.publication !== 'disabled_diagnostic_only' || !states.has(marker.state)
      || !text(marker.id) || !text(marker.commandOperationId) || !text(marker.sourceNodeId)
      || !text(marker.sourceAttemptId) || !text(marker.targetNodeId) || !proof(marker.sourceProofSha256)) return;
  const sources=workflow.nodes.filter(node=>node.id===marker.sourceNodeId);
  const targets=workflow.nodes.filter(node=>node.id===marker.targetNodeId);
  if (sources.length!==1 || targets.length!==1 || sources[0].procedureId!=='sq_cpmg'
      || sources[0].resourceId!=='Q11' || targets[0].procedureId!=='sq_rb' || targets[0].resourceId!=='Q11') return;
  return marker;
}

export function rbDiagnosticPassed(workflow: Workflow): boolean {
  const marker=rbDiagnosticMarker(workflow);
  if (!marker || marker.state!=='completed' || marker.resultStatus!=='passed' || !marker.attemptId) return false;
  const target=workflow.nodes.find(node=>node.id===marker.targetNodeId)!;
  const provenance=target.result?.provenance;
  return target.latestAttemptId===marker.attemptId && target.engineState==='verified'
    && target.result?.dataKind==='real' && target.result.quality?.status==='passed' && target.result.stale!==true
    && !!provenance && typeof provenance==='object'
    && 'freshAcquisition' in provenance && provenance.freshAcquisition===true;
}

/** Server eligibility is required. Local checks only reject stale or malformed inspection snapshots. */
export function rbDiagnosticEligible(workflow: Workflow, inspection?: RbDiagnosticInspection): boolean {
  if (workflow.policy === 'strict' || !rbDiagnosticWorkflow(workflow) || !['paused', 'attention'].includes(workflow.status)
      || !inspection || inspection.schema !== 'qcal-rb-diagnostic-inspection/1'
      || inspection.policyVersion !== RB_DIAGNOSTIC_POLICY || inspection.eligible !== true
      || inspection.workflowId !== workflow.id || !Number.isSafeInteger(inspection.revision)
      || inspection.revision < 0 || workflow.revision !== inspection.revision
      || !text(inspection.sourceNodeId) || !text(inspection.sourceAttemptId)
      || !text(inspection.targetNodeId) || !proof(inspection.sourceProofSha256)) return false;
  const sources = workflow.nodes.filter(node => node.id === inspection.sourceNodeId);
  const targets = workflow.nodes.filter(node => node.id === inspection.targetNodeId);
  return sources.length === 1 && targets.length === 1 && sources[0].resourceId === 'Q11'
    && sources[0].procedureId === 'sq_cpmg' && sources[0].latestAttemptId === inspection.sourceAttemptId
    && sources[0].result?.quality?.status === 'failed' && sources[0].result.stale !== true
    && targets[0].resourceId === 'Q11' && targets[0].procedureId === 'sq_rb'
    && targets[0].engineState !== 'verified';
}

export function rbDiagnosticRequest(workflow: Workflow, inspection: RbDiagnosticInspection | undefined,
  operationId: string): RbDiagnosticRequest | undefined {
  if (!rbDiagnosticEligible(workflow, inspection) || !text(operationId)) return;
  return { action: 'queue_rb_diagnostic', operationId, expectedRevision: inspection!.revision,
    nodeId: inspection!.sourceNodeId!, attemptId: inspection!.sourceAttemptId!,
    policyVersion: RB_DIAGNOSTIC_POLICY, sourceProofSha256: inspection!.sourceProofSha256! };
}

export function rbDiagnosticReceiptMatches(workflow: Workflow, request: RbDiagnosticRequest,
  targetNodeId: string): boolean {
  const marker = rbDiagnosticMarker(workflow);
  return !!marker && marker.commandOperationId === request.operationId
    && marker.policyVersion === request.policyVersion && marker.sourceNodeId === request.nodeId
    && marker.sourceAttemptId === request.attemptId && marker.sourceProofSha256 === request.sourceProofSha256
    && marker.targetNodeId === targetNodeId;
}

export const rbDiagnosticStateLabel = (state: RbDiagnosticMarker['state']): string => ({
  queued: 'RB 诊断已排队', running: 'RB 诊断采集中', completed: 'RB 诊断已结束',
  failed: 'RB 诊断未通过', interrupted: 'RB 诊断已中断', invalidated: 'RB 诊断许可已失效',
})[state];

export interface RbDiagnosticViewState {
  checking: boolean; submitting: boolean; awaitingReceipt: boolean;
  inspection?: RbDiagnosticInspection; error: string; message: string;
}
export const emptyRbDiagnosticView = (): RbDiagnosticViewState => ({ checking: false, submitting: false,
  awaitingReceipt: false, error: '', message: '' });

/** User-driven only. Observation and refresh can reconcile receipts but never submit a command. */
export function createRbDiagnosticFlow(options: {
  workflow(): Workflow; gateway: Pick<RemoteGateway, 'get' | 'rbDiagnosticInspection' | 'queueRbDiagnostic'>;
  operationId(): string; update(state: RbDiagnosticViewState): void;
}) {
  const workflowId = options.workflow().id;
  let state = emptyRbDiagnosticView(), disposed = false;
  let pending: { request: RbDiagnosticRequest; targetNodeId: string } | undefined;
  const publish = (patch: Partial<RbDiagnosticViewState>) => {
    if (!disposed) { state = { ...state, ...patch }; options.update(state); }
  };
  const receipt = (workflow: Workflow) => !!pending && workflow.id === workflowId
    && rbDiagnosticReceiptMatches(workflow, pending.request, pending.targetNodeId);
  const observed = (workflow: Workflow) => {
    if (disposed || !receipt(workflow)) return false;
    pending = undefined;
    publish({ awaitingReceipt: false, error: '', message: '已收到本次 RB 诊断指令回执；采集状态以服务器记录为准。', inspection: undefined });
    return true;
  };
  return {
    observe() { observed(options.workflow()); },
    dispose() { disposed = true; },
    async check() {
      if (disposed || state.checking || state.submitting || options.workflow().id !== workflowId) return;
      publish({ checking: true, error: '', inspection: undefined });
      try {
        const [workflow, inspection] = await Promise.all([
          options.gateway.get(workflowId), options.gateway.rbDiagnosticInspection(workflowId),
        ]);
        if (disposed || options.workflow().id !== workflowId) return;
        if (!workflow || workflow.id !== workflowId) throw new Error('任务状态回执不匹配，请重新检查。');
        observed(workflow);
        if (!inspection || inspection.workflowId !== workflowId || inspection.schema !== 'qcal-rb-diagnostic-inspection/1')
          throw new Error('检查回执与任务不匹配，请重新检查。');
        publish({ inspection, ...(pending ? { error: '提交回执尚未确认，请继续刷新状态核对。' } : {}) });
      } catch (cause) {
        publish({ error: cause instanceof Error ? cause.message : '读取诊断条件失败。' });
      } finally { publish({ checking: false }); }
    },
    async queue() {
      if (disposed || state.checking || state.submitting || pending || options.workflow().id !== workflowId) return;
      const request = rbDiagnosticRequest(options.workflow(), state.inspection, options.operationId());
      if (!request) { publish({ error: '诊断条件或任务版本已变化，请重新检查。' }); return; }
      pending = { request, targetNodeId: state.inspection!.targetNodeId! };
      publish({ submitting: true, awaitingReceipt: true, error: '', message: '' });
      try {
        const workflow = await options.gateway.queueRbDiagnostic(workflowId, request);
        if (disposed) return;
        if (!observed(workflow) && pending)
          publish({ error: '提交回执尚未确认，请刷新状态核对。' });
      } catch (cause) {
        if (disposed || !pending || observed(options.workflow())) return;
        if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
          pending = undefined;
          publish({ awaitingReceipt: false, inspection: undefined,
            error: cause.message + ' 请重新检查诊断条件。' });
        } else publish({ error: '提交回执未确认，请刷新状态核对。' });
      } finally { publish({ submitting: false }); }
    },
  };
}
