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

type Tab = "personal" | "experiment" | "data" | "connection" | "team";
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
}
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
}: Props) {
  const isLeader = user.role === "leader";
  const [tab, setTab] = useState<Tab>(initialTab);
  const [draft, setDraft] = useState(preferences);
  const [fileText, setFileText] = useState("");
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const fileReadSequence = useRef(0);
  useEffect(
    () => () => {
      fileReadSequence.current += 1;
    },
    [],
  );
  const [result, setResult] = useState<WorkspaceImportResult | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const preview = fileText
    ? previewWorkspaceImport(fileText, state.workflows)
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
          {tab === "team" && <>
            <h3>账号与权限</h3>
            <p className="ws-note">当前登录：{user.username} · {ROLE_LABELS[user.role]}。演示账号用于体验分工，正式账号和授权由后续服务端接管。</p>
            <div className="team-member-list">{DEMO_ACCOUNTS.map((account) => <div className="team-member" key={account.id}><UserRound size={19}/><div><strong>{account.displayName}{account.id === user.id ? " · 当前账号" : ""}</strong><small>{ROLE_LABELS[account.role]}</small></div><code>{account.username}</code></div>)}</div>
            <table className="permissions-table"><thead><tr><th>操作</th><th>团队负责人</th><th>工作人员</th></tr></thead><tbody><tr><td>查看参数、结果与导出</td><td>团队全部</td><td>团队全部</td></tr><tr><td>创建、运行、暂停与终止</td><td>全部任务</td><td>本人任务</td></tr><tr><td>异常审批与策略修改</td><td>允许</td><td>由负责人处理</td></tr><tr><td>导入、重置、空间配置</td><td>允许</td><td>只读</td></tr></tbody></table>
            <p className="ws-note">历史示例任务未绑定工作人员，负责人可以操作。工作人员可新建或复制为自己的实验。登录页提供两套演示账号，退出后可切换身份。</p>
          </>}
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
