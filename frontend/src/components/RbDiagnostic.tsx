import { useEffect, useRef, useState } from 'react';
import type { RemoteGateway } from '../lib/remoteGateway';
import type { Workflow } from '../types';
import { createRbDiagnosticFlow, emptyRbDiagnosticView, rbDiagnosticEligible, rbDiagnosticMarker, rbDiagnosticPassed,
  rbDiagnosticStateLabel, rbDiagnosticWorkflow } from '../lib/rbDiagnostic';

export function RbDiagnosticStatus({ workflow }: { workflow: Workflow }) {
  const marker = rbDiagnosticMarker(workflow);
  if (!marker) return null;
  return <div className="rp-info" aria-label="RB 诊断状态"><strong>{rbDiagnosticStateLabel(marker.state)}</strong>
    <p>CPMG 未通过记录保留。本次仅进行 RB 诊断，不发布校准参数，不表示 10 项全流程通过。</p>
    {rbDiagnosticPassed(workflow) && <p>RB 诊断独立验收通过。</p>}
    {marker.attemptId && <small>RB 采集记录：{marker.attemptId}</small>}
  </div>;
}

export default function RbDiagnostic({ workflow, gateway }: { workflow: Workflow; gateway: RemoteGateway }) {
  const current = useRef(workflow); current.current = workflow;
  const flow = useRef<ReturnType<typeof createRbDiagnosticFlow> | undefined>(undefined);
  const [state, setState] = useState(emptyRbDiagnosticView);
  useEffect(() => {
    const session = createRbDiagnosticFlow({ workflow: () => current.current, gateway,
      operationId: () => crypto.randomUUID(), update: setState });
    flow.current = session; setState(emptyRbDiagnosticView());
    return () => { session.dispose(); if (flow.current === session) flow.current = undefined; };
  }, [gateway, workflow.id]);
  useEffect(() => { flow.current?.observe(); }, [workflow]);
  if (!rbDiagnosticWorkflow(workflow)) return null;
  if (workflow.policy === 'strict') return <RbDiagnosticStatus workflow={workflow} />;
  const marker = rbDiagnosticMarker(workflow);
  const failedCpmg = workflow.nodes.some(node => node.procedureId === 'sq_cpmg' && node.resourceId === 'Q11'
    && node.result?.quality.status === 'failed');
  if (!marker && !failedCpmg) return null;
  const idle = ['paused', 'attention'].includes(workflow.status);
  const canQueue = rbDiagnosticEligible(workflow, state.inspection) && !state.awaitingReceipt;
  const stale = !!state.inspection && state.inspection.revision !== workflow.revision;
  const reasons = state.inspection?.eligible === false && Array.isArray(state.inspection.reasons)
    ? state.inspection.reasons.filter(reason => typeof reason?.message === 'string') : [];
  return <section className="rp-budget" aria-label="单次 RB 诊断">
    <div className="rp-budget-title"><strong>单次 RB 诊断</strong></div>
    <p>仅在服务端核验允许后执行一套 RB 首测与独立复测。CPMG 未通过记录保留，本次诊断不表示完整流程通过。</p>
    <RbDiagnosticStatus workflow={workflow} />
    <div className="rp-action-row">
      <button className="secondary" disabled={state.checking || state.submitting || (!idle && !state.awaitingReceipt)}
        onClick={() => void flow.current?.check()}>{state.checking ? '正在核对…' : state.awaitingReceipt ? '刷新执行回执' : '检查 RB 诊断条件'}</button>
      <button className="primary" disabled={!canQueue || state.checking || state.submitting}
        onClick={() => void flow.current?.queue()}>{state.submitting ? '正在提交…' : '启动一次 RB 诊断（含独立复测）'}</button>
    </div>
    {stale && <p>检查结果已过期，请重新检查当前任务版本。</p>}
    {reasons.map((reason, index) => <p key={`${reason.code}-${index}`}>{reason.message}</p>)}
    {state.error && <p className="rp-error" role="alert">{state.error}</p>}
    {state.message && <p role="status">{state.message}</p>}
  </section>;
}
