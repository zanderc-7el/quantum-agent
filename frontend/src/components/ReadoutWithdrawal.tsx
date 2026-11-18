import { useRef, useState } from 'react';
import type { ReadoutRevalidation, ReadoutWithdrawal as Receipt, ReadoutWithdrawalRequest, Workflow } from '../types';
import type { RemoteGateway } from '../lib/remoteGateway';
import { readoutWithdrawalCandidates, readoutWithdrawalError } from '../lib/readoutWithdrawal';
import './readout-withdrawal.css';

export function ReadoutWithdrawalForm({ marker, reason, busy, locked, error, onReason, onCancel, onSubmit }: {
  marker: ReadoutRevalidation; reason: string; busy: boolean; locked: boolean; error: string;
  onReason(value: string): void; onCancel(): void; onSubmit(): void;
}) {
  return <form role="dialog" aria-label="撤回读出候选并重新验证" className="rp-parameter-editor" onSubmit={event => { event.preventDefault(); onSubmit(); }}>
    <p className="rp-info">服务端将恢复同一任务原 IQ 验收记录中的读出基准，建立新参数版本，并将受影响的旧结果标为待复测。所有原始数据保留。保存后任务保持暂停，请通过任务操作明确继续。</p>
    <p className="rp-readout-source">候选来源：{marker.sourceAttemptId}</p>
    <div className="rp-parameter-fields"><label><span>撤回原因</span><input autoFocus aria-label="撤回原因" value={reason} maxLength={1000} disabled={busy || locked} onChange={event => onReason(event.target.value)} /></label></div>
    {locked && !busy && <p className="rp-info">再次提交将重试同一请求。需要修改原因时，请取消后重新打开。</p>}
    {error && <p role="alert" className="rp-error">{error}</p>}
    <div className="rp-action-row"><button className="secondary" type="button" disabled={busy} onClick={onCancel}>取消</button><button className="primary" type="submit" disabled={busy || !reason.trim()}>{busy ? '保存中…' : locked ? '重试同一请求' : '撤回并保留暂停'}</button></div>
  </form>;
}

export function ReadoutWithdrawalReceipt({ receipt }: { receipt: Receipt }) {
  const format = (value: number | undefined) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US', { useGrouping: false, maximumFractionDigits: 12 }) : '未记录';
  return <details className="rp-readout-receipt" open><summary>最近一次读出候选撤回</summary>
    <p>撤回记录的验收状态：{receipt.validationStatus === 'unverified' ? '未重新验证（unverified）' : `待核对（${receipt.validationStatus}）`}。当前测量进展见蓝图。</p>
    <table><thead><tr><th>参数</th><th>撤回前 / 版本</th><th>恢复基准 / 新版本</th></tr></thead><tbody>
      {([['readout_hz', '读出频率（Hz）'], ['readout_amp', '读出幅度（a.u.）']] as const).map(([key, label]) => <tr key={key}><th>{label}</th><td>{format(receipt.before.values[key])} / v{receipt.before.parameterVersions[key] ?? '未记录'}</td><td>{format(receipt.after.values[key])} / v{receipt.after.parameterVersions[key] ?? '未记录'}</td></tr>)}
    </tbody></table>
    <p className="rp-readout-source">候选来源：{receipt.sourceEvidence.optimizerAttemptId}<br />原 IQ 验收来源：{receipt.sourceEvidence.baselineIqAttemptId}</p>
    <p>待复测节点：{receipt.invalidatedNodes.length} 项。原因：{receipt.reason}</p>
    <small>{receipt.createdAt} · {receipt.ruleVersion}</small>
  </details>;
}

export default function ReadoutWithdrawal({ workflow, gateway }: { workflow: Workflow; gateway: RemoteGateway }) {
  const [draft, setDraft] = useState<{ marker: ReadoutRevalidation; revision: number }>();
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const pending = useRef<ReadoutWithdrawalRequest | undefined>(undefined);
  const submitting = useRef(false);
  const candidates = readoutWithdrawalCandidates(workflow);
  const receipt = workflow.lastReadoutWithdrawal;
  const saved = receipt?.operationId === pending.current?.operationId && !!receipt;
  if (workflow.mode !== 'real' || (!candidates.length && !receipt && !draft)) return null;
  const begin = (marker: ReadoutRevalidation) => {
    pending.current = undefined; setDraft({ marker, revision: workflow.revision ?? 0 }); setReason(''); setError(''); setMessage('');
  };
  const save = async () => {
    if (!draft || submitting.current) return;
    const request = pending.current ?? { operationId: crypto.randomUUID(), expectedRevision: draft.revision, revalidationId: draft.marker.id, sourceAttemptId: draft.marker.sourceAttemptId, reason: reason.trim() };
    const invalid = readoutWithdrawalError(workflow, request);
    if (invalid) { setError(invalid); return; }
    pending.current = request; submitting.current = true; setBusy(true); setError('');
    try {
      await gateway.readoutWithdrawal(workflow.id, request); setDraft(undefined);
      setMessage('读出候选已撤回，基准参数已建立新版本，尚未重新验证。任务保持暂停，请点击任务的“继续”开始复测。');
    } catch (cause) { setError(cause instanceof Error ? cause.message : '撤回请求失败'); }
    finally { submitting.current = false; setBusy(false); }
  };
  return <section className="rp-budget rp-readout-withdrawal" aria-label="读出候选撤回">
    <div className="rp-budget-title"><strong>读出工作点</strong></div>
    {!draft && candidates.map(marker => <button className="secondary rp-budget-edit-button" key={marker.id} onClick={() => begin(marker)}>撤回读出候选并重新验证{candidates.length > 1 ? ` · ${marker.resourceId}` : ''}</button>)}
    {draft && !saved && <ReadoutWithdrawalForm marker={draft.marker} reason={reason} busy={busy} locked={!!pending.current} error={error} onReason={setReason} onCancel={() => { setDraft(undefined); pending.current = undefined; }} onSubmit={() => void save()} />}
    {message && <p role="status">{message}</p>}
    {receipt && <ReadoutWithdrawalReceipt receipt={receipt} />}
  </section>;
}
