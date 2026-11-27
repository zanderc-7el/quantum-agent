import { useEffect, useRef, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import type { Catalog, CreateWorkflowInput } from '../types';
import type { Configuration, Qubit, WorkspaceGateway } from '../lib/workspaceGateway';
import { selectableResources } from '../lib/workflowResources';
import { creationInput, isReadyCreationQubit, publishedVersions, resetCreationBinding } from '../lib/workflowCreationBinding';
import type { CreationBinding } from '../lib/workflowCreationBinding';
import { topologyExecutionTarget } from '../lib/topologyExecutionTarget';
import ChipTopology from './ChipTopology';
import { ExecutionModeSelector } from './ManualStepPanel';
import { WorkflowCoverageSelect } from './WorkflowCoverage';
import { offersSmartStart } from '../lib/smartQ11Launch';

export default function WorkflowCreationForm({ api, catalog, form, onChange, busy, error, onCreate, onSmartStart, onCancel }: {
  api: WorkspaceGateway; catalog?: Catalog; form: CreateWorkflowInput; onChange(form: CreateWorkflowInput): void;
  busy: boolean; error?: string; onCreate(input: CreateWorkflowInput): Promise<void>; onSmartStart?(input: CreateWorkflowInput): Promise<void>; onCancel(): void;
}) {
  const [binding, setBinding] = useState<CreationBinding>('fixed');
  const [config, setConfig] = useState<{ chips: Configuration[]; deployments: Configuration[] }>();
  const [loadError, setLoadError] = useState(''); const [selectionError, setSelectionError] = useState('');
  const submitting = useRef(false); const [sending, setSending] = useState(false);
  const configurable = form.mode === 'real' && form.experiment === 'single';
  const published = configurable && binding === 'published';
  const smart = !!onSmartStart && offersSmartStart(form, catalog, published);
  useEffect(() => {
    if (!configurable) return;
    let current = true; setLoadError(''); setConfig(undefined);
    const list = (value: Configuration[] | { items: Configuration[] }) => Array.isArray(value) ? value : value.items;
    void Promise.all([api.get<Configuration[] | { items: Configuration[] }>('/chips'), api.get<Configuration[] | { items: Configuration[] }>('/deployments')])
      .then(([chips, deployments]) => { if (current) setConfig({ chips: list(chips), deployments: list(deployments) }); })
      .catch(cause => { if (current) setLoadError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { current = false; };
  }, [api, configurable]);
  const chips = publishedVersions(config?.chips ?? []), deployments = publishedVersions(config?.deployments ?? []);
  const chip = published ? chips.find(v => v.id === form.chipVersionId) : undefined;
  const deployment = published ? deployments.find(v => v.id === form.deploymentVersionId) : undefined;
  const stations = [...new Set(deployments.map(v => v.machine || v.name))];
  const resources: (Partial<Qubit> & { id: string; label: string; engineKey?: string })[] = published ? (chip?.qubits ?? []).map(q => ({ ...q, label: q.label || q.id })) : selectableResources(catalog, form.mode);
  useEffect(() => {
    if (published || !catalog) return;
    const available = selectableResources(catalog, form.mode);
    const valid = (form.resourceIds ?? []).filter(id => available.some(q => q.id === id));
    const ids = valid.length ? valid : available[0] ? [available[0].id] : [];
    if (JSON.stringify(ids) !== JSON.stringify(form.resourceIds)) onChange({ ...form, resourceIds: ids, target: ids.map(id => available.find(q => q.id === id)!.label).join(' / ') });
  }, [catalog, form.mode, form.experiment, published, JSON.stringify(form.resourceIds), onChange]);
  const selected = resources.find(q => q.id === form.resourceIds?.[0]);
  const locked = busy || sending;
  const coverageCatalog = published && catalog ? { ...catalog, workingPointCoverage: { ...catalog.workingPointCoverage, available: false, resourceIds: [], reason: '初值来自所选发布芯片；不复用固定配置的参考工作点。' } } as Catalog : catalog;
  let problem = '';
  try { if (published && !config) throw new Error('正在读取发布配置。'); creationInput(form, published ? 'published' : 'fixed', config?.chips ?? [], config?.deployments ?? [], catalog); }
  catch (cause) { problem = cause instanceof Error ? cause.message : String(cause); }
  const change = (next: CreateWorkflowInput) => { setSelectionError(''); onChange(next); };
  const changeMode = (mode: 'real' | 'replay') => {
    setBinding('fixed');
    const next = resetCreationBinding(form, {mode, machine:'八号机', ...(!published ? {resourceIds:form.resourceIds,target:form.target} : {})});
    const available = selectableResources(catalog,mode);
    const selected = next.resourceIds?.filter(id=>available.some(item=>item.id===id)) ?? [];
    const proposed = {...next, policy:'autopilot' as const, resourceIds:selected.length?selected:available.slice(0,1).map(item=>item.id)};
    change(offersSmartStart(proposed,catalog) ? proposed : next);
  };
  const chooseResource = (id: string) => {
    const resource = resources.find(q => q.id === id);
    if (!resource || (published && !isReadyCreationQubit(resource as Qubit))) { setSelectionError('该比特的实测身份或初始化配置尚未确认。'); return; }
    change({ ...form, resourceIds: [id], target: resource.label });
  };
  const submit = async () => {
    if (locked || submitting.current) return;
    try {
      if (published && !config) throw new Error('发布配置尚未读取完成。');
      const input = creationInput(form, published ? 'published' : 'fixed', config?.chips ?? [], config?.deployments ?? [], catalog);
      submitting.current = true; setSending(true);
      if (smart && onSmartStart) await onSmartStart(input);
      else await onCreate(input);
    } catch (cause) { setSelectionError(cause instanceof Error ? cause.message : String(cause)); }
    finally { submitting.current = false; setSending(false); }
  };
  return <><div className="rp-eyebrow">NEW EXPERIMENT</div><h2>新建工作流</h2>{catalog?.adapter && <div className="rp-info">当前测量执行器：{catalog.adapter.name ?? catalog.adapter.id}</div>}
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <label>运行方式<select aria-label="运行方式" disabled={locked} value={form.mode} onChange={event => changeMode(event.target.value as 'real' | 'replay')}><option value="replay">历史回放</option><option value="real">真实测量</option></select></label>
      {form.mode === 'real' && <ExecutionModeSelector policy={form.policy} onChange={policy => change({ ...form, policy })} disabled={locked} />}
      <label>实验流程<select aria-label="实验流程" disabled={locked} value={form.experiment} onChange={event => { setBinding('fixed'); const experiment = event.target.value as 'single' | 'two'; change(resetCreationBinding(form, { experiment, qubits: experiment === 'two' ? 2 : 1, machine: '八号机', ...(!published ? { resourceIds: form.resourceIds?.slice(0, 1), target: selected?.label ?? '', coverage: form.coverage } : {}) })); }}><option value="single">单比特校准</option><option value="two">双比特校准（包含单比特前置）</option></select></label>
      {configurable && <><label>配置来源<select aria-label="配置来源" disabled={locked} value={binding} onChange={event => { setBinding(event.target.value as CreationBinding); change(resetCreationBinding(form, { machine: event.target.value === 'fixed' ? '八号机' : '' })); }}><option value="fixed">当前固定配置</option><option value="published">已发布芯片与接线</option></select></label>{loadError && <p className="rp-error" role="alert">发布配置读取失败：{loadError}</p>}{config && !chips.length && <p className="rp-muted">尚无已发布芯片；当前固定配置仍可使用。</p>}</>}
      {published && <>
        <label>芯片版本<select aria-label="新建工作流芯片版本" value={form.chipVersionId ?? ''} disabled={locked || !config} onChange={event => change(resetCreationBinding(form, { chipVersionId: event.target.value || undefined, machine: '' }))}><option value="">选择已发布芯片</option>{chips.map(v => <option value={v.id} key={v.id}>{v.choiceLabel}</option>)}</select></label>
        <label>机台<select aria-label="新建工作流机台" value={form.machine} disabled={locked || !chip} onChange={event => change(resetCreationBinding(form, { chipVersionId: form.chipVersionId, machine: event.target.value }))}><option value="">选择机台</option>{stations.map(station => <option key={station} value={station}>{station}</option>)}</select></label>
        <label>接线版本<select aria-label="新建工作流接线版本" value={form.deploymentVersionId ?? ''} disabled={locked || !chip || !form.machine} onChange={event => change(resetCreationBinding(form, { chipVersionId: form.chipVersionId, machine: form.machine, deploymentVersionId: event.target.value || undefined }))}><option value="">选择已发布接线</option>{deployments.filter(v => (v.machine || v.name) === form.machine).map(v => <option value={v.id} key={v.id}>{v.choiceLabel}</option>)}</select></label>
      </>}
      <label>目标资源<select aria-label="目标资源" required disabled={locked || published && !chip} value={form.resourceIds?.[0] ?? ''} onChange={event => chooseResource(event.target.value)}><option value="" disabled>选择比特</option>{resources.map(q => <option key={q.id} value={q.id} disabled={published && !isReadyCreationQubit(q as Qubit)}>{q.label}{published && !isReadyCreationQubit(q as Qubit) ? ' · 待核对' : ''}{!published && q.engineKey ? ` · ${q.engineKey}` : ''}</option>)}</select></label>
      {published && chip && <><ChipTopology key={chip.id} scope={chip.id} topology={chip.topology} qubits={chip.qubits} selectedResourceId={form.resourceIds?.[0] ?? ''} disabled={locked} onSelect={id => { const target = topologyExecutionTarget(chip.topology, chip.qubits ?? [], id); if (target.resourceId) chooseResource(target.resourceId); else setSelectionError(target.reason ?? '该图示编号不能作为实测目标。'); }} /><p className="rp-info">初值来源：{chip.choiceLabel} · {deployment?.choiceLabel ?? '尚未选择接线'}{selected ? ` · ${selected.label}` : ''}</p></>}
      {form.experiment === 'two' && <label>第二个资源<select aria-label="第二个资源" required disabled={locked} value={form.resourceIds?.[1] ?? ''} onChange={event => { const second = resources.find(q => q.id === event.target.value); change({ ...form, resourceIds: selected && second ? [selected.id, second.id] : [], target: selected && second ? `${selected.label} / ${second.label}` : '' }); }}><option value="" disabled>选择第二个比特</option>{resources.filter(q => q.id !== form.resourceIds?.[0]).map(q => <option key={q.id} value={q.id}>{q.label}</option>)}</select></label>}
      {form.mode === 'real' && !resources.length && <p className="rp-info">暂无可用比特</p>}
      <WorkflowCoverageSelect catalog={coverageCatalog} form={form} onChange={coverage => change({ ...form, coverage })} />
      <label>任务名称<input value={form.name} disabled={locked} onChange={event => change({ ...form, name: event.target.value })} placeholder={`${form.target || '目标比特'} · 单比特测控`} maxLength={120} /></label>
      {(error || selectionError) && <p role="alert" className="rp-error">{error || selectionError}</p>}{published && problem && <p className="rp-muted">{problem}</p>}
      {smart && <p className="rp-info">八号机 Q11 · 主机读出、XY 驱动和采集卡。开始后自动复测，并记录每次采样。</p>}
      <div className="rp-modal-actions"><button type="button" className="secondary" onClick={onCancel}>取消</button><button className="primary" type="submit" disabled={locked || !!problem}>{locked ? '正在提交…' : smart ? '确认接线并开始 Q11 实测' : '创建工作流'}<ArrowRight size={15} /></button></div>
    </form></>;
}
