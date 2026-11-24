import { useEffect, useId, useRef, useState } from 'react';
import { Check, RotateCcw, Save } from 'lucide-react';
import type { ExperimentNode, NodeDetail, Workflow } from '../types';
import type { RemoteGateway } from '../lib/remoteGateway';
import {
  createManualParameterDraft, displayParameterValue, manualParameterChanges,
  manualParameterGroups, parseDisplayParameter, splitManualParameterFields, validateManualParameterDraft,
} from '../lib/manualParameters';
import type { ManualParameterDraft, ManualParameterField } from '../lib/manualParameters';
import './manual-parameters.css';

export interface ManualParameterFormProps {
  gateway: RemoteGateway; workflow: Workflow; node: ExperimentNode; detail?: NodeDetail; detailRevision?: number;
  busy: boolean; canEdit: boolean; onBusy(value: boolean): void;
  onDirtyChange?(value: boolean): void;
  onSaved?(workflow: Workflow): void | Promise<void>;
}

export default function ManualParameterForm({ gateway, workflow, node, detail, detailRevision, busy, canEdit, onBusy, onDirtyChange, onSaved }: ManualParameterFormProps) {
  const [draft, setDraft] = useState<ManualParameterDraft>();
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [awaitingDetail, setAwaitingDetail] = useState<{ revision: number; detail?: NodeDetail }>();
  const [latest, setLatest] = useState<{ detail: NodeDetail; revision: number }>();
  const savingRef = useRef(false);
  const mounted = useRef(true);
  const lastDetail = useRef<NodeDetail | undefined>(undefined);
  const dirtyCallback = useRef(onDirtyChange);
  const formId = useId();
  const revision = workflow.revision ?? 0;
  const receivedRevision = detailRevision ?? revision;
  const validDetail = detail?.node.id === node.id ? detail : undefined;
  const dirty = !!draft && !awaitingDetail && Object.keys(manualParameterChanges(draft)).length > 0;
  const stale = !!draft && !awaitingDetail && draft.revision !== revision;
  dirtyCallback.current = onDirtyChange;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; dirtyCallback.current?.(false); };
  }, []);
  useEffect(() => { dirtyCallback.current?.(dirty); }, [dirty]);
  useEffect(() => {
    setDraft(undefined); setLatest(undefined); setAwaitingDetail(undefined);
    setError(''); setNotice(''); lastDetail.current = undefined;
  }, [workflow.id, node.id]);
  useEffect(() => {
    if (!validDetail) return;
    const received = lastDetail.current !== validDetail;
    if (received) {
      lastDetail.current = validDetail;
      setLatest({ detail: validDetail, revision: receivedRevision });
    }
    // A revision update alone cannot rebase an old detail response. Dirty input
    // keeps its original baseline even when a fresh response has arrived.
    if (awaitingDetail) {
      if (validDetail !== awaitingDetail.detail && receivedRevision >= awaitingDetail.revision && revision === receivedRevision) {
        setDraft(createManualParameterDraft(validDetail, node, receivedRevision));
        setLatest({ detail: validDetail, revision: receivedRevision }); setAwaitingDetail(undefined);
      }
    } else if (received && !dirty && !savingRef.current) {
      setDraft(createManualParameterDraft(validDetail, node, receivedRevision));
    }
  }, [validDetail, revision, receivedRevision, node, dirty, awaitingDetail]);

  const edit = (key: string, value: string) => {
    if (!draft || busy || savingRef.current || !canEdit || stale || awaitingDetail) return;
    const next = { ...draft, values: { ...draft.values, [key]: value } };
    setDraft(next); setError(''); setNotice('');
    dirtyCallback.current?.(Object.keys(manualParameterChanges(next)).length > 0);
  };
  const restore = () => {
    if (busy || savingRef.current || !latest || latest.revision !== revision || awaitingDetail) return;
    setDraft(createManualParameterDraft(latest.detail, node, revision));
    setError(''); setNotice(''); dirtyCallback.current?.(false);
  };
  const save = async () => {
    if (!draft || !dirty || busy || savingRef.current || !canEdit || stale || awaitingDetail) return;
    setError(''); setNotice('');
    let changes: Record<string, number>;
    try { changes = validateManualParameterDraft(draft, revision); }
    catch (cause) { setError(cause instanceof Error ? cause.message : '请检查参数。'); return; }
    savingRef.current = true; setSaving(true); onBusy(true);
    try {
      const updated = await gateway.parameters(workflow.id, node.id, draft.revision, changes);
      if (!mounted.current) return;
      setAwaitingDetail({ revision: updated.revision ?? revision, detail: validDetail });
      setNotice('配置已保存，下次测量生效。'); dirtyCallback.current?.(false);
      try { await onSaved?.(updated); }
      catch (cause) { if (mounted.current) setError(`配置已保存，详情刷新失败：${cause instanceof Error ? cause.message : '请重新读取配置。'}`); }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '配置保存失败，修改已保留。');
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
      onBusy(false);
    }
  };

  if (!draft) return <section className="mpf-form" aria-label="实验参数"><p className="mpf-empty" role="status">正在读取配置…</p></section>;
  const disabled = busy || saving || !canEdit || stale || !!awaitingDetail;
  const changed = manualParameterChanges(draft);
  const fieldError = (field: ManualParameterField) => {
    if (!Object.hasOwn(changed, field.key)) return '';
    try { parseDisplayParameter(draft.values[field.key], field); return ''; }
    catch (cause) { return cause instanceof Error ? cause.message : '请输入有效数值'; }
  };
  const editableKeys = new Set(draft.fields.map(field => field.key));
  const readonlyValues = Object.entries(draft.baseline).filter(([key]) => !editableKeys.has(key));
  const { primary, more } = splitManualParameterFields(draft.fields, node.procedureId);
  const moreChanged = more.some(field => Object.hasOwn(changed, field.key));
  const renderGroups = (visibleFields: ManualParameterField[]) => manualParameterGroups.map(group => {
      const fields = visibleFields.filter(field => field.group === group.id);
      if (!fields.length) return null;
      return <fieldset className="mpf-group" key={group.id} disabled={disabled}><legend>{group.label}</legend><div className="mpf-fields">{fields.map(field => {
        const id = `${formId}-${field.key}`, problem = fieldError(field);
        const choices = field.rule.enum;
        return <div className={`mpf-field${Object.hasOwn(changed, field.key) ? ' is-edited' : ''}`} key={field.key}>
          <label htmlFor={id}>{field.label}</label>
          <div className="mpf-input-wrap">{choices ? <select id={id} aria-label={`参数 ${field.key}`} aria-describedby={`${id}-hint`} aria-invalid={!!problem} value={draft.values[field.key] ?? ''} onChange={event => edit(field.key, event.target.value)}><option value="" disabled>未设置</option>{choices.map(value => <option key={value} value={displayParameterValue(value, field)}>{displayParameterValue(value, field)}</option>)}</select>
            : <input id={id} aria-label={`参数 ${field.key}`} aria-describedby={`${id}-hint`} aria-invalid={!!problem} inputMode={field.rule.integer ? 'numeric' : 'decimal'} autoComplete="off" value={draft.values[field.key] ?? ''} placeholder="未设置" onChange={event => edit(field.key, event.target.value)} />}
            {field.unit && <span className="mpf-unit" aria-hidden="true">{field.unit}</span>}
          </div>
          <small id={`${id}-hint`} className={problem ? 'mpf-field-error' : ''}>{problem || `${field.min} – ${field.max}${field.unit ? ` ${field.unit}` : ''}${field.rule.integer ? ' · 整数' : ''}`}</small>
        </div>;
      })}</div></fieldset>;
    });

  return <section className="mpf-form" aria-label="实验参数" aria-busy={saving}>
    <div className="mpf-heading"><h3>参数配置</h3><span className={dirty ? 'mpf-unsaved' : 'mpf-state'}>{dirty ? '有未保存修改' : awaitingDetail ? '正在刷新配置…' : canEdit ? '已保存配置' : '当前状态不可编辑'}</span></div>
    {draft.fields.length ? renderGroups(primary) : <p className="mpf-empty">当前方法没有可编辑的数值参数。</p>}
    {more.length > 0 && <details className="mpf-more"><summary>更多参数<span>{more.length} 项{moreChanged ? ' · 有未保存修改' : ''}</span></summary>{renderGroups(more)}</details>}
    {stale && <p className="mpf-message mpf-warning" role="alert">配置版本已更新，修改已保留。{latest?.revision === revision ? '请恢复未保存修改后重新编辑。' : '正在读取最新配置…'}</p>}
    {error && <p className="mpf-message mpf-error" role="alert">{error}</p>}
    {notice && <p className="mpf-message mpf-saved" role="status"><Check size={14} />{notice}</p>}
    <footer className="mpf-actions"><button className="secondary" type="button" disabled={busy || saving || !!awaitingDetail || (!dirty && !stale) || latest?.revision !== revision} onClick={restore}><RotateCcw size={14} />恢复未保存修改</button><button className="primary" type="button" disabled={disabled || !dirty} onClick={() => void save()}><Save size={14} />{saving ? '正在保存…' : '保存配置'}</button></footer>
    {readonlyValues.length > 0 && <details className="mpf-readonly"><summary>其他生效参数 · {readonlyValues.length}</summary><dl>{readonlyValues.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value == null ? '未设置' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></details>}
  </section>;
}
