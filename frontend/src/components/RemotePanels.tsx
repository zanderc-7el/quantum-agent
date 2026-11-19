import ReportExportButton from "./ReportExportButton";
import SupplementalQuality from "./SupplementalQuality";
import { decisionBudgetRows } from "../lib/decisionBudget";
import { WorkingPointScope } from "./WorkflowCoverage";
import { abortedNodePresentation } from "../lib/abortedNodePresentation";
import { formatAxisTick } from "../lib/axisTicks";
import { createNodeDetailSync, nodeDetailBoundary } from "../lib/nodeDetailSync";
import type { NodeDetailSync } from "../lib/nodeDetailSync";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { Activity, ArrowRight, Check, ChevronDown, ChevronRight, Download, FileText, GitBranch, History, Layers, Minus, Plus, RotateCcw, Send, Settings2, ShieldCheck, Sparkles, SquarePen } from "lucide-react";
import { adapterIdentity, displayedNodeParameters, pulseStageLabel, staleNodeLabel } from "../lib/workflowResources";
import { remoteChatStore } from "../lib/remoteChatStore";
import NodeParameterEditor from "./NodeParameterEditor";
import RuleCard from "./RuleCard";
import { precedingDependencies, workflowControls } from "../lib/workflowControls";
import { groupHardwareOwners, hardwareCheckExplanation, hardwareOwnerRecordLabel, workflowHardwareSummary } from "../lib/hardwareStatusPresentation";
import type { HardwareOwnerRecord } from "../lib/hardwareStatusPresentation";
import type { AdapterIdentity, Catalog, ExperimentNode, MeasurementResult, NodeDetail, Policy, RemoteAction, Workflow } from "../types";
import type { RemoteGateway } from "../lib/remoteGateway";
import { artifactUrl, reportUrl } from "../lib/remoteGateway";
import "./flow-canvas.css";
import "./node-inspector.css";
import "./chat-report.css";

export const jsonText = (value: unknown): string => typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "—";
const statusLabels: Record<string, string> = { success: "已执行", pending: "待执行", running: "运行中", attention: "待决策", abandoned: "已终止" };
const nodeStateLabel = (node: ExperimentNode, workflow: Workflow) => abortedNodePresentation(workflow, node)?.label ?? (node.engineState === "historical" ? "历史已载入" : workflow.engineState === "replay_completed" && node.attempt === 0 ? "无历史记录" : node.engineState === "stale" ? staleNodeLabel(node) : statusLabels[node.status]);
const qualityLabels = { passed: "质量检查通过", failed: "质量检查未通过", unverified: "尚未验证" };
export function DecisionBudget({ value }: { value: unknown }) {
  const rows = decisionBudgetRows(value);
  if (!rows) return null;
  return <section className="rp-info" aria-label="控制器预算核算"><strong>控制器预算核算（shots）</strong><p>采集前预检 · 未预留</p>{rows.map(row => <p key={row.label}>{row.label}：<strong>{row.value}</strong></p>)}</section>;
}
export function StageSummary({ catalog, workflow }: { catalog?: Catalog; workflow: Workflow }) {
  if (!catalog?.stages?.length) return null;
  const procedures = (catalog.capabilities as { procedures?: Record<string, { available?: boolean; ownerId?: string }> })?.procedures ?? {};
  const available = Object.values(procedures).filter(procedure => procedure.available);
  const doubleCount = available.filter(procedure => procedure.ownerId?.startsWith("d")).length;
  return <section className="rp-stage-summary" aria-label="测量阶段与可用能力"><div className="rp-stage-grid">{catalog.stages.map((stage, index) => <div key={stage.id} className={`rp-stage ${stage.capability === "unavailable" ? "unavailable" : ""}`}><span className="rp-stage-number">{String(index + 1).padStart(2, "0")}</span><div><strong>{stage.id === "pulse" ? pulseStageLabel(catalog, workflow) : stage.label}</strong><p>{stage.capability === "unavailable" ? stage.reason ?? "当前尚未接入" : stage.requiresHardwareConfirmation ? workflow.engineState === "waiting_hardware" ? "等待接线与设备独占确认" : workflow.mode === "replay" ? "历史回放无需连接设备" : "由本次任务确认硬件交接" : workflow.mode === "replay" ? "原始测量代码见节点 · 正在查看已有实测证据" : "通过登记的执行器调度"}</p></div>{stage.capability === "unavailable" && <small>未接入</small>}</div>)}</div><div className="rp-capability-summary"><span>当前登记 {available.length} 项可执行方法</span><span>{doubleCount ? `其中 ${doubleCount} 项属于双比特流程` : "双比特执行方法尚未接入"}</span><span>节点是否验证以本轮结果为准</span></div></section>;
}
export function HardwareStatus({ hardware, adapter, workflow, compact = false, action }: { hardware?: unknown; adapter?: AdapterIdentity; workflow?: Workflow; compact?: boolean; action?: ReactNode }) {
  const state = hardware && typeof hardware === "object" ? hardware as { adapter?: AdapterIdentity; enabled?: boolean; ready?: boolean; reasons?: string[]; ownership?: { verified?: boolean; owners?: HardwareOwnerRecord[]; endpoint?: unknown; reason?: string } } : undefined;
  const ownership = state?.ownership;
  const identity = adapter ?? state?.adapter;
  const owners = ownership?.owners ?? [];
  const groups = groupHardwareOwners(owners);
  const taskSummary = workflowHardwareSummary(workflow);
  const checkExplanation = hardwareCheckExplanation(ownership?.reason);
  return <div className={`rp-hardware-status rp-info${compact ? " compact" : ""}`}>
    <strong><Activity size={14} />{owners.length ? "当前设备连接／占用" : ownership?.verified ? "设备连接占用检查已通过" : "真实测量连接状态"}</strong>
    {identity && <p>测量执行器：{identity.name ?? identity.id}</p>}
    {state?.ready === false && <p>执行器预检未通过{state.reasons?.length ? `：${state.reasons.join("；")}` : "，请查看运行环境详情"}</p>}
    {groups.map(group => <p key={group.key}><strong>PID {group.pid ?? "未知"}</strong>：{group.records.map((record, index) => <span key={index} title={record.path}>{index ? "；" : ""}{hardwareOwnerRecordLabel(record, index)}</span>)}。</p>)}
    {!!owners.length && <p>相同 PID 的记录已合并显示；网络连接和共享锁分别列出，进程与本任务的归属尚未核验。</p>}
    {taskSummary && <p>{taskSummary}</p>}
    {checkExplanation && <p>{checkExplanation}</p>}
    {!compact && ownership?.endpoint != null && <p>目标：{jsonText(ownership.endpoint)}</p>}
    {state?.enabled === false && <p>默认直接连接未启用；已确认交接的任务仍可由控制器单独授权采集。此项不代表仪器电源关闭或当前没有连接。</p>}
    {!state && <p>正在读取后端执行器与连接占用状态。</p>}
    {action && <div className="rp-hardware-action">{action}</div>}
  </div>;
}
export function MeasurementProgress({ node, workflow, compact = false }: { node: ExperimentNode; workflow?: Workflow; compact?: boolean }) {
  const archived = workflow?.status === "aborted";
  const data = node.telemetry;
  if (!data || !Object.keys(data).length) return null;
  const number = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : undefined;
  const job = number(data.jobIndex), jobs = number(data.jobCount);
  const rows = number(data.completedRows), total = number(data.totalRows);
  const row = number(data.rowNumber);
  const current = data.currentAxes && typeof data.currentAxes === "object" ? data.currentAxes as { capture?: unknown; axes?: unknown } : undefined;
  const axes = current?.axes && typeof current.axes === "object" ? current.axes as Record<string, unknown> : {};
  const axisValue = (suffix: string) => {
    const values = Object.entries(axes).filter(([key]) => key.endsWith(suffix)).map(([, value]) => number(value));
    return values.length === 1 ? values[0] : undefined;
  };
  const capture = number(current?.capture), scale = axisValue(".para.probCarryScale");
  const frequencies = [{ label: "读取频率", value: number(data.prob_hz) ?? axisValue(".para.probFreq") }, { label: "驱动频率", value: number(data.drive_hz) ?? axisValue(".para.drivFreq") }].filter(item => item.value !== undefined);
  return <section className={`rp-telemetry${compact ? " compact" : ""}`} aria-label={archived ? "最后采集记录" : "当前实际采集进度"}>
    <header><span><RadioProgress />{archived ? "最后采集记录" : "采集进度"}</span><small>第 {node.attempt} 次执行 · 设备任务回执</small></header>
    <div className="rp-telemetry-values">
      {job !== undefined && <span>任务 <strong>{job}{jobs !== undefined ? ` / ${jobs}` : ""}</strong></span>}
      {rows !== undefined && <span>扫描点 <strong>{rows}{total !== undefined ? ` / ${total}` : ""}</strong></span>}
      {rows === undefined && row !== undefined && <span>当前行 <strong>{row}</strong></span>}
      {capture !== undefined && <span>采集轮次 <strong>{capture + 1}</strong></span>}
      {scale !== undefined && <span>读出幅度 <strong>{scale} <small>a.u.</small></strong></span>}
      {frequencies.map(item => <span key={item.label}>{item.label} <strong>{(item.value! / 1e9).toFixed(9)} <small>GHz</small></strong></span>)}
    </div>
    {data.message && <p>{data.message}</p>}
    <details><summary>原始采集遥测 <ChevronDown size={12} /></summary><pre>{jsonText(data)}</pre></details>
  </section>;
}
function RadioProgress() { return <Activity size={13} />; }
export function EmptyState({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <div className="rp-empty"><Activity size={30} /><h3>{title}</h3><p>{text}</p>{action}</div>;
}

export { default as RealFlowCanvas } from "./RealFlowCanvas";

function SeriesChart({ series }: { series: NonNullable<MeasurementResult["series"]>[number] }) {
  const points = series.x.map((x, index) => ({ x, y: series.y[index] })).filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
  if (!points.length) return null;
  const xs = points.map(point => point.x), ys = points.map(point => point.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const sx = (x: number) => 58 + (x - minX) / (maxX - minX || 1) * 520;
  const sy = (y: number) => 230 - (y - minY) / (maxY - minY || 1) * 190;
  return <figure className="rp-series"><figcaption>{series.name}</figcaption><svg viewBox="0 0 620 280" role="img" aria-label={`${series.name}，${series.fit === true ? `拟合曲线，${points.length} 个绘制坐标` : series.fit === false ? `${points.length} 个实测数据点` : `${points.length} 个曲线坐标`}`}><line x1="58" y1="40" x2="58" y2="230" /><line x1="58" y1="230" x2="578" y2="230" />{[0, 0.5, 1].map(f => <g key={f}><line className="grid" x1="58" x2="578" y1={230 - f * 190} y2={230 - f * 190} /><text x="50" y={234 - f * 190} textAnchor="end">{formatAxisTick(minY + f * (maxY - minY), maxY - minY)}</text><text x={58 + f * 520} y="249" textAnchor="middle">{formatAxisTick(minX + f * (maxX - minX), maxX - minX)}</text></g>)}<polyline strokeDasharray={series.fit === true ? "6 4" : undefined} points={points.map(point => `${sx(point.x)},${sy(point.y)}`).join(" ")} />{series.fit !== true && points.length < 150 && points.map((point, index) => <circle key={index} cx={sx(point.x)} cy={sy(point.y)} r="2.1" />)}<text x="318" y="272" textAnchor="middle">{series.xUnit || "扫描坐标"}</text><text x="58" y="23">{series.yUnit || "测量值"}</text></svg></figure>;
}

export function RealResult({ result, compact = false }: { result?: MeasurementResult; compact?: boolean }) {
  if (result?.selectionResult) return <div className="rp-acquisitions">
    <section aria-label="首次采集结果"><h4>首次采集</h4><MeasurementResultPanel result={result.selectionResult} compact={compact} /></section>
    <section aria-label="独立复测结果"><h4>独立复测</h4><MeasurementResultPanel result={result} compact={compact} /></section>
  </div>;
  return <MeasurementResultPanel result={result} compact={compact} />;
}

function MeasurementResultPanel({ result, compact = false }: { result?: MeasurementResult; compact?: boolean }) {
  if (!result) return <EmptyState title="该节点尚无测量结果" text="采集或历史证据载入后，将显示原始曲线、分析指标和数据来源。" />;
  const metrics = Object.entries(result.metrics ?? {}).filter(([, value]) => value !== null);
  const images = (result.artifacts ?? []).filter(item => item.kind === "image");
  const others = (result.artifacts ?? []).filter(item => item.kind !== "image");
  return <div className={`rp-result ${compact ? "compact" : ""}`}><SupplementalQuality receipt={result.supplementalQuality} /><div className="rp-result-meta"><span className={`rp-quality ${result.quality?.status ?? "unverified"}`}>{qualityLabels[result.quality?.status ?? "unverified"]}</span><span>{result.dataKind === "historical" ? "历史实测数据" : "真实采集记录"}</span>{result.methodLabel && <span className="rp-method-label">当前方法：{result.methodLabel}</span>}{result.acquiredAt && <time>{new Date(result.acquiredAt).toLocaleString("zh-CN")}</time>}</div>{!!result.quality?.reasons?.length && <ul className="rp-quality-reasons">{result.quality.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}{!!metrics.length && <div className="rp-real-metrics">{metrics.map(([name, value]) => <div key={name}><span>{name}</span><strong>{typeof value === "number" ? Number(value.toPrecision(7)) : value}<small>{result.units?.[name] ?? ""}</small></strong></div>)}</div>}{!!images.length && <div className="rp-images">{images.map(image => <figure key={image.id}><a href={artifactUrl(image.id)} target="_blank" rel="noreferrer"><img src={artifactUrl(image.id)} loading="lazy" alt={image.name} onError={event => { event.currentTarget.classList.add("rp-image-failed"); }} /></a><figcaption><span>{image.name}</span><a href={artifactUrl(image.id)} download={image.name}><Download size={14} />原图</a></figcaption></figure>)}</div>}{result.series?.map((series, index) => <SeriesChart key={`${series.name}-${index}`} series={series} />)}{!images.length && !result.series?.length && <p className="rp-muted">本条记录未登记图像或数值曲线，可查看下方原始产物。</p>}{!!others.length && <div className="rp-artifact-links">{others.map(artifact => <a key={artifact.id} href={artifactUrl(artifact.id)} download={artifact.name}><FileText size={15} />{artifact.name}<Download size={13} /></a>)}</div>}<details className="rp-provenance"><summary>数据来源与方法 <ChevronDown size={13} /></summary><pre>{jsonText({ sourceRunId: result.sourceRunId, acquiredAt: result.acquiredAt, method: result.methodLabel, parameters: result.parameters, provenance: result.provenance, artifacts: result.artifacts?.map(item => ({ id: item.id, name: item.name, sha256: item.sha256, sourcePath: item.sourcePath })) })}</pre></details></div>;
}

export function RealNodeInspector({ gateway, workflow, node, onAction, onPolicy, busy, hardware, catalog, onBusy }: { gateway: RemoteGateway; workflow: Workflow; node: ExperimentNode; onAction: (action: RemoteAction, targetNodeId?: string) => Promise<void>; onPolicy: (policy: Policy) => Promise<void>; busy: boolean; hardware?: unknown; catalog?: Catalog; onBusy: (value: boolean) => void }) {
  const detailKey = JSON.stringify([workflow.id, node.id]);
  const detailBoundary = nodeDetailBoundary(workflow, node);
  const [detailSnapshot, setDetailSnapshot] = useState<{ key: string; value: NodeDetail }>();
  const [errorSnapshot, setErrorSnapshot] = useState<{ key: string; message: string }>();
  const detail = detailSnapshot?.key === detailKey ? detailSnapshot.value : undefined;
  const error = errorSnapshot?.key === detailKey ? errorSnapshot.message : "";
  const detailSync = useRef<NodeDetailSync | undefined>(undefined);
  const [tab, setTab] = useState("parameters");
  const [target, setTarget] = useState("");
  useEffect(() => {
    setErrorSnapshot(undefined); setDetailSnapshot(undefined);
    const sync = createNodeDetailSync({ read: () => gateway.node(workflow.id, node.id),
      onValue: value => { setDetailSnapshot({ key: detailKey, value }); setErrorSnapshot(undefined); },
      onError: message => setErrorSnapshot({ key: detailKey, message }) });
    detailSync.current = sync;
    return () => { sync.dispose(); if (detailSync.current === sync) detailSync.current = undefined; };
  }, [gateway, workflow.id, node.id, detailKey]);
  useEffect(() => {
    detailSync.current?.observe(workflow.revision, detailBoundary);
  }, [gateway, detailKey, workflow.revision, detailBoundary]);
  useEffect(() => setTarget(""), [node.id]);
  const controls = workflowControls(workflow);
  const { terminal, current, hardware: waitingHardware, reconciliation: needsReconciliation } = controls;
  const backtrackTargets = precedingDependencies(workflow, current);
  const rules = detail?.rules ?? node.rules ?? [];
  const presentation = abortedNodePresentation(workflow, node);
  return <aside className="ni-inspector rp-inspector"><header className="ni-heading"><div><Layers size={15} />节点详情</div><span className="rp-mode">{workflow.mode === "replay" ? "历史回放" : "真实测量"}</span></header><div className="ni-node-intro"><div className="ni-node-kicker"><span>{node.procedureId ?? node.name}</span><span>{nodeStateLabel(node, workflow)}</span></div><h2>{node.label}</h2><p>{presentation?.detail ?? node.detail}</p><div className="rp-node-state"><span>{presentation?.engineState ?? node.engineState ?? node.status}</span><span>第 {node.attempt} 轮</span></div></div><div className="rp-inspector-tabs">{[{ id: "parameters", label: "参数", icon: Settings2 }, { id: "attempts", label: "执行历史", icon: History }, { id: "rules", label: "回退规则", icon: GitBranch }].map(({ id, label, icon: Icon }) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}><Icon size={13} />{label}</button>)}</div><div className="rp-inspector-content"><MeasurementProgress node={node} workflow={workflow} compact />{error && <p className="rp-error" role="alert">{error}</p>}{tab === "parameters" && <><div className="rp-section-kicker">{workflow.mode === "replay" ? "历史实测参数" : "当前配置参数"}</div><pre>{jsonText(displayedNodeParameters(workflow, node, detail))}</pre>{workflow.mode === "real" && <NodeParameterEditor gateway={gateway} workflow={workflow} node={node} bounds={detail ? detail.parameterBounds ?? catalog?.parameterBounds : undefined} parameters={detail?.parameters} canEdit={controls.edit} busy={busy} onBusy={onBusy} />}<div className="rp-section-kicker">参数版本与依赖</div><pre>{jsonText({ parameterVersions: node.parameterVersions, dependsOn: node.dependsOn, latestAttemptId: node.latestAttemptId })}</pre><div className="rp-info">当前配置用于生成扫描；实际参数与原始数据保存在各次采集记录中。</div></>}{tab === "attempts" && <>{!detail ? <p className="rp-muted">正在读取执行历史…</p> : !detail.attempts.length ? <p className="rp-muted">此节点尚未执行。</p> : [...detail.attempts].reverse().map(attempt => <details className="rp-attempt" key={attempt.id}><summary><span>第 {attempt.number} 轮 · {attempt.state}</span><ChevronDown size={13} /></summary><p>{new Date(attempt.startedAt).toLocaleString("zh-CN")}</p>{attempt.error != null && <div className="rp-error">{jsonText(attempt.error)}</div>}<pre>{jsonText({ id: attempt.id, operationId: attempt.operationId, mode: attempt.mode, parameters: attempt.parameters, parameterVersions: attempt.parameterVersions })}</pre><RealResult result={attempt.result} compact /></details>)}{!!detail?.decisions?.length && <><div className="rp-section-kicker">决策与回退记录</div>{[...detail.decisions].reverse().map(decision => <article className="rp-decision" key={decision.id}><div><span>{decision.source === "model" ? "智能体" : decision.source === "rule" ? "规则" : "操作员"}</span><strong>{decision.action}</strong></div><p>{decision.source === "model" && <strong>模型判断：</strong>}{decision.reason}</p><DecisionBudget value={decision.budgetLookahead} />{decision.targetNodeId && <p>回退目标：{workflow.nodes.find(item => item.id === decision.targetNodeId)?.label ?? decision.targetNodeId}</p>}{!!decision.invalidatedNodes?.length && <p>待复测：{decision.invalidatedNodes.join("、")}</p>}{decision.parameterChanges && <pre>{jsonText(decision.parameterChanges)}</pre>}<small>依据：{decision.evidenceIds?.join("、") || "未附证据 ID"}</small></article>)}</>}</>}{tab === "rules" && <>{rules.length ? rules.map(rule => <RuleCard key={rule.id} rule={rule} />) : <p className="rp-muted">该节点尚未配置独立回退规则。实际可执行动作由后端校验。</p>}<div className="rp-section-kicker">前置依赖</div><p className="rp-muted">{node.dependsOn?.map(id => workflow.nodes.find(item => item.id === id)?.label ?? id).join(" → ") || "无前置节点"}</p></>}</div><footer className="ni-controls">
      <div className="ni-policy-heading"><ShieldCheck size={13} /><span>流程决策策略</span></div>
      <select aria-label="流程决策策略" value={workflow.policy} disabled={busy || !controls.policy} onChange={event => void onPolicy(event.target.value as Policy)}><option value="strict">每步确认</option><option value="threshold">异常时决策</option><option value="autopilot">自动执行与有界重试</option></select>
      <p className="rp-muted">操作对象：{terminal ? "任务已结束" : current?.label ?? "当前流程"}</p>
      {workflow.status === "running" && <p className="rp-muted">先暂停至检查点，再调整策略或参数。</p>}
      {workflow.status === "failed" && <p className="rp-muted">任务已停止，保留全部结果；重新测量请创建新任务。</p>}
      {waitingHardware && <HardwareStatus hardware={hardware} adapter={adapterIdentity(catalog, workflow)} workflow={workflow} compact />}
      {needsReconciliation && <button className="primary" disabled={busy} onClick={() => void onAction("reconcile")}><History size={14} />核查回执（不重发）</button>}
      {waitingHardware && <button className="primary" disabled={busy} onClick={() => void onAction("confirm_hardware")}><Check size={14} />重新检查并继续</button>}
      {controls.recheck && <button className="secondary" disabled={busy} onClick={() => void onAction("resume")}><RotateCcw size={13} />重新检查流程</button>}
      {controls.decide && <button className="primary" disabled={busy} onClick={() => void onAction("decide")}><Sparkles size={13} />重新请求智能体决策</button>}
      {(controls.approve || controls.retry) && <div className="rp-action-row">{controls.approve && <button className="primary" disabled={busy} onClick={() => void onAction("approve")}><Check size={13} />确认通过并继续</button>}{controls.retry && <button className="secondary" disabled={busy} onClick={() => void onAction("retry")}><RotateCcw size={13} />重试本步</button>}</div>}
      {controls.backtrack && backtrackTargets.length > 0 && <div className="rp-backtrack"><select aria-label="回退目标节点" value={target} onChange={event => setTarget(event.target.value)}><option value="">选择回退目标…</option>{backtrackTargets.map(item => <option value={item.id} key={item.id}>{item.label}</option>)}</select><button className="secondary" disabled={!target || busy || !backtrackTargets.some(item => item.id === target)} onClick={() => void onAction("backtrack", target)}><GitBranch size={13} />回退并复测</button></div>}
      <small className="rp-muted">确认仅用于已通过质量检查的结果；回退保留所有历史尝试。</small>
    </footer></aside>;
}

interface RealChatProps { gateway: RemoteGateway; workflow?: Workflow; projectId?: string; onOpenWorkflow: () => void; }
export function RealChatReport(props: RealChatProps) {
  if (!props.projectId) return <EmptyState title="正在读取本地工作空间" text="确认当前项目后恢复对应任务的对话。" />;
  return <PersistedRealChatReport {...props} projectId={props.projectId} />;
}
function PersistedRealChatReport({ gateway, workflow, projectId, onOpenWorkflow }: RealChatProps & { projectId: string }) {
  const scope = useMemo(() => ({ projectId, workflowId: workflow?.id ?? "general" }), [projectId, workflow?.id]);
  const binding = useMemo(() => ({ subscribe: (listener: () => void) => remoteChatStore.subscribe(scope, listener), snapshot: () => remoteChatStore.get(scope) }), [scope]);
  const conversation = useSyncExternalStore(binding.subscribe, binding.snapshot, binding.snapshot);
  const messages = conversation.messages;
  const pending = conversation.pending?.state === "running";
  const unresolved = conversation.pending?.state === "unresolved";
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const scroll = useRef<HTMLDivElement>(null);
  useEffect(() => { setError(""); setInput(""); }, [scope]);

  useEffect(() => { scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: "smooth" }); }, [messages, pending]);
  const send = async () => {
    const text = input.trim(); if (!text || pending || unresolved) return;
    setInput(""); setError("");
    try { await remoteChatStore.send(scope, text, (message, sessionId) => gateway.chat(message, workflow?.id, sessionId)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  };
  const reportKey = workflow ? JSON.stringify([workflow.id, workflow.status, ...workflow.nodes.map(node => [node.id, node.latestAttemptId, node.engineState, node.result?.quality?.status, node.result?.artifacts?.map(item => item.id)])]) : "";
  return <main className="rp-chat-page cr-workspace page-enter"><div className="cr-page-heading"><div><div className="cr-breadcrumb">AGENT WORKSPACE <ChevronRight size={11} /> LIVE EVIDENCE</div><h1>对话式 Agent <span>{workflow?.target ?? "选择任务后可讨论具体实验"}</span></h1></div>{workflow && <button className="cr-button" onClick={onOpenWorkflow}><GitBranch size={15} />查看蓝图<ArrowRight size={13} /></button>}</div><div className="cr-columns"><section className="cr-chat-panel"><header className="rp-chat-header"><div><Sparkles size={17} /><strong>测控智能体</strong></div><span>基于实际状态与证据</span><button aria-label="任务对话新对话" title="新对话，保留已有记录" onClick={() => { try { remoteChatStore.newConversation(scope); setInput(""); setError(""); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); } }}><SquarePen size={17} /></button></header><div className="rp-messages" ref={scroll}>{!messages.length && <div className="rp-chat-welcome"><div className="rp-agent-mark"><Sparkles size={27} /></div><h2>从一个问题，推进下一步实验</h2><p>我会结合当前节点、测量证据与经验规则回答，并把实际决策留在工作流中。</p><div>{["总结当前任务的实测结果", "当前步骤为什么停在这里？", "下一步应该调整哪些参数？", "有哪些回退规则和证据？"].map(prompt => <button key={prompt} onClick={() => setInput(prompt)}>{prompt}<ArrowRight size={13} /></button>)}</div></div>}{messages.map(message => <article className={`rp-chat-message ${message.role}`} key={message.id}><div className="rp-message-avatar">{message.role === "assistant" ? <Sparkles size={15} /> : message.role === "notice" ? <Activity size={15} /> : "你"}</div><div><header><strong>{message.role === "assistant" ? "测控智能体" : message.role === "notice" ? "请求状态" : "你"}</strong>{message.status && <span className={message.status === "waiting_model" ? "rp-waiting" : ""}>{message.status === "waiting_model" ? "模型请求未完成" : message.status === "unresolved" ? "结果待核查" : message.model ?? message.status}</span>}</header><p>{message.text}</p></div></article>)}{pending && <p className="rp-chat-thinking"><span />正在读取上下文与调用智能体…</p>}</div><div className="rp-composer">{conversation.storageError && <p className="rp-error" role="status">{conversation.storageError}</p>}{conversation.omittedMessages > 0 && <p className="rp-muted">本地展示缓存已省略 {conversation.omittedMessages} 条较早消息。</p>}{unresolved && <div className="rp-info">先前请求结果待核查，未自动重发。<button className="secondary" onClick={() => remoteChatStore.allowNewMessage(scope)}>保留待核查记录，允许发送新消息</button></div>}{error && <p className="rp-error" role="alert">{error}</p>}<textarea aria-label="发送给智能体的消息" value={input} onChange={event => setInput(event.target.value)} placeholder="描述目标、询问当前结果，或讨论下一轮参数…" onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} /><div><small>Enter 发送 · Shift + Enter 换行</small><button className="primary" disabled={pending || unresolved || !input.trim()} onClick={() => void send()}><Send size={15} />发送</button></div></div></section><section className="cr-report-panel"><header className="rp-chat-header"><div><FileText size={16} /><strong>任务实测报告</strong></div>{workflow && <a href={reportUrl(workflow.id)} target="_blank" rel="noreferrer" className="rp-report-export"><Download size={14} />打开图文报告</a>}</header>{workflow && <div className="report-export-bar"><ReportExportButton key={workflow.id} gateway={gateway} workflow={workflow} /></div>}<div className="rp-report-scroll">{!workflow ? <EmptyState title="尚未选择工作流" text="先创建或选择任务，报告将随实际结果更新。" /> : <iframe key={reportKey} className="human-report-preview" title={`${workflow.target} 图文实测报告`} src={reportUrl(workflow.id)} />}</div></section></div></main>;
}
