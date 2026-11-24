import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, ChevronDown, GitBranch, Pause, Play, Plus, RotateCcw, Square } from 'lucide-react';
import type { Catalog, CreateWorkflowInput, ExperimentNode, NodeDetail, RemoteAction, Workflow } from '../types';
import { ApiError } from '../lib/remoteGateway';
import type { RemoteGateway } from '../lib/remoteGateway';
import { adapterIdentity, selectableResources } from '../lib/workflowResources';
import { isManualWorkflow, manualStepControls, precedingDependencies, workflowControls } from '../lib/workflowControls';
import { manualDraftInput } from '../lib/manualWorkspaceSelection';
import { manualCreationMatches, manualWorkbenchAction } from '../lib/manualWorkbench';
import type { ManualCreationAttempt } from '../lib/manualWorkbench';
import { createNodeDetailSync, nodeDetailBoundary } from '../lib/nodeDetailSync';
import type { NodeDetailSync } from '../lib/nodeDetailSync';
import { engineLabel } from '../lib/runtimeLabels';
import { createWorkflowStopGate } from '../lib/workflowStopGate';
import { EmptyState, jsonText, MeasurementProgress, RealResult } from './RemotePanels';
import LiveMeasurementSeries from './LiveMeasurementSeries';
import ManualParameterForm from './ManualParameterForm';
import './manual-workbench.css';

export interface ManualExperimentWorkspaceProps {
  gateway: RemoteGateway;
  catalog?: Catalog;
  workflows: Workflow[];
  workflow?: Workflow;
  node?: ExperimentNode;
  busy: boolean;
  pendingStop?: 'pause' | 'abort';
  error?: string;
  onBusy(value: boolean): void;
  onCreate(input: CreateWorkflowInput): Promise<Workflow>;
  onSelectWorkflow(id: string): void;
  onSelectNode(id: string): void;
  onAction(action: RemoteAction, nodeId?: string): Promise<void>;
  onBlueprint(): void;
  onParameterDirty(value: boolean): void;
}

export default function ManualExperimentWorkspace(props: ManualExperimentWorkspaceProps) {
  const { catalog, workflows, workflow, node, busy, error, onCreate, onSelectWorkflow, onSelectNode, onAction, onBlueprint, onParameterDirty } = props;
  const [selectionMode, setSelectionMode] = useState<'new' | 'existing'>();
  const creating = selectionMode === 'new' || (selectionMode === undefined && (!workflow || !isManualWorkflow(workflow)));
  const [step, setStep] = useState<0 | 1>(0);
  const [name, setName] = useState('');
  const [resourceId, setResourceId] = useState('');
  const [pending, setPending] = useState(false);
  const [localError, setLocalError] = useState('');
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [creationCheck, setCreationCheck] = useState('');
  const [canRestart, setCanRestart] = useState(false);
  const pendingCreation = useRef<ManualCreationAttempt | undefined>(undefined);
  const [dirty, setDirty] = useState(false);
  const [, refreshStop] = useState(0);
  const stopGate = useMemo(() => createWorkflowStopGate(), []);
  const inFlight = useRef(false);
  const dirtyRef = useRef(false);
  const manual = workflows.filter(isManualWorkflow);
  const active = !creating && workflow && isManualWorkflow(workflow) ? workflow : undefined;
  const selected = active?.nodes.find(item => item.id === node?.id) ?? active?.nodes.find(item => item.id === active.currentNodeId) ?? active?.nodes[0];
  const resources = selectableResources(catalog, 'real');
  const resource = resources.find(item => item.id === resourceId) ?? (!resourceId ? resources[0] : undefined);
  const adapter = adapterIdentity(catalog, active);
  const controls = active ? workflowControls(active) : undefined;
  const locked = busy || pending;
  const pendingStop = props.pendingStop ?? (active ? stopGate.current(active.id)?.action : undefined);
  const dirtyChanged = useCallback((value: boolean) => {
    dirtyRef.current = value; setDirty(value); onParameterDirty(value);
  }, [onParameterDirty]);

  const chooseWorkflow = (id: string) => {
    if (locked || dirtyRef.current) return;
    setStep(0); setLocalError(''); setSelectionMode(id ? 'existing' : 'new');
    if (id && manual.some(item => item.id === id)) onSelectWorkflow(id);
  };
  const next = async () => {
    if (locked || inFlight.current || dirtyRef.current) return;
    if (active) { if (selected) onSelectNode(selected.id); setStep(1); return; }
    if (!creating || !resource || !name.trim() || unconfirmed) return;
    inFlight.current = true; setPending(true); setLocalError('');
    pendingCreation.current = { name: name.trim(), resourceId: resource.id, knownIds: workflows.map(item => item.id) };
    setCanRestart(false); setCreationCheck('');
    try {
      const created = await onCreate(manualDraftInput({ name: name.trim(), machine: '八号机', qubits: 1, target: resource.id, type: 'adaptive', policy: 'strict', scenario: 'normal', resourceIds: [resource.id] }));
      onSelectWorkflow(created.id);
      const first = created.nodes.find(item => item.capability === 'available') ?? created.nodes[0];
      if (first) onSelectNode(first.id);
      setSelectionMode('existing'); setStep(1);
      pendingCreation.current = undefined;
    } catch (cause) {
      const uncertain = !(cause instanceof ApiError) || cause.status >= 500 || cause.status === 408;
      setUnconfirmed(uncertain);
      setLocalError(uncertain ? '创建结果待核查，请从实验列表核对。' : cause.message);
    } finally { inFlight.current = false; setPending(false); }
  };
  const checkCreation = async () => {
    const attempt = pendingCreation.current;
    if (!attempt || locked || inFlight.current) return;
    inFlight.current = true; setPending(true); setCanRestart(false); setCreationCheck('');
    try {
      const matches = manualCreationMatches(await props.gateway.list(), attempt);
      if (matches.length === 1) {
        const found = await props.gateway.get(matches[0].id);
        if (!manualCreationMatches([found], attempt).length) throw new Error('实验信息已变化，请从任务列表核对。');
        onSelectWorkflow(found.id);
        const first = found.nodes.find(item => item.capability === 'available') ?? found.nodes[0];
        if (first) onSelectNode(first.id);
        setSelectionMode('existing'); setStep(1); setUnconfirmed(false); setLocalError(''); pendingCreation.current = undefined;
      } else if (matches.length > 1) {
        setCreationCheck('发现多个同名实验，请从任务列表选择。');
      } else {
        setCreationCheck('未找到同名实验。可再次核查，或继续新建。'); setCanRestart(true);
      }
    } catch (cause) { setCreationCheck(cause instanceof Error ? cause.message : String(cause)); }
    finally { inFlight.current = false; setPending(false); }
  };
  const act = async (action: RemoteAction, targetId?: string) => {
    if (!active) return;
    const stopping = action === 'pause' || action === 'abort';
    if (stopping) {
      const pendingAction = props.pendingStop ?? stopGate.current(active.id)?.action;
      if (pendingAction === 'abort' || pendingAction === action) return;
    } else if (locked || inFlight.current) return;
    const command = manualWorkbenchAction(active, selected?.id ?? '', action, dirtyRef.current, targetId);
    if (!command) return;
    if (stopping) {
      const stop = stopGate.begin(active.id, action);
      if (!stop) return;
      refreshStop(value => value + 1); setLocalError('');
      try { await onAction(command.action, command.nodeId); }
      catch (cause) { setLocalError(cause instanceof Error ? cause.message : String(cause)); }
      finally { if (stopGate.finish(stop)) refreshStop(value => value + 1); }
      return;
    }
    inFlight.current = true; setPending(true); setLocalError('');
    try { await onAction(command.action, command.nodeId); }
    catch (cause) { setLocalError(cause instanceof Error ? cause.message : String(cause)); }
    finally { inFlight.current = false; setPending(false); }
  };

  return <main className="mw-workspace page-enter">
    <header className="mw-heading"><div><h1>手动实验</h1></div><div className="mw-heading-actions">
      {active && <><button className="secondary" disabled={locked || dirty} onClick={onBlueprint}><GitBranch size={14} />查看蓝图</button><button className="secondary" disabled={!!pendingStop || !controls?.pause} onClick={() => void act('pause')}><Pause size={14} />{pendingStop === 'pause' ? '正在请求暂停…' : '暂停'}</button><button className="secondary mw-stop" disabled={pendingStop === 'abort' || !manualWorkbenchAction(active, selected?.id ?? '', 'abort')} onClick={() => void act('abort')}><Square size={13} />{pendingStop === 'abort' ? '正在请求终止…' : '终止'}</button></>}
      <button className="secondary" disabled={locked || dirty} onClick={() => chooseWorkflow('')}><Plus size={14} />新建实验</button>
    </div></header>
    <div className="mw-task-bar"><label><span>实验任务</span><select aria-label="手动实验任务" value={active?.id ?? ''} disabled={locked || dirty} onChange={event => chooseWorkflow(event.target.value)}><option value="">新建实验</option>{manual.map(item => <option key={item.id} value={item.id}>{item.name} · {item.target} · {engineLabel(item.engineState ?? item.status)}</option>)}</select></label>{active && <span className="mw-task-state">{engineLabel(active.engineState ?? active.status)}</span>}</div>
    <ol className="mw-steps" aria-label="实验配置步骤"><li className={step === 0 ? 'current' : 'complete'} aria-current={step === 0 ? 'step' : undefined}><span>{step === 1 ? <Check size={15} /> : '1'}</span>实验对象</li><li className={step === 1 ? 'current' : ''} aria-current={step === 1 ? 'step' : undefined}><span>2</span>实验配置</li></ol>
    {(localError || error) && <p className="rp-error" role="alert">{localError || error}</p>}
    {step === 0 ? <section className="mw-object" aria-label="实验对象">
      <header><h2>{active ? active.name : '新建实验'}</h2><span className="rp-mode real">人工逐步</span></header>
      <div className="mw-object-fields">
        <label><span>实验名称</span><input aria-label="实验名称" value={active?.name ?? name} readOnly={!!active} disabled={locked} maxLength={120} placeholder="输入实验名称" onChange={event => setName(event.target.value)} /></label>
        <label><span>实验对象</span>{active ? <input aria-label="实验对象" readOnly value={active.target} /> : <select aria-label="实验对象" value={resource?.id ?? ''} disabled={locked || !resources.length} onChange={event => setResourceId(event.target.value)}>{!resources.length && <option value="">暂无可用对象</option>}{resources.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select>}</label>
        <label><span>机台</span><input aria-label="机台" readOnly value={active?.machine ?? '八号机'} /></label>
        <label><span>测控配置</span><input aria-label="测控配置" readOnly value={adapter?.name ?? adapter?.id ?? '正在读取…'} /></label>
        {active && <label className="mw-wide"><span>实验方法</span><select aria-label="实验方法" value={selected?.id ?? ''} disabled={locked} onChange={event => onSelectNode(event.target.value)}>{active.nodes.map(item => <option key={item.id} value={item.id}>{item.label} · {item.qubits || active.target}</option>)}</select></label>}
      </div>
      {creating && unconfirmed && <div className="mw-create-recovery"><p role="status">{creationCheck || '请先核查上次创建结果。'}</p><div><button className="secondary" disabled={locked} onClick={() => void checkCreation()}>核查创建结果</button>{canRestart && <button className="secondary" disabled={locked} onClick={() => { setUnconfirmed(false); setCanRestart(false); setCreationCheck(''); setLocalError(''); pendingCreation.current = undefined; }}>继续新建</button>}</div></div>}
      <footer><span>{creating ? '下一步创建草稿' : `${active?.nodes.length ?? 0} 项实验方法`}</span><button className="primary" disabled={locked || dirty || (creating ? !resource || !name.trim() || unconfirmed : !active || !selected)} onClick={() => void next()}>{pending ? '正在创建…' : '下一步'}<ArrowRight size={14} /></button></footer>
    </section> : active && selected ? <ManualConfiguration key={active.id} {...props} workflow={active} node={selected} busy={locked} dirty={dirty} onDirty={dirtyChanged} onSelectNode={id => { if (!locked && !dirtyRef.current && active.nodes.some(item => item.id === id)) onSelectNode(id); }} onAction={act} onPrevious={() => { if (!locked && !dirtyRef.current) setStep(0); }} /> : <EmptyState title="请选择实验任务" />}
  </main>;
}

function ManualConfiguration({ gateway, workflow, node, busy, onBusy, dirty, onDirty, onSelectNode, onAction, onPrevious }: {
  gateway: RemoteGateway; workflow: Workflow; node: ExperimentNode; busy: boolean; dirty: boolean;
  onBusy(value: boolean): void; onDirty(value: boolean): void; onSelectNode(id: string): void;
  onAction(action: RemoteAction, nodeId?: string): Promise<void>; onPrevious(): void;
}) {
  const [tab, setTab] = useState<'config' | 'results' | 'history'>('config');
  const [target, setTarget] = useState('');
  const [reload, setReload] = useState(0);
  const [snapshot, setSnapshot] = useState<{ key: string; detail: NodeDetail; revision: number }>();
  const [error, setError] = useState('');
  const syncRef = useRef<NodeDetailSync | undefined>(undefined);
  const workflowRef = useRef(workflow);
  workflowRef.current = workflow;
  const key = JSON.stringify([workflow.id, node.id]);
  const boundary = nodeDetailBoundary(workflow, node);
  const detail = snapshot?.key === key ? snapshot.detail : undefined;
  useEffect(() => {
    setError('');
    const sync = createNodeDetailSync({
      read: async () => {
        const revision = workflowRef.current.revision ?? 0;
        const detail = await gateway.node(workflow.id, node.id);
        return { detail, revision };
      },
      onValue: value => {
        if (value.revision !== (workflowRef.current.revision ?? 0)) return;
        setSnapshot({ key, ...value }); setError('');
      }, onError: setError,
    });
    syncRef.current = sync;
    return () => { sync.dispose(); if (syncRef.current === sync) syncRef.current = undefined; };
  }, [gateway, workflow.id, node.id, key, reload]);
  useEffect(() => { syncRef.current?.observe(workflow.revision, boundary); }, [workflow.revision, boundary, key, reload]);
  useEffect(() => { setTarget(''); }, [node.id, workflow.currentNodeId]);
  const controls = workflowControls(workflow);
  const selectedControl = manualStepControls(workflow, node);
  const ancestors = precedingDependencies(workflow, controls.current);
  const dependencies = (node.dependsOn ?? []).map(id => workflow.nodes.find(item => item.id === id)?.label ?? id);
  const unavailable = node.capability !== 'available';

  return <section className="mw-config" aria-label="实验配置">
    <aside className="mw-methods"><header><h2>实验方法</h2><span>{workflow.target}</span></header><div className="mw-method-columns" aria-hidden="true"><span>对象 / 方法</span><span>状态</span></div><ol>{workflow.nodes.map(item => {
      const state = manualStepControls(workflow, item);
      return <li key={item.id}><button type="button" aria-current={node.id === item.id ? 'step' : undefined} className={node.id === item.id ? 'selected' : ''} disabled={busy || (dirty && node.id !== item.id)} onClick={() => onSelectNode(item.id)}><span className="mw-method-label"><small>{item.resourceId ?? item.qubits ?? workflow.target}</small><strong>{item.label}</strong></span><span className={`mw-method-state ${item.engineState === 'verified' ? 'verified' : ''}`}>{item.capability === 'available' ? state.label : '未接入'}</span></button></li>;
    })}</ol></aside>
    <div className="mw-detail"><header className="mw-detail-heading"><div><span>{node.qubits || workflow.target}</span><h2>{node.label}</h2></div><span className="mw-task-state">{selectedControl.label}</span></header>
      <div className="mw-tabs" role="tablist" aria-label="实验详情">{([{ id: 'config', label: '实验配置' }, { id: 'results', label: '实验结果' }, { id: 'history', label: '执行记录' }] as const).map(item => <button type="button" role="tab" id={`mw-tab-${item.id}`} aria-controls={`mw-panel-${item.id}`} aria-selected={tab === item.id} key={item.id} onClick={() => setTab(item.id)}>{item.label}</button>)}</div>
      <div className="mw-detail-content">
        {error && <p className="rp-error" role="alert">{error}<button className="secondary" disabled={busy} onClick={() => setReload(value => value + 1)}>重新读取</button></p>}
        <div role="tabpanel" id="mw-panel-config" aria-labelledby="mw-tab-config" hidden={tab !== 'config'}>
          <div className="mw-dependencies"><span>前置实验</span><p>{dependencies.length ? dependencies.join('、') : '无'}</p></div>
          <ManualParameterForm key={key} gateway={gateway} workflow={workflow} node={node} detail={detail} detailRevision={snapshot?.key === key ? snapshot.revision : undefined} busy={busy} onBusy={onBusy} canEdit={controls.edit && !unavailable && snapshot?.key === key && snapshot.revision === (workflow.revision ?? 0)} onDirtyChange={onDirty} onSaved={() => setReload(value => value + 1)} />
          <details className="mw-technical"><summary>方法与参数版本<ChevronDown size={13} /></summary><pre>{jsonText({ procedureId: node.procedureId, ownerId: node.ownerId, capability: node.capability, parameterVersions: node.parameterVersions, dependsOn: node.dependsOn, adapter: workflow.adapter, adapterId: workflow.adapterId })}</pre></details>
        </div>
        {tab === 'results' && <div role="tabpanel" id="mw-panel-results" aria-labelledby="mw-tab-results"><MeasurementProgress node={node} workflow={workflow} /><LiveMeasurementSeries key={key} gateway={gateway} workflowId={workflow.id} node={node} adapterId={workflow.adapterId} archived={controls.terminal} /><RealResult result={node.result} /></div>}
        {tab === 'history' && <div role="tabpanel" id="mw-panel-history" aria-labelledby="mw-tab-history"><ManualHistory detail={detail} workflow={workflow} /></div>}
      </div>
      <footer className="mw-controls">
        {(dirty || (!selectedControl.approve && selectedControl.reason)) && <p className="mw-action-reason" role="status">{dirty ? '请先保存或恢复参数' : selectedControl.reason}</p>}
        {controls.reconciliation && <button className="secondary" disabled={busy} onClick={() => void onAction('reconcile')}>核查回执</button>}
        {controls.hardware && <button className="secondary" disabled={busy || dirty} onClick={() => void onAction('confirm_hardware')}>确认接线</button>}
        <div className="mw-submit-row"><button className="secondary" disabled={busy || dirty} onClick={onPrevious}><ArrowLeft size={14} />上一步</button><div>
          {manualWorkbenchAction(workflow, node.id, 'backtrack', false, node.id) && <button className="secondary" disabled={busy || dirty} onClick={() => void onAction('backtrack', node.id)}><RotateCcw size={14} />重新调整</button>}
          {selectedControl.approve ? <button className="primary" disabled={busy || dirty} onClick={() => void onAction('approve', node.id)}><Check size={14} />确认结果</button> : <button className="primary" disabled={busy || dirty || !selectedControl.run} onClick={() => void onAction('run_step', node.id)}><Play size={14} />{node.attempt > 0 ? '重新测量' : '提交测量'}</button>}
        </div></div>
        {controls.backtrack && ancestors.length > 0 && <div className="mw-backtrack"><label><span>回退至</span><select aria-label="回退目标实验" value={target} disabled={busy || dirty} onChange={event => setTarget(event.target.value)}><option value="">选择前置实验</option>{ancestors.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label><button className="secondary" disabled={busy || dirty || !ancestors.some(item => item.id === target)} onClick={() => void onAction('backtrack', target)}><RotateCcw size={13} />回退</button></div>}
      </footer>
    </div>
  </section>;
}

function ManualHistory({ detail, workflow }: { detail?: NodeDetail; workflow: Workflow }) {
  return <div className="mw-history">{!detail ? <p className="rp-muted">正在读取执行记录…</p> : !detail.attempts.length ? <EmptyState title="暂无执行记录" /> : [...detail.attempts].reverse().map(attempt => <details className="rp-attempt" key={attempt.id}><summary><span>第 {attempt.number} 次 · {({ attention: '待处理', cancelled: '已取消' } as Record<string, string>)[attempt.state] ?? engineLabel(attempt.state)}</span><time>{new Date(attempt.startedAt).toLocaleString('zh-CN')}</time></summary>{attempt.error != null && <details className="rp-error"><summary>执行错误</summary><pre>{jsonText(attempt.error)}</pre></details>}<RealResult result={attempt.result} compact /><details><summary>执行参数与回执</summary><pre>{jsonText({ id: attempt.id, operationId: attempt.operationId, parameters: attempt.parameters, parameterVersions: attempt.parameterVersions })}</pre></details></details>)}
    {!!detail?.decisions.length && <section className="mw-decisions"><h3>操作与回退</h3>{[...detail.decisions].reverse().map(decision => <article key={decision.id}><div><strong>{({ approve: '确认结果', accept: '验收', backtrack: '回退', retry: '复测', adjust: '调参', pause: '暂停', abort: '终止', parameters_updated: '保存配置', commit: '提交校准' } as Record<string, string>)[decision.action] ?? decision.action}</strong><time>{new Date(decision.createdAt).toLocaleString('zh-CN')}</time></div><p>{decision.reason}</p>{decision.targetNodeId && <p>回退目标：{workflow.nodes.find(item => item.id === decision.targetNodeId)?.label ?? decision.targetNodeId}</p>}<details><summary>操作回执</summary><pre>{jsonText(decision)}</pre></details></article>)}</section>}
    {!!workflow.events.length && <details className="mw-events"><summary>任务事件</summary><ol>{[...workflow.events].reverse().map((event, index) => <li key={`${event.time}-${index}`}><time>{event.time}</time><span>{event.message}</span>{event.technicalDetails && <details><summary>原始记录</summary><pre>{JSON.stringify(event.technicalDetails, null, 2)}</pre></details>}</li>)}</ol></details>}
  </div>;
}
