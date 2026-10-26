import type {
  AppState,
  CreateWorkflowInput,
  ExperimentNode,
  Policy,
  Workflow,
  WorkflowAction,
} from "../types.ts";

export const STORAGE_KEY = "qcal-workspace-demo-v1";
export const NODE_DURATION_SECONDS = 4;
export const MAX_WORKFLOWS = 200;
export const MAX_WORKSPACE_BYTES = 5 * 1024 * 1024;
export const MAX_WORKFLOW_EVENTS = 10000;
const SINGLE_NODE_DEFINITIONS = [
  [
    "ReadoutFreqDip",
    "ReadoutFreqDip",
    "读取谐振频率",
    "PDF单比特流程：扫描读取谐振频率",
  ],
  [
    "F01Spectroscopy",
    "F01Spectroscopy",
    "量子比特频谱",
    "PDF单比特流程：测量0→1跃迁频率",
  ],
  [
    "XYGateAmpRabiImpl",
    "XYGateAmpRabiImpl",
    "XY门幅度 Rabi",
    "PDF单比特流程：校准XY门驱动幅度",
  ],
  [
    "IqToProbability",
    "IqToProbability",
    "IQ概率判别",
    "PDF单比特流程：建立IQ测量到概率的判别",
  ],
  [
    "ReadoutParaSweepByIQProb",
    "ReadoutParaSweepByIQProb",
    "读取参数优化",
    "PDF单比特流程：通过IQ概率扫描读取参数",
  ],
  [
    "IqToProbabilityRetest",
    "IqToProbability",
    "IQ概率复测",
    "读取参数优化后回到IqToProbability复测；独立执行实例",
  ],
  [
    "T1Idle",
    "T1Idle",
    "T1弛豫测量",
    "PDF并列分支之一；演示因共享测控资源采用串行调度",
  ],
  [
    "T2CPMG",
    "T2CPMG",
    "T2 CPMG测量",
    "PDF并列分支之一；演示因共享测控资源采用串行调度",
  ],
  [
    "RB1QCal",
    "RB1QCal",
    "单比特随机基准",
    "PDF并列分支之一；演示因共享测控资源采用串行调度",
  ],
] as const;
const TWO_NODE_DEFINITIONS = [
  [
    "F01ZPulseDependency",
    "F01ZPulseDependency",
    "Z脉冲频率依赖",
    "双比特流程以完整单比特校准为前置",
  ],
  [
    "ZeroPointFinderByQQSwap",
    "ZeroPointFinderByQQSwap",
    "QQ交换零点搜索",
    "PDF双比特流程：通过QQ交换搜索零点",
  ],
  [
    "CZSwapSweepZPulse",
    "CZSwapSweepZPulse",
    "CZ交换Z脉冲扫描",
    "PDF双比特流程：扫描CZ交换Z脉冲",
  ],
  [
    "TTGAczDelicateG",
    "TTGAczDelicateG",
    "CZ耦合精调",
    "PDF指定CZ_width=60ns；参数仅用于模拟展示",
  ],
  [
    "TTGAczDelicateDetune",
    "TTGAczDelicateDetune",
    "CZ失谐精调",
    "PDF双比特流程：精细调整CZ失谐参数",
  ],
  ["CZXEBCal", "CZXEBCal", "CZ交叉熵基准", "PDF双比特流程：CZ交叉熵基准校准"],
  [
    "CZParaOptimizer",
    "CZParaOptimizer",
    "CZ参数优化",
    "PDF双比特流程：基于基准结果优化CZ参数",
  ],
  [
    "CZXEBCalRetest",
    "CZXEBCal",
    "CZ交叉熵复测",
    "CZ参数优化后回到CZXEBCal复测；独立执行实例",
  ],
] as const;
function definitions(experiment: Workflow["experiment"]) {
  return experiment === "two"
    ? [...SINGLE_NODE_DEFINITIONS, ...TWO_NODE_DEFINITIONS]
    : [...SINGLE_NODE_DEFINITIONS];
}
function anomalyNodeId(workflow: Workflow): string {
  return workflow.experiment === "two"
    ? "CZXEBCal"
    : "ReadoutParaSweepByIQProb";
}
function describeNode(
  workflow: Workflow,
  node: ExperimentNode,
  outcome: string,
): string {
  return `${definitions(workflow.experiment).find((item) => item[0] === node.id)?.[3] ?? node.label} · ${outcome}`;
}
const TERMINAL = new Set<Workflow["status"]>([
  "completed",
  "failed",
  "aborted",
]);
const POLICIES: Policy[] = ["strict", "threshold", "autopilot"];
const POLICY_LABELS: Record<Policy, string> = {
  strict: "逐步审批",
  threshold: "异常时审批",
  autopilot: "自动运行",
};

function normalizeTarget(
  input: string,
  experiment: Workflow["experiment"],
): string {
  const value = input.trim() || (experiment === "two" ? "Q12–Q13" : "Q12");
  const canonicalQubit = (qubit: string) =>
    `Q${qubit.slice(1).replace(/^0+(?=\d)/, "")}`;
  if (experiment === "single") {
    if (!/^Q\d+$/i.test(value))
      throw new Error("单比特目标须为一个量子比特编号，例如 Q12。");
    return canonicalQubit(value);
  }
  const match = value.match(/^(Q\d+)\s*[-–,，/]\s*(Q\d+)$/i);
  if (!match)
    throw new Error(
      "双比特目标须为两个不同编号，例如 Q12–Q13，可用 -、–、逗号或 / 分隔。",
    );
  const first = canonicalQubit(match[1]);
  const second = canonicalQubit(match[2]);
  if (first === second)
    throw new Error(
      "双比特实验需要两个不同的量子比特，不能重复选择同一个编号。",
    );
  return `${first}–${second}`;
}

function clone(workflow: Workflow): Workflow {
  return {
    ...workflow,
    nodes: workflow.nodes.map((node) => ({ ...node })),
    events: workflow.events.map((event) => ({ ...event })),
  };
}

function event(
  workflow: Workflow,
  message: string,
  kind: "info" | "success" | "warning" = "info",
): void {
  workflow.events.push({ time: new Date().toISOString(), message, kind });
  if (workflow.events.length > MAX_WORKFLOW_EVENTS)
    workflow.events.splice(0, workflow.events.length - MAX_WORKFLOW_EVENTS);
}

function updateProgress(workflow: Workflow): void {
  workflow.progress = Math.floor(
    workflow.nodes.reduce(
      (sum, node) => sum + (node.status === "success" ? 100 : node.progress),
      0,
    ) / workflow.nodes.length,
  );
}

function launchNext(workflow: Workflow): void {
  const next = workflow.nodes.find((node) => node.status === "pending");
  if (!next) {
    workflow.status = "completed";
    workflow.progress = 100;
    event(
      workflow,
      `流程执行完成，${workflow.nodes.length} 个模拟实验节点均已完成或确认。`,
      "success",
    );
    return;
  }
  next.status = "running";
  next.attempt += 1;
  next.progress = 0;
  workflow.status = "running";
  event(workflow, `${next.name} 已开始 · 第 ${next.attempt} 次执行。`);
}

function finishNode(workflow: Workflow, node: ExperimentNode): void {
  const anomaly =
    workflow.scenario === "anomaly" &&
    node.id === anomalyNodeId(workflow) &&
    node.attempt === 1;
  if (anomaly && workflow.policy === "autopilot") {
    event(
      workflow,
      `${node.name} 模拟保真度低于演示阈值，自动策略调整参数并安排一次重试。`,
      "warning",
    );
    node.attempt += 1;
    node.progress = 0;
    node.detail = describeNode(
      workflow,
      node,
      "正在使用调整后的参数自动重试，仅模拟执行",
    );
    workflow.iterations += 1;
    event(workflow, `${node.name} 已开始重试 · 第 2 次执行。`);
    return;
  }
  if (anomaly || workflow.policy === "strict") {
    node.status = "attention";
    node.detail = describeNode(
      workflow,
      node,
      anomaly
        ? "模拟保真度 94.2% 低于 98% 演示阈值。审批可接受此结果，重试可重新校准。"
        : "模拟结果已生成，逐步审批策略要求确认后继续。",
    );
    workflow.status = "attention";
    workflow.approved = false;
    event(
      workflow,
      anomaly
        ? `${node.name} 等待决定：接受当前模拟结果，或重试当前实验。`
        : `${node.name} 已生成结果，等待逐步审批。`,
      "warning",
    );
    return;
  }
  node.status = "success";
  node.detail = describeNode(workflow, node, "模拟实验执行完成");
  event(workflow, `${node.name} 模拟执行完成。`, "success");
  launchNext(workflow);
}

export function createWorkflow(input: CreateWorkflowInput): Workflow {
  if (!input.name.trim()) throw new Error("请填写工作流名称。");
  if (!input.machine.trim()) throw new Error("请选择目标机器。");
  if (
    [input.name, input.machine, input.target].some(
      (value) => value.length > 500,
    )
  )
    throw new Error("名称、机器或目标比特字段过长，最多允许500个字符。");
  if (!Number.isInteger(input.qubits) || ![1, 2].includes(input.qubits))
    throw new Error("当前演示仅支持单比特或双比特实验。");
  const experiment =
    input.experiment ?? (input.qubits === 2 ? "two" : "single");
  if (
    !["single", "two"].includes(experiment) ||
    input.qubits !== (experiment === "two" ? 2 : 1)
  )
    throw new Error("实验类型须与所选量子比特数量一致。");
  if (!POLICIES.includes(input.policy))
    throw new Error("请选择有效的审批策略。");
  if (!["normal", "anomaly"].includes(input.scenario))
    throw new Error("请选择有效的演示场景。");
  if (!["adaptive", "fixed"].includes(input.type))
    throw new Error("请选择有效的工作流编排方式。");
  const target = normalizeTarget(input.target, experiment);
  const randomId =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return {
    ...input,
    experiment,
    name: input.name.trim(),
    machine: input.machine.trim(),
    target,
    id: `wf-${randomId}`,
    status: "draft",
    progress: 0,
    owner: "你",
    createdAt: new Date().toISOString(),
    elapsed: 0,
    iterations: 0,
    approved: false,
    nodes: definitions(experiment).map(([id, name, label, detail]) => ({
      id,
      name,
      label,
      detail,
      status: "pending",
      progress: 0,
      attempt: 0,
      qubits: target,
    })),
    events: [
      {
        time: new Date().toISOString(),
        message: "已创建本地模拟工作流，尚未连接设备或真实后端。",
        kind: "info",
      },
    ],
  };
}

/** Advance active simulation time only. Each experiment takes four demo seconds. */
export function tickWorkflow(workflow: Workflow, seconds = 1): Workflow {
  if (
    workflow.status !== "running" ||
    !Number.isFinite(seconds) ||
    seconds <= 0
  )
    return workflow;
  const next = clone(workflow);
  let remaining = seconds;
  while (remaining > 0 && next.status === "running") {
    const node = next.nodes.find((item) => item.status === "running");
    if (!node) break;
    const timeToFinish = Math.max(0, (100 - node.progress) / 25);
    const consumed = Math.min(remaining, timeToFinish);
    node.progress = Math.min(100, node.progress + consumed * 25);
    next.elapsed += consumed;
    remaining -= consumed;
    if (node.progress >= 100) finishNode(next, node);
    else break;
  }
  updateProgress(next);
  return next;
}

/** Invalid/repeated commands are idempotent no-ops; every accepted command is logged. */
export function applyAction(
  workflow: Workflow,
  action: WorkflowAction,
): Workflow {
  if (action === "start" && !["draft", "queued"].includes(workflow.status))
    return workflow;
  if (action === "pause" && workflow.status !== "running") return workflow;
  if (action === "resume" && workflow.status !== "paused") return workflow;
  if (action === "approve" && workflow.status !== "attention") return workflow;
  if (action === "retry" && !["attention", "failed"].includes(workflow.status))
    return workflow;
  if (
    action === "abort" &&
    (TERMINAL.has(workflow.status) || workflow.status === "draft")
  )
    return workflow;
  const next = clone(workflow);
  switch (action) {
    case "start":
      event(next, "模拟任务已启动，运行时间按演示秒数累计。");
      launchNext(next);
      break;
    case "pause":
      next.status = "paused";
      event(next, "任务已暂停，实验进度与演示计时均已冻结。");
      break;
    case "resume":
      next.status = "running";
      event(next, "任务已恢复运行。");
      break;
    case "approve": {
      const node = next.nodes.find((item) => item.status === "attention");
      if (!node) return workflow;
      node.status = "success";
      node.progress = 100;
      const acceptedAnomaly =
        next.scenario === "anomaly" &&
        node.id === anomalyNodeId(next) &&
        node.attempt === 1;
      node.detail = describeNode(
        next,
        node,
        acceptedAnomaly
          ? "操作者已接受低于演示阈值的模拟结果；接受不代表指标达标"
          : "操作者已明确确认当前模拟结果",
      );
      next.approved = true;
      event(
        next,
        acceptedAnomaly
          ? `${node.name} 当前低指标结果已由你接受，继续执行后续实验；此操作不代表指标达标。`
          : `${node.name} 模拟结果已由你确认，继续执行后续实验。`,
        acceptedAnomaly ? "warning" : "success",
      );
      launchNext(next);
      break;
    }
    case "retry": {
      const index = next.nodes.findIndex(
        (item) => item.status === "attention" || item.status === "running",
      );
      if (index < 0) return workflow;
      const node = next.nodes[index];
      node.status = "running";
      node.progress = 0;
      node.attempt += 1;
      node.detail = describeNode(
        next,
        node,
        "正在使用调整后的参数重试当前模拟实验",
      );
      next.nodes.slice(index + 1).forEach((item) => {
        item.status = "pending";
        item.progress = 0;
      });
      next.status = "running";
      next.approved = false;
      next.iterations += 1;
      event(
        next,
        `${node.name} 已开始重试 · 第 ${node.attempt} 次执行，尚未批准本次结果。`,
      );
      break;
    }
    case "abort":
      next.status = "aborted";
      next.nodes.forEach((node) => {
        if (node.status !== "success") node.status = "abandoned";
      });
      event(next, "任务已由你终止，本次运行不能恢复。", "warning");
      break;
  }
  updateProgress(next);
  return next;
}

/** Policy changes govern future decisions; an existing review requires an explicit action. */
export function setPolicy(workflow: Workflow, policy: Policy): Workflow {
  if (
    !POLICIES.includes(policy) ||
    workflow.policy === policy ||
    TERMINAL.has(workflow.status)
  )
    return workflow;
  const next = clone(workflow);
  next.policy = policy;
  event(
    next,
    `审批策略已改为“${POLICY_LABELS[policy]}”，已有待处理决定仍须明确审批或重试。`,
  );
  return next;
}

function seedRun(
  id: string,
  name: string,
  machine: string,
  status: Workflow["status"],
  successCount: number,
  scenario: "normal" | "anomaly" = "normal",
  policy: Policy = "threshold",
  experiment: Workflow["experiment"] = "single",
): Workflow {
  const workflow = createWorkflow({
    name,
    machine,
    experiment,
    qubits: experiment === "two" ? 2 : 1,
    target: experiment === "two" ? "Q12–Q13" : "Q12",
    type: "adaptive",
    policy,
    scenario,
  });
  workflow.id = id;
  workflow.status = status;
  workflow.createdAt = "2026-10-23T01:30:00.000Z";
  workflow.owner = id === "wf-m7-bringup" ? "你" : "演示团队";
  workflow.elapsed =
    (successCount + (["attention", "failed"].includes(status) ? 1 : 0)) *
    NODE_DURATION_SECONDS;
  workflow.nodes.forEach((node, index) => {
    if (index < successCount) {
      node.status = "success";
      node.progress = 100;
      node.attempt = 1;
      node.detail = describeNode(workflow, node, "此前已完成的模拟实验");
    } else if (
      index === successCount &&
      ["running", "paused", "attention", "failed"].includes(status)
    ) {
      node.status = ["attention", "failed"].includes(status)
        ? "attention"
        : "running";
      node.attempt = 1;
      node.progress = node.status === "attention" ? 100 : 0;
      if (node.status === "attention")
        node.detail = describeNode(
          workflow,
          node,
          "模拟保真度 94.2% 低于 98% 演示阈值。",
        );
    } else if (status === "aborted") node.status = "abandoned";
  });
  workflow.events = [
    {
      time: workflow.createdAt,
      message: "已载入示例任务，全部实验、指标及历史记录均为模拟数据。",
      kind: "info",
    },
  ];
  if (status === "attention")
    event(
      workflow,
      `${workflow.nodes[successCount].name} 等待决定：接受当前结果，或重试当前实验。`,
      "warning",
    );
  if (status === "failed")
    event(workflow, "模拟读取超时，可重试当前实验。", "warning");
  if (status === "running")
    event(workflow, `${workflow.nodes[successCount].name} 正在模拟运行。`);
  if (status === "paused") event(workflow, "模拟任务已由操作者暂停。");
  if (status === "completed")
    event(
      workflow,
      `${workflow.nodes.length} 个模拟实验节点均已执行完成。`,
      "success",
    );
  if (status === "aborted") event(workflow, "示例任务已终止。", "warning");
  updateProgress(workflow);
  return workflow;
}

export function createInitialState(): AppState {
  return {
    version: 1,
    workflows: [
      seedRun("wf-m7-bringup", "M7 单比特完整校准", "M7", "running", 4),
      seedRun(
        "wf-q12-investigation",
        "Q12 读取异常调查",
        "M7",
        "attention",
        4,
        "anomaly",
      ),
      seedRun(
        "wf-m3-nightly",
        "M3 双比特夜间校准",
        "M3",
        "completed",
        17,
        "normal",
        "autopilot",
        "two",
      ),
      seedRun("wf-m7-coherence", "M7 单比特相干刷新", "M7", "queued", 0),
      seedRun(
        "wf-m2-gates",
        "M2 双比特CZ门优化",
        "M2",
        "paused",
        12,
        "normal",
        "threshold",
        "two",
      ),
      seedRun(
        "wf-m5-readout",
        "M5 单比特读取恢复",
        "M5",
        "failed",
        4,
        "anomaly",
      ),
      seedRun(
        "wf-m3-characterization",
        "M3 单比特表征草稿",
        "M3",
        "draft",
        0,
        "normal",
        "strict",
      ),
      seedRun("wf-m2-validation", "M2 单比特验证", "M2", "aborted", 2),
    ],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function boundedNumber(
  value: unknown,
  max = Number.MAX_SAFE_INTEGER,
): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= max
  );
}
export function isValidWorkflow(value: unknown): value is Workflow {
  if (!isRecord(value)) return false;
  const allowedKeys = new Set([
    "importSourceId",
    "experiment",
    "id",
    "name",
    "machine",
    "qubits",
    "target",
    "type",
    "status",
    "progress",
    "owner",
    "ownerId",
    "createdAt",
    "policy",
    "scenario",
    "elapsed",
    "iterations",
    "nodes",
    "events",
    "approved",
  ]);
  if (Object.keys(value).some((key) => !allowedKeys.has(key))) return false;
  if (value.ownerId !== undefined && (typeof value.ownerId !== "string" || !/^[a-zA-Z0-9_-]{1,80}$/.test(value.ownerId))) return false;
  if (
    !["id", "name", "machine", "target", "owner", "createdAt"].every(
      (key) => typeof value[key] === "string",
    )
  )
    return false;
  if (
    ["id", "name", "machine", "target", "owner"].some(
      (key) =>
        !(value[key] as string).trim() || (value[key] as string).length > 500,
    )
  )
    return false;
  if (
    value.importSourceId !== undefined &&
    (typeof value.importSourceId !== "string" ||
      !value.importSourceId.trim() ||
      value.importSourceId.length > 500)
  )
    return false;
  if (
    !value.id ||
    !value.name ||
    !Number.isFinite(Date.parse(value.createdAt as string))
  )
    return false;
  if (
    ![
      "draft",
      "queued",
      "running",
      "attention",
      "paused",
      "completed",
      "failed",
      "aborted",
    ].includes(value.status as string)
  )
    return false;
  if (
    !POLICIES.includes(value.policy as Policy) ||
    !["normal", "anomaly"].includes(value.scenario as string) ||
    !["adaptive", "fixed"].includes(value.type as string)
  )
    return false;
  if (
    !["single", "two"].includes(value.experiment as string) ||
    value.qubits !== (value.experiment === "two" ? 2 : 1)
  )
    return false;
  try {
    normalizeTarget(
      value.target as string,
      value.experiment as Workflow["experiment"],
    );
  } catch {
    return false;
  }
  if (
    !boundedNumber(value.progress, 100) ||
    !boundedNumber(value.elapsed) ||
    !boundedNumber(value.iterations) ||
    !Number.isInteger(value.iterations) ||
    typeof value.approved !== "boolean"
  )
    return false;
  const expectedNodes = definitions(value.experiment as Workflow["experiment"]);
  if (
    !Array.isArray(value.nodes) ||
    value.nodes.length !== expectedNodes.length
  )
    return false;
  for (const [index, node] of value.nodes.entries()) {
    if (
      !isRecord(node) ||
      node.id !== expectedNodes[index][0] ||
      node.name !== expectedNodes[index][1] ||
      !["label", "qubits", "detail"].every(
        (key) => typeof node[key] === "string",
      )
    )
      return false;
    const nodeKeys = new Set([
      "id",
      "name",
      "label",
      "status",
      "progress",
      "attempt",
      "qubits",
      "detail",
    ]);
    if (Object.keys(node).some((key) => !nodeKeys.has(key))) return false;
    if (
      (node.label as string).length > 500 ||
      (node.detail as string).length > 5000 ||
      (node.qubits as string).length > 500
    )
      return false;
    try {
      if (
        normalizeTarget(
          node.qubits as string,
          value.experiment as Workflow["experiment"],
        ) !==
        normalizeTarget(
          value.target as string,
          value.experiment as Workflow["experiment"],
        )
      )
        return false;
    } catch {
      return false;
    }
    if (
      !["success", "running", "pending", "attention", "abandoned"].includes(
        node.status as string,
      ) ||
      !boundedNumber(node.progress, 100) ||
      !boundedNumber(node.attempt) ||
      !Number.isInteger(node.attempt)
    )
      return false;
    if (node.status === "success" && node.progress !== 100) return false;
    if (
      node.status === "pending" &&
      (node.progress !== 0 || node.attempt !== 0)
    )
      return false;
    if (node.status === "running" && node.progress >= 100) return false;
    if (node.status === "attention" && node.progress !== 100) return false;
    if (node.attempt > 10000) return false;
  }
  const active = value.nodes.filter(
    (node) => node.status === "running" || node.status === "attention",
  );
  if (active.length > 1) return false;
  const firstUnfinished = value.nodes.findIndex(
    (node) => node.status !== "success",
  );
  if (active.length && value.nodes[firstUnfinished] !== active[0]) return false;
  if (
    active.length &&
    value.nodes
      .slice(firstUnfinished + 1)
      .some((node) => node.status !== "pending")
  )
    return false;
  if (
    ["running", "paused"].includes(value.status as string) &&
    (active.length !== 1 || active[0].status !== "running")
  )
    return false;
  if (
    value.status === "attention" &&
    (active.length !== 1 || active[0].status !== "attention")
  )
    return false;
  if (
    value.status === "failed" &&
    (active.length !== 1 || active[0].status !== "attention")
  )
    return false;
  if (
    ["draft", "queued"].includes(value.status as string) &&
    value.nodes.some((node) => node.status !== "pending" || node.progress !== 0)
  )
    return false;
  if (
    value.status === "completed" &&
    value.nodes.some((node) => node.status !== "success")
  )
    return false;
  if (
    value.status === "aborted" &&
    value.nodes.some(
      (node) => node.status !== "success" && node.status !== "abandoned",
    )
  )
    return false;
  let finishedPrefix = true;
  for (const node of value.nodes) {
    if (node.status !== "success") finishedPrefix = false;
    else if (!finishedPrefix) return false;
    if (
      ["success", "running", "attention"].includes(node.status) &&
      node.attempt < 1
    )
      return false;
  }
  if (
    value.progress !==
    Math.floor(
      value.nodes.reduce((sum, node) => sum + node.progress, 0) /
        value.nodes.length,
    )
  )
    return false;
  const retryCount = value.nodes.reduce(
    (sum, node) => sum + Math.max(0, node.attempt - 1),
    0,
  );
  const expectedElapsed = value.nodes.reduce(
    (sum, node) =>
      sum +
      node.progress / 25 +
      Math.max(0, node.attempt - 1) * NODE_DURATION_SECONDS,
    0,
  );
  if (
    value.iterations !== retryCount ||
    Math.abs(value.elapsed - expectedElapsed) > 0.000001
  )
    return false;
  if (!Array.isArray(value.events) || value.events.length > MAX_WORKFLOW_EVENTS)
    return false;
  return value.events.every(
    (item) =>
      isRecord(item) &&
      Object.keys(item).every((key) =>
        ["time", "message", "kind"].includes(key),
      ) &&
      typeof item.time === "string" &&
      Number.isFinite(Date.parse(item.time)) &&
      typeof item.message === "string" &&
      item.message.length <= 5000 &&
      ["info", "success", "warning"].includes(item.kind as string),
  );
}

export function loadState(): AppState {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw || new TextEncoder().encode(raw).byteLength > MAX_WORKSPACE_BYTES)
      return createInitialState();
    const parsed: unknown = JSON.parse(raw);
    if (
      isRecord(parsed) &&
      parsed.version === 1 &&
      Array.isArray(parsed.workflows) &&
      parsed.workflows.length <= MAX_WORKFLOWS &&
      parsed.workflows.every(isValidWorkflow) &&
      new Set(parsed.workflows.map((item) => item.id)).size ===
        parsed.workflows.length
    )
      return parsed as unknown as AppState;
  } catch {
    /* Private mode, missing storage, invalid JSON, or invalid persisted schema. */
  }
  return createInitialState();
}

export function assertWorkspaceSize(state: AppState): void {
  if (state.workflows.length > MAX_WORKFLOWS)
    throw new Error(`工作区最多保存 ${MAX_WORKFLOWS} 个任务，本次变更未保存。`);
  if (
    new TextEncoder().encode(JSON.stringify(state)).byteLength >
    MAX_WORKSPACE_BYTES
  )
    throw new Error(
      "工作区已超过5MB容量限制，本次变更未保存。请先导出已有记录，再精简任务或重置演示。",
    );
}

export function saveState(state: AppState): void {
  assertWorkspaceSize(state);
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* Storage denial/quota must not break the in-memory demonstration. */
  }
}
