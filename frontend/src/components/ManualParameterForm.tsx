import { useEffect, useId, useRef, useState } from 'react';
import { Check, RotateCcw, Save } from 'lucide-react';
import type { ExperimentNode, NodeDetail, Workflow } from '../types';
import type { RemoteGateway } from '../lib/remoteGateway';
import {
  applyManualScanStep, createManualParameterDraft, displayParameterValue, manualParameterChanges,
  manualParameterGroups, parseDisplayParameter, splitManualParameterFields, validateManualParameterDraft,
} from '../lib/manualParameters';
import type { ManualParameterDraft, ManualParameterField } from '../lib/manualParameters';
import './manual-parameters.css';
import {manualScanKeys} from '../lib/manualExperimentDraft';
import {executionDefaultLabel} from '../lib/executionPlan';
import ManualPulsePreview from './ManualPulsePreview';
import {manualPreviewChanges} from '../lib/manualPulsePreview';

export interface ManualParameterFormProps {
  gateway: RemoteGateway; workflow: Workflow; node: ExperimentNode; detail?: NodeDetail; detailRevision?: number;
  busy: boolean; canEdit: boolean; onBusy(value: boolean): void;
  onDirtyChange?(value: boolean): void;
  onSaved?(workflow: Workflow): void | Promise<void>;
}

export default function ManualParameterForm({ gateway, workflow, node, detail, detailRevision, busy, canEdit, onBusy, onDirtyChange, onSaved }: ManualParameterFormProps) {
  const [draft, setDraft] = useState<ManualParameterDraft>();
  const [error, setError] = useState('');
  const [scanStep,setScanStep] = useState<string>();
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [awaitingDetail, setAwaitingDetail] = useState<{ revision: number; detail?: NodeDetail }>();
  const [latest, setLatest] = useState<{ detail: NodeDetail; revision: number }>();
  const savingRef = useRef(false);
  const mounted = useRef(true);
  const scopeRef = useRef('');
  scopeRef.current = `${workflow.id}/${node.id}`;
  const lastDetail = useRef<NodeDetail | undefined>(undefined);
  const dirtyCallback = useRef(onDirtyChange);
  const formId = useId();
  const revision = workflow.revision ?? 0;
  const receivedRevision = detailRevision ?? revision;
  const validDetail = detail?.node.id === node.id ? detail : undefined;
  const dirty = !!draft && !awaitingDetail && (scanStep!==undefined || Object.keys(manualParameterChanges(draft)).length > 0);
  const stale = !!draft && !awaitingDetail && draft.revision !== revision;
  const executionVersion=validDetail?.attemptExecutionVersion;
  const versionFrozen=executionVersion?.state==='selected';
  dirtyCallback.current = onDirtyChange;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; dirtyCallback.current?.(false); };
  }, []);
  useEffect(() => { dirtyCallback.current?.(dirty); }, [dirty]);
  useEffect(() => {
    setDraft(undefined); setLatest(undefined); setAwaitingDetail(undefined);
    setError(''); setNotice(''); setScanStep(undefined); lastDetail.current = undefined;
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
        setLatest({ detail: validDetail, revision: receivedRevision }); setAwaitingDetail(undefined);setScanStep(undefined);
      }
    } else if (received && !dirty && !savingRef.current) {
      setDraft(createManualParameterDraft(validDetail, node, receivedRevision));
    }
  }, [validDetail, revision, receivedRevision, node, dirty, awaitingDetail]);

  const edit = (key: string, value: string) => {
    if (!draft || busy || savingRef.current || !canEdit || stale || awaitingDetail || versionFrozen) return;
    const next = { ...draft, values: { ...draft.values, [key]: value } };
    setDraft(next); setScanStep(undefined); setError(''); setNotice('');
    dirtyCallback.current?.(Object.keys(manualParameterChanges(next)).length > 0);
  };

  const selectVersion = async (version:'rabi16-v1'|'default') => {
    if(!gateway.executionVersion||!draft||dirty||busy||savingRef.current||!canEdit||stale||awaitingDetail)return;
    const scope=scopeRef.current;
    savingRef.current=true;setSaving(true);onBusy(true);setError('');setNotice('');
    try {
      const updated=await gateway.executionVersion(workflow.id,node.id,draft.revision,version,
        version==='default'?'操作员取消本节点下一次尝试的扩展版本':'操作员明确选择本节点下一次幅度 Rabi 使用 16 个独立块，每块 2000 shots；首测与复测使用同一版本');
      if(!mounted.current||scopeRef.current!==scope)return;
      setAwaitingDetail({revision:updated.revision??revision,detail:validDetail});
      setNotice(version==='default'?'已恢复原执行版本。':'已选择下一次尝试的 16 块版本，尚未开始采集。');
      await onSaved?.(updated);
    } catch(cause){if(mounted.current&&scopeRef.current===scope)setError(cause instanceof Error?cause.message:'执行版本保存失败。');}
    finally {savingRef.current=false;if(mounted.current)setSaving(false);onBusy(false);}
  };
  const restore = () => {
    if (busy || savingRef.current || !latest || latest.revision !== revision || awaitingDetail) return;
    setDraft(createManualParameterDraft(latest.detail, node, revision));
    setError(''); setNotice(''); setScanStep(undefined); dirtyCallback.current?.(false);
  };
  const savedDriveConflict = validDetail?.resolvedExecutionPlan?.code==='SPECTRUM_DRIVE_CONTEXT_CONFLICT';
  const save = async (clearDrive = false) => {
    if (!draft || (clearDrive ? dirty || !savedDriveConflict : !dirty) || busy || savingRef.current || !canEdit || stale || awaitingDetail) return;
    setError(''); setNotice('');
    let changes: Record<string, number | number[]> = {};
    try {
      if (!clearDrive) {
        const scanKeys = manualScanKeys(node.procedureId ?? '');
        const submitted = scanStep === undefined ? draft : applyManualScanStep(draft, scanKeys[0], scanKeys[1], scanStep);
        changes = validateManualParameterDraft(submitted, revision);
      }
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : '请检查参数。'); return; }
    savingRef.current = true; setSaving(true); onBusy(true);
    try {
      const updated = clearDrive
        ? await gateway.parameters(workflow.id, node.id, draft.revision, {}, true)
        : await gateway.parameters(workflow.id, node.id, draft.revision, changes);
      if (!mounted.current) return;
      setAwaitingDetail({ revision: updated.revision ?? revision, detail: validDetail });
      setNotice(clearDrive ? '本步已恢复继承已提交的驱动幅度。' : '配置已保存，下次测量生效。'); dirtyCallback.current?.(false);
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
  const scanKeys=manualScanKeys(node.procedureId??'');
  const scanStart=draft.fields.find(f=>f.key===scanKeys[0]),scanStop=draft.fields.find(f=>f.key===scanKeys[1]);
  const canScan=!!scanStart&&!!scanStop&&draft.fields.some(f=>f.key==='points');
  const pointCount=Number(draft.values.points),first=Number(draft.values[scanKeys[0]]),last=Number(draft.values[scanKeys[1]]);
  const initialStep=pointCount>1&&Number.isFinite(first)&&Number.isFinite(last)&&last>first?Number(((last-first)/(pointCount-1)).toPrecision(12)).toString():'';
  let stepProblem='';if(scanStep!==undefined)try{applyManualScanStep(draft,scanKeys[0],scanKeys[1],scanStep);}catch(cause){stepProblem=cause instanceof Error?cause.message:String(cause);}
  const disabled = busy || saving || !canEdit || stale || !!awaitingDetail || versionFrozen;
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
  let previewChanges:Record<string,number|number[]>={},previewProblem='';
  try{previewChanges=manualPreviewChanges(draft,revision,stepProblem||((awaitingDetail||!validDetail)?'等待当前配置载入后预览。':''));}
  catch(cause){previewProblem=cause instanceof Error?cause.message:'请检查参数后预览。';}
  const focusPreviewField=(key:string)=>{const element=document.getElementById(`${formId}-${key}`);if(!element)return;
    const details=element.closest('details');if(details)details.open=true;element.scrollIntoView({block:'center',behavior:'smooth'});element.focus();};
  const renderGroups = (visibleFields: ManualParameterField[]) => manualParameterGroups.map(group => {
      const fields = visibleFields.filter(field => field.group === group.id);
      if (!fields.length) return null;
      return <fieldset className="mpf-group" key={group.id} disabled={disabled}><legend>{group.label}</legend><div className="mpf-fields">{fields.map(field => {
        const id = `${formId}-${field.key}`, problem = fieldError(field);
        const defaultLabel = executionDefaultLabel(validDetail, field);
        const choices = field.rule.enum;
        return <div className={`mpf-field${Object.hasOwn(changed, field.key) ? ' is-edited' : ''}`} key={field.key}>
          <label htmlFor={id}>{field.label}</label>
          <div className="mpf-input-wrap">{choices ? <select id={id} aria-label={`参数 ${field.key}`} aria-describedby={`${id}-hint`} aria-invalid={!!problem} value={draft.values[field.key] ?? ''} onChange={event => edit(field.key, event.target.value)}><option value="" disabled>未设置</option>{choices.map(value => <option key={value} value={displayParameterValue(value, field)}>{displayParameterValue(value, field)}</option>)}</select>
            : <input id={id} aria-label={`参数 ${field.key}`} aria-describedby={`${id}-hint`} aria-invalid={!!problem} inputMode={field.rule.integer ? 'numeric' : 'decimal'} autoComplete="off" value={draft.values[field.key] ?? ''} placeholder="未设置" onChange={event => edit(field.key, event.target.value)} />}
            {field.unit && <span className="mpf-unit" aria-hidden="true">{field.unit}</span>}
          </div>
          <small id={`${id}-hint`} className={problem ? 'mpf-field-error' : ''}>{problem || (field.rule.type==='integerArray'?`${field.rule.minItems}–${field.rule.maxItems} 个整数，以逗号分隔；每项 ${field.min}–${field.max}${field.rule.strictlyIncreasing?'，严格递增':''}`:`${field.min} – ${field.max}${field.unit ? ` ${field.unit}` : ''}${field.rule.integer ? ' · 整数' : ''}`)}</small>
          {defaultLabel && !Object.hasOwn(changed, field.key) && <small>{defaultLabel}</small>}
        </div>;
      })}</div></fieldset>;
    });

  return <section className="mpf-form" aria-label="实验参数" aria-busy={saving}>
    {validDetail?.resolvedExecutionPlan?.status==='ready' && <p className="mpf-message">本次计划：{validDetail.resolvedExecutionPlan.totalRows??'—'} 个采集点 · {validDetail.resolvedExecutionPlan.estimatedTotalShots?.toLocaleString()??'—'} 次采样{dirty?'（修改尚未保存）':''}</p>}
    {validDetail?.resolvedExecutionPlan?.status==='unavailable' && <p className="mpf-message mpf-warning">计划暂不可生成：{validDetail.resolvedExecutionPlan.reason||'请检查参数与前置记录'}</p>}
    {savedDriveConflict && <button type="button" className="secondary" disabled={!canEdit || busy || saving || stale || dirty || !!awaitingDetail} onClick={()=>void save(true)}>恢复继承已提交幅度</button>}
    <div className="mpf-heading"><h3>参数配置</h3><span className={dirty ? 'mpf-unsaved' : 'mpf-state'}>{dirty ? '有未保存修改' : awaitingDetail ? '正在刷新配置…' : canEdit ? '已保存配置' : '当前状态不可编辑'}</span></div>
    {!!validDetail?.executionVersionOptions?.length && <section aria-label="本次采集版本">
      {executionVersion && <p>第 {executionVersion.nextAttemptNumber} 次尝试 · {executionVersion.label}{versionFrozen?' · 参数已冻结':''}</p>}
      <p>仅本次首测与独立复测使用 16 块，每块 2000 次。保存后仍需开始本步。</p>
      <button type="button" className="secondary" disabled={!gateway.executionVersion||!canEdit||busy||saving||stale||dirty||!!awaitingDetail||versionFrozen}
        onClick={()=>void selectVersion('rabi16-v1')}>选择下一次 16 块采集</button>
      {executionVersion && <button type="button" className="secondary" disabled={!gateway.executionVersion||!canEdit||busy||saving||stale||dirty||!!awaitingDetail}
        onClick={()=>void selectVersion('default')}>恢复原执行版本</button>}
      {executionVersion && <details><summary>执行版本详情</summary><p>{executionVersion.scope}</p><dl>
        <dt>执行器</dt><dd>{executionVersion.adapterId}</dd><dt>配置摘要</dt><dd>{executionVersion.adapterProfileHash}</dd>
        <dt>版本选择记录</dt><dd>{executionVersion.authorizationId}</dd></dl></details>}
    </section>}
    <ManualPulsePreview gateway={gateway} workflowId={workflow.id} nodeId={node.id} resourceId={node.resourceId??workflow.target}
      procedureId={node.procedureId??''} revision={revision} changes={previewChanges} fields={draft.fields}
      problem={previewProblem} dirty={dirty} onFocusField={focusPreviewField}/>
    {canScan&&<div className="mpf-field"><label htmlFor={`${formId}-step`}>扫描步进 ({scanStart?.unit})</label><input id={`${formId}-step`} aria-label="已建实验扫描步进" aria-invalid={!!stepProblem} aria-describedby={`${formId}-step-error`} disabled={disabled} inputMode="decimal" value={scanStep??initialStep} onChange={event=>{const raw=event.target.value;setScanStep(raw);setError('');dirtyCallback.current?.(true);try{setDraft(applyManualScanStep(draft,scanKeys[0],scanKeys[1],raw));}catch{/* Keep the prior valid point count until the step is corrected. */}}}/><small id={`${formId}-step-error`} className={stepProblem?'mpf-field-error':''}>{stepProblem||'包含起止点；修改步进会更新扫描点数，保存后才生效。'}</small></div>}
    {draft.fields.length ? renderGroups(primary) : <p className="mpf-empty">当前方法没有可编辑的数值参数。</p>}
    {more.length > 0 && <details className="mpf-more"><summary>更多参数<span>{more.length} 项{moreChanged ? ' · 有未保存修改' : ''}</span></summary>{renderGroups(more)}</details>}
    {stale && <p className="mpf-message mpf-warning" role="alert">配置版本已更新，修改已保留。{latest?.revision === revision ? '请恢复未保存修改后重新编辑。' : '正在读取最新配置…'}</p>}
    {error && <p className="mpf-message mpf-error" role="alert">{error}</p>}
    {notice && <p className="mpf-message mpf-saved" role="status"><Check size={14} />{notice}</p>}
    <footer className="mpf-actions"><button className="secondary" type="button" disabled={busy || saving || !!awaitingDetail || (!dirty && !stale) || latest?.revision !== revision} onClick={restore}><RotateCcw size={14} />恢复未保存修改</button><button className="primary" type="button" disabled={disabled || !dirty} onClick={() => void save()}><Save size={14} />{saving ? '正在保存…' : '保存配置'}</button></footer>
    {readonlyValues.length > 0 && <details className="mpf-readonly"><summary>其他生效参数 · {readonlyValues.length}</summary><dl>{readonlyValues.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value == null ? '未设置' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></details>}
  </section>;
}
