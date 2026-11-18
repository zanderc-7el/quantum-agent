import { Gauge } from "lucide-react";
import { useState } from "react";
import type { Workflow } from "../types";
import { workflowBudget } from "../lib/workflowResources";
import type { RemoteGateway } from "../lib/remoteGateway";
import { budgetEditError, BUDGET_LIMITS, type BudgetValues } from "../lib/budgetEdit";

export default function WorkflowBudget({ workflow, gateway }: { workflow: Workflow; gateway?: RemoteGateway }) {
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<BudgetValues>({ maxTotalShots: 0, maxAttemptsPerNode: 0, maxWorkflowAttempts: 0 });
  const [revision, setRevision] = useState(0);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  if (workflow.mode !== "real") return null;
  const { limit, reserved, remaining, attempts, attemptLimit, nodeLimit } = workflowBudget(workflow);
  const format = (value: number | undefined) => value === undefined ? "未提供" : new Intl.NumberFormat("zh-CN").format(value);
  const editable = ['draft', 'paused', 'attention'].includes(workflow.status);
  const begin = () => {
    setValues({ maxTotalShots: workflow.maxTotalShots ?? 0, maxAttemptsPerNode: workflow.maxAttemptsPerNode ?? 0, maxWorkflowAttempts: workflow.maxWorkflowAttempts ?? 0 });
    setRevision(workflow.revision ?? 0); setReason(''); setError(''); setMessage(''); setEditing(true);
  };
  const save = async () => {
    if (!gateway || busy) return;
    const invalid = budgetEditError(values, workflow, reason);
    if (invalid) { setError(invalid); return; }
    setBusy(true); setError('');
    try { await gateway.budget(workflow.id, revision, values, reason.trim()); setEditing(false); setMessage('预算已保存并记录调整原因。采集尚未启动，请通过任务操作继续。'); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '预算保存失败'); }
    finally { setBusy(false); }
  };
  return <section className="rp-budget" aria-label="本次任务测量预算"><div className="rp-budget-title"><Gauge size={15} /><strong>本次任务预算</strong><small>随执行回执更新</small></div><div className="rp-budget-values">
    <span>累计预留 / 总 shots 上限<strong>{format(reserved)} / {format(limit)}</strong></span>
    <span>剩余 shots 预算<strong>{format(remaining)}</strong></span>
    <span>累计尝试 / 总次数上限<strong>{format(attempts)} / {format(attemptLimit)}</strong></span>
    <span>单节点尝试上限<strong>{format(nodeLimit)}</strong></span>
  </div><p>shots 按控制器预留量记账，包含独立复测；执行结果未知的预留不会返还，不等于已完成采集量。</p>
    {gateway && !editing && <button className="secondary rp-budget-edit-button" disabled={!editable} onClick={begin} title={editable ? '记录原因并调整本任务预算' : '采集结束或暂停到检查点后可调整'}>调整任务预算</button>}
    {editing && <form className="rp-parameter-editor" aria-label="调整任务预算" onSubmit={event => { event.preventDefault(); void save(); }}>
      <div className="rp-parameter-fields">
        {([['maxTotalShots', '总 shots 上限'], ['maxAttemptsPerNode', '单节点尝试上限'], ['maxWorkflowAttempts', '任务总尝试上限']] as const).map(([key, label]) => <label key={key}><span>{label}</span><input aria-label={label} type="number" step="1" min={workflow[key]} max={BUDGET_LIMITS[key]} value={values[key]} disabled={busy} onChange={event => setValues({ ...values, [key]: Number(event.target.value) })} /><small>当前 {format(workflow[key])}；可设置上限 {format(BUDGET_LIMITS[key])}</small></label>)}
        <label><span>调整原因</span><input aria-label="预算调整原因" value={reason} maxLength={1000} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
      </div>
      <p className="rp-info">模型无法提高预算。此次保存不改变质量标准、设备参数或已消耗计数，也不启动采集。</p>
      {error && <p role="alert" className="rp-error">{error}</p>}
      <div className="rp-action-row"><button className="secondary" type="button" disabled={busy} onClick={() => setEditing(false)}>取消</button><button className="primary" type="submit" disabled={busy || !editable}>{busy ? '保存中…' : '保存预算'}</button></div>
    </form>}
    {message && <p role="status">{message}</p>}
  </section>;
}
