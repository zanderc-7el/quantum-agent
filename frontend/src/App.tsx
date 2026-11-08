import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  Atom,
  Bell,
  Boxes,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  CircleHelp,
  Clock3,
  Download,
  FlaskConical,
  Folder,
  GitBranch,
  LayoutDashboard,
  LogOut,
  LockKeyhole,
  PanelRightOpen,
  ListFilter,
  MessageSquare,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCw,
  RotateCcw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Sun,
  Moon,
  Square,
  X,
  Zap,
} from "lucide-react";
import type {
  CreateWorkflowInput,
  Policy,
  Workflow,
  WorkflowAction,
  WorkflowStatus,
} from "./types";
import { useWorkflowStore } from "./lib/useWorkflowStore";
import type { WorkflowGateway } from "./lib/gateway";
import {
  measurementStage,
  qubitMeasurements,
  reportMeasurements,
} from "./lib/measurements";
import ChatReport, { clearLocalConversations } from "./components/ChatReport";
import FlowCanvas from "./components/FlowCanvas";
import NodeInspector from "./components/NodeInspector";
import DecisionTrail from "./components/DecisionTrail";
import WorkspaceSettings from "./components/WorkspaceSettings";
import { buildNodeDecisions } from "./lib/decisionTrail";
import { useDecisionTrail } from "./lib/useDecisionTrail";
import { useRemoteMeasurements } from "./lib/useRemoteMeasurements";
import { useRemoteTargets } from "./lib/useRemoteTargets";
import { loadPreferences, persistPreferences } from "./lib/preferences";
import type { Preferences } from "./lib/preferences";
import { exportWorkspace } from "./lib/workspaceTransfer";
import { PLATFORM_NAME } from "./lib/branding";

import LoginScreen from "./components/LoginScreen";
import ParameterDrawer from "./components/ParameterDrawer";
import ApiOnlyWorkbench from "./components/ApiOnlyWorkbench";
import { buildExperimentCsv } from "./lib/experimentCsv";
import { readSession, clearSession } from "./lib/auth";
import type { User } from "./lib/auth";
import {
  logoutRemote,
  onUnauthorized,
  readRemoteSession,
  remoteEnabled,
} from "./lib/apiClient";
import { canPerformAction, canManageTeam, canOperateWorkflow } from "./lib/access";
import { createRoleGateway } from "./lib/roleGateway";
import { BLUEPRINTS, BLUEPRINT_STAGES } from "./lib/blueprints";
import "./components/data-access.css";

type View = "dashboard" | "blueprint" | "chat" | "api";
const STATUS: Record<WorkflowStatus, string> = {
  draft: "草稿",
  queued: "排队中",
  running: "运行中",
  attention: "需要关注",
  paused: "已暂停",
  completed: "已完成",
  failed: "失败",
  aborted: "已终止",
};
const POLICY: Record<Policy, string> = {
  strict: "逐步审批",
  threshold: "异常时审批",
  autopilot: "自动运行",
};
const viewLabels: Record<View, string> = {
  dashboard: "工作看台",
  blueprint: "蓝图版工作流",
  chat: "对话式 Agent",
  api: "接口验证台",
};
const pad = (n: number) => String(n).padStart(2, "0");
const elapsed = (n: number) =>
  `${pad(Math.floor(n / 3600))}:${pad(Math.floor(n / 60) % 60)}:${pad(n % 60)}`;
function Badge({ status }: { status: WorkflowStatus }) {
  return (
    <span className={`status status-${status}`}>
      <i />
      {STATUS[status]}
    </span>
  );
}
function Sparkline({ index = 0 }: { index?: number }) {
  const points = Array.from(
    { length: 32 },
    (_, i) => `${i * 4},${39 - i * 0.76 - Math.sin(i * 1.8 + index) * 4}`,
  ).join(" ");
  return (
    <svg viewBox="0 0 124 48" className="sparkline">
      <defs>
        <linearGradient id={`area${index}`} x1="0" y1="0" x2="0" y2="1">
          <stop stopColor="#42bfb7" stopOpacity=".25" />
          <stop offset="1" stopColor="#42bfb7" stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={`0,48 ${points} 124,48`} fill={`url(#area${index})`} />
      <polyline
        points={points}
        fill="none"
        stroke="#52c9c2"
        strokeWidth="1.6"
      />
    </svg>
  );
}

export default function App({ gateway }: { gateway?: WorkflowGateway } = {}) {
  const [user, setUser] = useState<User | null>(() => {
    const stored = readSession();
    if (stored && remoteEnabled() && !readRemoteSession()) {
      // A local demo session without a backend token cannot open remote data.
      clearSession();
      return null;
    }
    return stored;
  });
  const [loginPreferences, setLoginPreferences] = useState(loadPreferences);
  const [logoutError, setLogoutError] = useState("");
  useEffect(
    () =>
      // Any 401 from the API ends the session rather than leaving a half-loaded UI.
      onUnauthorized(() => {
        clearSession();
        setUser(null);
      }),
    [],
  );
  useEffect(() => {
    document.documentElement.dataset.theme = loginPreferences.theme;
    document.documentElement.style.colorScheme = loginPreferences.theme;
  }, [loginPreferences.theme]);
  useEffect(() => {
    if (!user) return;
    const check = () => { if (readSession()?.id !== user.id) setUser(null); };
    const timer = window.setInterval(check, 15000);
    window.addEventListener("focus", check);
    return () => { clearInterval(timer); window.removeEventListener("focus", check); };
  }, [user]);
  const logout = () => {
    const cleared = clearSession();
    if (remoteEnabled())
      // Revoke the server-side session (token version bump), then clear the
      // local copy. The local UI does not wait for the network round trip.
      void logoutRemote();
    setLoginPreferences(loadPreferences());
    setLogoutError(cleared ? "" : "已退出当前页面，但浏览器会话清理失败，请关闭此标签页。");
    setUser(null);
  };
  if (!user) return <><LoginScreen theme={loginPreferences.theme} onLogin={(value) => { setLogoutError(""); setUser(value); }} onToggleTheme={() => {
    const next = { ...loginPreferences, theme: loginPreferences.theme === "dark" ? "light" as const : "dark" as const };
    persistPreferences(next); setLoginPreferences(next);
  }} />{logoutError && <div className="toast" role="alert">{logoutError}</div>}</>;
  return <><WorkspaceApp key={user.id} gateway={gateway} user={user} onLogout={logout} />{logoutError && <div className="toast" role="alert">{logoutError}</div>}</>;
}

function WorkspaceApp({ gateway, user, onLogout }: { gateway?: WorkflowGateway; user: User; onLogout: () => void }) {
  const [preferences, setPreferences] = useState(loadPreferences);
  const roleGateway = useMemo(() => createRoleGateway(user, gateway), [user, gateway]);
  const store = useWorkflowStore(roleGateway);
  const isLeader = canManageTeam(user);
  const [parameterTarget, setParameterTarget] = useState<{workflowId: string; nodeId?: string} | null>(null);
  const [settingsTab, setSettingsTab] = useState<"personal" | "data" | "team">("personal");
  const [scope, setScope] = useState(isLeader ? "all" : "mine");
  const { state } = store;
  const [view, setView] = useState<View>(() => {
    const v = location.hash.slice(1);
    return ["chat", "blueprint", "api"].includes(v) ? (v as View) : "dashboard";
  });
  const [selectedId, setSelectedId] = useState(
    () => (isLeader ? state.workflows[0] : state.workflows.find((w) => w.ownerId === user.id) || state.workflows[0])?.id || "",
  );
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [machineFilter, setMachineFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [sortNewest, setSortNewest] = useState(true);
  const [abortTarget, setAbortTarget] = useState<string>("");
  const [modal, setModal] = useState<
    "create" | "settings" | "help" | "reset" | "abort" | null
  >(null);
  const creating = useRef(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [lockedBlueprint, setLockedBlueprint] = useState<"single" | "two" | null>(null);
  const [menuId, setMenuId] = useState<string | null>(null);
  const [notify, setNotify] = useState("");
  const [notifications, setNotifications] = useState(false);
  const autoDemo = preferences.autoDemo;
  const [form, setForm] = useState<CreateWorkflowInput>({
    name: `${preferences.defaultMachine} 单比特完整校准`,
    machine: preferences.defaultMachine,
    qubits: 1,
    target: "Q12",
    type: "adaptive",
    policy: user.role === "operator" ? "threshold" : preferences.defaultPolicy,
    scenario: "normal",
    experiment: "single",
  });
  const lastTick = useRef(performance.now());
  const workflow =
    state.workflows.find((w) => w.id === selectedId) || state.workflows[0];
  const node =
    workflow?.nodes.find((n) => n.id === selectedNodeId) ||
    workflow?.nodes.find(
      (n) => n.status === "running" || n.status === "attention",
    ) ||
    workflow?.nodes[0];
  // Server audit is only available in remote mode and only rendered in the blueprint.
  const decisionTrail = useDecisionTrail(
    workflow,
    store.mode === "remote" && view === "blueprint",
  );
  // Recorded node results replace the demo selectors in every view (metric
  // tiles, inspector charts, report). A failed fetch keeps the last snapshot.
  const remoteMeasurements = useRemoteMeasurements(
    workflow,
    store.mode === "remote",
  );
  // Design targets and acceptance floors bound to the workflow's Skill. The
  // hook publishes to a module cache; the returned state drives the inline
  // sync error and the manual retry.
  const remoteTargets = useRemoteTargets(workflow, store.mode === "remote");
  const nodeDecisions = useMemo(
    () => buildNodeDecisions(decisionTrail.audit, decisionTrail.replay),
    [decisionTrail.audit, decisionTrail.replay],
  );
  const parameterWorkflow = parameterTarget ? state.workflows.find((w) => w.id === parameterTarget.workflowId) : undefined;
  const ownWorkflows = state.workflows.filter((w) => w.ownerId === user.id);
  const scopedWorkflows = scope === "mine" ? ownWorkflows : state.workflows;
  const approvals = state.workflows.filter((w) => w.status === "attention");
  const showParameters = (target: Workflow, nodeId?: string) => setParameterTarget({ workflowId: target.id, nodeId });
  const closeParameters = () => setParameterTarget(null);
  const showSettings = (tab: "personal" | "data" | "team" = "personal") => { setSettingsTab(tab); setModal("settings"); setParameterTarget(null); };
  const copyWorkflow = (w: Workflow) => {
    setLockedBlueprint(w.experiment);
    setForm({ name: `${w.name} · 副本`, machine: w.machine, qubits: w.qubits, target: w.target, type: w.type, policy: isLeader ? w.policy : "threshold", scenario: w.scenario, experiment: w.experiment });
    setModal("create"); setMenuId(null); setParameterTarget(null);
  };
  const toast = (s: string) => setNotify(s);
  const savePreferences = (next: Preferences) => {
    if (!isLeader) next = { ...next, workspaceName: preferences.workspaceName, autoDemo: preferences.autoDemo, provider: preferences.provider, model: preferences.model, endpoint: preferences.endpoint, defaultPolicy: "threshold" };
    setPreferences(next);
    toast(
      persistPreferences(next)
        ? "设置已保存"
        : "设置已应用，但浏览器存储不可用，刷新后将恢复默认值",
    );
  };
  const openCreate = () => {
    setLockedBlueprint(null);
    setForm((value) => ({
      ...value,
      name: /^M\d+ (单比特完整校准|双比特 CZ 校准)$/.test(value.name)
        ? value.name.replace(/^M\d+/, preferences.defaultMachine)
        : value.name,
      machine: preferences.defaultMachine,
      policy: user.role === "operator" ? "threshold" : preferences.defaultPolicy,
    }));
    setModal("create");
  };
  const openBlueprint = (experiment: "single" | "two") => {
    setLockedBlueprint(experiment);
    setForm((value) => ({ ...value, experiment, qubits: experiment === "single" ? 1 : 2,
      target: experiment === "single" ? "Q12" : "Q12–Q13",
      name: `${value.machine} ${experiment === "single" ? "单比特完整校准" : "双比特 CZ 校准"}` }));
    setModal("create");
  };
  useEffect(() => {
    document.documentElement.dataset.theme = preferences.theme;
    document.documentElement.style.colorScheme = preferences.theme;
  }, [preferences.theme]);

  useEffect(() => {
    if (!notify) return;
    const t = setTimeout(() => setNotify(""), 4200);
    return () => clearTimeout(t);
  }, [notify]);
  useEffect(() => {
    location.hash = view;
  }, [view]);
  useEffect(() => {
    const fn = () => {
      const v = location.hash.slice(1);
      if (["dashboard", "blueprint", "chat", "api"].includes(v)) setView(v as View);
    };
    window.addEventListener("hashchange", fn);
    return () => window.removeEventListener("hashchange", fn);
  }, []);
  useEffect(() => {
    if (!autoDemo) return;
    lastTick.current = performance.now();
    const id = setInterval(() => {
      const now = performance.now();
      const seconds = Math.min(1, Math.max(0, (now - lastTick.current) / 1000));
      lastTick.current = now;
      if (readSession()?.id !== user.id) { onLogout(); return; }
      if (document.visibilityState === "visible") store.advance(seconds);
    }, 1000);
    return () => clearInterval(id);
  }, [autoDemo, store.advance]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setModal(null);
        setMenuId(null);
        setNotifications(false);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const openWorkflow = (w: Workflow, target: View = "blueprint") => {
    setLockedBlueprint(null);
    setSelectedId(w.id);
    setSelectedNodeId("");
    setParameterTarget(null);
    setView(target);
    setMenuId(null);
  };
  const action = async (a: WorkflowAction, id = workflow.id) => {
    if (readSession()?.id !== user.id) { onLogout(); return; }
    const target = state.workflows.find((w) => w.id === id);
    if (!target || !canPerformAction(user, target, a)) { toast("此操作需要任务所有者或团队负责人权限；审批仅限负责人。"); return; }
    if (a === "abort") {
      setAbortTarget(id);
      setModal("abort");
      return;
    }
    try {
      const before = state.workflows.find((w) => w.id === id);
      const updated = await store.act(id, a);
      setMenuId(null);
      if (
        before &&
        before.status === updated.status &&
        before.events.length === updated.events.length
      ) {
        toast("任务状态已更新，此操作当前不适用");
        return;
      }
      toast(
        (
          {
            start: "模拟任务已启动",
            pause: "任务已暂停",
            resume: "任务已恢复",
            approve: "已批准当前节点，继续模拟",
            retry: "已重新运行当前节点",
            abort: "任务已终止",
          } as const
        )[a],
      );
    } catch (e) {
      toast(e instanceof Error ? e.message : "操作失败");
    }
  };
  const changePolicy = async (p: Policy) => {
    try {
      await store.policy(workflow.id, p);
      toast(`审批策略已设为${POLICY[p]}`);
    } catch (e) {
      toast(e instanceof Error ? e.message : "策略修改失败");
    }
  };
  const create = async (input: CreateWorkflowInput) => {
    if (readSession()?.id !== user.id) { onLogout(); return; }
    if (lockedBlueprint && input.experiment !== lockedBlueprint) { toast("当前蓝图只能创建对应类型的校准工作流。"); return; }
    if (creating.current) return;
    creating.current = true;
    setCreateBusy(true);
    try {
      const w = await store.create(input);
      setSelectedId(w.id);
      setSelectedNodeId("");
      setView("blueprint");
      setModal(null);
      setLockedBlueprint(null);
      toast("工作流已创建，点击“开始运行”体验完整流程。");
    } catch (e) {
      toast(e instanceof Error ? e.message : "创建失败，请检查参数");
    } finally {
      creating.current = false;
      setCreateBusy(false);
    }
  };
  useEffect(() => {
    if (!modal) return;
    const prior = document.activeElement as HTMLElement | null;
    const panel = document.querySelector<HTMLElement>(".modal");
    panel?.querySelector<HTMLElement>("input,select,button")?.focus();
    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !panel) return;
      const controls = [
        ...panel.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
        ),
      ].filter((item) => item.getClientRects().length > 0);
      const first = controls[0],
        last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    panel?.addEventListener("keydown", trapFocus);
    return () => {
      panel?.removeEventListener("keydown", trapFocus);
      prior?.focus();
    };
  }, [modal]);
  const metrics = useMemo(
    () => ({
      total: scopedWorkflows.length,
      running: scopedWorkflows.filter((w) => w.status === "running").length,
      attention: scopedWorkflows.filter(
        (w) => w.status === "attention" || w.status === "failed",
      ).length,
      completed: scopedWorkflows.filter((w) => w.status === "completed").length,
    }),
    [state, scope, user.id],
  );
  const filtered = useMemo(
    () =>
      state.workflows
        .filter(
          (w) =>
            (scope === "all" || w.ownerId === user.id) &&
            (statusFilter === "all" ||
              w.status === statusFilter ||
              (statusFilter === "attention" && w.status === "failed")) &&
            (machineFilter === "all" || w.machine === machineFilter) &&
            (typeFilter === "all" || w.experiment === typeFilter) &&
            `${w.name} ${w.target} ${w.machine}`
              .toLowerCase()
              .includes(search.toLowerCase()),
        )
        .sort((a, b) =>
          sortNewest
            ? b.createdAt.localeCompare(a.createdAt)
            : a.createdAt.localeCompare(b.createdAt),
        ),
    [state, search, statusFilter, machineFilter, typeFilter, sortNewest, scope, user.id],
  );
  const totalPages = Math.max(1, Math.ceil(filtered.length / 8));
  const currentPage = Math.min(page, totalPages);
  const rows = filtered.slice((currentPage - 1) * 8, currentPage * 8);
  const exportCsv = () => {
    try {
      const result = buildExperimentCsv(state.workflows);
      if (!result.workflowCount) { toast("还没有运行过的实验，请先启动一个任务。"); return; }
      const url = URL.createObjectURL(new Blob([result.content], { type: result.mimeType }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = result.filename; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast(`已导出 ${result.workflowCount} 个已运行实验，${result.nodeCount} 个节点、${result.eventCount} 条事件（不受当前筛选影响）`);
    } catch (cause) { toast(cause instanceof Error ? cause.message : "CSV 导出失败"); }
  };
  const exportState = () => {
    try {
      const a = document.createElement("a");
      const u = URL.createObjectURL(
        new Blob([exportWorkspace(state)], { type: "application/json" }),
      );
      a.href = u;
      a.download = `${PLATFORM_NAME}-工作空间-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(u), 1000);
      toast("工作区记录已导出");
    } catch (cause) {
      toast(cause instanceof Error ? cause.message : "导出失败");
    }
  };
  const reported = workflow ? reportMeasurements(workflow) : null;
  const qmetric = qubitMeasurements(
    workflow?.target.match(/Q\d+/i)?.[0] || "Q12",
    workflow,
  );
  const metricItems = [
    {
      title: "T1 纵向弛豫",
      value: qmetric.t1.toFixed(2),
      unit: "μs",
      node: reported?.t1Node,
    },
    {
      title: "T2 相干时间",
      value: qmetric.t2.toFixed(2),
      unit: "μs",
      node: reported?.t2Node,
    },
    {
      title: "读取保真度",
      value: reported?.readout.toFixed(2),
      unit: "%",
      node: reported?.readoutNode,
    },
    {
      title: workflow?.experiment === "two" ? "CZ 门保真度" : "单门保真度",
      value:
        workflow?.experiment === "two"
          ? reported?.cz.toFixed(2)
          : (reported?.rb ?? qmetric.rbFidelity).toFixed(2),
      unit: "%",
      node:
        workflow?.experiment === "two" ? reported?.czNode : reported?.rbNode,
    },
  ];
  if (!workflow || !node)
    return (
      <div className="boot-empty">
        <Atom size={40} />
        <h1>
          {store.error
            ? "工作流加载失败"
            : store.mode === "remote"
              ? "正在连接工作空间"
              : `欢迎使用${PLATFORM_NAME}`}
        </h1>
        {store.error && <p role="alert">{store.error}</p>}
        <button
          className="primary"
          onClick={() =>
            store.mode === "mock" ? store.resetDemo() : location.reload()
          }
        >
          {store.mode === "mock" ? "载入演示任务" : "重新加载"}
        </button>
      </div>
    );
  return (
    <div className={`app-shell view-${view} role-${user.role}`}>
      <aside className="sidebar" inert={!!modal}>
        <button
          className="brand"
          onClick={() => setView("dashboard")}
          aria-label={`${PLATFORM_NAME} · 工作看台`}
          title={PLATFORM_NAME}
        >
          <div className="brand-mark">
            <Atom size={28} />
          </div>
          <span className="brand-name">
            <strong><span>超导量子</span><span>自进化测控平台</span></strong>
          </span>
        </button>
        <div className="lab-select">
          <span className="lab-icon">
            <Boxes size={18} />
          </span>
          <div>
            <strong title={preferences.workspaceName}>
              {preferences.workspaceName}
            </strong>
            <small>{isLeader ? "团队管理空间" : "个人实验空间"} · {store.mode === "remote" ? "远程" : "演示"}</small>
          </div>
          <span className="lab-green" />
        </div>
        <div className="nav-label">
          工作空间 <span>WORKSPACE</span>
        </div>
        <nav className="main-nav">
          {(
            [
              {
                id: "chat",
                icon: MessageSquare,
                subtitle: "从想法开始一场实验",
              },
              {
                id: "blueprint",
                icon: GitBranch,
                subtitle: "编排、观测与控制",
              },
              {
                id: "dashboard",
                icon: LayoutDashboard,
                subtitle: "所有任务，一目了然",
              },
              {
                id: "api",
                icon: Radio,
                subtitle: "离线接口与数据验证",
              },
            ] as const
          ).map(({ id, icon: Icon, subtitle }) => (
            <button
              key={id}
              className={view === id ? "active" : ""}
              onClick={() => setView(id)}
              title={viewLabels[id]}
            >
              <Icon size={19} />
              <span>
                <strong>{viewLabels[id]}</strong>
                <small>{subtitle}</small>
              </span>
              {id === "dashboard" && metrics.attention > 0 && (
                <i className="nav-count">{metrics.attention}</i>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-divider" />
        <div className="nav-label">
          {isLeader ? "最近团队工作流" : "我的最近工作流"}{" "}
          <button aria-label="新建工作流" onClick={openCreate}>
            <Plus size={15} />
          </button>
        </div>
        <div className="recent-list">
          {!isLeader && ownWorkflows.length === 0 && <p className="recent-empty">尚无本人任务，点击 + 创建。</p>}
          {(isLeader ? state.workflows : ownWorkflows).slice(0, 4).map((w) => (
            <button
              key={w.id}
              className={selectedId === w.id ? "chosen" : ""}
              onClick={() => openWorkflow(w)}
              title={w.name}
            >
              <Folder size={15} />
              <span>{w.name.replace(/^M\d /, "")}</span>
              <i className={`dot dot-${w.status}`} />
            </button>
          ))}
        </div>
        <div className="sidebar-bottom">
          <div className="sandbox-card">
            <FlaskConical size={17} />
            <strong>自由探索，放心试用</strong>
            <p>
              所有任务使用模拟数据。
              <br />
              创建实验，体验完整校准流程。
            </p>
            <button onClick={() => setModal("help")}>
              查看演示指南 <ArrowRight size={13} />
            </button>
          </div>
          <button
            className="settings-link"
            onClick={() => showSettings()}
          >
            <Settings2 size={17} />
            <span>{isLeader ? "工作空间管理" : "个人设置与数据"}</span>
            <ChevronRight size={14} />
          </button>
          <div className="profile">
            <div className="avatar">
              {user.displayName.slice(0, 2).toUpperCase()}
            </div>
            <div>
              <strong title={`${user.displayName} · ${user.username}`}>{user.displayName}</strong>
              <small>{isLeader ? "团队负责人" : "工作人员"} · {store.mode === "remote" ? "后端账号" : "演示账号"}</small>
            </div>
            <button className="icon-button" onClick={onLogout} aria-label="退出登录" title="退出登录"><LogOut size={16} /></button>
          </div>
        </div>
      </aside>
      <div className="main-shell" inert={!!modal}>
        <header className="global-bar">
          <div className="breadcrumb">
            <span>工作空间</span>
            <ChevronRight size={13} />
            <b>{viewLabels[view]}</b>
            {view === "blueprint" && (
              <>
                <ChevronRight size={13} />
                <span className="breadcrumb-task">{workflow.name}</span>
              </>
            )}
          </div>
          <div className="bar-actions">
            <button className="role-indicator" onClick={() => showSettings("team")} title={`当前账号 ${user.username} · 查看权限`}><ShieldCheck size={13}/>{isLeader ? "负责人 · 管理权限" : "工作人员 · 执行权限"}</button>
            <button className="icon-button" aria-label="打开实时参数侧栏" title="实时参数侧栏" aria-expanded={!!parameterTarget} onClick={() => parameterTarget ? closeParameters() : showParameters(workflow, selectedNodeId || undefined)}><PanelRightOpen size={18}/></button>
            <button className="icon-button mobile-logout" onClick={onLogout} aria-label="退出当前账号"><LogOut size={16} /></button>
            <button
              className="icon-button theme-toggle"
              aria-label={
                preferences.theme === "dark"
                  ? "切换到明亮主题"
                  : "切换到深色主题"
              }
              title={
                preferences.theme === "dark"
                  ? "切换到明亮主题"
                  : "切换到深色主题"
              }
              onClick={() =>
                savePreferences({
                  ...preferences,
                  theme: preferences.theme === "dark" ? "light" : "dark",
                })
              }
            >
              {preferences.theme === "dark" ? (
                <Sun size={18} />
              ) : (
                <Moon size={18} />
              )}
            </button>
            <span className="local-date">
              {new Intl.DateTimeFormat("zh-CN", {
                month: "short",
                day: "numeric",
              }).format(new Date())}
            </span>
            <span className="demo-pill">
              <span />
              {store.mode === "remote" ? "远程工作空间" : "模拟演示环境"}
            </span>
            <button
              className="icon-button"
              aria-label="查看通知"
              onClick={() => setNotifications(!notifications)}
            >
              <Bell size={17} />
              {metrics.attention > 0 && <i className="notification-dot" />}
            </button>
            <button
              className="icon-button settings-toggle"
              aria-label="工作空间设置"
              title="工作空间设置"
              onClick={() => showSettings()}
            >
              <Settings2 size={17} />
            </button>
          </div>
          {notifications && (
            <div className="notifications popover">
              <strong>任务动态</strong>
              <p>{metrics.attention} 个任务需要关注</p>
              {scopedWorkflows
                .filter(
                  (w) => w.status === "attention" || w.status === "failed",
                )
                .map((w) => (
                  <button
                    key={w.id}
                    onClick={() => {
                      openWorkflow(w);
                      setNotifications(false);
                    }}
                  >
                    <span className={`dot dot-${w.status}`} />
                    <span>
                      {w.name}
                      <small>
                        {STATUS[w.status]} · {w.target}
                      </small>
                    </span>
                    <ChevronRight size={14} />
                  </button>
                ))}
              {metrics.attention === 0 && <p>当前没有待处理任务。</p>}
            </div>
          )}
        </header>
        <section className={`role-workspace-banner ${user.role}`} aria-label="当前角色与权限">
          <span className="role-workspace-icon">{isLeader ? <ShieldCheck size={21}/> : <FlaskConical size={21}/>}</span>
          <div><strong>{isLeader ? "团队负责人工作空间" : "工作人员工作空间"}</strong><p>{isLeader ? "统筹全部实验，审批异常节点，管理策略与工作区数据。" : "执行本人实验；团队任务可查看与导出，异常节点等待负责人审批。"}</p></div>
          <div className="role-capabilities"><span>{isLeader ? "全部任务可管理" : "本人任务可执行"}</span><span>{isLeader ? "审批 / 策略可编辑" : "团队任务只读"}</span></div>
          <button className="secondary" onClick={() => showSettings("team")}>权限说明</button>
        </section>
        {store.error && (
          <div className="workspace-error" role="alert">
            <AlertCircle size={17} />
            <span>{store.error}</span>
          </div>
        )}
        {view === "dashboard" && (
          <main className="dashboard page-enter">
            <div className="page-heading">
              <div>
                <div className="eyebrow">WORKFLOW WORKSPACE</div>
                <h1>
                  {isLeader ? "团队工作看台" : "我的实验工作台"}
                  <span className="heading-dot" />
                </h1>
                <p>{isLeader ? "查看团队进展，处理审批，让实验有序推进。" : "从本人任务开始，运行实验并跟进结果。"}</p>
              </div>
              <div className="heading-actions">
                <button className="secondary" onClick={exportCsv}>
                  <Download size={16} />
                  导出全部实验 CSV
                </button>
                <button className="primary" onClick={openCreate}>
                  <Plus size={17} />
                  新建工作流
                </button>
              </div>
            </div>
            <section className="overview-stats">
              <div>
                <span className="stat-icon blue">
                  <Boxes size={20} />
                </span>
                <div>
                  <small>{scope === "mine" ? "我的工作流" : "团队工作流"}</small>
                  <strong>
                    {metrics.total.toString().padStart(2, "0")}
                    <span>个任务</span>
                  </strong>
                </div>
                <span className="stat-trend">单 / 双比特</span>
              </div>
              <div>
                <span className="stat-icon cyan">
                  <Activity size={20} />
                </span>
                <div>
                  <small>正在运行</small>
                  <strong>
                    {metrics.running.toString().padStart(2, "0")}
                    <span>个任务</span>
                  </strong>
                </div>
                <span className="live-label">
                  <i />
                  实时模拟
                </span>
              </div>
              <div>
                <span className="stat-icon amber">
                  <Bell size={19} />
                </span>
                <div>
                  <small>需要关注</small>
                  <strong>
                    {metrics.attention.toString().padStart(2, "0")}
                    <span>个任务</span>
                  </strong>
                </div>
                <button
                  className="stat-link"
                  onClick={() => {
                    setStatusFilter("attention");
                    setPage(1);
                  }}
                >
                  {isLeader ? "立即处理" : "查看状态"} <ArrowRight size={13} />
                </button>
              </div>
              <div>
                <span className="stat-icon green">
                  <CheckCheck size={21} />
                </span>
                <div>
                  <small>已完成</small>
                  <strong>
                    {metrics.completed.toString().padStart(2, "0")}
                    <span>个任务</span>
                  </strong>
                </div>
                <Sparkline />
              </div>
            </section>
            {isLeader && <section className="leader-approval-queue" aria-label="负责人审批队列"><div className="approval-queue-heading"><ShieldCheck size={18}/><strong>待我审批</strong><span>{approvals.length} 个任务</span><small>仅负责人可批准或退回</small></div>{approvals.length ? approvals.slice(0, 3).map((w) => <div className="approval-queue-row" key={w.id}><button onClick={() => openWorkflow(w)}><strong>{w.name}</strong><span>{w.target} · {w.nodes.find((n) => n.status === "attention")?.label || "等待审批"}</span></button><button className="secondary" onClick={() => openWorkflow(w)}>查看并审批 <ArrowRight size={13}/></button></div>) : <p className="approval-empty">当前没有待审批任务，异常或逐步审批节点将在这里出现。</p>}</section>}
            <div className="role-scope-switch" role="group" aria-label="任务视角"><button className={scope === "mine" ? "selected" : ""} aria-pressed={scope === "mine"} onClick={() => { setScope("mine"); setPage(1); }}>我的任务 <b>{ownWorkflows.length}</b></button><button className={scope === "all" ? "selected" : ""} aria-pressed={scope === "all"} onClick={() => { setScope("all"); setPage(1); }}>{isLeader ? "团队全部任务" : "团队任务 · 只读浏览"} <b>{state.workflows.length}</b></button><span>{isLeader ? "你可管理所有任务" : scope === "mine" ? "仅本人创建的任务可运行、暂停与终止" : "本人任务仍可操作，其他任务只能查看或复制"}</span></div>
            <div className="list-heading">
              <div>
                <h2>
                  实验工作流 <span>{filtered.length}</span>
                </h2>
                <p>统一管理实验，追踪每一步决策</p>
              </div>
              <label className="search-box">
                <Search size={16} />
                <input
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setPage(1);
                  }}
                  placeholder="搜索名称、机器或比特…"
                  aria-label="搜索工作流"
                />
                {search && (
                  <button
                    onClick={() => {
                      setSearch("");
                      setPage(1);
                    }}
                    aria-label="清空搜索"
                  >
                    <X size={13} />
                  </button>
                )}
              </label>
            </div>
            <div className="filter-row">
              <div className="status-tabs">
                {(
                  [
                    "all",
                    "running",
                    "attention",
                    "completed",
                    "draft",
                    "paused",
                    "queued",
                    "failed",
                    "aborted",
                  ] as const
                ).map((s) => (
                  <button
                    className={statusFilter === s ? "selected" : ""}
                    key={s}
                    onClick={() => {
                      setStatusFilter(s);
                      setPage(1);
                    }}
                  >
                    {s === "all" ? "全部" : STATUS[s]}
                    {s === "attention" && <span className="amber-dot" />}
                  </button>
                ))}
              </div>
              <div className="filter-selects">
                <ListFilter size={15} />
                <select
                  aria-label="筛选机器"
                  value={machineFilter}
                  onChange={(e) => {
                    setMachineFilter(e.target.value);
                    setPage(1);
                  }}
                >
                  <option value="all">全部机器</option>
                  {[...new Set(state.workflows.map((w) => w.machine))]
                    .sort()
                    .map((m) => (
                      <option key={m}>{m}</option>
                    ))}
                </select>
                <select
                  aria-label="筛选实验类型"
                  value={typeFilter}
                  onChange={(e) => {
                    setTypeFilter(e.target.value);
                    setPage(1);
                  }}
                >
                  <option value="all">单双比特</option>
                  <option value="single">单比特</option>
                  <option value="two">双比特</option>
                </select>
              </div>
            </div>
            <div className="workflow-table-wrap">
              <table className="workflow-table">
                <thead>
                  <tr>
                    <th className="name-col">工作流名称</th>
                    <th>类型</th>
                    <th>状态</th>
                    <th>进度</th>
                    <th>机器 / 目标</th>
                    <th>审批策略</th>
                    <th>
                      <button onClick={() => setSortNewest(!sortNewest)}>
                        创建时间{" "}
                        <ArrowDown
                          size={12}
                          style={{
                            transform: sortNewest ? "none" : "rotate(180deg)",
                          }}
                        />
                      </button>
                    </th>
                    <th>负责人</th>
                    <th aria-label="操作" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((w) => (
                    <tr
                      key={w.id}
                      className={
                        w.status === "attention" ? "row-attention" : ""
                      }
                    >
                      <td>
                        <button
                          className="workflow-name"
                          onClick={() => openWorkflow(w)}
                        >
                          <span
                            className={`workflow-icon ${w.experiment === "two" ? "purple" : ""}`}
                          >
                            {w.experiment === "two" ? (
                              <GitBranch size={18} />
                            ) : (
                              <Activity size={18} />
                            )}
                          </span>
                          <span>
                            <strong>{w.name}</strong>
                            <small>
                              {w.experiment === "two"
                                ? "双比特 CZ 校准"
                                : "单比特完整流程"}{" "}
                              · {w.nodes.length} 个实验节点
                            </small>
                          </span>
                        </button>
                        <div className="workflow-access-row"><span className={`task-access-badge ${canOperateWorkflow(user, w) ? "editable" : "readonly"}`}>{canOperateWorkflow(user, w) ? <ShieldCheck size={11}/> : <LockKeyhole size={11}/>} {isLeader ? "可管理" : w.ownerId === user.id ? "本人 · 可执行" : "团队任务 · 只读"}</span><button className="parameter-row-toggle" aria-label={`查看 ${w.name} 实时参数`} aria-expanded={parameterTarget?.workflowId === w.id} aria-controls="parameter-drawer" onClick={() => showParameters(w)}><PanelRightOpen size={13}/> 实时参数</button></div>
                      </td>
                      <td>
                        <span className="type-label">
                          {w.type === "adaptive" ? (
                            <Sparkles size={12} />
                          ) : (
                            <GitBranch size={12} />
                          )}{" "}
                          {w.type === "adaptive" ? "自适应" : "固定"}
                        </span>
                      </td>
                      <td>
                        <Badge status={w.status} />
                      </td>
                      <td>
                        <div className="progress-cell">
                          <span>
                            {Math.floor(w.progress)}%
                            <small>
                              {
                                w.nodes.filter((n) => n.status === "success")
                                  .length
                              }
                              /{w.nodes.length}
                            </small>
                          </span>
                          <div className={`progress-track track-${w.status}`}>
                            <i style={{ width: `${w.progress}%` }} />
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className="machine-name">
                          <span className="tiny-chip" />
                          {w.machine}
                        </span>
                        <small className="subtext">{w.target}</small>
                      </td>
                      <td>
                        <span className={`policy-tag ${w.policy}`}>
                          <ShieldCheck size={11} />
                          {POLICY[w.policy]}
                        </span>
                      </td>
                      <td className="date-cell">
                        {new Intl.DateTimeFormat("zh-CN", {
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                          hour12: false,
                        }).format(new Date(w.createdAt))}
                      </td>
                      <td>
                        <span className="owner-avatar" title={`${w.owner}${w.ownerId === user.id ? "（本人）" : ""}`}>
                          {w.owner === "system"
                            ? "S"
                            : w.owner.split(" ").at(-1)?.[0] || "L"}
                        </span>
                      </td>
                      <td className="row-menu-cell">
                        <button
                          className="icon-button"
                          aria-label={`操作 ${w.name}`}
                          onClick={() =>
                            setMenuId(menuId === w.id ? null : w.id)
                          }
                        >
                          <MoreHorizontal size={19} />
                        </button>
                        {menuId === w.id && (
                          <div className="row-menu popover">
                            <button onClick={() => openWorkflow(w)}>
                              <GitBranch size={14} />
                              打开蓝图
                            </button>
                            <button onClick={() => openWorkflow(w, "chat")}>
                              <MessageSquare size={14} />
                              查看报告
                            </button>
                            {["draft", "queued"].includes(w.status) && (
                              <button disabled={!canPerformAction(user, w, "start")} onClick={() => action("start", w.id)}>
                                <Play size={14} />
                                开始运行
                              </button>
                            )}
                            {w.status === "running" && (
                              <button disabled={!canPerformAction(user, w, "pause")} onClick={() => action("pause", w.id)}>
                                <Pause size={14} />
                                暂停任务
                              </button>
                            )}
                            {w.status === "paused" && (
                              <button disabled={!canPerformAction(user, w, "resume")} onClick={() => action("resume", w.id)}>
                                <Play size={14} />
                                恢复任务
                              </button>
                            )}
                            {["attention", "failed"].includes(w.status) && (
                              <button disabled={!canPerformAction(user, w, "retry")} onClick={() => action("retry", w.id)}>
                                <RotateCcw size={14} />
                                重新尝试
                              </button>
                            )}
                            <button
                              onClick={() => copyWorkflow(w)}
                            >
                              <Plus size={14} />
                              {isLeader ? "复制为新任务" : "复制为我的任务"}
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {rows.length === 0 && (
                <div className="empty-state">
                  <Search size={28} />
                  <h3>{!isLeader && scope === "mine" && ownWorkflows.length === 0 ? "开始你的第一个实验" : "没有匹配的工作流"}</h3>
                  <p>{!isLeader && scope === "mine" && ownWorkflows.length === 0 ? "新建任务后，你可以运行、暂停、查看参数并导出实验结果。" : "试试其他关键词，或清除筛选条件。"}</p>
                  {!isLeader && scope === "mine" && ownWorkflows.length === 0 && <button className="primary" onClick={openCreate}><Plus size={15}/> 创建我的实验</button>}
                  <button
                    className="secondary"
                    onClick={() => {
                      setSearch("");
                      setStatusFilter("all");
                      setMachineFilter("all");
                      setTypeFilter("all");
                      setPage(1);
                    }}
                  >
                    清除筛选
                  </button>
                </div>
              )}
            </div>
            <div className="table-footer">
              <span>
                显示 {filtered.length ? (currentPage - 1) * 8 + 1 : 0}–
                {Math.min(currentPage * 8, filtered.length)} 条，共{" "}
                {filtered.length} 个工作流
              </span>
              <div>
                <span>每页 8 条</span>
                <button
                  className="icon-button"
                  aria-label="第一页"
                  disabled={currentPage === 1}
                  onClick={() => setPage(1)}
                >
                  <ChevronsLeft size={16} />
                </button>
                <button
                  className="icon-button"
                  aria-label="上一页"
                  disabled={currentPage === 1}
                  onClick={() => setPage(currentPage - 1)}
                >
                  <ChevronRight
                    size={16}
                    style={{ transform: "rotate(180deg)" }}
                  />
                </button>
                <span className="page-number">{currentPage}</span>
                <button
                  className="icon-button"
                  aria-label="下一页"
                  disabled={currentPage === totalPages}
                  onClick={() => setPage(currentPage + 1)}
                >
                  <ChevronRight size={16} />
                </button>
                <button
                  className="icon-button"
                  aria-label="最后一页"
                  disabled={currentPage === totalPages}
                  onClick={() => setPage(totalPages)}
                >
                  <ChevronsRight size={16} />
                </button>
              </div>
            </div>
            <div className="dashboard-bottom">
              <div className="blueprint-promo">
                <span className="promo-icon">
                  <GitBranch size={23} />
                </span>
                <div>
                  <strong>让实验流程，变得清晰可见</strong>
                  <p>从定频、读取优化，到单比特表征与双比特 CZ 校准。</p>
                </div>
                <button
                  className="text-button"
                  onClick={() => setView("blueprint")}
                >
                  探索实验蓝图 <ArrowRight size={15} />
                </button>
              </div>
              <div className="source-note">
                <ShieldCheck size={14} />
                <span>流程依据已提供的单双比特测试图 · 数据仅供交互演示</span>
              </div>
            </div>
          </main>
        )}
        {view === "blueprint" && (
          <main className="blueprint-page page-enter">
            <div className="blueprint-header">
              <div>
                <button
                  className="back-link"
                  onClick={() => setView("dashboard")}
                >
                  <ArrowLeft size={13} />
                  全部工作流
                </button>
                <div className="blueprint-title">
                  <h1>{workflow.name}</h1>
                  <Badge status={workflow.status} />
                  <span className="blueprint-kind">
                    {workflow.experiment === "two" ? "双比特" : "单比特"}
                  </span>
                </div>
                <div className="blueprint-meta">
                  <span>
                    <Boxes size={13} /> {workflow.machine} / {workflow.target}
                  </span>
                  <span>
                    <Clock3 size={13} /> 演示计时{" "}
                    {elapsed(Math.floor(workflow.elapsed))}
                  </span>
                  <span>
                    <RotateCcw size={13} /> {workflow.iterations} 次重试
                  </span>
                  <span>
                    <ShieldCheck size={13} /> {POLICY[workflow.policy]}
                  </span>
                </div>
              </div>
              <div className="heading-actions">
                <button className="secondary" onClick={() => showParameters(workflow, selectedNodeId || undefined)}><PanelRightOpen size={15}/> 实时参数</button>
                <button className="secondary" onClick={() => setView("chat")}>
                  <MessageSquare size={15} />
                  任务对话
                </button>
                {["draft", "queued"].includes(workflow.status) ? (
                  <button className="primary" disabled={!canPerformAction(user, workflow, "start")} onClick={() => action("start")}>
                    <Play size={16} />
                    开始运行
                  </button>
                ) : workflow.status === "running" ? (
                  <button className="secondary" disabled={!canPerformAction(user, workflow, "pause")} onClick={() => action("pause")}>
                    <Pause size={16} />
                    暂停
                  </button>
                ) : workflow.status === "paused" ? (
                  <button className="primary" disabled={!canPerformAction(user, workflow, "resume")} onClick={() => action("resume")}>
                    <Play size={16} />
                    继续运行
                  </button>
                ) : workflow.status === "completed" ? (
                  <button className="primary" onClick={() => setView("chat")}>
                    <CheckCheck size={16} />
                    查看报告
                  </button>
                ) : (
                  <button className="primary" onClick={() => openBlueprint(workflow.experiment)}>
                    <Plus size={16} />
                    新建工作流
                  </button>
                )}
              </div>
            </div>
            <section className="blueprint-library" aria-label="校准蓝图选择">
              <div><strong>选择校准蓝图</strong><span>当前任务按所选蓝图限定实验节点和对话计划</span></div>
              <div className="blueprint-library-options">
                {(["single", "two"] as const).map((kind) => <button key={kind} type="button"
                  className={workflow.experiment === kind ? "active" : ""}
                  onClick={() => openBlueprint(kind)}
                  aria-label={`使用${BLUEPRINTS[kind].title}新建工作流`}>
                  {kind === "single" ? <Activity size={20}/> : <GitBranch size={20}/>}
                  <span><strong>{BLUEPRINTS[kind].title}</strong><small>{BLUEPRINTS[kind].description}</small></span>
                  <em>{BLUEPRINTS[kind].nodeCount} 节点</em>
                </button>)}
              </div>
            </section>
            <section className="blueprint-stages" aria-label="当前蓝图阶段进度">
              {BLUEPRINT_STAGES[workflow.experiment].map((stage) => {
                const done = stage.nodes.filter((id) => workflow.nodes.find((node) => node.id === id)?.status === "success").length;
                return <div className="blueprint-stage" key={stage.title}>
                  <strong>{stage.title}</strong><span>{done}/{stage.nodes.length}</span>
                  <div className="blueprint-stage-track"><i style={{ width: `${done / stage.nodes.length * 100}%` }} /></div>
                </div>;
              })}
            </section>
            {!isLeader && <div className={`task-permission-banner ${canOperateWorkflow(user, workflow) ? "own" : "readonly"}`}><LockKeyhole size={16}/><div><strong>{canOperateWorkflow(user, workflow) ? workflow.status === "attention" ? "本人任务 · 等待负责人审批" : "本人任务 · 你有执行权限" : "团队任务 · 当前为只读模式"}</strong><span>{canOperateWorkflow(user, workflow) ? "可以运行、暂停或终止；审批与策略调整由团队负责人处理。" : `任务负责人：${workflow.owner}。你可查看参数、结果与记录；复制后可独立运行。`}</span></div>{!canOperateWorkflow(user, workflow) && <button className="secondary" onClick={() => copyWorkflow(workflow)}><Plus size={14}/> 复制为我的任务</button>}</div>}
            <div className="blueprint-body">
              <section className="blueprint-main">
                <div className="canvas-top">
                  <div>
                    <span className="live-label">
                      <i /> {store.mode === "remote" ? (workflow.status === "running" ? "服务器执行中" : "服务器已同步") : autoDemo ? "模拟引擎在线" : "模拟时钟已停止"}
                    </span>
                    <span className="canvas-separator" />
                    <span>按原始实验流程编排</span>
                  </div>
                  <span>
                    {
                      workflow.nodes.filter((n) => n.status === "success")
                        .length
                    }{" "}
                    / {workflow.nodes.length} 节点完成{" "}
                    <span className="mini-progress">
                      <i style={{ width: `${workflow.progress}%` }} />
                    </span>
                  </span>
                </div>
                <FlowCanvas
                  workflow={workflow}
                  selectedNodeId={node.id}
                  onSelectNode={setSelectedNodeId}
                  decisions={nodeDecisions}
                />
                {store.mode === "remote" && (
                  <div
                    className={
                      remoteTargets.error ? "dt-sync dt-sync-error" : "dt-sync"
                    }
                    role={remoteTargets.error ? "alert" : "status"}
                  >
                    <span>
                      {remoteTargets.error
                        ? `设计目标同步失败：${remoteTargets.error}。当前沿用上次读取的绑定，不会回退到演示目标。`
                        : "设计目标与验收下限取自当前工作流绑定的 Skill 与设备档案。"}
                    </span>
                    <button
                      type="button"
                      className="dt-replay-button"
                      onClick={remoteTargets.refresh}
                      disabled={remoteTargets.refreshing}
                    >
                      <RefreshCw size={12} />
                      {remoteTargets.refreshing
                        ? "刷新中…"
                        : remoteTargets.error
                          ? "重新读取"
                          : "刷新绑定"}
                    </button>
                  </div>
                )}
                {store.mode === "remote" && (
                  <DecisionTrail
                    workflow={workflow}
                    audit={decisionTrail.audit}
                    replay={decisionTrail.replay}
                    loading={decisionTrail.loading}
                    error={decisionTrail.error}
                    replayBusy={decisionTrail.replayBusy}
                    replayError={decisionTrail.replayError}
                    onReplay={decisionTrail.runReplay}
                    selectedNodeId={node?.id ?? ""}
                    onSelectNode={setSelectedNodeId}
                  />
                )}
                <div className="metric-heading">
                  <span>实验指标概览</span>
                  <small>
                    {reported?.source === "server"
                      ? "服务端测量 · 随节点完成展示"
                      : "模拟样本 · 随节点完成展示"}
                    {remoteMeasurements.loading ? " · 同步中" : ""}
                    {remoteMeasurements.parametersError
                      ? " · 回读同步失败"
                      : ""}
                  </small>
                </div>
                <div className="metric-grid">
                  {metricItems.map((m, i) => {
                    const ready = measurementStage(m.node) === "ready";
                    return (
                      <div className="metric-card" key={m.title}>
                        <div className="metric-card-label">
                          {m.title}
                          <Activity size={13} />
                        </div>
                        <div className="metric-number">
                          {ready ? m.value : "—"}
                          <span>{m.unit}</span>
                        </div>
                        <div className="metric-card-bottom">
                          <span
                            className={ready ? "improved" : "pending-metric"}
                          >
                            {ready ? "已采集 · 模拟结果" : "等待实验完成"}
                          </span>
                          {ready && <Sparkline index={i} />}
                        </div>
                        <div className="metric-strip">
                          {Array.from({ length: 26 }, (_, j) => (
                            <i
                              key={j}
                              style={{
                                opacity: ready ? 0.35 + (j % 7) * 0.09 : 0.16,
                                background:
                                  ready && j === 20 && i === 2
                                    ? "#d9ac58"
                                    : undefined,
                              }}
                            />
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="events-panel">
                  <div>
                    <h3>
                      <Radio size={14} />
                      运行动态
                    </h3>
                    <small>{store.mode === "remote" ? "服务器事件 · 最新 4 条" : "前端模拟事件 · 最新 4 条"}</small>
                  </div>
                  {workflow.events
                    .slice(-4)
                    .reverse()
                    .map((e, i) => (
                      <p key={`${e.time}-${i}`}>
                        <time>
                          {new Intl.DateTimeFormat("zh-CN", {
                            hour: "2-digit",
                            minute: "2-digit",
                            second: "2-digit",
                            hour12: false,
                          }).format(new Date(e.time))}
                        </time>
                        <span className={`event-dot ${e.kind}`} />
                        <span>{e.message}</span>
                      </p>
                    ))}
                </div>
                <details className="blueprint-timeline">
                  <summary>节点执行时间线 · {workflow.nodes.filter((item) => item.status === "success").length}/{workflow.nodes.length} 已完成</summary>
                  <ol>
                    {workflow.nodes.map((item, index) => <li key={item.id} className={`timeline-${item.status}`}>
                      <button type="button" onClick={() => setSelectedNodeId(item.id)}>
                        <span>{String(index + 1).padStart(2, "0")}</span>
                        <strong>{item.label}</strong>
                        <em>{item.status === "success" ? "已完成" : item.status === "running" ? "运行中" : item.status === "attention" ? "待审批" : item.status === "abandoned" ? "已终止" : "待运行"}</em>
                        {item.attempt > 0 && <small>第 {item.attempt} 次</small>}
                      </button>
                    </li>)}
                  </ol>
                </details>
              </section>
              <NodeInspector
                workflow={workflow}
                node={node}
                onAction={action}
                onChat={() => setView("chat")}
                onPolicy={changePolicy}
                canAction={(a) => canPerformAction(user, workflow, a)}
                canEditPolicy={isLeader}
                onOpenParameters={() => showParameters(workflow, node.id)}
              />
            </div>
          </main>
        )}
        {view === "api" && <ApiOnlyWorkbench />}
        <div className="cr-host" hidden={view !== "chat"}>
          <ChatReport
            workflow={workflow}
            onOpenWorkflow={() => setView("blueprint")}
            onCreateWorkflow={(input) => {
              if (input.experiment !== workflow.experiment) { toast("当前任务不能创建其他蓝图的工作流；请先切换蓝图。"); return; }
              void create(input);
            }}
            onNotify={toast}
            canEditConversation={canOperateWorkflow(user, workflow)}
          />
        </div>
        <footer className="app-footer">
          <span>
            <i />
            {PLATFORM_NAME} <b>v1.3.1</b>
          </span>
          <span>{store.mode === "remote" ? "远程模式 · 数据由 QuantaMind 后端保存" : "本地模拟 · 未连接真实设备或后端"}</span>
          <button onClick={() => setModal("help")}>
            使用指南 <ArrowRight size={11} />
          </button>
        </footer>
      </div>
      {parameterWorkflow && parameterTarget && !modal && <ParameterDrawer key={parameterWorkflow.id} workflow={parameterWorkflow} selectedNodeId={parameterTarget.nodeId} onClose={closeParameters} />}
      {notify && (
        <div className="toast" role="status">
          <Check size={17} />
          {notify}
          <button onClick={() => setNotify("")} aria-label="关闭提示">
            <X size={14} />
          </button>
        </div>
      )}
      {modal && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setModal(null);
          }}
        >
          <section
            className={`modal modal-${modal}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
          >
            <button
              className="modal-close icon-button"
              onClick={() => setModal(null)}
              aria-label="关闭"
            >
              <X size={20} />
            </button>
            {modal === "create" && (
              <>
                <div className="modal-icon">
                  <GitBranch size={25} />
                </div>
                <span className="eyebrow">NEW WORKFLOW</span>
                <h2 id="modal-title">创建实验工作流</h2>
                <p className="modal-description">
                  {lockedBlueprint ? `已选择${BLUEPRINTS[lockedBlueprint].title}；此工作流仅包含对应的校准实验。` : "选择实验路径，一次走通从校准到性能验证。"}
                </p>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    create(form);
                  }}
                >
                  <div className="experiment-choice">
                    {(["single", "two"] as const).filter((ex) => !lockedBlueprint || ex === lockedBlueprint).map((ex) => (
                      <button
                        type="button"
                        className={form.experiment === ex ? "chosen" : ""}
                        key={ex}
                        onClick={() =>
                          setForm({
                            ...form,
                            experiment: ex,
                            qubits: ex === "single" ? 1 : 2,
                            target: ex === "single" ? "Q12" : "Q12–Q13",
                            name: `${form.machine} ${ex === "single" ? "单比特完整校准" : "双比特 CZ 校准"}`,
                          })
                        }
                      >
                        {ex === "single" ? (
                          <Activity size={22} />
                        ) : (
                          <GitBranch size={22} />
                        )}
                        <strong>
                          {ex === "single" ? "单比特测试" : "双比特测试"}
                        </strong>
                        <small>
                          {ex === "single"
                            ? "定频 · 定幅 · 读取 · 性能"
                            : "单比特前置 · CZ 校准 · XEB"}
                        </small>
                        <span>
                          {ex === "single" ? "9" : "17"} 个执行节点
                          {form.experiment === ex && <Check size={13} />}
                        </span>
                      </button>
                    ))}
                  </div>
                  <label className="field">
                    工作流名称
                    <input
                      required
                      maxLength={80}
                      value={form.name}
                      onChange={(e) =>
                        setForm({ ...form, name: e.target.value })
                      }
                    />
                  </label>
                  <div className="form-row">
                    <label className="field">
                      目标机器
                      <select
                        value={form.machine}
                        onChange={(e) =>
                          setForm({ ...form, machine: e.target.value })
                        }
                      >
                        {["M7", "M3", "M5", "M8"].map((x) => (
                          <option key={x}>{x}</option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      {form.experiment === "two" ? "目标比特对" : "目标比特"}
                      <input
                        required
                        maxLength={40}
                        value={form.target}
                        onChange={(e) =>
                          setForm({ ...form, target: e.target.value })
                        }
                        placeholder={
                          form.experiment === "two" ? "Q12–Q13" : "Q12"
                        }
                        title={
                          form.experiment === "two"
                            ? "例如 Q12–Q13，两个不同量子比特"
                            : "例如 Q12"
                        }
                      />
                    </label>
                  </div>
                  <div className="form-row">
                    <label className="field">
                      编排方式
                      <select
                        value={form.type}
                        onChange={(e) =>
                          setForm({
                            ...form,
                            type: e.target.value as "adaptive" | "fixed",
                          })
                        }
                      >
                        <option value="adaptive">自适应 · 支持异常重试</option>
                        <option value="fixed">固定 · 既定节点顺序</option>
                      </select>
                    </label>
                    <label className="field">
                      审批策略
                      <select
                        value={form.policy}
                        disabled={!isLeader}
                        onChange={(e) =>
                          setForm({ ...form, policy: e.target.value as Policy })
                        }
                      >
                        {Object.entries(POLICY).map(([k, v]) => (
                          <option key={k} value={k}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <label className="field">
                    演示场景
                    <select
                      value={form.scenario}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          scenario: e.target.value as "normal" | "anomaly",
                        })
                      }
                    >
                      <option value="normal">
                        顺利完成 · 体验完整校准流程
                      </option>
                      <option value="anomaly">注入异常 · 体验审批与重试</option>
                    </select>
                  </label>
                  <div className="form-note">
                    <FlaskConical size={16} />
                    <span>
                      使用内置模拟数据，不调用设备。单节点约 4
                      秒；逐步审批时会等待你的决定。
                    </span>
                  </div>
                  <div className="modal-footer">
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => setModal(null)}
                    >
                      取消
                    </button>
                    <button
                      type="submit"
                      className="primary"
                      disabled={createBusy}
                    >
                      <Plus size={16} />
                      {createBusy ? "正在创建…" : "创建工作流"}
                    </button>
                  </div>
                </form>
              </>
            )}
            {modal === "settings" && (
              <WorkspaceSettings
                preferences={preferences}
                onSave={savePreferences}
                state={state}
                mode={store.mode}
                user={user}
                canImport={store.canImport && isLeader}
                onImport={store.importWorkflows}
                onExport={exportState}
                onExportCsv={exportCsv}
                initialTab={settingsTab}
                onReset={() => setModal("reset")}
                onNotify={toast}
                workflowId={workflow?.id}
              />
            )}
            {modal === "help" && (
              <>
                <div className="modal-icon">
                  <Sparkles size={25} />
                </div>
                <span className="eyebrow">A SMALL GUIDE TO BIG IDEAS</span>
                <h2 id="modal-title">从这里，开启一次实验</h2>
                <p className="modal-description">
                  三个视图共享同一组任务与状态。可在任意阶段切换。
                </p>
                <div className="guide-steps">
                  <div>
                    <span>01</span>
                    <div>
                      <strong>对话式 Agent</strong>
                      <p>
                        输入“生成单比特校准计划”或“双比特 CZ
                        校准”，由规则演示助手生成计划并创建任务。
                      </p>
                    </div>
                  </div>
                  <div>
                    <span>02</span>
                    <div>
                      <strong>蓝图版工作流</strong>
                      <p>
                        点击开始运行，查看节点、观测与决策；可以暂停、审批、重试，体验读取优化及
                        CZ 复测回路。
                      </p>
                    </div>
                  </div>
                  <div>
                    <span>03</span>
                    <div>
                      <strong>工作看台</strong>
                      <p>
                        筛选、搜索并管理所有实验。完成后打开任务对话，下载模拟分析报告。
                      </p>
                    </div>
                  </div>
                </div>
                <div className="form-note">
                  <FlaskConical size={17} />
                  <span>
                    这是一套前端模拟演示。拟合结果、质量阈值与耗时均不能作为设备性能依据。
                  </span>
                </div>
                <div className="modal-footer">
                  <button
                    className="primary"
                    onClick={() => {
                      openCreate();
                    }}
                  >
                    开始一次新实验 <ArrowRight size={16} />
                  </button>
                </div>
              </>
            )}
            {(modal === "reset" || modal === "abort") && (
              <>
                <div className="modal-icon warning">
                  <RotateCcw size={24} />
                </div>
                <h2 id="modal-title">
                  {modal === "reset"
                    ? "重置模拟工作空间？"
                    : "终止当前模拟任务？"}
                </h2>
                <p className="modal-description">
                  {modal === "reset"
                    ? "当前浏览器的工作流、对话和附件将被清除，并恢复 8 条初始示例。JSON 仅备份工作流；重要附件请先下载副本。"
                    : `“${state.workflows.find((w) => w.id === abortTarget)?.name || workflow.name}”将停止推进，保留已有实验记录。可以复制任务重新开始。`}
                </p>
                <div className="modal-footer">
                  <button className="secondary" onClick={() => setModal(null)}>
                    取消
                  </button>
                  <button
                    className="danger"
                    onClick={async () => {
                      try {
                      if (modal === "reset") {
                        if (readSession()?.id !== user.id) { onLogout(); return; }
                        if (!isLeader) throw new Error("只有团队负责人可以重置工作区");
                        await clearLocalConversations();
                        store.resetDemo();
                        setSelectedId("wf-m7-bringup");
                        setSelectedNodeId("");
                        setStatusFilter("all");
                        setMachineFilter("all");
                        setTypeFilter("all");
                        setSearch("");
                        setPage(1);
                        setView("dashboard");
                        toast("已恢复初始演示任务");
                      } else {
                        void store
                          .act(abortTarget, "abort")
                          .then(() => toast("任务已终止"))
                          .catch((e) => toast(e.message));
                      }
                      setModal(null);
                      } catch (e) { toast(e instanceof Error ? e.message : "操作失败，请重试"); }
                    }}
                  >
                    {modal === "reset" ? "确认重置" : "确认终止"}
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
