import { engineLabel } from "./lib/runtimeLabels";
import { WorkspaceAgent, ChipWiring, Campaigns, AttentionQueue } from './components/WorkspaceControl';
import { createWorkspaceGateway } from './lib/workspaceGateway';
import ReportExportButton from "./components/ReportExportButton";
import LocalArchivePanel from "./components/LocalArchivePanel";
import QuantumMark from "./components/QuantumMark";
import WorkflowFloatingChat from "./components/WorkflowFloatingChat";
import SupplementalAcceptance from "./components/SupplementalAcceptance";
import RbDiagnostic from "./components/RbDiagnostic";
import ReadoutWithdrawal from "./components/ReadoutWithdrawal";
import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, ArrowLeft, ArrowRight, Boxes, CheckCheck, ChevronRight, Download, Folder, GitBranch, LayoutDashboard, MessageSquare, Moon, Pause, Play, Plus, Radio, RefreshCw, Search, Settings2, ShieldCheck, Sparkles, Square, Sun, X } from "lucide-react";
import type { Catalog, CreateWorkflowInput, Policy, RemoteAction, Workflow } from "./types";
import { ApiError, createRemoteGateway, reportUrl } from "./lib/remoteGateway";
import type { ConnectionState, Health, LocalSession } from "./lib/remoteGateway";
import MembershipAccount, { MembershipEntry } from "./components/MembershipAccount";
import { modelProviderName } from "./lib/account";
import type { MembershipStatus } from "./lib/account";
import { bindAccountSnapshotRefresh, createAccountSnapshotSync } from "./lib/accountSnapshotSync";
import type { AccountSnapshotSync } from "./lib/accountSnapshotSync";
import { useWorkflowStore } from "./lib/useWorkflowStore";
import WorkflowBudget from "./components/WorkflowBudget";
import WorkflowElapsed from "./components/WorkflowElapsed";
import { adapterIdentity, selectableResources } from "./lib/workflowResources";
import { isManualWorkflow, workflowControls, workflowPolicyLabel } from "./lib/workflowControls";
import ManualStepPanel, { ExecutionModeSelector } from "./components/ManualStepPanel";
import ManualExperimentWorkspace from "./components/ManualExperimentWorkspace";
import { manualDraftInput, manualSelectionKey, selectedManualWorkflow } from "./lib/manualWorkspaceSelection";
import { createWorkflowStopGate } from "./lib/workflowStopGate";
import { loadPreferences, persistPreferences } from "./lib/preferences";
import { RealFlowCanvas, RealNodeInspector, RealChatReport, RealResult, HardwareStatus, StageSummary, MeasurementProgress, EmptyState, jsonText } from "./components/RemotePanels";
import LiveMeasurementSeries from "./components/LiveMeasurementSeries";
import { WorkingPointScope, WorkflowCoverageSelect } from "./components/WorkflowCoverage";
import { defaultCoverage, workflowCoverageError } from "./lib/workflowCoverage";

type View = "dashboard" | "blueprint" | "manual" | "chat" | "agent" | "chips" | "campaigns";
const titles = { dashboard: "实验任务", blueprint: "蓝图版工作流", manual: "手动实验", chat: "任务报告与讨论", agent: "总 Agent", chips: "设备与芯片", campaigns: "实验任务" };
const statusNames: Record<string, string> = { draft: "草稿", queued: "排队中", running: "运行中", attention: "待决策", paused: "已暂停", completed: "已完成", failed: "失败", aborted: "已终止" };
export { engineLabel } from "./lib/runtimeLabels";
function Badge({ workflow }: { workflow: Workflow }) { return <span className={`status status-${workflow.status}`}><i />{workflow.mode === "replay" && workflow.status === "completed" ? "回放已完成" : workflow.coverage === "working_point" && workflow.status === "completed" ? `所选 ${workflow.completionScope?.selectedNodeCount ?? workflow.nodes.length} 项已完成` : statusNames[workflow.status] ?? workflow.status}</span>; }

export default function RemoteApp() {
  const gateway = useMemo(() => createRemoteGateway(), []);
  const store = useWorkflowStore(gateway);
  const workspaceApi = useMemo(() => createWorkspaceGateway(() => gateway.session()), [gateway]);
  const [session, setSession] = useState<LocalSession>();
  const [catalog, setCatalog] = useState<Catalog>();
  const [health, setHealth] = useState<Health>();
  const [account, setAccount] = useState<MembershipStatus>();
  const [accountUnavailable, setAccountUnavailable] = useState(false);
  const accountSync = useRef<AccountSnapshotSync | undefined>(undefined);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState("");
  const [preferences, setPreferences] = useState(loadPreferences);
  const [view, setView] = useState<View>(() => ["chat", "blueprint", "manual", "agent", "chips", "campaigns", "dashboard"].includes(location.hash.slice(1)) ? location.hash.slice(1) as View : "agent");
  const [selectedId, setSelectedId] = useState("");
  const [manualSelectedId, setManualSelectedId] = useState("");
  const manualCreating = useRef(false);
  const manualSelectionTouched = useRef(false);
  const restoredManualProject = useRef("");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [search, setSearch] = useState("");
  const [modal, setModal] = useState<"create" | "settings" | "account" | null>(null);
  const [busy, setBusy] = useState(false);
  const [parameterDirty, setParameterDirty] = useState(false);
  const stopGate = useMemo(() => createWorkflowStopGate(), []);
  const [, refreshStopState] = useState(0);
  const commandMessage = useRef(0);
  const [unconfirmedCommand, setUnconfirmedCommand] = useState<{workflowId: string; sequence: number}>();
  const [form, setForm] = useState<CreateWorkflowInput>({ name: "", machine: "八号机", qubits: 1, target: "", type: "adaptive", policy: "threshold", scenario: "normal", experiment: "single", mode: "replay", coverage: "main", resourceIds: [] });
  const availableResources = selectableResources(catalog, form.mode);
  const coverageError = workflowCoverageError(catalog, form);
  const workflows = store.state.workflows;
  const workflow = view === "manual" ? selectedManualWorkflow(workflows, manualSelectedId) : workflows.find(item => item.id === selectedId) ?? workflows[0];
  const node = workflow?.nodes.find(item => item.id === selectedNodeId) ?? workflow?.nodes.find(item => item.id === workflow.currentNodeId) ?? workflow?.nodes.find(item => ["running", "attention"].includes(item.status)) ?? workflow?.nodes[0];
  const reload = () => {
    setError("");
    void gateway.session().then(setSession).catch(cause => setError(String(cause.message)));
    void gateway.catalog().then(setCatalog).catch(cause => setError(String(cause.message)));
    void gateway.health().then(setHealth).catch(cause => setError(String(cause.message)));
    void accountSync.current?.refresh(true);
    const receipt = unconfirmedCommand;
    const ids = [...new Set([workflow?.id, receipt?.workflowId].filter((id): id is string => !!id))];
    void Promise.all(ids.map(id => gateway.get(id))).then(() => { setUnconfirmedCommand(current => current === receipt ? undefined : current); }).catch(cause => setError(String(cause.message)));
  };
  useEffect(() => {
    // Construct per effect setup so StrictMode's cleanup/setup cannot reuse a
    // disposed synchronizer. Pending GETs from the old instance are ignored.
    const sync = createAccountSnapshotSync({
      readStatus: () => gateway.accountStatus(),
      onStatus: next => { setAccount(next); setAccountUnavailable(false); },
      onUnavailable: () => setAccountUnavailable(true),
    });
    accountSync.current = sync;
    const unbind = bindAccountSnapshotRefresh(sync, {
      windowTarget: window, documentTarget: document,
      visible: () => document.visibilityState === "visible",
      subscribeConnection: listener => gateway.connection(listener), onConnection: setConnection,
    });
    reload();
    const timer = setInterval(() => {
      void gateway.health().then(setHealth).catch(() => {});
      void sync.refresh();
    }, 15000);
    return () => {
      clearInterval(timer); unbind(); sync.dispose();
      if (accountSync.current === sync) accountSync.current = undefined;
    };
  }, [gateway]);
  useEffect(() => { location.hash = view; }, [view]);
  const manualProject = health?.projectId && health?.projectRoot ? `${health.projectId}:${health.projectRoot}` : "";
  useEffect(() => {
    if (!manualProject || restoredManualProject.current === manualProject) return;
    restoredManualProject.current = manualProject;
    // Late health metadata must never replace an object already selected/edited.
    if (manualSelectionTouched.current || parameterDirty) return;
    try { setManualSelectedId(localStorage.getItem(manualSelectionKey(manualProject)) ?? ""); } catch { /* Storage may be disabled. */ }
  }, [manualProject, parameterDirty]);
  const selectManualWorkflow = (id: string) => {
    if (parameterDirty) { setError("请先保存或恢复参数修改"); return; }
    manualSelectionTouched.current = true;
    setManualSelectedId(id); setSelectedId(id); setSelectedNodeId("");
    if (manualProject) try { localStorage.setItem(manualSelectionKey(manualProject), id); } catch { /* Current selection remains usable. */ }
  };
  useEffect(() => {
    if (modal !== "create" || !catalog) return;
    setForm(current => {
      const available = selectableResources(catalog, current.mode);
      const selected = (current.resourceIds ?? []).filter(id => available.some(resource => resource.id === id));
      const ids = selected.length ? selected : available[0] ? [available[0].id] : [];
      if (JSON.stringify(ids) === JSON.stringify(current.resourceIds)) return current;
      return { ...current, resourceIds: ids, target: ids.map(id => available.find(resource => resource.id === id)?.label ?? id).join(" / ") };
    });
  }, [catalog, form.mode, modal]);
  useEffect(() => { const onEscape = (event: KeyboardEvent) => { if (event.key === "Escape" && modal !== "account") setModal(null); }; window.addEventListener("keydown", onEscape); return () => window.removeEventListener("keydown", onEscape); }, [modal]);
  useEffect(() => { document.documentElement.dataset.theme = preferences.theme; document.documentElement.style.colorScheme = preferences.theme; }, [preferences.theme]);
  const toggleTheme = () => { const next = { ...preferences, theme: preferences.theme === "dark" ? "light" as const : "dark" as const }; setPreferences(next); persistPreferences(next); };
  const navigate = (next: View) => { if (parameterDirty && next !== view) { setError("请先保存或恢复参数修改"); return; } setView(next); };
  const open = (item: Workflow) => {
    if (parameterDirty) { setError("请先保存或恢复参数修改"); return; }
    setSelectedId(item.id); setSelectedNodeId("");
    if (isManualWorkflow(item)) { selectManualWorkflow(item.id); setView("manual"); }
    else setView("blueprint");
  };
  const action = async (action: RemoteAction, targetNodeId?: string) => {
    if (!workflow) return;
    if (isManualWorkflow(workflow) && parameterDirty && ["run_step", "approve"].includes(action)) { setError("请先保存或取消参数修改"); return; }
    const id = workflow.id;
    const stopping = action === "pause" || action === "abort";
    if (!stopping && busy) return;
    const stop = stopping ? stopGate.begin(id, action) : undefined;
    if (stopping && !stop) return;
    const sequence = ++commandMessage.current;
    setError(""); setUnconfirmedCommand(undefined);
    if (stop) refreshStopState(value => value + 1); else setBusy(true);
    try { await gateway.command(id, action, targetNodeId); }
    catch (cause) {
      if (commandMessage.current === sequence && (!stop || stopGate.owns(stop))) {
        setError(cause instanceof Error ? cause.message : String(cause));
        if (cause instanceof ApiError && cause.code === "RECEIPT_UNCONFIRMED") setUnconfirmedCommand({workflowId: id, sequence});
      }
    } finally {
      if (stop) { if (stopGate.finish(stop)) refreshStopState(value => value + 1); }
      else setBusy(false);
    }
  };
  const selectNode = (id: string) => { if (parameterDirty && id !== node?.id) { setError("请先保存或取消参数修改"); return; } if (view === "manual") manualSelectionTouched.current = true; setSelectedNodeId(id); };
  const policy = async (value: Policy) => { if (!workflow || busy) return; setBusy(true); setError(""); try { await store.policy(workflow.id, value); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } finally { setBusy(false); } };
  const openCreate = () => {
    if (parameterDirty) { setError("请先保存或恢复参数修改"); return; }
    const first = selectableResources(catalog, form.mode)[0];
    setForm(current => ({ ...current, coverage: defaultCoverage(current.mode), experiment: "single", qubits: 1, name: "", target: first?.label ?? "", resourceIds: first ? [first.id] : [] }));
    setModal("create");
  };
  const create = async () => {
    if (coverageError || !form.resourceIds?.length || form.resourceIds.some(id => !availableResources.some(resource => resource.id === id)) || (form.experiment === "two" && form.resourceIds.length !== 2) || busy) return;
    setBusy(true); setError("");
    try { const created = await store.create({ ...form, name: form.name.trim() || `${form.target} · ${form.mode === "replay" ? "历史实测回放" : form.coverage === "working_point" ? "已有工作点复刻" : "单比特测控"}` }); setModal(null); open(created); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  const createManual = async (input: CreateWorkflowInput): Promise<Workflow> => {
    if (manualCreating.current) throw new Error("正在保存实验对象");
    manualCreating.current = true; setBusy(true); setError("");
    try {
      const created = await store.create(manualDraftInput(input));
      selectManualWorkflow(created.id); setView("manual");
      return created;
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); throw cause; }
    finally { manualCreating.current = false; setBusy(false); }
  };
  const controls = workflow ? workflowControls(workflow) : undefined;
  const pendingStop = workflow ? stopGate.current(workflow.id)?.action : undefined;
  const adapter = adapterIdentity(catalog, workflow, health);
  const activeAdapter = adapterIdentity(catalog, undefined, health);
  const filtered = workflows.filter(item => `${item.name} ${item.target} ${item.machine}`.toLowerCase().includes(search.toLowerCase()));
  const selectedResults = workflow?.nodes.filter(item => item.result) ?? [];
  const agentState = health?.agent as { status?: string; configured?: boolean; model?: string; provider?: string } | undefined;
  const actualConnection = account?.modelConnection;
  const modelActive = actualConnection ? actualConnection.active : agentState?.configured;
  const accountUpdated = (next: MembershipStatus) => { accountSync.current?.accept(next); void gateway.health().then(setHealth).catch(() => {}); };
  return <div className={`app-shell view-${view} role-leader remote-app`}>
    <aside className="sidebar">
      <button className="brand" onClick={() => navigate("dashboard")}><QuantumMark /><span className="brand-name"><strong><span>超导量子</span><span>自进化测控平台</span></strong></span></button>
      <div className="lab-select"><span className="lab-icon"><Boxes size={18} /></span><div><strong>八号机 · 测控工作空间</strong></div><span className={connection === "live" ? "lab-green" : "rp-connection-dot"} /></div>
      <div className="nav-label">工作空间</div>
      <nav className="main-nav">{([{ id: "agent", icon: MessageSquare }, { id: "dashboard", icon: LayoutDashboard }, { id: "manual", icon: Activity }, { id: "blueprint", icon: GitBranch }, { id: "chat", icon: Folder }, { id: "chips", icon: Settings2 }] as const).map(({ id, icon: Icon }) => <button key={id} className={view === id || (id === "dashboard" && view === "campaigns") ? "active" : ""} onClick={() => navigate(id)}><Icon size={19} /><span><strong>{titles[id]}</strong></span></button>)}</nav>
      <div className="sidebar-divider" /><div className="nav-label">最近工作流<button aria-label="新建工作流" onClick={openCreate}><Plus size={15} /></button></div>
      <div className="recent-list">{workflows.slice(0, 5).map(item => <button key={item.id} className={workflow?.id === item.id ? "chosen" : ""} onClick={() => open(item)} title={item.name}><Folder size={15} /><span>{item.name}</span><i className={`dot dot-${item.status}`} /></button>)}</div>
      <div className="sidebar-bottom"><div className="sandbox-card"><Sparkles size={17} /><strong>{accountUnavailable ? "账户状态待刷新" : modelActive ? `${modelProviderName(actualConnection?.provider ?? agentState?.provider)} · 已连接` : "智能体等待模型连接"}</strong>{!accountUnavailable && modelActive && <p>{actualConnection?.model ?? agentState?.model}</p>}<button onClick={() => setModal("settings")}>查看运行环境 <ArrowRight size={13} /></button></div><MembershipEntry status={account} onOpen={() => setModal("account")} /><button className="settings-link" onClick={() => setModal("settings")}><Settings2 size={17} /><span>工作空间与连接</span><ChevronRight size={14} /></button><div className="profile"><div className="avatar">{session?.actor.displayName.slice(0, 2) ?? "本地"}</div><div><strong>{session?.actor.displayName ?? "正在获取会话"}</strong><small>{session?.actor.role === "leader" ? "管理权限" : "操作权限"}</small></div></div></div>
    </aside>
    <div className="main-shell">
      <header className="global-bar"><div className="breadcrumb"><span>工作空间</span><ChevronRight size={13} /><b>{titles[view]}</b></div><div className="bar-actions"><span className={`rp-live ${connection}`}><i />{({ connecting: "连接中", live: "事件流在线", reconnecting: "正在重连", offline: "连接中断" })[connection]}</span><button className="icon-button" onClick={reload} aria-label="刷新连接"><RefreshCw size={17} /></button><button className="icon-button" onClick={toggleTheme} aria-label="切换主题">{preferences.theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button></div></header>
      {(error || store.error) && <div className="rp-error" role="alert"><span>{error || store.error}</span>{unconfirmedCommand && <button className="secondary" onClick={reload}>刷新任务</button>}<button onClick={() => setError("")} aria-label="关闭错误"><X size={15} /></button></div>}
      {["dashboard","campaigns"].includes(view) && <div className="wc-task-tabs" role="tablist" aria-label="实验任务视图"><button role="tab" aria-selected={view === "dashboard"} className={view === "dashboard" ? "primary" : "secondary"} onClick={() => navigate("dashboard")}>任务列表</button><button role="tab" aria-selected={view === "campaigns"} className={view === "campaigns" ? "primary" : "secondary"} onClick={() => setView("campaigns")}>多比特任务</button></div>}
      {health?.projectId && health?.projectRoot && <>
        <div className="wc-persistent-view" hidden={view !== "agent"}><WorkspaceAgent api={workspaceApi} project={`${health.projectId}:${health.projectRoot}`} active={view === "agent"} selectedWorkflowId={workflow?.id} /></div>
        <div className="wc-persistent-view" hidden={view !== "chips"}><ChipWiring api={workspaceApi} active={view === "chips"} /></div>
        <div className="wc-persistent-view" hidden={view !== "campaigns"}><Campaigns api={workspaceApi} active={view === "campaigns"} onOpenWorkflow={id => { const target = workflows.find(item => item.id === id); if (target) open(target); }} /></div>
        <AttentionQueue api={workspaceApi} project={`${health.projectId}:${health.projectRoot}`} onOpenWorkflow={id => { const target = workflows.find(item => item.id === id); if (target) open(target); }} />
      </>}
      {["agent","chips","campaigns"].includes(view) && (!health?.projectId || !health?.projectRoot) && <EmptyState title="正在连接工作空间"  />}
      {view === "dashboard" && <main className="rp-dashboard page-enter"><div className="page-heading"><div><h1>实验任务</h1></div><button className="primary" onClick={openCreate} disabled={!session}><Plus size={16} />新建工作流</button></div><div className="rp-overview">{[{ label: "全部任务", value: workflows.length, icon: Boxes }, { label: "正在执行", value: workflows.filter(item => item.status === "running").length, icon: Activity }, { label: "等待决策", value: workflows.filter(item => item.status === "attention").length, icon: ShieldCheck }, { label: "任务完成", value: workflows.filter(item => item.status === "completed").length, icon: CheckCheck }].map(({ label, value, icon: Icon }) => <div className="rp-stat" key={label}><span>{label}<Icon size={18} /></span><strong>{value}</strong></div>)}</div><section className="rp-workflow-list"><div className="rp-section-heading"><h2>实验工作流 <small>{workflows.length}</small></h2><label className="rp-search"><Search size={16} /><input aria-label="搜索任务" placeholder="搜索任务、比特或机器" value={search} onChange={event => setSearch(event.target.value)} /></label></div>{filtered.length ? <div className="rp-table-scroll"><table className="rp-table"><thead><tr><th>任务与目标</th><th>运行方式</th><th>状态</th><th>当前步骤</th><th>进度</th><th /></tr></thead><tbody>{filtered.map(item => <tr key={item.id}><td><button className="rp-task-link" onClick={() => open(item)}>{item.name}</button><small>{item.machine} · {item.target}</small></td><td><span className={`rp-mode ${item.mode}`}>{item.mode === "real" ? "真实测量" : "历史回放"}</span>{item.mode === "real" && <small>{workflowPolicyLabel(item)}</small>}</td><td><Badge workflow={item} /></td><td>{item.nodes.find(n => n.id === item.currentNodeId)?.label ?? engineLabel(item.engineState)}</td><td><span>{Math.round(item.progress)}%</span><div className="rp-progress"><i style={{ width: `${item.progress}%` }} /></div></td><td><button className="icon-button" onClick={() => open(item)} aria-label={`打开 ${item.name}`}><ArrowRight size={17} /></button></td></tr>)}</tbody></table></div> : <EmptyState title={search ? "没有匹配的任务" : "暂无任务"} action={<button className="primary" onClick={openCreate}><Plus size={15} />创建任务</button>} />}</section></main>}
      {view === "blueprint" && (workflow && node ? <main className="blueprint-page page-enter"><div className="blueprint-header"><div><button className="back-link" onClick={() => navigate("dashboard")}><ArrowLeft size={13} />全部工作流</button><div className="blueprint-title"><h1>{workflow.name}</h1><Badge workflow={workflow} /><span className={`rp-mode ${workflow.mode}`}>{workflow.mode === "real" ? "真实测量" : "历史实测回放"}</span>{workflow.mode === "real" && <span className="rp-mode">{workflowPolicyLabel(workflow)}</span>}</div><div className="blueprint-meta"><span><Boxes size={13} />{workflow.machine} / {workflow.target}</span><WorkflowElapsed workflow={workflow} /><span>版本 {workflow.revision} · {engineLabel(workflow.engineState)}</span></div></div><div className="heading-actions"><ReportExportButton key={workflow.id} gateway={gateway} workflow={workflow} /><a className="secondary rp-link-button" href={reportUrl(workflow.id)} download={`${workflow.target}-实测报告.html`}><Download size={15} />下载图文报告</a><button className="secondary" onClick={() => navigate("chat")}><MessageSquare size={15} />任务对话</button>{controls?.start && <button className="primary" disabled={busy} onClick={() => void action("start")}><Play size={15} />开始{workflow.mode === "replay" ? "回放" : "运行"}</button>}{controls?.pause && <button className="secondary" disabled={!!pendingStop} onClick={() => void action("pause")}><Pause size={15} />{pendingStop === "pause" ? "正在请求暂停…" : "暂停"}</button>}{controls?.resume && <button className="primary" disabled={busy} onClick={() => void action("resume")}><Play size={15} />继续</button>}{["running", "paused", "attention", "queued"].includes(workflow.status) && <button className="secondary" disabled={pendingStop === "abort"} onClick={() => void action("abort")}><Square size={13} />{pendingStop === "abort" ? "正在请求终止…" : "终止"}</button>}</div></div>{workflow.mode === "real" && <ExecutionModeSelector policy={workflow.policy} disabled={busy || parameterDirty || !controls?.policy} onChange={value => void policy(value)} />}<ManualStepPanel workflow={workflow} node={node} onSelect={selectNode} onAction={action} busy={busy} parameterDirty={parameterDirty} />{isManualWorkflow(workflow) && <button className="secondary" disabled={parameterDirty} onClick={() => { selectManualWorkflow(workflow.id); setView("manual"); }}>打开手动实验</button>}<LocalArchivePanel gateway={gateway} workflowId={workflow.id} /><StageSummary catalog={catalog} workflow={workflow} /><WorkingPointScope workflow={workflow} /><WorkflowBudget key={workflow.id} workflow={workflow} gateway={gateway} /><SupplementalAcceptance key={`rules-${workflow.id}`} workflow={workflow} gateway={gateway} /><ReadoutWithdrawal key={`readout-${workflow.id}`} workflow={workflow} gateway={gateway} /><RbDiagnostic key={`rb-diagnostic-${workflow.id}`} workflow={workflow} gateway={gateway} />{workflow.mode === "real" && <HardwareStatus hardware={health?.hardware} adapter={adapter} workflow={workflow} action={controls?.hardware ? <button className="primary" disabled={busy} onClick={() => void action("confirm_hardware")}><CheckCheck size={14} />{isManualWorkflow(workflow) ? "确认接线" : "确认交接并继续"}</button> : undefined} />}<div className="blueprint-body"><section className="blueprint-main"><div className="canvas-top"><span className="live-label">{workflow.status !== "aborted" && <i />}{workflow.status === "aborted" ? "已终止任务 · 结果留档" : workflow.mode === "replay" ? "历史证据回放" : "测量执行引擎"}</span><span>{workflow.mode === "replay" ? `${workflow.nodes.filter(item => item.result).length} / ${workflow.nodes.length} 节点已载入 · ` : ""}{workflow.nodes.filter(item => item.status === "success").length} / {workflow.nodes.length} 节点已验证 · 蓝图 {workflow.blueprintVersion}</span></div><RealFlowCanvas workflow={workflow} selectedNodeId={node.id} onSelectNode={selectNode} /><div className="rp-selected-result"><div className="rp-section-heading"><h2>{node.label} · 测量结果</h2><span>{node.result?.dataKind === "historical" ? "历史证据" : node.result ? "本轮采集" : "等待数据"}</span></div><MeasurementProgress node={node} workflow={workflow} />{workflow.mode === "real" && <LiveMeasurementSeries key={`${workflow.id}-${node.id}`} gateway={gateway} workflowId={workflow.id} node={node} adapterId={workflow.adapterId} archived={workflow.status === "aborted"} />}{node.engineState === "stale" && <div className="rp-info">{node.attempt === 0 && !node.result ? "等待前置测量" : "前置参数已变化 · 以下结果待复测"}</div>}<RealResult result={node.result} /></div><div className="events-panel"><div><h3><Radio size={14} />运行动态</h3><small>最近 8 条</small></div>{workflow.events.slice(-8).reverse().map((event, index) => <p key={`${event.time}-${index}`}><time>{new Date(event.time).toLocaleTimeString("zh-CN")}</time><span className={`event-dot ${event.kind}`} /><span>{event.message}</span></p>)}{workflow.events.some(event => event.technicalDetails) && <details><summary>事件原始记录</summary><pre>{JSON.stringify(workflow.events.filter(event => event.technicalDetails).map(event => ({ time: event.time, ...event.technicalDetails })), null, 2)}</pre></details>}</div></section><RealNodeInspector gateway={gateway} workflow={workflow} node={node} onAction={action} onParameterDirty={setParameterDirty} busy={busy} hardware={health?.hardware} catalog={catalog} onBusy={setBusy} /></div></main> : <EmptyState title="选择一个工作流"  action={<button className="primary" onClick={openCreate}>新建工作流</button>} />)}
      {view === "manual" && <ManualExperimentWorkspace gateway={gateway} catalog={catalog} workflows={workflows} workflow={workflow} node={node} busy={busy} onBusy={setBusy} onCreate={createManual} onSelectWorkflow={selectManualWorkflow} onSelectNode={selectNode} onAction={action} onParameterDirty={setParameterDirty} pendingStop={pendingStop} onBlueprint={() => { if (workflow && !parameterDirty) { setSelectedId(workflow.id); setView("blueprint"); } }} />}
      {view === "chat" && <RealChatReport projectId={health?.projectId && health?.projectRoot ? `${health.projectId}:${health.projectRoot}` : undefined} gateway={gateway} workflow={workflow} onOpenWorkflow={() => setView("blueprint")} />}
      <footer className="app-footer"><span><i />超导量子自进化测控平台 <b>v2 · Local</b></span><span>{selectedResults.length ? `${selectedResults.length} 个节点已有数据` : ""}</span><button onClick={() => setModal("settings")}>运行环境 <ArrowRight size={11} /></button></footer>
    </div>
    {view === "blueprint" && workflow && health?.projectId && health?.projectRoot && <WorkflowFloatingChat gateway={gateway} workflow={workflow} node={node} projectId={`${health.projectId}:${health.projectRoot}`} />}
    {modal && modal !== "account" && <div className="rp-modal-backdrop" onClick={event => { if (event.target === event.currentTarget) setModal(null); }}><section className="rp-modal" role="dialog" aria-modal="true" aria-label={modal === "create" ? "新建工作流" : "运行环境"}><button className="rp-modal-close icon-button" onClick={() => setModal(null)} aria-label="关闭"><X size={20} /></button>{modal === "create" ? <><div className="rp-eyebrow">NEW EXPERIMENT</div><h2>新建工作流</h2>{activeAdapter && <div className="rp-info">当前测量执行器：{activeAdapter.name ?? activeAdapter.id}</div>}<form onSubmit={event => { event.preventDefault(); void create(); }}><label>运行方式<select aria-label="运行方式" value={form.mode} onChange={event => setForm({ ...form, mode: event.target.value as "real" | "replay", coverage: defaultCoverage(event.target.value as "real" | "replay") })}><option value="replay">历史回放</option><option value="real">真实测量</option></select></label>{form.mode === "real" && <ExecutionModeSelector policy={form.policy} onChange={value => setForm({ ...form, policy: value })} disabled={busy} />}<label>实验流程<select aria-label="实验流程" value={form.experiment} onChange={event => setForm({ ...form, experiment: event.target.value as "single" | "two", qubits: event.target.value === "two" ? 2 : 1, resourceIds: form.resourceIds?.slice(0, 1), target: catalog?.resources.find(resource => resource.id === form.resourceIds?.[0])?.label ?? "" })}><option value="single">单比特校准</option><option value="two">双比特校准（包含单比特前置）</option></select></label><label>目标资源<select aria-label="目标资源" required value={form.resourceIds?.[0] ?? ""} onChange={event => { const resource = catalog?.resources.find(item => item.id === event.target.value); setForm({ ...form, resourceIds: resource ? [resource.id] : [], target: resource?.label ?? "" }); }}><option value="" disabled>选择比特</option>{availableResources.map(resource => <option key={resource.id} value={resource.id}>{resource.label}{resource.engineKey ? ` · ${resource.engineKey}` : ""}</option>)}</select></label>{form.experiment === "two" && <label>第二个资源<select aria-label="第二个资源" required value={form.resourceIds?.[1] ?? ""} onChange={event => { const resource = catalog?.resources.find(item => item.id === event.target.value); const first = catalog?.resources.find(item => item.id === form.resourceIds?.[0]); setForm({ ...form, resourceIds: first && resource ? [first.id, resource.id] : [], target: first && resource ? `${first.label} / ${resource.label}` : "" }); }}><option value="" disabled>选择第二个比特</option>{availableResources.filter(resource => resource.id !== form.resourceIds?.[0]).map(resource => <option key={resource.id} value={resource.id}>{resource.label}</option>)}</select></label>}{form.mode === "real" && !availableResources.length && <div className="rp-info">暂无可用比特</div>}<WorkflowCoverageSelect catalog={catalog} form={form} onChange={coverage => setForm({ ...form, coverage })} /><label>任务名称<input value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} placeholder={`${form.target || "目标比特"} · ${form.coverage === "working_point" ? "已有工作点复刻" : "单比特测控"}`} maxLength={120} /></label>{error && <p role="alert" className="rp-error">{error}</p>}<div className="rp-modal-actions"><button type="button" className="secondary" onClick={() => setModal(null)}>取消</button><button className="primary" type="submit" disabled={busy || Boolean(coverageError) || !form.resourceIds?.length || !availableResources.length || form.resourceIds.some(id => !availableResources.some(resource => resource.id === id)) || (form.experiment === "two" && form.resourceIds.length !== 2)}>{busy ? "正在创建…" : "创建工作流"}<ArrowRight size={15} /></button></div></form></> : <><div className="rp-eyebrow">RUNTIME STATUS</div><h2>运行环境与连接</h2><LocalArchivePanel gateway={gateway} /><details><summary>服务连接</summary><p>{location.origin}/api · {connection}</p></details><details><summary>智能体服务高级信息</summary><pre>{jsonText(health?.agent ?? { status: "正在检查" })}</pre></details><h3>测量执行器</h3><HardwareStatus hardware={health?.hardware} adapter={activeAdapter} /><details><summary>硬件高级信息</summary><pre>{jsonText(health?.hardware ?? { status: "正在检查" })}</pre></details><h3>登记资源</h3><p>{catalog?.resources.length ?? 0} 个资源 · 蓝图版本 {catalog?.blueprint.version ?? "—"}</p><button className="primary" onClick={reload}><RefreshCw size={15} />重新检查</button></>}</section></div>}
    {modal === "account" && <MembershipAccount gateway={gateway} initialStatus={account} onStatus={accountUpdated} onClose={() => setModal(null)} />}
  </div>;
}
