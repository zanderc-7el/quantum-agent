import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUp,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Code2,
  Database,
  FileText,
  FlaskConical,
  GitBranch,
  Maximize2,
  LockKeyhole,
  MessageSquare,
  MoreHorizontal,
  Paperclip,
  RefreshCw,
  Sparkles,
  Table2,
  WandSparkles,
  X,
} from "lucide-react";
import type { CreateWorkflowInput, ExperimentNode, Workflow } from "../types";
import {
  decaySamples,
  DEMO_THRESHOLDS,
  measurementStage,
  measurementStageLabel,
  qubitMeasurements,
  reportMeasurements,
} from "../lib/measurements";
import {
  buildNodeComparisons,
  formatMetricValue,
  THRESHOLD_SOURCE_LABELS,
} from "../lib/metricComparison";
import type { MetricComparison } from "../lib/metricComparison";
import {
  ATTACHMENT_ACCEPT,
  ATTACHMENT_MAX_COUNT,
  formatAttachmentSize,
  prepareLocalAttachments,
  WorkflowLocalStore,
} from "../lib/attachments";
import type { LocalAttachment } from "../lib/attachments";
import {
  chatStorageError, clearChatStorage, loadChatSnapshot, mergeHydratedSnapshot, saveChatSnapshot,
} from "../lib/chatPersistence";
import type { ChatReplyKind, StoredChatMessage } from "../lib/chatPersistence";
import { PLATFORM_NAME } from "../lib/branding";
import { allowsBlueprintIntent, BLUEPRINTS } from "../lib/blueprints";
import "./chat-report.css";

interface ChatReportProps {
  workflow: Workflow;
  onOpenWorkflow: () => void;
  onCreateWorkflow: (input: CreateWorkflowInput) => void;
  onNotify: (message: string) => void;
  /** Shared team conversations remain readable but may only be edited by their owner or a leader. */
  canEditConversation?: boolean;
}

type ReplyKind = ChatReplyKind;
type ChatMessage = StoredChatMessage;
interface LocalConversation {
  workflowId: string;
  generation: string;
  input: string;
  messages: ChatMessage[];
  attachments: LocalAttachment[];
  attachmentErrors: string[];
  readingAttachments: boolean;
  storageStatus: 'loading' | 'saving' | 'saved' | 'error';
  storageError?: string;
}
const localConversations = new WorkflowLocalStore<LocalConversation>();
const conversationListeners = new Set<(id: string) => void>();
const clearConversationListeners = new Set<() => void>();
interface ConversationRuntime { revision: number; savedRevision: number; dirtyInput?: boolean; loaded: boolean; epoch?: number; loading?: Promise<void>; saving?: Promise<void>; timer?: ReturnType<typeof setTimeout>; }
const conversationRuntime = new Map<string, ConversationRuntime>();
let messageSequence = Date.now();
const notifyConversation = (id: string) => conversationListeners.forEach((listener) => listener(id));
function setStorageStatus(id: string, storageStatus: LocalConversation['storageStatus'], storageError?: string) {
  const current = localConversations.get(id);
  if (!current) return;
  localConversations.set(id, { ...current, storageStatus, storageError });
  notifyConversation(id);
}
function runtimeFor(id: string): ConversationRuntime {
  let runtime = conversationRuntime.get(id);
  if (!runtime) { runtime = { revision: 0, savedRevision: 0, loaded: false }; conversationRuntime.set(id, runtime); }
  return runtime;
}
async function persistConversation(id: string): Promise<void> {
  const runtime = runtimeFor(id);
  if (!runtime.loaded || runtime.epoch === undefined) return;
  if (runtime.timer) { clearTimeout(runtime.timer); runtime.timer = undefined; }
  if (runtime.saving) return runtime.saving;
  // Reading a shared conversation (including the default preview) must never create a durable write.
  // Revisions are advanced only by an authorized edit, and remain pending across role/view changes.
  if (runtime.revision <= runtime.savedRevision) return;
  const snapshot = localConversations.get(id);
  if (!snapshot) return;
  const revision = runtime.revision;
  setStorageStatus(id, 'saving');
  const operation = saveChatSnapshot(snapshot, runtime.epoch).then(() => {
    if (conversationRuntime.get(id) !== runtime) return;
    runtime.savedRevision = revision;
    if (revision === runtime.revision) runtime.dirtyInput = false;
    setStorageStatus(id, revision === runtime.revision ? 'saved' : 'saving');
  }).catch((error) => {
    if (conversationRuntime.get(id) !== runtime) return;
    setStorageStatus(id, 'error', chatStorageError(error));
  }).finally(() => {
    runtime.saving = undefined;
    if (conversationRuntime.get(id) === runtime && revision !== runtime.revision) void persistConversation(id);
  });
  runtime.saving = operation;
  return operation;
}
function queueConversationSave(id: string) {
  const runtime = runtimeFor(id);
  runtime.revision += 1;
  if (!runtime.loaded) return;
  setStorageStatus(id, 'saving');
  if (runtime.timer) clearTimeout(runtime.timer);
  runtime.timer = setTimeout(() => { runtime.timer = undefined; void persistConversation(id); }, 180);
}
async function hydrateConversation(id: string): Promise<void> {
  const runtime = runtimeFor(id);
  if (runtime.loaded) return;
  if (runtime.loading) return runtime.loading;
  const baseline = localConversations.get(id);
  if (!baseline) return;
  const revision = runtime.revision;
  setStorageStatus(id, 'loading');
  runtime.loading = loadChatSnapshot(id).then(({ snapshot, epoch }) => {
    if (conversationRuntime.get(id) !== runtime) return;
    const current = localConversations.get(id);
    if (!current) return;
    runtime.loaded = true;
    runtime.epoch = epoch;
    // Editing is allowed during hydration; an async read must not replace that newer draft.
    const replace = snapshot && current.generation === baseline.generation;
    const merged: LocalConversation = {
      ...current,
      ...(replace ? mergeHydratedSnapshot(snapshot, current, !!runtime.dirtyInput) : {}),
      storageStatus: 'saved', storageError: undefined,
    };
    merged.messages.forEach((message) => { messageSequence = Math.max(messageSequence, message.id); });
    localConversations.set(id, merged);
    notifyConversation(id);
    if (runtime.revision !== revision || runtime.dirtyInput) void persistConversation(id);
  }).catch((error) => {
    if (conversationRuntime.get(id) !== runtime) return;
    setStorageStatus(id, 'error', chatStorageError(error));
  }).finally(() => { runtime.loading = undefined; });
  return runtime.loading;
}

/** Clears durable records and files; callers must await failure-sensitive reset confirmation. */
export async function clearLocalChatSessions(): Promise<void> {
  conversationRuntime.forEach((runtime) => { if (runtime.timer) clearTimeout(runtime.timer); });
  conversationRuntime.clear();
  localConversations.clear();
  const operation = clearChatStorage(); // invalidates pending IndexedDB writes synchronously
  clearConversationListeners.forEach((listener) => listener());
  try { await operation; }
  catch (error) {
    conversationRuntime.forEach((_runtime, id) => setStorageStatus(id, 'error', `清空失败：${chatStorageError(error)}`));
    throw error;
  }
}
export const clearLocalConversations = clearLocalChatSessions;

function getLocalConversation(workflow: Workflow): LocalConversation {
  const saved = localConversations.get(workflow.id);
  if (saved) return saved;
  const session: LocalConversation = {
    workflowId: workflow.id,
    generation: `${++messageSequence}`,
    input: "",
    attachments: [],
    attachmentErrors: [],
    readingAttachments: false,
    storageStatus: "loading",
    messages: [
      {
        id: ++messageSequence,
        role: "user",
        text: `帮我查看 ${workflow.machine} 上 ${workflow.target} 的${workflow.experiment === "two" ? "双比特 CZ 校准" : "单比特校准"}实验，并分析校准结果。`,
      },
      {
        id: ++messageSequence,
        role: "assistant",
        kind: "result",
        text: `正在查看 ${workflow.target} 的校准演示。右侧报告随流程更新，尚未执行的指标会标为示例预览。可添加本地资料，但目前未接入文档理解模型。`,
      },
    ],
  };
  localConversations.set(workflow.id, session);
  return session;
}

function AttachmentItem({
  attachment,
  onRemove,
}: {
  attachment: LocalAttachment;
  onRemove?: () => void;
}) {
  function downloadCopy() {
    const url = URL.createObjectURL(attachment.file);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = attachment.name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <div className="cr-attachment-item" role="listitem">
      <div className="cr-attachment-header">
        <span className="cr-attachment-type">
          {attachment.extension.toUpperCase()}
        </span>
        <div className="cr-attachment-name">
          <strong>{attachment.name}</strong>
          <span>{formatAttachmentSize(attachment.size)} · 仅在本地</span>
        </div>
        <button
          type="button"
          className="cr-icon-button"
          aria-label={`下载附件副本 ${attachment.name}`}
          onClick={downloadCopy}
        >
          <ArrowDownToLine size={13} />
        </button>
        {onRemove && (
          <button
            type="button"
            className="cr-icon-button"
            aria-label={`移除附件 ${attachment.name}`}
            onClick={onRemove}
          >
            <X size={14} />
          </button>
        )}
      </div>
      <details className="cr-attachment-preview">
        <summary>
          {attachment.preview === null ? "PDF 文件说明" : "展开纯文本预览"}
        </summary>
        {attachment.preview === null ? (
          <p>
            已保留本地 PDF
            文件。尚未解析正文、识别图表或上传服务器；文档理解将在接入模型后提供。
          </p>
        ) : (
          <>
            <pre>{attachment.preview}</pre>
            <p>
              {attachment.previewTruncated
                ? "仅展示前 4,000 字符内的预览，完整原文件仍保留。"
                : "按纯文本显示，不执行 HTML、Markdown、JSON 或 CSV 内容。"}
            </p>
          </>
        )}
      </details>
    </div>
  );
}
interface T1Result {
  qubit: string;
  tau: number;
  error: number;
  fidelity: number;
  r2: number;
  points: { time: number; measured: number; fitted: number }[];
}

const quickCommands = {
  single: ["运行 T1Idle", "解释结果", "生成单比特校准计划"],
  two: ["运行 T1Idle", "对比 Q11 和 Q13", "解释结果", "生成双比特 CZ 校准计划"],
} as const;
const singleSteps = [
  "ReadoutFreqDip",
  "F01Spectroscopy",
  "XYGateAmpRabiImpl",
  "IqToProbability",
  "ReadoutParaSweepByIQProb",
  "IqToProbability · 复测",
  "T1Idle",
  "T2CPMG",
  "RB1QCal",
];
const twoSteps = [
  "F01ZPulseDependency",
  "ZeroPointFinderByQQSwap",
  "CZSwapSweepZPulse",
  "TTGAczDelicateG · 60 ns",
  "TTGAczDelicateDetune",
  "CZXEBCal",
  "CZParaOptimizer",
  "CZXEBCal · 复测",
];
const threshold = DEMO_THRESHOLDS.coherenceUs;
const fixed = (n: number, places = 2) => n.toFixed(places);
const escapeHtml = (value: string) =>
  value.replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ] || character,
  );
const markdownCell = (value: string) =>
  value.replace(/\|/g, "\\|").replace(/[\r\n]+/g, " ");

/**
 * The same resolved comparison the parameter panel shows. A comparison is a
 * reporting aid: any failure here must not take the report down.
 */
function nodeComparison(
  workflow: Workflow,
  node: ExperimentNode | undefined,
): MetricComparison | null {
  if (!node) return null;
  try {
    return buildNodeComparisons(workflow, node)[0] ?? null;
  } catch {
    return null;
  }
}

/** Acceptance floor text shared by the on-screen table and both exports. */
function acceptanceText(row: {
  threshold: number | null;
  unit: string;
  comparison: MetricComparison | null;
}): string {
  if (row.threshold === null) return "未设验收下限";
  if (!row.comparison) return `≥ ${row.threshold} ${row.unit}`;
  const operator =
    row.comparison.direction === "higher"
      ? "≥"
      : row.comparison.direction === "lower"
        ? "≤"
        : "偏差 ≤";
  return `${operator} ${formatMetricValue(row.threshold, row.comparison.unit, row.comparison.decimals)}（${THRESHOLD_SOURCE_LABELS[row.comparison.thresholdSource]}）`;
}

/** CSV fields must stay numeric when a floor exists, empty when it does not. */
function acceptanceCsv(row: {
  threshold: number | null;
}): number | "" {
  return row.threshold === null ? "" : row.threshold;
}

function generateResult(qubit: string, workflow?: Workflow): T1Result {
  const measurement = qubitMeasurements(qubit, workflow);
  return {
    qubit,
    tau: measurement.t1,
    error: measurement.error,
    fidelity: measurement.rbFidelity,
    r2: measurement.r2,
    points: decaySamples(qubit, measurement.t1),
  };
}

function FitChart({
  result,
  expanded = false,
}: {
  result: T1Result;
  expanded?: boolean;
}) {
  const width = 620;
  const height = expanded ? 330 : 245;
  const left = 47;
  const right = 17;
  const top = 20;
  const bottom = 40;
  const x = (time: number) => left + (time / 160) * (width - left - right);
  const y = (population: number) =>
    height - bottom - population * (height - top - bottom);
  const curve = Array.from({ length: 81 }, (_, i) => {
    const time = i * 2;
    return `${i === 0 ? "M" : "L"}${x(time).toFixed(2)},${y(0.96 * Math.exp(-time / result.tau) + 0.02).toFixed(2)}`;
  }).join(" ");
  return (
    <svg
      className="cr-fit-svg"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${result.qubit} 模拟 T1 弛豫曲线，T1 为 ${result.tau} 微秒，数据点与指数拟合曲线`}
    >
      {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
        <g key={tick}>
          <line
            x1={left}
            y1={y(tick)}
            x2={width - right}
            y2={y(tick)}
            stroke="currentColor"
            opacity=".13"
            strokeDasharray="3 5"
          />
          <text x={left - 13} y={y(tick) + 4} textAnchor="end">
            {tick.toFixed(2)}
          </text>
        </g>
      ))}
      {[0, 40, 80, 120, 160].map((tick) => (
        <g key={tick}>
          <line
            x1={x(tick)}
            y1={top}
            x2={x(tick)}
            y2={height - bottom}
            stroke="currentColor"
            opacity=".07"
          />
          <text x={x(tick)} y={height - bottom + 20} textAnchor="middle">
            {tick}
          </text>
        </g>
      ))}
      <text
        transform={`translate(12,${(height - bottom + top) / 2}) rotate(-90)`}
        textAnchor="middle"
      >
        激发态概率 P(|1⟩)
      </text>
      <text x={(width + left) / 2} y={height - 3} textAnchor="middle">
        延迟时间 (μs)
      </text>
      <path d={curve} stroke="#4ad0c6" strokeWidth="2" fill="none" />
      {result.points.map((point) => (
        <g key={point.time}>
          <line
            x1={x(point.time)}
            y1={y(point.measured + 0.018)}
            x2={x(point.time)}
            y2={y(point.measured - 0.018)}
            stroke="#6995ff"
            opacity=".5"
          />
          <circle
            cx={x(point.time)}
            cy={y(point.measured)}
            r="3.2"
            fill="#111d30"
            stroke="#739cff"
            strokeWidth="1.6"
          />
        </g>
      ))}
      <text
        className="cr-chart-annotation"
        x={width - 29}
        y={42}
        textAnchor="end"
      >
        T₁ = {fixed(result.tau)} ± {fixed(result.error)} μs
      </text>
      <text x={width - 29} y={62} textAnchor="end">
        R² = {fixed(result.r2, 4)}
      </text>
    </svg>
  );
}

export default function ChatReport({
  workflow,
  onOpenWorkflow,
  onCreateWorkflow,
  onNotify,
  canEditConversation = true,
}: ChatReportProps) {
  const isTwo = workflow.experiment === "two";
  const report = reportMeasurements(workflow);
  const czFidelity = report.cz;
  const t1Ready = measurementStage(report.t1Node) === "ready";
  const t1Label = measurementStageLabel(report.t1Node, report.source);
  const snapshotLabel = `${report.label} · ${workflow.status} · ${workflow.progress}%`;
  const optimizerNode = workflow.nodes.find(
    (node) => node.id === "CZParaOptimizer",
  );
  const retestNode = workflow.nodes.find(
    (node) => node.id === "CZXEBCalRetest",
  );
  // Not memoized on the workflow object alone: a server measurement arrives
  // without mutating `workflow`, and the report must re-render with it.
  const results = (() => {
    const qubits = [
      ...new Set(
        workflow.target.match(/Q\d+/gi)?.map((qubit) => qubit.toUpperCase()) ||
          (workflow.experiment === "two" ? ["Q12", "Q13"] : ["Q12"]),
      ),
    ].slice(0, workflow.experiment === "two" ? 2 : 1);
    return qubits.map((qubit) => generateResult(qubit, workflow));
  })();
  const [activeQubit, setActiveQubit] = useState(0);
  const [conversationState, setConversationState] = useState(() =>
    getLocalConversation(workflow),
  );
  const conversation =
    conversationState.workflowId === workflow.id
      ? conversationState
      : getLocalConversation(workflow);
  const { input, messages, attachments, attachmentErrors, readingAttachments, storageStatus, storageError } =
    conversation;
  const storageLoaded = runtimeFor(workflow.id).loaded;
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const [chartExpanded, setChartExpanded] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [generatedAt, setGeneratedAt] = useState(() => new Date());
  const messageEnd = useRef<HTMLDivElement>(null);
  const initialized = useRef(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const activeWorkflowId = useRef(workflow.id);
  const mounted = useRef(true);
  const editPermission = useRef({ workflowId: workflow.id, allowed: canEditConversation });
  if (editPermission.current.workflowId !== workflow.id || editPermission.current.allowed !== canEditConversation) {
    editPermission.current = { workflowId: workflow.id, allowed: canEditConversation };
  }
  activeWorkflowId.current = workflow.id;

  const mayEdit = (id: string) => mounted.current && editPermission.current.allowed && editPermission.current.workflowId === id;

  function updateConversation(
    id: string,
    update: (current: LocalConversation) => LocalConversation,
  ) {
    if (!mayEdit(id)) return;
    const current = localConversations.get(id);
    if (!current) return;
    const next = update(current);
    localConversations.set(id, next);
    if (mounted.current && activeWorkflowId.current === id)
      setConversationState(next);
    if (next.input !== current.input) runtimeFor(id).dirtyInput = true;
    if (next.input !== current.input || next.messages !== current.messages || next.attachments !== current.attachments || next.generation !== current.generation) queueConversationSave(id);
  }

  function setInput(value: string) {
    updateConversation(workflow.id, (current) => ({
      ...current,
      input: value,
    }));
  }

  async function addAttachments(files: File[]) {
    if (
      !mayEdit(workflow.id) || !files.length || !runtimeFor(workflow.id).loaded ||
      localConversations.get(workflow.id)?.readingAttachments
    )
      return;
    const targetId = workflow.id;
    const generation = conversation.generation;
    const permissionAtStart = editPermission.current;
    updateConversation(targetId, (current) => ({
      ...current,
      readingAttachments: true,
      attachmentErrors: [],
    }));
    const result = await prepareLocalAttachments(
      files,
      conversation.attachments,
    );
    // A reset/clear may occur while a file is being read. Never restore that cleared data.
    if (localConversations.get(targetId)?.generation !== generation) return;
    if (!mayEdit(targetId) || editPermission.current !== permissionAtStart) {
      // A role or task change cancels this read without creating a new persistent edit.
      const current = localConversations.get(targetId);
      if (current) localConversations.set(targetId, { ...current, readingAttachments: false });
      notifyConversation(targetId);
      return;
    }
    updateConversation(targetId, (current) => ({
      ...current,
      attachments: [...current.attachments, ...result.accepted],
      attachmentErrors: result.errors,
      readingAttachments: false,
    }));
  }

  function clearConversation() {
    if (!mayEdit(workflow.id) || !runtimeFor(workflow.id).loaded) return;
    updateConversation(workflow.id, (current) => ({
      ...current,
      generation: `${++messageSequence}`,
      input: "",
      attachments: [],
      attachmentErrors: [],
      readingAttachments: false,
      messages: [
        {
          id: ++messageSequence,
          role: "assistant",
          text: "当前对话与本地附件已清空。你可以添加资料，或选择快捷指令体验校准流程。",
        },
      ],
    }));
    if (fileInputRef.current) fileInputRef.current.value = "";
    void persistConversation(workflow.id);
    onNotify("已清空当前对话，正在更新浏览器本地存储");
  }
  const result = results[activeQubit] || results[0];
  const passed = !report.qualityFailed;
  // The table, both exports and the prose all use the comparison's resolved
  // floor, so a device-specific acceptance criterion can never be displayed
  // next to the demo's 98% / 30 μs constants.
  const readoutComparison = nodeComparison(workflow, report.readoutNode);
  const t1Comparison = nodeComparison(workflow, report.t1Node);
  const t2Comparison = nodeComparison(workflow, report.t2Node);
  const rbComparison = nodeComparison(workflow, report.rbNode);
  const czComparison = nodeComparison(workflow, report.czNode);
  const thresholdFor = (
    comparison: MetricComparison | null,
    fallback: number,
  ) => (comparison ? comparison.threshold : fallback);
  const t1Floor = thresholdFor(t1Comparison, threshold);
  const t1FloorText =
    t1Floor === null
      ? "未设验收下限"
      : formatMetricValue(t1Floor, "μs", t1Comparison?.decimals ?? 1);
  // A completed node is "within floor" only when the resolved comparison (or
  // the resolved floor, if no comparison exists) says so.
  const t1WithinFloor =
    t1Floor === null
      ? null
      : t1Comparison
        ? t1Comparison.status === "achieved" || t1Comparison.status === "passing"
        : results.every((item) => item.tau >= t1Floor);
  const readoutFloor = thresholdFor(
    readoutComparison,
    DEMO_THRESHOLDS.fidelityPercent,
  );
  const czFloor = thresholdFor(czComparison, DEMO_THRESHOLDS.fidelityPercent);
  const metricRows = [
    {
      name: "读取判别",
      value: report.readout,
      unit: "%",
      threshold: thresholdFor(readoutComparison, DEMO_THRESHOLDS.fidelityPercent),
      comparison: readoutComparison,
      stage: measurementStage(report.readoutNode),
      label: measurementStageLabel(report.readoutNode, report.source),
    },
    ...results.map((item) => ({
      name: `${item.qubit} · T1Idle`,
      value: item.tau,
      unit: "μs",
      threshold: thresholdFor(t1Comparison, threshold),
      comparison: t1Comparison,
      stage: measurementStage(report.t1Node),
      label: t1Label,
    })),
    ...(isTwo
      ? [
          {
            name: "CZ XEB",
            value: czFidelity,
            unit: "%",
            threshold: thresholdFor(
              czComparison,
              DEMO_THRESHOLDS.fidelityPercent,
            ),
            comparison: czComparison,
            stage: measurementStage(report.czNode),
            label: measurementStageLabel(report.czNode, report.source),
          },
        ]
      : [
          {
            name: `${result.qubit} · T2CPMG`,
            value: qubitMeasurements(result.qubit, workflow).t2,
            unit: "μs",
            threshold: thresholdFor(t2Comparison, threshold),
            comparison: t2Comparison,
            stage: measurementStage(report.t2Node),
            label: measurementStageLabel(report.t2Node, report.source),
          },
          {
            name: "RB1QCal",
            value: report.rb,
            unit: "%",
            threshold: thresholdFor(rbComparison, DEMO_THRESHOLDS.fidelityPercent),
            comparison: rbComparison,
            stage: measurementStage(report.rbNode),
            label: measurementStageLabel(report.rbNode, report.source),
          },
        ]),
  ];
  const assessment = (row: (typeof metricRows)[number]) =>
    row.stage !== "ready"
      ? "未完成 / 预览"
      : row.comparison
        ? row.comparison.status === "unavailable"
          ? "无有效值"
          : row.comparison.thresholdSource === "none"
            ? "未设验收下限"
            : row.comparison.status === "achieved" ||
                row.comparison.status === "passing"
              ? "通过"
              : row.comparison.status === "failing"
                ? "需复核"
                : "无有效值"
        : row.threshold === null
          ? "未设验收下限"
          : row.value >= row.threshold
            ? "通过"
            : "需复核";
  // Machine-readable counterpart of the table's verdict.
  const outcome = (comparison: MetricComparison | null, ready: boolean) =>
    !ready
      ? "NOT_FINAL_PREVIEW"
      : !comparison
        ? "PASS"
        : comparison.status === "failing"
          ? "REVIEW"
          : comparison.status === "unavailable"
            ? "NO_VALUE"
            : comparison.threshold === null
              ? "NO_FLOOR"
              : "PASS";

  useEffect(() => {
    setGeneratedAt(new Date());
  }, [workflow.status, workflow.progress, workflow.iterations]);

  useEffect(() => {
    setActiveQubit(0);
    setGeneratedAt(new Date());
    setConversationState(getLocalConversation(workflow));
    initialized.current = false;
  }, [workflow.id, workflow.target, workflow.machine, isTwo]);

  useEffect(() => {
    mounted.current = true;
    const updated = (id: string) => { const next = localConversations.get(id); if (id === workflow.id && next) setConversationState(next); };
    const reset = () => { setConversationState(getLocalConversation(workflow)); void hydrateConversation(workflow.id); };
    conversationListeners.add(updated);
    clearConversationListeners.add(reset);
    void hydrateConversation(workflow.id);
    const flush = () => { if (canEditConversation && document.visibilityState === 'hidden') void persistConversation(workflow.id); };
    document.addEventListener('visibilitychange', flush);
    return () => {
      mounted.current = false;
      conversationListeners.delete(updated);
      clearConversationListeners.delete(reset);
      document.removeEventListener('visibilitychange', flush);
      if (canEditConversation) void persistConversation(workflow.id);
    };
  }, [workflow.id, canEditConversation]);

  useEffect(() => {
    const end = messageEnd.current;
    if (
      initialized.current &&
      messages.length > 2 &&
      end &&
      !end.closest("[hidden]") &&
      end.getClientRects().length > 0
    )
      end.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    initialized.current = true;
  }, [messages.length]);

  useEffect(() => {
    const textarea = composerRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(168, Math.max(54, textarea.scrollHeight))}px`;
  }, [input, workflow.id]);

  useEffect(() => {
    if (!chartExpanded) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setChartExpanded(false);
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [chartExpanded]);

  function sendMessage(value: string) {
    if (!mayEdit(workflow.id)) return;
    const text = value.trim();
    const current = localConversations.get(workflow.id) || conversation;
    const sentAttachments = [...current.attachments];
    if (!runtimeFor(workflow.id).loaded || current.readingAttachments || (!text && !sentAttachments.length))
      return;
    let kind: ReplyKind = "unsupported";
    let reply =
      `当前为${BLUEPRINTS[workflow.experiment].title}，可运行本蓝图节点、解释结果或生成本蓝图计划。当前未连接通用 AI 或真实量子设备。`;
    if (!allowsBlueprintIntent(workflow.experiment, text) ||
        (!isTwo && /对比|比较/.test(text) && new Set(text.match(/Q\d+/gi)?.map((q) => q.toUpperCase())).size > 1)) {
      reply = `当前任务使用${BLUEPRINTS[workflow.experiment].title}，不能在这里生成或执行其他蓝图的工作。请到蓝图版选择对应蓝图并新建任务。`;
    } else if (sentAttachments.length) {
      kind = "attachments";
      reply = `已将 ${sentAttachments.length} 个本地附件关联到这条消息，并请求保存到本浏览器。请以输入框上方的存储状态确认是否保存成功；成功后刷新可恢复，没有上传服务器。你可以展开安全文本预览，或下载原文件副本；PDF 尚未解析正文。当前未接入文档理解模型，尚未分析这些附件，也没有根据论文生成或运行实验。${text ? "你的说明已随附件保留，待接入模型后处理。" : "可继续补充你的需求，待接入模型后处理。"}`;
    } else if (
      current.messages.some((message) => message.attachments?.length) &&
      /论文|附件|文档|资料|文章|复现|解读|总结|分析它|分析这些/.test(text)
    ) {
      kind = "attachments";
      reply =
        "当前对话中的附件仍与原消息关联，但尚未接入文档理解模型。我不能声称已读懂论文、复现实验或生成实验代码。你的补充要求已记录在本地对话中；可以继续查看附件预览，或单独使用快捷指令演示固定校准流程。";
    } else if (/双比特|CZ/i.test(text)) {
      kind = "two-plan";
      reply =
        "已为 Q12–Q13 生成双比特 CZ 演示计划：先完成 9 个单比特前置节点，再执行 8 个双比特节点，包括 CZXEBCal 初测、CZParaOptimizer 优化与 CZXEBCal 复测。TTGAczDelicateG 的 CZ_width 固定为 60 ns。点击下方按钮创建工作流。";
    } else if (
      /对比|比较/.test(text) &&
      /Q11/i.test(text) &&
      /Q13/i.test(text)
    ) {
      kind = "comparison";
      const q11 = generateResult("Q11", workflow);
      const q13 = generateResult("Q13", workflow);
      reply =
        report.source === "server"
          ? `根据后端记录的测量参数，Q11 的 T1 为 ${fixed(q11.tau)} μs，Q13 为 ${fixed(q13.tau)} μs，相差 ${fixed(Math.abs(q11.tau - q13.tau))} μs。是否达到当前设备验收标准以对应节点的冻结判定为准。`
          : `在同一套确定性模拟规则下，Q11 的 T1 为 ${fixed(q11.tau)} μs，Q13 为 ${fixed(q13.tau)} μs，相差 ${fixed(Math.abs(q11.tau - q13.tau))} μs。${t1Floor === null ? "当前未设 T1 验收下限。" : `与解析出的 T1 验收下限 ${formatMetricValue(t1Floor, "μs", t1Comparison?.decimals ?? 1)} 相比，Q11 ${q11.tau >= t1Floor ? "高于" : "低于"}下限，Q13 ${q13.tau >= t1Floor ? "高于" : "低于"}下限。`}此比较不代表设备实测表现。`;
    } else if (/生成|制定|创建|给我/.test(text) && /计划|校准/.test(text)) {
      kind = "plan";
      reply = isTwo
        ? "生成双比特 CZ 演示计划：9 个单比特前置节点 + 8 个双比特节点，CZ_width = 60 ns。工作流会模拟执行、质量检查和人工审批。"
        : "为 Q12 生成单比特校准演示计划：从 ReadoutFreqDip 开始，经频谱、Rabi、读出与概率映射，最终执行 T1Idle、T2CPMG、RB1QCal，共 9 个执行节点。工作流会模拟执行、质量检查和人工审批。";
    } else if (/解释|分析|为什么/.test(text)) {
      kind = "explanation";
      reply = report.source === "server"
        ? `${snapshotLabel}。${isTwo ? "双比特报告的 CZXEBCal 服务端保真度为 " + fixed(czFidelity) + "%（" + measurementStageLabel(report.czNode, report.source) + "），脉冲宽度为 60 ns。前置单比特检查包括 T1Idle、T2CPMG 和 RB1QCal。" : ""}T1Idle 测量激发态能量弛豫的时间尺度。${result.qubit} 的${t1Label}参数为 ${fixed(result.tau)} μs。数值来自后端记录的服务端测量结果；是否达到验收标准以冻结判定为准，未执行节点仍显示示例预览。`
        : `${snapshotLabel}。${isTwo ? "双比特报告的 CZXEBCal 模拟保真度为 " + fixed(czFidelity) + "%（" + measurementStageLabel(report.czNode) + "），脉冲宽度为 60 ns。前置单比特检查包括 T1Idle、T2CPMG 和 RB1QCal。" : ""}T1Idle 测量激发态能量弛豫的时间尺度。本演示采用 P₁(t) = 0.96 × exp(−t/T1) + 0.02 生成数据，并叠加固定的微小噪声。${result.qubit} 的${t1Label}参数为 ${fixed(result.tau)} μs，${t1Floor === null ? "当前未设 T1 验收下限" : `${result.tau >= t1Floor ? "高于" : "低于"}验收下限 ${formatMetricValue(t1Floor, "μs", t1Comparison?.decimals ?? 1)}（${THRESHOLD_SOURCE_LABELS[t1Comparison?.thresholdSource ?? "demo"]}）`}。右侧参数和 R² 是模拟展示值，尚未调用真实拟合或设备接口。`;
    } else if (/运行|执行|开始|测量/.test(text) && /T\s*1/i.test(text)) {
      kind = "result";
      reply = `已打开 ${workflow.target} 的 T1Idle ${t1Label}，共 ${results.length} 个比特、每比特 21 个采样点。固定输入会得到相同结果，便于演示与复现。点击下方按钮可创建完整的模拟执行工作流。`;
      setGeneratedAt(new Date());
    }
    updateConversation(workflow.id, (session) => ({
      ...session,
      messages: [
        ...session.messages,
        {
          id: ++messageSequence,
          role: "user",
          text,
          attachments: sentAttachments,
        },
        { id: ++messageSequence, role: "assistant", text: reply, kind },
      ],
      input: "",
      attachments: [],
      attachmentErrors: [],
    }));
    composerRef.current?.focus();
  }

  function createPlan(experiment: "single" | "two" = workflow.experiment) {
    if (experiment !== workflow.experiment) {
      onNotify(`当前任务仅允许${BLUEPRINTS[workflow.experiment].title}；请切换蓝图后新建任务。`);
      return;
    }
    const target = experiment === "two" ? "Q12–Q13" : "Q12";
    onCreateWorkflow({
      experiment,
      name: `${target} · ${experiment === "two" ? "双比特 CZ 校准" : "单比特校准"}`,
      machine: workflow.machine,
      qubits: experiment === "two" ? 2 : 1,
      target,
      type: "adaptive",
      policy: workflow.policy,
      scenario: workflow.scenario,
    });
  }

  function downloadReport(format: "md" | "html" | "csv") {
    const title = `${workflow.machine} · ${workflow.target} ${isTwo ? "双比特 CZ 校准" : "单比特校准"}分析报告`;
    const stamp = generatedAt.toLocaleString("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour12: false,
    });
    const disclaimer =
      report.source === "server"
        ? "服务端测量报告：指标来自后端记录的节点测量结果；尚未执行的节点仅提供显著标注的示例预览，阶段结果不代表完整流程完成。"
        : "模拟数据报告：由前端确定性规则生成，未连接真实设备或拟合服务。尚未执行的节点仅提供显著标注的示例预览；阶段结果不代表完整流程完成。";
    const optimization = isTwo
      ? `CZ_width = 60 ns；流程定义为优化 1 次 + 复测 1 次。当前优化节点：${measurementStageLabel(optimizerNode, report.source)}，复测节点：${measurementStageLabel(retestNode, report.source)}。`
      : "";
    let content = "";
    let mime = "text/plain;charset=utf-8";
    if (format === "md") {
      content = `# ${markdownCell(title)}\n\n${PLATFORM_NAME}\n\n> ${disclaimer}\n\n**${snapshotLabel}**\n\n生成时间：${stamp}（北京时间）\n\n工作流：${markdownCell(workflow.id)}\n\n## 指标与节点状态\n\n| 指标 | ${report.source === "server" ? "服务端值" : "模拟值"} | 验收下限 | 节点状态 | 判定 |\n| --- | ---: | ---: | --- | --- |\n${metricRows.map((row) => `| ${row.name} | ${fixed(row.value)} ${row.unit} | ${acceptanceText(row)} | ${row.label} | ${assessment(row)} |`).join("\n")}\n\n${optimization}\n\n## T1Idle 图表来源\n\n${t1Label}。P₁(t) = 0.96 × exp(−t/T1) + 0.02。采样范围 0–160 μs，共 21 点，叠加固定噪声。${report.source === "server" ? "参数与节点详情来自后端记录的服务端测量结果。" : "参数、曲线与节点详情使用同一套生成规则。"}\n\n## 当前结论\n\n${report.conclusion}\n\n展示误差与 R² 为预设模拟参数，不来自真实数值拟合。当前报告不支持真实设备性能结论。\n`;
    } else if (format === "csv") {
      mime = "text/csv;charset=utf-8";
      const header =
        "data_source,workflow_status,workflow_progress_percent,report_stage,t1_node_stage,qubit,delay_us,simulated_population,theoretical_population,t1_us,display_error_us,display_r_squared,t1_threshold_us,t1_assessment,readout_percent,readout_node_stage,readout_threshold_percent,cz_xeb_percent,cz_node_stage,cz_threshold_percent,cz_width_ns";
      const rows = results.flatMap((item) =>
        item.points.map((point) =>
          [
            report.source === "server"
              ? t1Ready
                ? "SERVER_RECORDED"
                : "SERVER_PREVIEW"
              : t1Ready
                ? "SIMULATED_EXECUTED"
                : "SIMULATED_PREVIEW",
            workflow.status,
            workflow.progress,
            report.preview
              ? "PREVIEW_NOT_EXECUTED"
              : report.completed
                ? "COMPLETED"
                : "PARTIAL_WORKFLOW",
            measurementStage(report.t1Node),
            item.qubit,
            point.time,
            fixed(point.measured, 6),
            fixed(point.fitted, 6),
            fixed(item.tau),
            fixed(item.error),
            fixed(item.r2, 4),
            acceptanceCsv({ threshold: t1Floor }),
            outcome(t1Comparison, t1Ready),
            fixed(report.readout),
            measurementStage(report.readoutNode),
            acceptanceCsv({ threshold: readoutFloor }),
            isTwo ? fixed(czFidelity) : "",
            isTwo ? measurementStage(report.czNode) : "",
            isTwo ? acceptanceCsv({ threshold: czFloor }) : "",
            isTwo ? 60 : "",
          ].join(","),
        ),
      );
      content = "\uFEFF" + header + "\r\n" + rows.join("\r\n");
    } else {
      mime = "text/html;charset=utf-8";
      content = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)} · ${escapeHtml(PLATFORM_NAME)}</title><style>body{font:15px/1.7 system-ui,sans-serif;background:#f5f7fb;color:#182335;max-width:960px;margin:48px auto;padding:0 24px}h1{font-size:26px}h2{font-size:19px;margin-top:32px}.notice{background:#e4eefb;border-left:4px solid #4f7fff;padding:16px}.stage{font-size:20px;font-weight:600;color:#315799}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #e2e6ee}th{background:#edf1f8}.muted{color:#667084}svg{display:block;width:100%;max-width:700px;background:#fff;border-radius:10px;margin:16px 0}text{font:11px system-ui;fill:#69778b}</style></head><body><p class="muted">${escapeHtml(PLATFORM_NAME)} · ${report.source === "server" ? "服务端测量报告" : "模拟实验报告"}</p><h1>${escapeHtml(title)}</h1><p class="stage">${escapeHtml(snapshotLabel)}</p><p class="notice">${disclaimer}</p><p class="muted">生成时间：${escapeHtml(stamp)}（北京时间） · 工作流 ${escapeHtml(workflow.id)}</p><h2>指标与节点状态</h2><table><thead><tr><th>指标</th><th>模拟值</th><th>验收下限</th><th>节点状态</th><th>判定</th></tr></thead><tbody>${metricRows.map((row) => `<tr><td>${escapeHtml(row.name)}</td><td>${fixed(row.value)} ${row.unit}</td><td>${escapeHtml(acceptanceText(row))}</td><td>${row.label}</td><td>${assessment(row)}</td></tr>`).join("")}</tbody></table><p>${optimization}</p><h2>T1Idle ${report.source === "server" ? "服务端测量曲线" : "模拟曲线"} · ${t1Label}</h2>${results
        .map((item) => {
          const points = item.points
            .map(
              (point) =>
                `<circle cx="${45 + (point.time / 160) * 610}" cy="${225 - point.measured * 200}" r="3" fill="#4f7fff"/>`,
            )
            .join("");
          const path = item.points
            .map(
              (point, index) =>
                `${index === 0 ? "M" : "L"}${45 + (point.time / 160) * 610},${225 - point.fitted * 200}`,
            )
            .join(" ");
          return `<h3>${escapeHtml(item.qubit)} · T1 ${fixed(item.tau)} μs</h3><svg viewBox="0 0 700 265" role="img" aria-label="${escapeHtml(item.qubit)} ${t1Label} T1曲线"><path d="M45,25 V225 H655" fill="none" stroke="#c6cfdf"/><path d="${path}" fill="none" stroke="#109f95" stroke-width="2"/>${points}<text x="350" y="252" text-anchor="middle">延迟时间 0–160 μs</text></svg>`;
        })
        .join(
          "",
        )}<h2>当前结论</h2><p>${report.conclusion}</p><p class="muted">模型：P₁(t) = 0.96 × exp(−t/T1) + 0.02。误差与 R² 为预设模拟值。当前报告不支持真实设备性能结论。</p></body></html>`;
    }
    const url = URL.createObjectURL(new Blob([content], { type: mime }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${PLATFORM_NAME}-${isTwo ? "CZ" : "T1Idle"}-${workflow.status}-SIMULATED-${workflow.id.replace(/[^a-zA-Z0-9_-]/g, "_")}.${format}`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    onNotify(`已导出 ${format.toUpperCase()} ${report.label}`);
  }

  return (
    <section className="cr-workspace">
      <header className="cr-page-heading">
        <div>
          <div className="cr-breadcrumb">
            工作空间 <ChevronRight size={11} /> 智能实验
          </div>
          <h1>
            对话式 Agent <span>让每一次实验，都有迹可循。</span>
          </h1>
        </div>
        <button className="cr-button cr-top-workflow" onClick={onOpenWorkflow}>
          <GitBranch size={15} />
          查看工作流
          <ArrowRight size={14} />
        </button>
      </header>
      <div className="cr-columns">
        <section className="cr-chat-panel" aria-label="演示实验助手">
          <header className="cr-panel-header">
            <div className="cr-heading-icon">
              <MessageSquare size={17} />
            </div>
            <div className="cr-panel-title">
              <h2>与测控助手对话</h2>
              <span>
                <i className="cr-live-dot" />
                规则驱动 · 演示助手
              </span>
            </div>
            <button
              className="cr-icon-button"
              aria-label="查看助手支持的功能"
              aria-expanded={showHelp}
              onClick={() => setShowHelp(!showHelp)}
            >
              <CircleHelp size={17} />
            </button>
            <button
              className="cr-icon-button"
              aria-label="清空对话"
              disabled={!canEditConversation || !storageLoaded}
              onClick={clearConversation}
            >
              <RefreshCw size={15} />
            </button>
          </header>
          {!canEditConversation && <div className="cr-readonly-banner" role="note"><LockKeyhole size={15}/><span>团队任务对话只读，复制为本人任务后可编辑。</span></div>}
          {showHelp && (
            <div className="cr-help-box">
              <strong>演示助手支持范围</strong>
              <p>
                使用下方快捷指令体验完整流程。回复按固定规则生成，不需要 API
                Key；图表和报告均为模拟数据。
              </p>
            </div>
          )}
          <div className="cr-context-strip">
            <span className="cr-context-label">当前上下文</span>
            <span>
              <i />
              {workflow.machine}
            </span>
            <span className="cr-context-qubits">{workflow.target}</span>
            <span className="cr-demo-label">DEMO</span>
          </div>
          <div
            className="cr-messages"
            role="log"
            aria-label="对话记录"
            aria-live="polite"
          >
            <div className="cr-date-divider">
              <span>实验会话 · {workflow.id}</span>
            </div>
            {messages.map((message) => (
              <div
                className={`cr-message cr-message-${message.role}`}
                key={message.id}
              >
                <div
                  className={`cr-avatar ${message.role === "assistant" ? "cr-avatar-assistant" : ""}`}
                >
                  {message.role === "assistant" ? <Sparkles size={15} /> : "Y"}
                </div>
                <div className="cr-message-body">
                  <div className="cr-message-author">
                    {message.role === "assistant" ? "测控助手" : "你"}
                    {message.role === "assistant" && <span>演示助手</span>}
                  </div>
                  {message.text && (
                    <div className="cr-message-text">{message.text}</div>
                  )}
                  {!!message.attachments?.length && (
                    <div
                      className="cr-message-attachments"
                      role="list"
                      aria-label="与这条消息关联的本地附件"
                    >
                      {message.attachments.map((attachment) => (
                        <AttachmentItem
                          key={attachment.id}
                          attachment={attachment}
                        />
                      ))}
                      <p className="cr-local-notice">
                        已关联本条消息 · 本浏览器保存 · 无服务器上传
                      </p>
                    </div>
                  )}
                  {message.kind === "result" && (
                    <div className="cr-result-card">
                      <div className="cr-result-title">
                        <span>
                          <FlaskConical size={15} />
                          {isTwo ? "前置 T1Idle 检查" : "T1Idle 弛豫实验"}
                        </span>
                        <span className="cr-small-badge">{report.label}</span>
                      </div>
                      <div className="cr-result-meta">
                        {workflow.machine} <span>·</span> {results.length}{" "}
                        个目标比特 <span>·</span> 21 个采样点 / 比特
                      </div>
                      <div className="cr-result-values">
                        {results.slice(0, 2).map((item) => (
                          <div key={item.qubit}>
                            <span>{item.qubit} · T₁</span>
                            <strong>
                              {fixed(item.tau)}
                              <small>μs</small>
                            </strong>
                            <span
                              className={
                                t1Ready && t1WithinFloor !== false
                                  ? "cr-good-text"
                                  : "cr-preview-text"
                              }
                            >
                              {t1Ready && t1WithinFloor !== false ? (
                                <Check size={11} />
                              ) : (
                                <CircleHelp size={11} />
                              )}
                              {t1Ready
                                ? t1WithinFloor === false
                                  ? "低于验收下限"
                                  : t1Floor === null
                                    ? "已执行 · 未设验收下限"
                                    : "达到验收下限"
                                : "示例预览·节点未完成"}
                            </span>
                          </div>
                        ))}
                      </div>
                      <button
                        className="cr-card-action"
                        onClick={() => createPlan()}
                      >
                        创建演示工作流
                        <ArrowRight size={13} />
                      </button>
                    </div>
                  )}
                  {(message.kind === "plan" || message.kind === "two-plan") && (
                    <div className="cr-plan-card">
                      <div className="cr-plan-top">
                        <GitBranch size={15} />
                        <strong>
                          {message.kind === "two-plan" || isTwo
                            ? "双比特 CZ 校准计划"
                            : "单比特校准计划"}
                        </strong>
                        <span>
                          {message.kind === "two-plan" || isTwo ? "17" : "9"}{" "}
                          个节点
                        </span>
                      </div>
                      {(message.kind === "two-plan" || isTwo
                        ? [...singleSteps, ...twoSteps]
                        : singleSteps
                      ).map((step, i) => (
                        <div className="cr-plan-step" key={step}>
                          <span>{String(i + 1).padStart(2, "0")}</span>
                          {step}
                          <ChevronRight size={12} />
                        </div>
                      ))}
                      <button
                        className="cr-primary-button"
                        disabled={message.kind === "two-plan" && !isTwo}
                        onClick={() =>
                          createPlan(
                            message.kind === "two-plan"
                              ? "two"
                              : workflow.experiment,
                          )
                        }
                      >
                        创建演示工作流
                        <ArrowRight size={14} />
                      </button>
                    </div>
                  )}
                  {message.kind === "comparison" && (
                    <div className="cr-comparison-card">
                      {["Q11", "Q13"].map((qubit) => {
                        const item = generateResult(qubit);
                        return (
                          <div className="cr-comparison-row" key={qubit}>
                            <span>{qubit}</span>
                            <div>
                              <i
                                style={{ width: `${(item.tau / 60) * 100}%` }}
                              />
                            </div>
                            <strong>
                              {fixed(item.tau)} <small>μs</small>
                            </strong>
                          </div>
                        );
                      })}
                      <span>确定性模拟 · T1 验收下限 {t1FloorText}</span>
                    </div>
                  )}
                  {message.role === "assistant" &&
                    message.kind !== "unsupported" && (
                      <div className="cr-message-footer">
                        <CheckCheck size={12} />
                        {message.kind === "attachments"
                          ? "本地附件 · 未上传 · 未接模型"
                          : "仅使用演示数据"}
                      </div>
                    )}
                </div>
              </div>
            ))}
            <div ref={messageEnd} />
          </div>
          <div className="cr-chat-composer">
            <div className={`cr-storage-status cr-storage-${storageStatus}`} role={storageStatus === 'error' ? 'alert' : 'status'}><div><Database size={13} /><strong>{storageStatus === 'loading' ? '正在恢复本浏览器对话…' : storageStatus === 'saving' ? '正在保存到本浏览器…' : storageStatus === 'saved' ? '本浏览器存储已就绪 · 更改已保存' : '本地存储未成功'}</strong></div>{storageError && <p>{storageError} 未保存的新内容在刷新后可能丢失。</p>}{storageStatus === 'error' && (!storageLoaded || canEditConversation) && <button type="button" onClick={() => { if (!runtimeFor(workflow.id).loaded) void hydrateConversation(workflow.id); else if (mayEdit(workflow.id)) void persistConversation(workflow.id); }}>{storageLoaded ? '重试保存' : '重试读取'}</button>}</div>
            <div className="cr-quick-label">
              <WandSparkles size={12} />
              试着问我
            </div>
            <div className="cr-quick-commands">
              {quickCommands[workflow.experiment].map((command) => (
                <button
                  key={command}
                  disabled={!canEditConversation || readingAttachments || !storageLoaded}
                  onClick={() => sendMessage(command)}
                >
                  {command}
                </button>
              ))}
            </div>
            <div className="cr-attachment-toolbar">
              <button
                type="button"
                className="cr-add-attachment"
                disabled={
                  !canEditConversation || readingAttachments || !storageLoaded ||
                  attachments.length >= ATTACHMENT_MAX_COUNT
                }
                aria-controls="cr-local-file-input"
                onClick={() => fileInputRef.current?.click()}
              >
                <Paperclip size={14} />
                {readingAttachments ? "正在读取…" : "添加本地附件"}
              </button>
              <span>
                {attachments.length} / {ATTACHMENT_MAX_COUNT} · 单文件 ≤ 10 MB
              </span>
              <input
                ref={fileInputRef}
                id="cr-local-file-input"
                className="cr-file-input"
                tabIndex={-1}
                type="file"
                accept={ATTACHMENT_ACCEPT}
                multiple
                aria-label="选择本地附件，支持 PDF TXT MD CSV JSON"
                disabled={!canEditConversation || readingAttachments || !storageLoaded}
                onChange={(event) => {
                  const files = Array.from(event.target.files || []);
                  event.target.value = "";
                  void addAttachments(files);
                }}
              />
            </div>
            {!!attachments.length && (
              <div
                className="cr-draft-attachments"
                role="list"
                aria-label="待发送的本地附件"
              >
                {attachments.map((attachment) => (
                  <AttachmentItem
                    key={attachment.id}
                    attachment={attachment}
                    onRemove={canEditConversation ? () =>
                      updateConversation(workflow.id, (current) => ({
                        ...current,
                        attachments: current.attachments.filter(
                          (item) => item.id !== attachment.id,
                        ),
                      })) : undefined
                    }
                  />
                ))}
              </div>
            )}
            {!!attachmentErrors.length && (
              <div className="cr-attachment-errors" role="alert">
                <div>
                  <strong>部分附件未添加</strong>
                  <button
                    type="button"
                    className="cr-icon-button"
                    aria-label="关闭附件错误提示"
                    disabled={!canEditConversation}
                    onClick={() =>
                      updateConversation(workflow.id, (current) => ({
                        ...current,
                        attachmentErrors: [],
                      }))
                    }
                  >
                    <X size={12} />
                  </button>
                </div>
                <ul>
                  {attachmentErrors.map((error, index) => (
                    <li key={index}>{error}</li>
                  ))}
                </ul>
              </div>
            )}
            <form
              className="cr-input-box"
              onSubmit={(event) => {
                event.preventDefault();
                sendMessage(input);
              }}
            >
              <textarea
                ref={composerRef}
                readOnly={!canEditConversation}
                aria-readonly={!canEditConversation}
                aria-label="向演示助手发送指令"
                placeholder={
                  attachments.length
                    ? "可填写附件用途，也可以直接发送附件…"
                    : "描述实验目标，或添加论文、数据与说明…"
                }
                rows={2}
                value={input}
                maxLength={4000}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault();
                    sendMessage(input);
                  }
                }}
              />
              <div className="cr-input-bottom">
                <span>
                  <span className="cr-input-model">
                    <Sparkles size={11} />
                    测控演示助手
                  </span>
                  <span className="cr-enter-tip">
                    Enter 发送 · Shift + Enter 换行
                  </span>
                </span>
                <button
                  type="submit"
                  disabled={
                    !canEditConversation || readingAttachments || !storageLoaded || (!input.trim() && !attachments.length)
                  }
                  aria-label={
                    attachments.length ? "发送消息及本地附件" : "发送指令"
                  }
                >
                  <ArrowUp size={17} />
                </button>
              </div>
            </form>
            <div className="cr-composer-meta">
              <span>PDF / TXT / MD / CSV / JSON</span>
              <span aria-live="polite">{input.length} / 4000</span>
            </div>
            <p className="cr-composer-disclaimer">
              对话与附件保存在本浏览器，保存成功后刷新可恢复；总附件上限 100 MB。清除浏览器站点数据会删除记录。未上传、未接模型。
            </p>
          </div>
        </section>
        <section className="cr-report-panel" aria-label="模拟实验分析报告">
          <header className="cr-panel-header">
            <div className="cr-heading-icon cr-heading-icon-report">
              <FileText size={17} />
            </div>
            <div className="cr-panel-title">
              <h2>实验分析报告</h2>
              <span>与当前工作流同步</span>
            </div>
            <span className="cr-report-version">v1.1</span>
            <button
              className="cr-icon-button"
              aria-label="跳转到报告导出"
              onClick={() =>
                document
                  .getElementById("cr-export-section")
                  ?.scrollIntoView({ behavior: "smooth", block: "nearest" })
              }
            >
              <MoreHorizontal size={18} />
            </button>
          </header>
          <div className="cr-report-scroll">
            <div className="cr-report-intro">
              <div className="cr-report-eyebrow">
                <span>EXPERIMENT REPORT</span>
                <span className="cr-demo-label">模拟数据</span>
              </div>
              <h2>{isTwo ? "双比特 CZ 校准分析" : "单比特校准分析"}</h2>
              <p>
                {workflow.machine} <span>/</span> {workflow.target}{" "}
                <span>/</span> 自适应校准
              </p>
              <div className="cr-report-id">
                <span>{workflow.id}</span>
                <span>
                  {generatedAt.toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                    hour12: false,
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}{" "}
                  CST
                </span>
              </div>
            </div>
            <div
              className={`cr-report-stage ${report.completed ? "cr-report-stage-complete" : ""}`}
            >
              <strong>{report.label}</strong>
              <span>
                {workflow.status} · 流程进度 {workflow.progress}%
              </span>
              <p>
                {report.preview
                  ? "此流程尚未启动，下方曲线与指标为示例预览。"
                  : report.completed
                    ? "全部节点已结束。指标按实际节点轮次及最新复测状态展示。"
                    : "此流程尚未结束。未完成节点的全曲线与参数为示例预览。"}
              </p>
            </div>
            <div className="cr-calibration-summary">
              <div className="cr-calibration-summary-title">
                <GitBranch size={13} />
                <span>{isTwo ? "双比特 CZ 校准" : "单比特性能检查"}</span>
                <span>{isTwo ? "9 前置 + 8 校准节点" : "9 个执行节点"}</span>
              </div>
              <div className="cr-calibration-metrics">
                {isTwo ? (
                  <>
                    <div>
                      <span>CZXEBCal</span>
                      <strong>
                        {fixed(czFidelity)}
                        <small>%</small>
                      </strong>
                      <p>{measurementStageLabel(report.czNode, report.source)}</p>
                    </div>
                    <div>
                      <span>TTGAczDelicateG</span>
                      <strong>
                        60<small>ns</small>
                      </strong>
                      <p>CZ_width 固定输入</p>
                    </div>
                    <div>
                      <span>优化 + 复测</span>
                      <strong>
                        1 + 1<small>次</small>
                      </strong>
                      <p>流程定义 · 各执行一次</p>
                    </div>
                  </>
                ) : (
                  <>
                    <div>
                      <span>T1Idle</span>
                      <strong>
                        {fixed(result.tau)}
                        <small>μs</small>
                      </strong>
                      <p>{t1Label}</p>
                    </div>
                    <div>
                      <span>T2CPMG</span>
                      <strong>
                        {fixed(qubitMeasurements(result.qubit, workflow).t2)}
                        <small>μs</small>
                      </strong>
                      <p>{measurementStageLabel(report.t2Node, report.source)}</p>
                    </div>
                    <div>
                      <span>RB1QCal</span>
                      <strong>
                        {fixed(report.rb)}<small>%</small>
                      </strong>
                      <p>{measurementStageLabel(report.rbNode, report.source)}</p>
                    </div>
                  </>
                )}
              </div>
            </div>
            <div
              className={`cr-conclusion ${!passed ? "cr-conclusion-warning" : !report.completed ? "cr-conclusion-pending" : ""}`}
            >
              <div className="cr-conclusion-icon">
                {passed && report.completed ? (
                  <Check size={17} />
                ) : (
                  <CircleHelp size={17} />
                )}
              </div>
              <div>
                <strong>
                  {!passed
                    ? "当前指标需要复核"
                    : report.completed
                      ? report.source === "server"
                        ? "服务端结果已按冻结标准判定"
                        : "模拟结果符合演示验收下限"
                      : "等待完整流程结果"}
                </strong>
                <p>{report.conclusion}</p>
              </div>
            </div>
            <div className="cr-report-section">
              <div className="cr-section-heading">
                <h3>
                  <span>01</span>
                  {isTwo ? "前置 T1Idle 弛豫曲线" : "T1Idle 弛豫曲线"}
                </h3>
                <button
                  className="cr-icon-button"
                  aria-label="放大 T1 曲线"
                  onClick={() => setChartExpanded(true)}
                >
                  <Maximize2 size={14} />
                </button>
              </div>
              <p className="cr-report-data-state">
                {t1Ready ? t1Label : "示例预览 · T1Idle 节点尚未完成"}
              </p>
              <div className="cr-chart-card">
                <div className="cr-chart-toolbar">
                  <div
                    className="cr-qubit-tabs"
                    role="tablist"
                    aria-label="曲线比特"
                  >
                    {results.map((item, index) => (
                      <button
                        key={item.qubit}
                        role="tab"
                        aria-selected={activeQubit === index}
                        className={activeQubit === index ? "cr-active" : ""}
                        onClick={() => setActiveQubit(index)}
                      >
                        {item.qubit}
                      </button>
                    ))}
                  </div>
                  <div className="cr-chart-legend">
                    <span>
                      <i className="cr-point-key" />
                      {t1Ready ? "模拟采样" : "示例采样"}
                    </span>
                    <span>
                      <i className="cr-line-key" />
                      理论曲线
                    </span>
                  </div>
                </div>
                <FitChart result={result} />
                <div className="cr-chart-caption">
                  P₁(t) = A · exp(−t / T₁) + B<span>21 points · 0–160 μs</span>
                </div>
              </div>
            </div>
            <div className="cr-report-section">
              <div className="cr-section-heading">
                <h3>
                  <span>02</span>模拟拟合参数 <small>{result.qubit}</small>
                </h3>
                <span className="cr-section-note">
                  {t1Ready ? "已执行模拟值" : "示例预览值"}
                </span>
              </div>
              <div className="cr-parameter-grid">
                <div>
                  <span>T₁ 弛豫时间</span>
                  <strong>
                    {fixed(result.tau)}
                    <small>μs</small>
                  </strong>
                  <span className="cr-parameter-detail">
                    ± {fixed(result.error)} μs
                  </span>
                </div>
                <div>
                  <span>拟合优度 R²</span>
                  <strong>{fixed(result.r2, 4)}</strong>
                  <span className="cr-parameter-detail cr-good-text">
                    <i />
                    模拟高拟合度
                  </span>
                </div>
                <div>
                  <span>信号振幅 A</span>
                  <strong>0.960</strong>
                  <span className="cr-parameter-detail">偏置 B = 0.020</span>
                </div>
              </div>
            </div>
            <div className="cr-report-section">
              <div className="cr-section-heading">
                <h3>
                  <span>03</span>按节点判定质量
                </h3>
                <span className="cr-section-note">未执行指标不判通过</span>
              </div>
              <div className="cr-quality-table">
                <table>
                  <thead>
                    <tr>
                      <th>指标</th>
                      <th>模拟值</th>
                      <th>阈值</th>
                      <th>结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    {metricRows.map((row) => (
                      <tr key={row.name}>
                        <td>
                          <span className="cr-qubit-dot" />
                          {row.name}
                        </td>
                        <td>
                          {fixed(row.value)} <span>{row.unit}</span>
                        </td>
                        <td>{acceptanceText(row)}</td>
                        <td>
                          <span
                            title={row.label}
                            className={`cr-status-pill ${row.stage !== "ready" ? "cr-status-preview" : assessment(row) === "需复核" ? "cr-status-review" : ""}`}
                          >
                            {row.stage === "ready" &&
                            assessment(row) === "通过" ? (
                              <Check size={11} />
                            ) : (
                              <CircleHelp size={11} />
                            )}
                            {assessment(row)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="cr-reasoning">
              <button
                onClick={() => setReasoningOpen(!reasoningOpen)}
                aria-expanded={reasoningOpen}
              >
                <Code2 size={15} />
                <span>查看生成与判定依据</span>
                <span>可追溯</span>
                <ChevronDown
                  className={reasoningOpen ? "cr-rotated" : ""}
                  size={15}
                />
              </button>
              {reasoningOpen && (
                <div className="cr-reasoning-body">
                  <ol>
                    <li>
                      T1Idle、T2CPMG 与节点详情共用确定性参数和采样规则；读取/CZ
                      异常不会改写 T1。
                    </li>
                    <li>
                      在 0–160 μs 上取 21
                      个采样点，由指数函数叠加固定噪声生成散点。
                    </li>
                    <li>
                      演示验收下限：相干时间 ≥ {DEMO_THRESHOLDS.coherenceUs} μs，读取/CZ/RB 保真度 ≥{" "}
                      {DEMO_THRESHOLDS.fidelityPercent}
                      %；远程结果使用服务端冻结标准或设备/Skill 验收下限，不借用演示值。异常仅在指定节点第一轮出现，重试或已执行复测后以最新节点结果为准。
                    </li>
                    <li>
                      误差与 R²
                      为预设模拟值。真实拟合、实验执行及模型推理需后端接入后提供。
                    </li>
                  </ol>
                </div>
              )}
            </div>
            <div className="cr-export-section" id="cr-export-section">
              <div>
                <ArrowDownToLine size={15} />
                <h3>导出报告</h3>
              </div>
              <div className="cr-export-buttons">
                <button onClick={() => downloadReport("md")}>
                  <FileText size={13} />
                  Markdown
                </button>
                <button onClick={() => downloadReport("html")}>
                  <Code2 size={13} />
                  HTML
                </button>
                <button onClick={() => downloadReport("csv")}>
                  <Table2 size={13} />
                  CSV 数据
                </button>
              </div>
            </div>
            <p className="cr-report-disclaimer">
              此报告仅用于产品流程演示。未连接真机，所有参数均为模拟数据。
            </p>
          </div>
        </section>
      </div>
      {chartExpanded && (
        <div
          className="cr-modal-overlay"
          onClick={() => setChartExpanded(false)}
        >
          <div
            className="cr-chart-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`${result.qubit} 模拟 T1 曲线详情`}
            onClick={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <span className="cr-demo-label">模拟数据</span>
                <h2>{result.qubit} · T1 弛豫曲线</h2>
                <p>{t1Ready ? t1Label : "示例预览 · 节点尚未完成"}</p>
              </div>
              <button
                autoFocus
                className="cr-icon-button"
                aria-label="关闭曲线详情"
                onClick={() => setChartExpanded(false)}
              >
                <X size={20} />
              </button>
            </header>
            <FitChart result={result} expanded />
            <p>
              21 个模拟采样点 · T₁ = {fixed(result.tau)} ± {fixed(result.error)}{" "}
              μs · 预设 R² = {fixed(result.r2, 4)}
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
