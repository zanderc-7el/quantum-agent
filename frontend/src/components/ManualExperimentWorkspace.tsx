import { currentResultComparisonSource, attemptResultComparisonSource } from '../lib/resultComparisonSource';
import type { ResultComparisonSource } from '../lib/resultComparisonSource';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, ChevronDown, GitBranch, Pause, Play, Plus, RotateCcw, Square } from 'lucide-react';
import type { Catalog, CreateWorkflowInput, ExperimentNode, NodeDetail, RemoteAction, Workflow } from '../types';
import { ApiError } from '../lib/remoteGateway';
import type { RemoteGateway } from '../lib/remoteGateway';
import { adapterIdentity } from '../lib/workflowResources';
import { isManualWorkflow, manualStepControls, precedingDependencies, workflowControls } from '../lib/workflowControls';
import { manualDraftInput } from '../lib/manualWorkspaceSelection';
import { manualWorkbenchAction } from '../lib/manualWorkbench';
import { createNodeDetailSync, nodeDetailBoundary } from '../lib/nodeDetailSync';
import type { NodeDetailSync } from '../lib/nodeDetailSync';
import { engineLabel } from '../lib/runtimeLabels';
import { createWorkflowStopGate } from '../lib/workflowStopGate';
import { EmptyState, jsonText, MeasurementProgress, RealResult } from './RemotePanels';
import LiveMeasurementSeries from './LiveMeasurementSeries';
import ManualParameterForm from './ManualParameterForm';
import './manual-workbench.css';
import ManualCreationParameters from './ManualCreationParameters';
import ChipTopology from './ChipTopology';
import {isConfirmedExecutionQubit,topologyExecutionTarget} from '../lib/topologyExecutionTarget';
import { manualCreationParameters, catalogManualMethods, manualResources, workingPointBlockReason } from '../lib/manualExperimentDraft';
import { experimentPhases } from '../lib/experimentEvidence';
import type { ScienceManualContext } from '../lib/scienceGateway';
import type { Configuration, WorkspaceGateway } from '../lib/workspaceGateway';
import ScienceExecutionSetup from './ScienceExecutionSetup';
import {ScienceFluxComparison} from './ScienceWorkbench';

export interface ManualExperimentWorkspaceProps {
  onCompareResult?: (source: ResultComparisonSource) => void;
  gateway: RemoteGateway;
  workspaceApi?: WorkspaceGateway;
  workingPoint?: ScienceManualContext;
  onClearWorkingPoint?(): void;
  catalog?: Catalog;
  workflows: Workflow[];
  workflow?: Workflow;
  workflowOpenToken?: number;
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
  const [executionSetup, setExecutionSetup] = useState<'classic'|'science-v3'|'science-v3-irb'|'science-shape-repeat5'|'science-flux'>('classic');
  const [scienceDraft, setScienceDraft] = useState<CreateWorkflowInput>();
  const headingRef = useRef<HTMLElement>(null);
  useEffect(() => { headingRef.current?.scrollIntoView({ block: 'start' }); }, [step, selectionMode]);
  const [name, setName] = useState('');
  const [resourceId, setResourceId] = useState('');
  const [procedureId, setProcedureId] = useState('');
  const [creationValues, setCreationValues] = useState<Record<string,string>>({});
  const [chips, setChips] = useState<Configuration[]>([]);
  const [chipVersionId, setChipVersionId] = useState('');
  const [deployments, setDeployments] = useState<Configuration[]>([]);
  const [deploymentVersionId, setDeploymentVersionId] = useState('');
  const creationOperation = useRef<string|undefined>(undefined);
  const [chipError, setChipError] = useState('');
  const pointBlock=executionSetup==='science-flux'?undefined:workingPointBlockReason(catalog,props.workingPoint);
  useEffect(()=>{if(props.workingPoint){setExecutionSetup(props.workingPoint.executionProfile==='science-flux'?'science-flux':'classic');setSelectionMode('new');setStep(0);setResourceId(props.workingPoint.resourceId??'');setChipVersionId(props.workingPoint.chipVersionId??'');setDeploymentVersionId('');}},[props.workingPoint]);
  useEffect(() => { let current=true; if(props.workspaceApi) void Promise.all([props.workspaceApi.get<Configuration[]|{items:Configuration[]}>('/chips'),props.workspaceApi.get<Configuration[]|{items:Configuration[]}>('/deployments')]).then(([data,wiring])=>{if(current){setChips(Array.isArray(data)?data:data.items);setDeployments(Array.isArray(wiring)?wiring:wiring.items);setChipError('');}}).catch(cause=>{if(current)setChipError(String(cause));}); return ()=>{current=false;}; }, [props.workspaceApi]);
  const [pending, setPending] = useState(false);
  const [localError, setLocalError] = useState('');
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [creationCheck, setCreationCheck] = useState('');
  const pendingCreation = useRef<CreateWorkflowInput | undefined>(undefined);
  const [dirty, setDirty] = useState(false);
  const [, refreshStop] = useState(0);
  const stopGate = useMemo(() => createWorkflowStopGate(), []);
  const inFlight = useRef(false);
  const dirtyRef = useRef(false);
  const openedToken=useRef(props.workflowOpenToken);
  useEffect(()=>{
    if(openedToken.current===props.workflowOpenToken)return;
    openedToken.current=props.workflowOpenToken;
    if(dirtyRef.current||busy||pending||unconfirmed||!workflow||!isManualWorkflow(workflow))return;
    setSelectionMode('existing');setStep(0);setLocalError('');
  },[props.workflowOpenToken]);
  const manual = workflows.filter(isManualWorkflow);
  const active = !creating && workflow && isManualWorkflow(workflow) ? workflow : undefined;
  const selected = active?.nodes.find(item => item.id === node?.id) ?? active?.nodes.find(item => item.id === active.currentNodeId) ?? active?.nodes[0];
  const chipVersion = chips.flatMap(c=>c.versions??[]).find(v=>v.id===chipVersionId);
  const deploymentVersion = deployments.flatMap(c=>c.versions??[]).find(v=>v.id===deploymentVersionId);
  const resources = chipVersionId&&!chipVersion?[]:manualResources(catalog,chipVersion);
  const confirmedResource = (id:string) => !chipVersionId || !!chipVersion && chipVersion.qubits?.filter(q=>q.id===id).length===1 && isConfirmedExecutionQubit(chipVersion.qubits.find(q=>q.id===id));
  const resource = resources.find(item => item.id === resourceId && confirmedResource(item.id));
  const methods = catalogManualMethods(catalog);
  const method = methods.find(item=>item.procedureId===procedureId);
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
  const chooseResource = (id:string) => {
    if (locked || !procedureId) return;
    if (id && (!resources.some(item=>item.id===id) || !confirmedResource(id))) {setLocalError('此编号的实测身份尚未确认或不属于当前芯片版本，仅供浏览。');return;}
    setResourceId(id);setLocalError('');
  };
  const next = async () => {
    if (locked || inFlight.current || dirtyRef.current) return;
    if (pointBlock) {setLocalError(pointBlock);return;}
    if (active) { if (selected) onSelectNode(selected.id); setStep(1); return; }
    if (!creating || unconfirmed) return;
    let input:CreateWorkflowInput;
    if(executionSetup!=='classic'){
      if(!scienceDraft)return;
      input=scienceDraft;
    }else{
      if(!resource || !name.trim() || !method || method.capability !== 'available' || (chipVersionId && !deploymentVersion))return;
      let parameters:Record<string,number>;
      try { parameters=manualCreationParameters(procedureId,creationValues,method.parameterBounds??catalog?.parameterBounds); }
      catch(cause){setLocalError(cause instanceof Error?cause.message:String(cause));return;}
      input={name:name.trim(),machine:chipVersionId&&deploymentVersion?((deploymentVersion as {machine?:string}).machine||deploymentVersion.name||'已发布接线'):'八号机',qubits:1,target:resource.id,type:'adaptive',policy:'strict',scenario:'normal',resourceIds:[resource.id],manualProcedureId:procedureId,manualParameters:parameters,...(chipVersionId?{chipVersionId,deploymentVersionId}:{})};
    }
    creationOperation.current ??= crypto.randomUUID();
    inFlight.current = true; setPending(true); setLocalError('');
    const request = manualDraftInput({...input,operationId:creationOperation.current});
    pendingCreation.current = structuredClone(request);
    setCreationCheck('');
    try {
      const created = await onCreate(structuredClone(request));
      onSelectWorkflow(created.id);
      const first = created.nodes.find(item => item.id === created.manualExperiment?.nodeId) ?? created.nodes.find(item => item.procedureId === request.manualProcedureId) ?? created.nodes[0];
      if (first) onSelectNode(first.id);
      setSelectionMode('existing'); setStep(1);
      pendingCreation.current = undefined; creationOperation.current = undefined;
    } catch (cause) {
      const uncertain = !(cause instanceof ApiError) || cause.status >= 500 || cause.status === 408;
      setUnconfirmed(uncertain);
      setLocalError(uncertain ? '创建结果待核查，请从实验列表核对。' : cause.message);
    } finally { inFlight.current = false; setPending(false); }
  };
  const checkCreation = async () => {
    const attempt = pendingCreation.current;
    if (!attempt?.operationId || locked || inFlight.current) return;
    inFlight.current = true; setPending(true); setCreationCheck('');
    try {
      // Replay only the frozen draft-creation request. The controller binds its
      // operation ID to the complete body hash, including chip and parameters.
      // No instrument action is retried, and name/resource resemblance is never identity.
      const found = await onCreate(structuredClone(attempt));
      onSelectWorkflow(found.id);
      const first = found.nodes.find(item => item.id === found.manualExperiment?.nodeId) ?? found.nodes.find(item => item.procedureId === attempt.manualProcedureId) ?? found.nodes[0];
      if (first) onSelectNode(first.id);
      setSelectionMode('existing'); setStep(1); setUnconfirmed(false); setLocalError(''); pendingCreation.current = undefined; creationOperation.current = undefined;
    } catch (cause) { setCreationCheck(cause instanceof Error ? cause.message : String(cause)); }
    finally { inFlight.current = false; setPending(false); }
  };
  const act = async (action: RemoteAction, targetId?: string) => {
    if (!active) return;
    if (pointBlock && ['run_step','start','resume','retry','confirm_hardware'].includes(action)) {setLocalError(pointBlock);return;}
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

  return <main ref={headingRef} className="mw-workspace page-enter">
    <header className="mw-heading"><div><h1>手动实验</h1></div><div className="mw-heading-actions">
      {active && <><button className="secondary" disabled={locked || dirty} onClick={onBlueprint}><GitBranch size={14} />查看蓝图</button><button className="secondary" disabled={!!pendingStop || !controls?.pause} onClick={() => void act('pause')}><Pause size={14} />{pendingStop === 'pause' ? '正在请求暂停…' : '暂停'}</button><button className="secondary mw-stop" disabled={pendingStop === 'abort' || !manualWorkbenchAction(active, selected?.id ?? '', 'abort')} onClick={() => void act('abort')}><Square size={13} />{pendingStop === 'abort' ? '正在请求终止…' : '终止'}</button></>}
      <button className="secondary" disabled={locked || dirty} onClick={() => chooseWorkflow('')}><Plus size={14} />新建实验</button>
    </div></header>
    {props.workingPoint && <section className="cd-panel" aria-label="带入的闲置点或工作点"><h3>{props.workingPoint.purpose==='idle'?'闲置点':'工作点'} · {props.workingPoint.executionProfile==='science-flux'?'原生偏置扫描派生':'保存点位记录'}</h3><p>配置：{props.workingPoint.chipVersionId?'已绑定发布版本':'实测资源或输入记录，版图身份未确认'} · 比特：{props.workingPoint.resourceId||'未记录'}</p><p>磁通通道：{props.workingPoint.fluxChannel} · {props.workingPoint.biasMeaning==='commanded_setpoint'?'偏置设定电压（未独立测压）':'偏置'}：{props.workingPoint.bias} {props.workingPoint.biasUnit} · 读出腔频率：{Number((props.workingPoint.readoutFrequencyHz/1e9).toPrecision(9))} GHz · 比特频率：{props.workingPoint.driveFrequencyHz?`${Number((props.workingPoint.driveFrequencyHz/1e9).toPrecision(9))} GHz`:'待同偏置定频'}</p><details><summary>点位来源技术详情</summary><pre>{JSON.stringify(props.workingPoint,null,2)}</pre></details><p className="mw-scan-error" role="status">{pointBlock}</p><button type="button" className="secondary" disabled={locked} onClick={props.onClearWorkingPoint}>清除点位，返回普通手动实验</button></section>}
    <div className="mw-task-bar"><label><span>实验任务</span><select aria-label="手动实验任务" value={active?.id ?? ''} disabled={locked || dirty} onChange={event => chooseWorkflow(event.target.value)}><option value="">新建实验</option>{manual.map(item => <option key={item.id} value={item.id}>{item.name} · {item.target} · {engineLabel(item.engineState ?? item.status)}</option>)}</select></label>{active && <span className="mw-task-state">{engineLabel(active.engineState ?? active.status)}</span>}</div>
    <ol className="mw-steps" aria-label="实验配置步骤"><li className={step === 0 ? 'current' : 'complete'} aria-current={step === 0 ? 'step' : undefined}><span>{step === 1 ? <Check size={15} /> : '1'}</span>实验对象</li><li className={step === 1 ? 'current' : ''} aria-current={step === 1 ? 'step' : undefined}><span>2</span>实验配置</li></ol>
    {(localError || error) && <p className="rp-error" role="alert">{localError || error}</p>}
    {step === 0 ? <section className="mw-object" aria-label="实验对象">
      <header><h2>{active ? active.name : '新建实验'}</h2><span className="rp-mode real">人工逐步</span></header>
      {creating&&props.workspaceApi&&<div className="mw-tabs" role="group" aria-label="实验执行方式"><button type="button" aria-pressed={executionSetup==='classic'} disabled={locked||unconfirmed} onClick={()=>{setExecutionSetup('classic');setLocalError('');}}>常规实验</button><button type="button" aria-pressed={executionSetup==='science-v3'} disabled={locked||unconfirmed} onClick={()=>{setExecutionSetup('science-v3');setScienceDraft(undefined);setLocalError('');}}>形状 / DRAG / SHELVE 实测</button><button type="button" aria-pressed={executionSetup==='science-v3-irb'} disabled={locked||unconfirmed} onClick={()=>{setExecutionSetup('science-v3-irb');setScienceDraft(undefined);setLocalError('');}}>X/2 DRAG / 独立 IRB</button><button type="button" aria-pressed={executionSetup==='science-shape-repeat5'} disabled={locked||unconfirmed} onClick={()=>{setExecutionSetup('science-shape-repeat5');setScienceDraft(undefined);setLocalError('');}}>五脉冲形状标定与门验证</button><button type="button" aria-pressed={executionSetup==='science-flux'} disabled={locked||unconfirmed} onClick={()=>{setExecutionSetup('science-flux');setScienceDraft(undefined);setLocalError('');}}>偏置扫描 / 工作点核查</button></div>}
      {creating&&executionSetup!=='classic'&&props.workspaceApi?<ScienceExecutionSetup key={executionSetup} initialWorkingPoint={executionSetup==='science-flux'?props.workingPoint:undefined} executionProfile={executionSetup as 'science-v3'|'science-v3-irb'|'science-shape-repeat5'|'science-flux'} api={props.workspaceApi} chips={chips} deployments={deployments} workflows={workflows} name={name} onNameChange={setName} disabled={locked||unconfirmed} onDraftChange={setScienceDraft}/>:<>
      <div className="mw-object-fields">
        {!active && <label className="mw-wide"><span>1. 实验类型</span><select aria-label="新建实验类型" value={procedureId} disabled={locked} onChange={e=>{setProcedureId(e.target.value);setCreationValues({});setResourceId('');setLocalError('');}}><option value="">先选择实验类型</option>{methods.map(item=><option key={item.procedureId} value={item.procedureId}>{item.label}{item.capability!=='available'?' · 未接入':''}</option>)}</select></label>}
        {!active && <label><span>2. 芯片版本</span><select aria-label="手动实验芯片版本" value={chipVersionId} disabled={locked||!procedureId} onChange={e=>{setChipVersionId(e.target.value);setDeploymentVersionId('');setResourceId('');}}><option value="">当前执行器配置（未指定发布芯片）</option>{chips.flatMap(c=>(c.versions??[]).map((v,i)=><option key={v.id} value={v.id}>{c.name} · 发布 {i+1}</option>))}</select></label>}
        {!active && chipVersionId && <label><span>接线版本</span><select aria-label="手动实验接线版本" value={deploymentVersionId} disabled={locked} onChange={e=>setDeploymentVersionId(e.target.value)}><option value="">选择已核对的接线版本</option>{deployments.flatMap(c=>(c.versions??[]).map((v,i)=><option key={v.id} value={v.id}>{c.name} · 发布 {i+1}</option>))}</select></label>}
        <label><span>实验名称</span><input aria-label="实验名称" value={active?.name ?? name} readOnly={!!active} disabled={locked} maxLength={120} placeholder="输入实验名称" onChange={event => setName(event.target.value)} /></label>
        <label><span>实验对象</span>{active ? <input aria-label="实验对象" readOnly value={active.target} /> : <select aria-label="实验对象" value={resource?.id ?? ''} disabled={locked || !procedureId || !resources.length} onChange={event => chooseResource(event.target.value)}><option value="">{resources.length?'选择目标比特':'暂无可用对象'}</option>{resources.map(item => <option key={item.id} value={item.id} disabled={!confirmedResource(item.id)}>{item.label}{confirmedResource(item.id)?'':' · 实测身份待核实'}</option>)}</select>}</label>
        <label><span>机台</span><input aria-label="机台" readOnly value={active?.machine ?? '八号机'} /></label>
        <label><span>测控配置</span><input aria-label="测控配置" readOnly value={adapter?.name ?? adapter?.id ?? '正在读取…'} /></label>
        {active && <label className="mw-wide"><span>实验方法</span><select aria-label="实验方法" value={selected?.id ?? ''} disabled={locked} onChange={event => onSelectNode(event.target.value)}>{active.nodes.map(item => <option key={item.id} value={item.id}>{item.label} · {item.qubits || active.target}</option>)}</select></label>}
      </div>
      {creating && chipError && <p role="alert" className="mw-scan-error">芯片版本读取失败：{chipError}</p>}
      {creating && method && method.capability!=='available' && <p role="status" className="mw-scan-error">此实验尚未接入当前执行器，不能创建可运行任务。</p>}
      {creating && chipVersion && <ChipTopology key={chipVersionId} scope={chipVersionId} topology={chipVersion.topology} qubits={chipVersion.qubits} selectedResourceId={resource?.id??''} disabled={locked||!procedureId} onSelect={id=>{const target=topologyExecutionTarget(chipVersion.topology,chipVersion.qubits??[],id);if(target.resourceId)chooseResource(target.resourceId);else setLocalError(target.reason??'此比特仅供浏览');}}/>}
      {creating && procedureId && <ManualCreationParameters procedureId={procedureId} bounds={method?.parameterBounds??catalog?.parameterBounds} values={creationValues} onChange={setCreationValues} disabled={locked}/>}
      {creating && <p className="cd-note">流程：参数配置 → 数据采集 → 结果分析 → 结果与验收。所选实验的前置测量仍由控制器检查。</p>}
      </>}
      {creating&&executionSetup!=='classic'&&chipError&&<p role="alert" className="mw-scan-error">配置读取失败：{chipError}</p>}
      {creating && unconfirmed && <div className="mw-create-recovery"><p role="status">{creationCheck || '使用原始芯片、参数和同一操作号恢复创建回执；若上次未创建，将恢复该草稿。不会启动测量。'}</p><div><button className="secondary" disabled={locked} onClick={() => void checkCreation()}>核查创建结果</button></div></div>}
      <footer><span>{creating ? '下一步创建草稿' : `${active?.nodes.length ?? 0} 项实验方法`}</span><button className="primary" disabled={locked || dirty || !!pointBlock || (creating ? unconfirmed||(executionSetup!=='classic'?!scienceDraft:!resource || !name.trim() || !method || method.capability!=='available' || !!(chipVersionId&&!deploymentVersion)) : !active || !selected)} onClick={() => void next()}>{pending ? '正在创建…' : '下一步'}<ArrowRight size={14} /></button></footer>
    </section> : active && selected ? <ManualConfiguration key={active.id} {...props} workflow={active} node={selected} busy={locked} dirty={dirty} onDirty={dirtyChanged} onSelectNode={id => { if (!locked && !dirtyRef.current && active.nodes.some(item => item.id === id)) onSelectNode(id); }} onAction={act} onPrevious={() => { if (!locked && !dirtyRef.current) setStep(0); }} /> : <EmptyState title="请选择实验任务" />}
  </main>;
}

function ManualConfiguration({ gateway, workflow, node, busy, onBusy, dirty, onDirty, onSelectNode, onAction, onPrevious, onCompareResult }: {
  gateway: RemoteGateway; workflow: Workflow; node: ExperimentNode; busy: boolean; dirty: boolean;
  onBusy(value: boolean): void; onDirty(value: boolean): void; onSelectNode(id: string): void;
  onAction(action: RemoteAction, nodeId?: string): Promise<void>; onPrevious(): void;
  onCompareResult?: (source: ResultComparisonSource) => void;
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
      <ol className="mw-experiment-phases" aria-label="当前实验实际执行流程">{experimentPhases(node).map(phase=><li key={phase.id}><button type="button" className={phase.state} onClick={()=>setTab(phase.id==='parameters'?'config':'results')}>{phase.label} · {({done:'已回报',active:'进行中',pending:'待执行/验收',failed:'失败/待核查'} as const)[phase.state]}</button></li>)}</ol>
      <div className="mw-tabs" role="tablist" aria-label="实验详情">{([{ id: 'config', label: '实验配置' }, { id: 'results', label: '实验结果' }, { id: 'history', label: '执行记录' }] as const).map(item => <button type="button" role="tab" id={`mw-tab-${item.id}`} aria-controls={`mw-panel-${item.id}`} aria-selected={tab === item.id} key={item.id} onClick={() => setTab(item.id)}>{item.label}</button>)}</div>
      <div className="mw-detail-content">
        {error && <p className="rp-error" role="alert">{error}<button className="secondary" disabled={busy} onClick={() => setReload(value => value + 1)}>重新读取</button></p>}
        <div role="tabpanel" id="mw-panel-config" aria-labelledby="mw-tab-config" hidden={tab !== 'config'}>
          <div className="mw-dependencies"><span>前置实验</span><p>{dependencies.length ? dependencies.join('、') : '无'}</p></div>
          <ManualParameterForm key={key} gateway={gateway} workflow={workflow} node={node} detail={detail} detailRevision={snapshot?.key === key ? snapshot.revision : undefined} busy={busy} onBusy={onBusy} canEdit={controls.edit && !unavailable && snapshot?.key === key && snapshot.revision === (workflow.revision ?? 0)} onDirtyChange={onDirty} onSaved={() => setReload(value => value + 1)} />
          <details className="mw-technical"><summary>方法与参数版本<ChevronDown size={13} /></summary><pre>{jsonText({ workflowId: workflow.id, resourceId: node.resourceId, procedureId: node.procedureId, ownerId: node.ownerId, capability: node.capability, parameterVersions: node.parameterVersions, dependsOn: node.dependsOn, adapter: workflow.adapter, adapterId: workflow.adapterId })}</pre></details>
        </div>
        {tab === 'results' && <div role="tabpanel" id="mw-panel-results" aria-labelledby="mw-tab-results"><MeasurementProgress node={node} workflow={workflow} /><LiveMeasurementSeries key={key} gateway={gateway} workflowId={workflow.id} node={node} adapterId={workflow.adapterId} archived={controls.terminal} /><RealResult result={node.result} comparisonSource={currentResultComparisonSource(workflow, node)} signalSource={currentResultComparisonSource(workflow, node, 'signals')} onCompareResult={onCompareResult} /><ScienceFluxComparison result={node.result} /></div>}
        {tab === 'history' && <div role="tabpanel" id="mw-panel-history" aria-labelledby="mw-tab-history"><ManualHistory detail={detail} workflow={workflow} node={node} onCompareResult={onCompareResult} /></div>}
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

function ManualHistory({ detail, workflow, node, onCompareResult }: { detail?: NodeDetail; workflow: Workflow; node: ExperimentNode; onCompareResult?: (source: ResultComparisonSource) => void }) {
  return <div className="mw-history">{!detail ? <p className="rp-muted">正在读取执行记录…</p> : !detail.attempts.length ? <EmptyState title="暂无执行记录" /> : [...detail.attempts].reverse().map(attempt => <details className="rp-attempt" key={attempt.id}><summary><span>第 {attempt.number} 次 · {({ attention: '待处理', cancelled: '已取消' } as Record<string, string>)[attempt.state] ?? engineLabel(attempt.state)}</span><time>{new Date(attempt.startedAt).toLocaleString('zh-CN')}</time></summary>{attempt.error != null && <details className="rp-error"><summary>执行错误</summary><pre>{jsonText(attempt.error)}</pre></details>}<RealResult result={attempt.result} compact comparisonSource={attemptResultComparisonSource(workflow, node, attempt)} signalSource={attemptResultComparisonSource(workflow, node, attempt, 'signals')} onCompareResult={onCompareResult} /><ScienceFluxComparison result={attempt.result} /><details><summary>执行参数与回执</summary><pre>{jsonText({ id: attempt.id, operationId: attempt.operationId, parameters: attempt.parameters, parameterVersions: attempt.parameterVersions })}</pre></details></details>)}
    {!!detail?.decisions.length && <section className="mw-decisions"><h3>操作与回退</h3>{[...detail.decisions].reverse().map(decision => <article key={decision.id}><div><strong>{({ approve: '确认结果', accept: '验收', backtrack: '回退', retry: '复测', adjust: '调参', pause: '暂停', abort: '终止', parameters_updated: '保存配置', commit: '提交校准' } as Record<string, string>)[decision.action] ?? decision.action}</strong><time>{new Date(decision.createdAt).toLocaleString('zh-CN')}</time></div><p>{decision.reason}</p>{decision.targetNodeId && <p>回退目标：{workflow.nodes.find(item => item.id === decision.targetNodeId)?.label ?? decision.targetNodeId}</p>}<details><summary>操作回执</summary><pre>{jsonText(decision)}</pre></details></article>)}</section>}
    {!!workflow.events.length && <details className="mw-events"><summary>任务事件</summary><ol>{[...workflow.events].reverse().map((event, index) => <li key={`${event.time}-${index}`}><time>{event.time}</time><span>{event.message}</span>{event.technicalDetails && <details><summary>原始记录</summary><pre>{JSON.stringify(event.technicalDetails, null, 2)}</pre></details>}</li>)}</ol></details>}
  </div>;
}
