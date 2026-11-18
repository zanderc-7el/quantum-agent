import { useState } from "react";
import type { Workflow } from "../types";
import type { RemoteGateway } from "../lib/remoteGateway";

const procedures = new Set(['sq_allxy', 'sq_ramsey', 'sq_echo', 'sq_cpmg']);

export default function SupplementalAcceptance({ workflow, gateway }: { workflow: Workflow; gateway: RemoteGateway }) {
  const [editing, setEditing] = useState(false);
  const [revision, setRevision] = useState(0);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (workflow.mode !== 'real' || !workflow.adapterId?.startsWith('arclight-')) return null;
  const installed = workflow.supplementalAcceptance;
  const untouched = workflow.nodes.filter(node => procedures.has(node.procedureId ?? '')).every(node => node.attempt === 0);
  const editable = ['draft', 'paused', 'attention'].includes(workflow.status) && untouched;
  const save = async () => {
    if (busy || !editable || !reason.trim()) return;
    setBusy(true); setError('');
    try { await gateway.supplementalAcceptance(workflow.id, revision, reason.trim()); setEditing(false); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '规则添加失败'); }
    finally { setBusy(false); }
  };
  return <section className="rp-budget rp-supplement" aria-label="重复采集验收规则">
    <details><summary>重复采集验收 · {installed ? `已启用 ${installed.policyVersion}` : '尚未添加补充规则'}</summary>
    <p>逐次检查 AllXY 误差，并核对 Ramsey、Echo、CPMG 各次 T2 拟合的一致性。补充检查与原有验收共同生效。</p>
    {installed ? <details><summary>规则版本与添加记录</summary><p>{installed.reason}</p><p>{installed.installedAt}</p><small>规则指纹 {installed.policyHash}</small></details> : <>
      <p>{untouched ? '可在采集停止的检查点添加；仅适用于尚未开始测量的相关节点。' : '相关节点已有尝试记录，本任务不能再添加此版本。'}</p>
      {!editing && <button className="secondary" disabled={!editable} onClick={() => { setRevision(workflow.revision ?? 0); setEditing(true); setError(''); }}>添加重复性验收</button>}
      {editing && <form className="rp-parameter-editor" onSubmit={event => { event.preventDefault(); void save(); }}>
        <div className="rp-parameter-fields"><label><span>添加原因</span><input aria-label="补充验收原因" value={reason} maxLength={1000} disabled={busy} onChange={event => setReason(event.target.value)} /></label></div>
        <p>版本 capture-consistency/1。添加记录会保留，不修改已有结果；保存后等待继续运行。</p>
        {error && <p className="rp-error" role="alert">{error}</p>}
        <div className="rp-action-row"><button className="secondary" type="button" disabled={busy} onClick={() => setEditing(false)}>取消</button><button className="primary" disabled={busy || !editable || !reason.trim()} type="submit">{busy ? '保存中…' : '添加并记录版本'}</button></div>
      </form>}
    </>}</details>
  </section>;
}
