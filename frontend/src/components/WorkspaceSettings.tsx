import { useEffect, useRef, useState } from "react";
import {
  Check,
  Download,
  Upload,
  Sun,
  Moon,
  UserRound,
  Users,
  SlidersHorizontal,
  Database,
  Plug,
  ShieldCheck,
  RotateCcw,
  FileJson,
  FileSpreadsheet,
  AlertCircle,
} from "lucide-react";
import type { AppState } from "../types";
import type { Preferences } from "../lib/preferences";
import { validateEndpoint } from "../lib/preferences";
import { previewWorkspaceImport } from "../lib/workspaceTransfer";
import type { WorkspaceImportResult } from "../lib/workspaceTransfer";
import { countExecutedWorkflows, EXPERIMENT_CSV_HISTORY_NOTE } from "../lib/experimentCsv";
import { PLATFORM_NAME } from "../lib/branding";
import "./workspace-settings.css";
import DataStorageCard from "./DataStorageCard";
import type { User } from "../lib/auth";
import { DEMO_ACCOUNTS, ROLE_LABELS } from "../lib/auth";
import {
  describeAuditEntry,
  describeReplayItem,
  fetchWorkflowAudit,
  fetchWorkflowReplay,
  fetchWorkflowSkill,
} from "../lib/remoteInsights";
import type {
  SkillBinding,
  WorkflowAudit,
  WorkflowReplay,
} from "../lib/remoteInsights";
import { formatWireCriterion, formatWireTarget } from "../lib/metricFormat";
import {
  ACCOUNT_ROLE_LABELS,
  changeOwnPassword,
  createAccount,
  fetchAccounts,
  resetAccountPassword,
  updateAccount,
} from "../lib/accountAdmin";
import type { AccountPatch, AccountRecord, RemoteAccountRole } from "../lib/accountAdmin";

type Tab = "personal" | "experiment" | "data" | "connection" | "team" | "skills";
interface Props {
  user: User;
  preferences: Preferences;
  onSave: (value: Preferences) => void;
  state: AppState;
  mode: "mock" | "remote";
  canImport: boolean;
  onImport: (json: string) => Promise<WorkspaceImportResult>;
  onExport: () => void;
  onExportCsv: () => void;
  initialTab?: Tab;
  onReset: () => void;
  onNotify: (text: string) => void;
  workflowId?: string;
}

function knownAccountRole(value: string): RemoteAccountRole | null {
  return value === "calibration_leader" || value === "calibration_operator"
    ? value
    : null;
}

function accountErrorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

const EMPTY_NEW_ACCOUNT = {
  username: "",
  displayName: "",
  password: "",
  role: "calibration_operator" as RemoteAccountRole,
};

export default function WorkspaceSettings({
  user,
  preferences,
  onSave,
  state,
  mode,
  canImport,
  onImport,
  onExport,
  onExportCsv,
  initialTab = "personal",
  onReset,
  onNotify,
  workflowId,
}: Props) {
  const isLeader = user.role === "leader";
  const [tab, setTab] = useState<Tab>(initialTab);
  const [skillView, setSkillView] = useState<SkillBinding | null>(null);
  const [auditView, setAuditView] = useState<WorkflowAudit | null>(null);
  const [replayView, setReplayView] = useState<WorkflowReplay | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayError, setReplayError] = useState("");
  const [insightBusy, setInsightBusy] = useState(false);
  const [insightError, setInsightError] = useState("");
  const [insightReload, setInsightReload] = useState(0);
  const [draft, setDraft] = useState(preferences);
  const [fileText, setFileText] = useState("");
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [accounts, setAccounts] = useState<AccountRecord[] | null>(null);
  const [accountBusy, setAccountBusy] = useState(false);
  const [accountError, setAccountError] = useState("");
  const [accountReload, setAccountReload] = useState(0);
  const [newAccount, setNewAccount] = useState(EMPTY_NEW_ACCOUNT);
  const [resetTarget, setResetTarget] = useState<string | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [ownPassword, setOwnPassword] = useState({
    current: "",
    next: "",
    confirm: "",
  });
  const [ownPasswordBusy, setOwnPasswordBusy] = useState(false);
  const fileReadSequence = useRef(0);
  useEffect(
    () => () => {
      fileReadSequence.current += 1;
    },
    [],
  );
  useEffect(() => {
    if (tab !== "skills" || mode !== "remote" || !workflowId) return;
    let cancelled = false;
    setInsightBusy(true);
    setInsightError("");
    setReplayView(null);
    setReplayError("");
    Promise.all([
      fetchWorkflowSkill(workflowId),
      fetchWorkflowAudit(workflowId),
    ])
      .then(([binding, audit]) => {
        if (cancelled) return;
        setSkillView(binding);
        setAuditView(audit);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setInsightError(
            cause instanceof Error ? cause.message : "无法读取 Skill 与审计记录。",
          );
      })
      .finally(() => {
        if (!cancelled) setInsightBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, mode, workflowId, insightReload]);
  useEffect(() => {
    if (tab !== "team" || mode !== "remote" || !isLeader) {
      setAccounts(null);
      setAccountError("");
      setResetTarget(null);
      return;
    }
    let cancelled = false;
    setAccountBusy(true);
    setAccountError("");
    fetchAccounts()
      .then((page) => {
        if (!cancelled) setAccounts(page.items);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setAccounts(null);
          setAccountError(accountErrorText(cause, "无法读取账号目录。"));
        }
      })
      .finally(() => {
        if (!cancelled) setAccountBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab, mode, isLeader, accountReload]);
  const replaceAccount = (record: AccountRecord) => {
    setAccounts((list) =>
      list
        ? list.map((item) => (item.userId === record.userId ? record : item))
        : list,
    );
  };
  const addAccount = async () => {
    const username = newAccount.username.trim();
    const displayName = newAccount.displayName.trim();
    if (!username || !displayName) {
      setAccountError("请填写用户名和显示名称。");
      return;
    }
    if (newAccount.password.length < 8) {
      setAccountError("初始密码至少需要 8 个字符。");
      return;
    }
    setAccountBusy(true);
    setAccountError("");
    try {
      const created = await createAccount({
        ...newAccount,
        username,
        displayName,
      });
      setAccounts((list) => (list ? [...list, created] : [created]));
      setNewAccount(EMPTY_NEW_ACCOUNT);
      onNotify(`已创建账号 ${created.username}`);
    } catch (cause) {
      setAccountError(accountErrorText(cause, "创建账号失败。"));
    } finally {
      setAccountBusy(false);
    }
  };
  const patchAccount = async (record: AccountRecord, patch: AccountPatch) => {
    setAccountBusy(true);
    setAccountError("");
    try {
      replaceAccount(await updateAccount(record.userId, patch));
    } catch (cause) {
      setAccountError(accountErrorText(cause, "更新账号失败。"));
    } finally {
      setAccountBusy(false);
    }
  };
  const submitReset = async (record: AccountRecord) => {
    if (resetPassword.length < 8) {
      setAccountError("新密码至少需要 8 个字符。");
      return;
    }
    setAccountBusy(true);
    setAccountError("");
    try {
      replaceAccount(await resetAccountPassword(record.userId, resetPassword));
      setResetTarget(null);
      setResetPassword("");
      onNotify(`已重置 ${record.username} 的密码，该账号现有会话已失效。`);
    } catch (cause) {
      setAccountError(accountErrorText(cause, "重置密码失败。"));
    } finally {
      setAccountBusy(false);
    }
  };
  const submitOwnPassword = async () => {
    if (!ownPassword.current) {
      setAccountError("请输入当前密码。");
      return;
    }
    if (ownPassword.next.length < 8) {
      setAccountError("新密码至少需要 8 个字符。");
      return;
    }
    if (ownPassword.next !== ownPassword.confirm) {
      setAccountError("两次输入的新密码不一致。");
      return;
    }
    setOwnPasswordBusy(true);
    setAccountError("");
    try {
      await changeOwnPassword(ownPassword.current, ownPassword.next);
      setOwnPassword({ current: "", next: "", confirm: "" });
      onNotify("密码已修改，其他标签页的登录已失效。");
    } catch (cause) {
      setAccountError(accountErrorText(cause, "修改密码失败。"));
    } finally {
      setOwnPasswordBusy(false);
    }
  };
  const runReplay = () => {
    if (!workflowId) return;
    setReplayBusy(true);
    setReplayError("");
    fetchWorkflowReplay(workflowId)
      .then((value) => setReplayView(value))
      .catch((cause: unknown) => {
        setReplayError(
          cause instanceof Error ? cause.message : "无法重放验收记录。",
        );
      })
      .finally(() => setReplayBusy(false));
  };
  const bindingSteps = skillView
    ? skillView.effectiveSteps ??
      skillView.skill.steps.map((step) => ({
        ...step,
        effectiveAcceptance: step.acceptance,
        deviceOverride: false,
        effectiveTarget: step.target ?? null,
        targetSource: step.target ? ("skill" as const) : null,
      }))
    : [];
  const [result, setResult] = useState<WorkspaceImportResult | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const preview = fileText
    ? previewWorkspaceImport(
        fileText,
        // The server deduplicates against the caller's own workspace, not the
        // team view a leader can see.
        mode === "remote"
          ? state.workflows.filter((workflow) => workflow.ownerId === user.id)
          : state.workflows,
      )
    : null;
  const dirty = JSON.stringify(draft) !== JSON.stringify(preferences);
  const patch = <K extends keyof Preferences>(
    key: K,
    value: Preferences[K],
  ) => {
    setDraft((p) => ({ ...p, [key]: value }));
    setError("");
  };
  const save = () => {
    if (!draft.displayName.trim() || !draft.workspaceName.trim()) {
      setError("请填写显示名称和工作空间名称。");
      return;
    }
    if (!validateEndpoint(draft.endpoint)) {
      setError(
        "服务地址需为 HTTP(S) 地址，不能包含用户名、密码、查询参数或片段。",
      );
      return;
    }
    const normalized = {
      ...draft,
      displayName: draft.displayName.trim(),
      workspaceName: draft.workspaceName.trim(),
      model: draft.model.trim(),
      endpoint: draft.endpoint.trim(),
    };
    setDraft(normalized);
    onSave(normalized);
    setError("");
  };
  const chooseFile = async (file?: File) => {
    const readSequence = ++fileReadSequence.current;
    setReading(false);
    setResult(null);
    setError("");
    setFileText("");
    setFileName("");
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".json")) {
      setError(`请选择${PLATFORM_NAME}导出的 JSON 文件。`);
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setError("导入文件不能超过 5 MB。");
      return;
    }
    setReading(true);
    try {
      const text = await file.text();
      if (readSequence !== fileReadSequence.current) return;
      if (!text.trim()) {
        setError("文件为空，请选择有效的工作区 JSON。");
        return;
      }
      setFileText(text);
      setFileName(file.name);
    } catch {
      if (readSequence === fileReadSequence.current)
        setError("无法读取此文件，请重新选择。");
    } finally {
      if (readSequence === fileReadSequence.current) setReading(false);
    }
  };
  const permissionTable = (
    <table className="permissions-table">
      <thead>
        <tr>
          <th>操作</th>
          <th>团队负责人</th>
          <th>工作人员</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>查看参数、结果与导出</td>
          <td>团队全部</td>
          <td>团队全部</td>
        </tr>
        <tr>
          <td>创建、运行、暂停与终止</td>
          <td>全部任务</td>
          <td>本人任务</td>
        </tr>
        <tr>
          <td>异常审批与策略修改</td>
          <td>允许</td>
          <td>由负责人处理</td>
        </tr>
        <tr>
          <td>导入、重置、空间配置</td>
          <td>允许</td>
          <td>只读</td>
        </tr>
        <tr>
          <td>账号管理（新增、停用、重置密码）</td>
          <td>允许</td>
          <td>仅本人密码</td>
        </tr>
      </tbody>
    </table>
  );
  return (
    <>
      <span className="eyebrow">WORKSPACE SETTINGS</span>
      <h2 id="modal-title">{isLeader ? "工作空间管理" : "个人设置与数据"}</h2>
      <p className="modal-description">让工作空间适合你的实验习惯。</p>
      <div className="ws-layout">
        <nav className="ws-tabs" aria-label="设置分类">
          {(
            [
              { id: "personal", label: "外观与个人", icon: UserRound },
              { id: "experiment", label: "实验偏好", icon: SlidersHorizontal },
              { id: "data", label: "数据存储", icon: Database },
              { id: "team", label: "账号与权限", icon: Users },
              { id: "skills", label: "Skill 与审计", icon: ShieldCheck },
              { id: "connection", label: "模型连接", icon: Plug },
            ] as const
          ).filter((item) => isLeader || item.id !== "connection").map((item) => (
            <button
              key={item.id}
              className={tab === item.id ? "active" : ""}
              aria-current={tab === item.id ? "page" : undefined}
              onClick={() => {
                setTab(item.id);
                setError("");
              }}
            >
              <item.icon size={16} />
              {item.label}
            </button>
          ))}
        </nav>
        <div className="ws-content">
          {tab === "personal" && (
            <>
              <h3>界面外观</h3>
              <p className="ws-note">两套配色，同一套实验工作空间。</p>
              <div className="ws-theme-options">
                {(["dark", "light"] as const).map((theme) => (
                  <button
                    key={theme}
                    className={`ws-choice ${draft.theme === theme ? "selected" : ""}`}
                    aria-pressed={draft.theme === theme}
                    onClick={() => patch("theme", theme)}
                  >
                    <span className={`ws-theme-preview ${theme}`}>
                      <i />
                      <i />
                      <i />
                    </span>
                    <span>
                      {theme === "dark" ? (
                        <Moon size={14} />
                      ) : (
                        <Sun size={14} />
                      )}
                      {theme === "dark" ? "深邃深色" : "清透明亮"}
                      {draft.theme === theme && <Check size={14} />}
                    </span>
                  </button>
                ))}
              </div>
              <h3>个人与工作空间</h3>
              <label className="ws-field">
                登录身份
                <input value={`${user.displayName}（${user.username}）`} readOnly />
              </label>
              <label className="ws-field">
                工作空间名称
                <input
                  value={draft.workspaceName}
                  disabled={!isLeader}
                  maxLength={50}
                  onChange={(e) => patch("workspaceName", e.target.value)}
                />
              </label>
              <p className="ws-note">
                登录身份由演示账号确定；工作空间名称由负责人维护。
              </p>
            </>
          )}
          {tab === "experiment" && (
            <>
              <h3>新建实验的默认值</h3>
              <p className="ws-note">
                只影响之后打开的新建表单，已有工作流保持原配置。
              </p>
              <label className="ws-field">
                默认设备
                <select
                  aria-label="默认设备"
                  value={draft.defaultMachine}
                  onChange={(e) => patch("defaultMachine", e.target.value)}
                >
                  <option>M7</option>
                  <option>M3</option>
                  <option>M5</option>
                  <option>M8</option>
                </select>
              </label>
              <label className="ws-field">
                默认审批策略
                <select
                  aria-label="默认审批策略"
                  disabled={!isLeader}
                  value={draft.defaultPolicy}
                  onChange={(e) =>
                    patch(
                      "defaultPolicy",
                      e.target.value as Preferences["defaultPolicy"],
                    )
                  }
                >
                  <option value="threshold">异常时审批</option>
                  <option value="strict">逐步审批</option>
                  <option value="autopilot">自动运行</option>
                </select>
              </label>
              <div className="setting-row">
                <div>
                  <strong>模拟时钟</strong>
                  <p>自动推进运行中的演示任务；关闭后可保留现场。</p>
                </div>
                <button
                  className={`switch ${draft.autoDemo ? "on" : ""}`}
                  role="switch"
                  aria-label="模拟时钟"
                  aria-checked={draft.autoDemo}
                  disabled={mode !== "mock" || !isLeader}
                  onClick={() => patch("autoDemo", !draft.autoDemo)}
                >
                  <i />
                </button>
              </div>
              <div className="ws-banner">
                <ShieldCheck size={17} />
                <span>
                  仅单、双比特流程，依据提供的测试图。所有结果仍为模拟数据。
                </span>
              </div>
            </>
          )}
          {tab === "data" && (
            <>
              <DataStorageCard workflowCount={state.workflows.length} />
              <h3>全部实验明细 CSV</h3>
              <p className="ws-note">一次导出本工作区所有跑过的实验，不受列表筛选影响。包含任务、每个节点、配置参数、模拟结果和事件记录，可用 Excel / WPS 打开。</p>
              <div className="ws-card csv-export-card"><FileSpreadsheet size={24}/><div><strong>{countExecutedWorkflows(state.workflows)} 个已运行实验</strong><p>运行中、已完成、暂停、异常及已终止任务</p></div><button className="primary" onClick={onExportCsv} disabled={!countExecutedWorkflows(state.workflows)}><Download size={15}/> 导出全部实验 CSV</button></div>
              <p className="ws-note">{EXPERIMENT_CSV_HISTORY_NOTE}</p>
              <h3>{isLeader ? "工作区备份与导入" : "工作区备份"}</h3>
              <p className="ws-note">
                JSON
                包含工作流、节点状态和事件。对话附件、个人设置和模型配置不在工作区备份内。
              </p>
              <div className="ws-card">
                <FileJson size={22} />
                <div>
                  <strong>{state.workflows.length} 个工作流</strong>
                  <p>
                    当前来源：{mode === "mock" ? "本地模拟器" : "远程 Gateway"}
                  </p>
                </div>
                <button className="secondary" onClick={onExport}>
                  <Download size={15} />
                  导出 JSON
                </button>
              </div>
              <input
                ref={fileInput}
                type="file"
                accept=".json,application/json"
                aria-label="导入工作区 JSON"
                className="ws-file-input"
                onChange={(e) => {
                  void chooseFile(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
              <button
                className="ws-import-area"
                disabled={!canImport || busy}
                onClick={() => fileInput.current?.click()}
              >
                <Upload size={22} />
                <strong>
                  {reading ? "读取中…可重新选择文件" : "选择工作区 JSON 文件"}
                </strong>
                <span>先校验再合并 · 最大 5 MB · 不覆盖现有任务</span>
              </button>
              {!canImport && (
                <p className="ws-note">{isLeader ? "当前数据接口尚未提供导入能力。" : "工作区导入仅限团队负责人，工作人员可导出备份。"}</p>
              )}
              {fileName && (
                <div className="ws-preview">
                  <strong title={fileName}>{fileName}</strong>
                  {preview?.valid ? (
                    <>
                      <div className="ws-summary">
                        <span>
                          文件内 <b>{preview.total}</b>
                        </span>
                        <span>
                          可导入 <b>{preview.importable}</b>
                        </span>
                        <span>
                          重复跳过 <b>{preview.skipped}</b>
                        </span>
                        <span>
                          转为暂停 <b>{preview.runningToPause}</b>
                        </span>
                      </div>
                      <p className="ws-note">
                        运行中的任务导入后暂停，等待你手动恢复。重复文件可安全再次导入。
                      </p>
                      <button
                        className="primary"
                        disabled={busy || !preview.importable || !canImport}
                        onClick={async () => {
                          setBusy(true);
                          setError("");
                          try {
                            const value = await onImport(fileText);
                            setResult(value);
                            onNotify(
                              `已导入 ${value.imported} 个任务，跳过 ${value.skipped} 个重复任务`,
                            );
                          } catch (cause) {
                            setError(
                              cause instanceof Error
                                ? cause.message
                                : "导入失败",
                            );
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        {busy ? "正在导入…" : "确认合并导入"}
                      </button>
                    </>
                  ) : (
                    <p className="ws-error" role="alert">
                      {preview?.errors.join("；")}
                    </p>
                  )}
                </div>
              )}
              {result && (
                <div className="ws-banner">
                  <Check size={17} />
                  <span>
                    导入完成：新增 {result.imported} 个，跳过 {result.skipped}{" "}
                    个，{result.paused} 个转为暂停。
                  </span>
                </div>
              )}
              <div className="setting-row ws-reset">
                <div>
                  <strong>恢复初始演示</strong>
                  <p>会清除工作流、对话与附件，仅负责人可操作。</p>
                </div>
                <button
                  className="danger-outline"
                  disabled={mode !== "mock" || !isLeader}
                  onClick={onReset}
                >
                  <RotateCcw size={14} />
                  重置
                </button>
              </div>
            </>
          )}
          {tab === "team" && mode === "mock" && <>
            <h3>账号与权限</h3>
            <p className="ws-note">当前登录：{user.username} · {ROLE_LABELS[user.role]}。本地模拟模式的账号来自前端演示目录；连接 QuantaMind 后端后，负责人可在此新增账号、停用账号或重置密码。</p>
            <div className="team-member-list">{DEMO_ACCOUNTS.map((account) => <div className="team-member" key={account.id}><UserRound size={19}/><div><strong>{account.displayName}{account.id === user.id ? " · 当前账号" : ""}</strong><small>{ROLE_LABELS[account.role]}</small></div><code>{account.username}</code></div>)}</div>
            {permissionTable}
            <p className="ws-note">历史示例任务未绑定工作人员，负责人可以操作。工作人员可新建或复制为自己的实验。登录页提供两套演示账号，退出后可切换身份。</p>
          </>}
          {tab === "team" && mode === "remote" && <>
            <h3>账号与权限</h3>
            <p className="ws-note">当前登录：{user.displayName}（{user.username}） · {ROLE_LABELS[user.role]}。账号、角色与授权以 QuantaMind 后端目录为准，界面只展示服务器返回的结果。</p>
            {accountError && <p className="ws-error" role="alert"><AlertCircle size={15} />{accountError}</p>}
            {isLeader ? <>
              <div className="ws-card">
                <Users size={22} />
                <div>
                  <strong>{accounts ? `团队共 ${accounts.length} 个账号` : accountBusy ? "正在读取账号…" : "账号目录尚未读取"}</strong>
                  <p>停用账号或修改角色会立即使该账号的现有登录令牌失效。</p>
                </div>
                <button className="secondary" disabled={accountBusy} onClick={() => setAccountReload((value) => value + 1)}>
                  <RotateCcw size={15} />
                  {accountBusy ? "读取中…" : "刷新"}
                </button>
              </div>
              {accounts && (
                <div className="account-list">
                  {accounts.map((account) => {
                    const role = knownAccountRole(account.role);
                    const self = account.userId === user.id;
                    return (
                      <div className={`account-row ${account.isActive ? "" : "is-disabled"}`} key={account.userId}>
                        <div className="account-identity">
                          <strong>{account.displayName}{self ? " · 当前账号" : ""}</strong>
                          <small>
                            <code>{account.username}</code>
                            <span>{account.roleLabel || account.role}</span>
                            {!account.isActive && <span className="account-flag">已停用</span>}
                          </small>
                        </div>
                        <div className="account-actions">
                          {role ? (
                            <select aria-label={`${account.username} 的角色`} value={role} disabled={accountBusy || self} onChange={(event) => void patchAccount(account, { role: event.target.value as RemoteAccountRole })}>
                              {(Object.keys(ACCOUNT_ROLE_LABELS) as RemoteAccountRole[]).map((key) => <option key={key} value={key}>{ACCOUNT_ROLE_LABELS[key]}</option>)}
                            </select>
                          ) : (
                            <code className="account-unknown-role">{account.role}（不受此工作空间支持）</code>
                          )}
                          <button className="secondary" disabled={accountBusy || self} onClick={() => void patchAccount(account, { isActive: !account.isActive })}>
                            {account.isActive ? "停用" : "启用"}
                          </button>
                          <button className="secondary" disabled={accountBusy || self} onClick={() => { setResetTarget(resetTarget === account.userId ? null : account.userId); setResetPassword(""); setAccountError(""); }}>
                            重置密码
                          </button>
                        </div>
                        {resetTarget === account.userId && (
                          <div className="account-reset">
                            <input type="password" aria-label={`${account.username} 的新密码`} placeholder="新密码，至少 8 个字符" autoComplete="new-password" maxLength={128} value={resetPassword} onChange={(event) => { setResetPassword(event.target.value); setAccountError(""); }} />
                            <button className="primary" disabled={accountBusy} onClick={() => void submitReset(account)}>确认重置</button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              <h3>新增账号</h3>
              <div className="account-create">
                <label className="ws-field">用户名<input value={newAccount.username} maxLength={64} autoComplete="off" placeholder="登录名，创建后不可修改" onChange={(event) => { setNewAccount((value) => ({ ...value, username: event.target.value })); setAccountError(""); }} /></label>
                <label className="ws-field">显示名称<input value={newAccount.displayName} maxLength={128} autoComplete="off" placeholder="团队内展示的名称" onChange={(event) => { setNewAccount((value) => ({ ...value, displayName: event.target.value })); setAccountError(""); }} /></label>
                <label className="ws-field">初始密码<input type="password" value={newAccount.password} maxLength={128} autoComplete="new-password" placeholder="至少 8 个字符" onChange={(event) => { setNewAccount((value) => ({ ...value, password: event.target.value })); setAccountError(""); }} /></label>
                <label className="ws-field">角色<select value={newAccount.role} onChange={(event) => { setNewAccount((value) => ({ ...value, role: event.target.value as RemoteAccountRole })); setAccountError(""); }}>{(Object.keys(ACCOUNT_ROLE_LABELS) as RemoteAccountRole[]).map((key) => <option key={key} value={key}>{ACCOUNT_ROLE_LABELS[key]}</option>)}</select></label>
                <button className="primary" disabled={accountBusy} onClick={() => void addAccount()}><Users size={15} />创建账号</button>
              </div>
              <p className="ws-note">新账号需要至少 8 位密码，创建后只能由负责人调整角色或停用。</p>
            </> : (
              <div className="ws-banner">
                <ShieldCheck size={17} />
                <span>账号列表与授权由团队负责人维护。你可以修改自己的密码；如需新账号或角色调整，请联系负责人。</span>
              </div>
            )}
            <h3>修改我的密码</h3>
            <div className="account-password">
              <label className="ws-field">当前密码<input type="password" value={ownPassword.current} maxLength={128} autoComplete="current-password" onChange={(event) => { setOwnPassword((value) => ({ ...value, current: event.target.value })); setAccountError(""); }} /></label>
              <label className="ws-field">新密码<input type="password" value={ownPassword.next} maxLength={128} autoComplete="new-password" placeholder="至少 8 个字符" onChange={(event) => { setOwnPassword((value) => ({ ...value, next: event.target.value })); setAccountError(""); }} /></label>
              <label className="ws-field">确认新密码<input type="password" value={ownPassword.confirm} maxLength={128} autoComplete="new-password" onChange={(event) => { setOwnPassword((value) => ({ ...value, confirm: event.target.value })); setAccountError(""); }} /></label>
              <button className="primary" disabled={ownPasswordBusy} onClick={() => void submitOwnPassword()}>{ownPasswordBusy ? "正在修改…" : "修改密码"}</button>
            </div>
            <p className="ws-note">修改密码后，此标签页会自动续用新令牌；其他标签页与设备上的登录立即失效。</p>
            <h3>权限说明</h3>
            {permissionTable}
            <p className="ws-note">历史示例任务未绑定工作人员，负责人可以操作。工作人员只能操作自己创建的任务，审批与账号管理由负责人完成。</p>
          </>}
          {tab === "skills" && (
            <>
              <h3>Skill 与审计</h3>
              {mode !== "remote" ? (
                <div className="ws-banner">
                  <ShieldCheck size={17} />
                  <span>
                    本地模拟模式没有服务端审计。设置 VITE_GATEWAY_MODE=remote
                    后，这里显示任务绑定的 Skill 版本、量化验收标准与不可变运行记录。
                  </span>
                </div>
              ) : !workflowId ? (
                <p className="ws-note">请先在蓝图或工作看台选择一个任务。</p>
              ) : (
                <>
                  <p className="ws-note">
                    阈值与决策随测量结果一起冻结；修改 Skill
                    不会改写历史记录。未执行节点不会出现在审计中。
                  </p>
                  <div className="ws-card">
                    <ShieldCheck size={22} />
                    <div>
                      <strong>
                        {skillView
                          ? `${skillView.skill.name} · ${skillView.binding.skillId}@${skillView.binding.version}`
                          : "正在读取 Skill…"}
                      </strong>
                      <p>
                        {skillView
                          ? `自主等级 ${skillView.autonomy.level} · ${skillView.autonomy.name}`
                          : workflowId}
                      </p>
                      {skillView?.device && (
                        <p>
                          {skillView.device.known
                            ? `${skillView.device.name} · 适配器 ${skillView.device.adapter} · 设备档案 v${skillView.device.version}`
                            : `${skillView.device.deviceId} 未登记，使用 Skill 默认阈值`}
                        </p>
                      )}
                    </div>
                    <button
                      className="secondary"
                      disabled={insightBusy}
                      onClick={() => setInsightReload((value) => value + 1)}
                    >
                      <RotateCcw size={15} />
                      {insightBusy ? "读取中…" : "刷新"}
                    </button>
                  </div>
                  {insightError && (
                    <p className="ws-error" role="alert">
                      {insightError}
                    </p>
                  )}
                  {skillView && (
                    <>
                      <h3>节点设计目标与验收标准</h3>
                      <div className="ws-skill-table">
                        <table>
                          <thead>
                            <tr>
                              <th>步骤</th>
                              <th>设计目标（设备生效）</th>
                              <th>验收标准（设备生效）</th>
                              <th>失败处理</th>
                            </tr>
                          </thead>
                          <tbody>
                            {bindingSteps.map((step) => (
                              <tr key={step.nodeId}>
                                <td>
                                  <strong>{step.label}</strong>
                                  <br />
                                  <code>{step.nodeId}</code>
                                </td>
                                <td>
                                  {step.effectiveTarget ? (
                                    <>
                                      <code>
                                        {step.effectiveTarget.metric}{" "}
                                        {formatWireTarget(step.effectiveTarget)}
                                      </code>
                                      {step.targetSource === "device" && (
                                        <span className="ws-device-tag">
                                          设备覆盖
                                        </span>
                                      )}
                                      <br />
                                      {step.effectiveTarget.description}
                                    </>
                                  ) : (
                                    "—"
                                  )}
                                </td>
                                <td>
                                  <code>
                                    {step.effectiveAcceptance.metric}{" "}
                                    {formatWireCriterion(
                                      step.effectiveAcceptance,
                                    )}
                                  </code>
                                  {step.deviceOverride && (
                                    <span className="ws-device-tag">
                                      设备覆盖
                                    </span>
                                  )}
                                  <br />
                                  {step.effectiveAcceptance.description}
                                </td>
                                <td>
                                  {step.onFail === "retry"
                                    ? "自动重试"
                                    : "升级人工"}{" "}
                                  · 最多 {step.maxAttempts} 次
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <p className="ws-note">
                        设计目标是期望达到的理想值，验收标准是触发人工决策的下限；两者互相独立，不能用阈值代替目标。
                      </p>
                    </>
                  )}
                  {auditView && (
                    <>
                      <h3>审计时间线</h3>
                      <p className="ws-note">
                        共 {auditView.counts.results} 条测量、
                        {auditView.counts.actions} 条命令、
                        {auditView.counts.events} 条事件
                        {auditView.counts.escalations
                          ? `、${auditView.counts.escalations} 次升级`
                          : ""}
                        ；显示最近{" "}
                        {Math.min(40, auditView.timeline.length)} 条。
                      </p>
                      <ol className="ws-audit">
                        {auditView.timeline
                          .slice(-40)
                          .map((entry, index) => {
                            const line = describeAuditEntry(entry);
                            return (
                              <li
                                key={`${entry.type}-${entry.time}-${index}`}
                                className={line.warning ? "warning" : ""}
                              >
                                <span className="ws-audit-kind">
                                  {line.label}
                                </span>
                                <span className="ws-audit-time">
                                  {entry.time.slice(11, 19)}
                                </span>
                                <p>{line.detail}</p>
                              </li>
                            );
                          })}
                      </ol>
                    </>
                  )}
                  <h3>重放校验</h3>
                  <p className="ws-note">
                    用当前 Skill 重新判定已记录的测量，只读，不写回任何数据。
                    “标准漂移”表示今天的验收下限会给出不同判定；“目标漂移”表示设计目标已变化，即使判定一致也会列出。
                  </p>
                  <div className="ws-card">
                    <RotateCcw size={22} />
                    <div>
                      <strong>
                        {replayView
                          ? `已重放 ${replayView.summary.total} 条 · 判定漂移 ${replayView.summary.changed} 条 · 目标漂移 ${replayView.summary.targetChanged ?? 0} 条`
                          : "尚未重放"}
                      </strong>
                      <p>
                        {replayView
                          ? `${replayView.skill.skillId}@${replayView.skill.version}`
                          : "读取审计记录后重新计算验收结论"}
                      </p>
                    </div>
                    <button
                      className="secondary"
                      disabled={replayBusy || insightBusy}
                      onClick={runReplay}
                    >
                      <RotateCcw size={15} />
                      {replayBusy ? "计算中…" : "重放"}
                    </button>
                  </div>
                  {replayError && (
                    <p className="ws-error" role="alert">
                      {replayError}
                    </p>
                  )}
                  {replayView && replayView.items.length > 0 && (
                    <ol className="ws-audit">
                      {replayView.items.map((item) => {
                        const line = describeReplayItem(item);
                        return (
                          <li
                            key={`${item.nodeId}-${item.attempt}`}
                            className={line.warning ? "warning" : ""}
                          >
                            <span className="ws-audit-kind">{line.label}</span>
                            <span className="ws-audit-time">
                              #{item.attempt}
                            </span>
                            <p>{line.detail}</p>
                          </li>
                        );
                      })}
                    </ol>
                  )}
                </>
              )}
            </>
          )}
          {tab === "connection" && (
            <>
              <h3>模型连接配置</h3>
              <div className="ws-banner">
                <Plug size={17} />
                <span>配置草稿 · 尚未连接。当前对话由本地演示规则响应。</span>
              </div>
              <label className="ws-field">
                模型提供方
                <select
                  aria-label="模型提供方"
                  disabled={!isLeader}
                  value={draft.provider}
                  onChange={(e) =>
                    patch("provider", e.target.value as Preferences["provider"])
                  }
                >
                  <option value="local">本地模型 / 自部署</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="minimax">MiniMax</option>
                  <option value="openai">OpenAI</option>
                  <option value="custom">其他兼容服务</option>
                </select>
              </label>
              <label className="ws-field">
                模型名称
                <input
                  disabled={!isLeader}
                  placeholder="待后端提供，例如模型部署名称"
                  maxLength={100}
                  value={draft.model}
                  onChange={(e) => patch("model", e.target.value)}
                />
              </label>
              <label className="ws-field">
                服务地址
                <input
                  disabled={!isLeader}
                  placeholder="https://your-service.example/api"
                  maxLength={250}
                  value={draft.endpoint}
                  onChange={(e) => patch("endpoint", e.target.value)}
                />
              </label>
              <p className="ws-note">
                仅保存本地配置草稿，不发起网络请求。API
                密钥由后端管理，无需在此填写。
              </p>
              <div className="ws-card ws-roadmap">
                <div>
                  <strong>后续接入能力</strong>
                  <p>
                    论文理解 → 自然语言编排 → 实验代码 → 执行与分析 →
                    报告。当前可演示固定单双比特校准流程。
                  </p>
                </div>
              </div>
            </>
          )}
          {error && (
            <p className="ws-error" role="alert">
              <AlertCircle size={15} />
              {error}
            </p>
          )}
        </div>
      </div>
      <div className="ws-footer">
        <span>{dirty ? "有尚未保存的设置" : "设置保存在当前浏览器"}</span>
        <button className="primary" disabled={!dirty || busy} onClick={save}>
          <Check size={15} />
          保存设置
        </button>
      </div>
    </>
  );
}
