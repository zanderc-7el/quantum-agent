import { SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Database,
  FileCheck2,
  FlaskConical,
  GitBranch,
  MessageSquare,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
  Square,
  TriangleAlert,
} from "lucide-react";
import type {
  ExperimentNode,
  Policy,
  Workflow,
  WorkflowAction,
} from "../types";
import { decaySamples, nodeMeasurements } from "../lib/measurements";

import "./node-inspector.css";

interface NodeInspectorProps {
  workflow: Workflow;
  node: ExperimentNode;
  onAction: (action: WorkflowAction) => void;
  onChat: () => void;
  onPolicy: (policy: Policy) => void;
  canAction?: (action: WorkflowAction) => boolean;
  canEditPolicy?: boolean;
  onOpenParameters: () => void;
}
type InspectorTab =
  | "goal"
  | "inputs"
  | "observations"
  | "fit"
  | "decision"
  | "evidence";
const tabs: { key: InspectorTab; label: string }[] = [
  { key: "goal", label: "目标" },
  { key: "inputs", label: "参数" },
  { key: "observations", label: "观测" },
  { key: "fit", label: "拟合" },
  { key: "decision", label: "决策" },
  { key: "evidence", label: "依据" },
];
const nodeStatusLabels: Record<ExperimentNode["status"], string> = {
  success: "已完成",
  running: "模拟执行中",
  pending: "等待执行",
  attention: "等待决策",
  abandoned: "已终止",
};
const policyLabels: Record<Policy, string> = {
  strict: "严格审批",
  threshold: "阈值审批",
  autopilot: "自动执行",
};
const policyDetails: Record<Policy, string> = {
  strict: "每个节点结束后，等待人工批准继续。",
  threshold: "满足演示阈值时自动继续，异常进入人工复核。",
  autopilot: "遇到模拟异常时自动调整参数并重试一次。",
};

function getModel(node: ExperimentNode, workflow: Workflow) {
  const isIQ = /IqToProbability|ReadoutParaSweep/.test(node.name);
  const isXEB = /XEB|RB1QCal|CZParaOptimizer/.test(node.name);
  const isRabi = /Rabi|Swap/.test(node.name);
  const isDecay = /T1Idle|T2CPMG/.test(node.name);
  const isSpectrum = /ReadoutFreqDip|Spectroscopy/.test(node.name);
  const measurement = nodeMeasurements(workflow, node);
  const { anomaly, fidelity, tau } = measurement;
  const seed = node.id
    .split("")
    .reduce((total, character) => total + character.charCodeAt(0), 0);
  const points = isDecay
    ? decaySamples(measurement.qubit, tau).map((point) => ({
        x: point.time,
        value: point.measured,
        fit: point.fitted,
      }))
    : Array.from({ length: 17 }, (_, index) => {
        const t = index / 16;
        const curve = isDecay
          ? 0.96 * Math.exp((-t * 160) / tau) + 0.02
          : isRabi
            ? 0.5 - 0.45 * Math.cos(t * Math.PI * 3)
            : isSpectrum
              ? 0.85 - 0.62 * Math.exp(-((t - 0.5125) ** 2) / 0.018)
              : isXEB
                ? 0.96 * Math.pow(fidelity / 100, t * 80)
                : 0.5 + 0.35 * Math.sin((t - 0.5) * Math.PI);
        return {
          x: isDecay
            ? t * 160
            : isXEB
              ? t * 80
              : isSpectrum
                ? (node.name === "ReadoutFreqDip" ? 6.66 : 4.9) + t * 0.16
                : t,
          value: Math.max(
            0,
            Math.min(1, curve + Math.sin(index * 1.73 + seed) * 0.011),
          ),
          fit: curve,
        };
      });
  return {
    isIQ,
    isXEB,
    isRabi,
    isDecay,
    isSpectrum,
    anomaly,
    fidelity,
    tau,
    points,
    xLabel: isDecay
      ? "延迟时间 (μs)"
      : isXEB
        ? "电路深度"
        : isSpectrum
          ? "扫描频率 (GHz)"
          : "归一化扫描参数",
    model: isDecay
      ? "P(t) = A · exp(−t / τ) + B"
      : isRabi
        ? "P(x) = A · cos(ωx + φ) + B"
        : isSpectrum
          ? "S(f) = B − A · exp[−(f−f₀)²/σ²]"
          : isXEB
            ? "F(m) = A · pᵐ"
            : "y(x) = A · sin(πx + φ) + B",
  };
}

function MiniCurve({
  model,
  fraction,
}: {
  model: ReturnType<typeof getModel>;
  fraction: number;
}) {
  const visiblePoints = model.points.slice(
    0,
    Math.max(1, Math.ceil(model.points.length * fraction)),
  );
  const x = (index: number) => 35 + (index / (model.points.length - 1)) * 258;
  const y = (value: number) => 157 - value * 125;
  return (
    <svg
      className="ni-chart-svg"
      viewBox="0 0 314 194"
      role="img"
      aria-label={`${model.xLabel}与模拟测量值的关系，当前显示 ${visiblePoints.length} 个演示采样点`}
    >
      {[0, 0.5, 1].map((value) => (
        <g key={value}>
          <line
            x1="35"
            x2="293"
            y1={y(value)}
            y2={y(value)}
            stroke="currentColor"
            opacity=".15"
            strokeDasharray="3 4"
          />
          <text x="27" y={y(value) + 3} textAnchor="end">
            {value.toFixed(1)}
          </text>
        </g>
      ))}
      {[
        0,
        Math.floor((model.points.length - 1) / 2),
        model.points.length - 1,
      ].map((index) => (
        <text key={index} x={x(index)} y="172" textAnchor="middle">
          {model.isSpectrum
            ? model.points[index].x.toFixed(2)
            : model.points[index].x.toFixed(
                model.isDecay || model.isXEB ? 0 : 1,
              )}
        </text>
      ))}
      <path
        d={visiblePoints
          .map(
            (point, index) =>
              `${index === 0 ? "M" : "L"}${x(index)},${y(point.fit)}`,
          )
          .join(" ")}
        stroke="#4bc7bb"
        fill="none"
        strokeWidth="1.5"
      />
      {visiblePoints.map((point, index) => (
        <circle
          key={index}
          cx={x(index)}
          cy={y(point.value)}
          r="2.6"
          fill="#121e32"
          stroke="#7aa3ff"
          strokeWidth="1.3"
        />
      ))}
      <text x="165" y="189" textAnchor="middle">
        {model.xLabel}
      </text>
      <text x="9" y="94" transform="rotate(-90 9 94)" textAnchor="middle">
        归一化响应
      </text>
    </svg>
  );
}

function IQScatter({
  fraction,
  anomaly,
}: {
  fraction: number;
  anomaly: boolean;
}) {
  const count = Math.max(1, Math.floor(44 * fraction));
  return (
    <svg
      className="ni-chart-svg"
      viewBox="0 0 314 194"
      role="img"
      aria-label={`模拟 IQ 散点图，显示 ${count * 2} 个代表性演示点，两组分别对应零态与一态`}
    >
      {[-1, 0, 1].map((tick) => (
        <g key={tick}>
          <line
            x1={166 + tick * 112}
            x2={166 + tick * 112}
            y1="22"
            y2="158"
            stroke="currentColor"
            opacity=".13"
            strokeDasharray="3 4"
          />
          <line
            x1="40"
            x2="293"
            y1={90 + tick * 57}
            y2={90 + tick * 57}
            stroke="currentColor"
            opacity=".13"
            strokeDasharray="3 4"
          />
          <text x={166 + tick * 112} y="172" textAnchor="middle">
            {tick}
          </text>
          <text x="31" y={93 - tick * 57} textAnchor="end">
            {tick}
          </text>
        </g>
      ))}
      {[0, 1].flatMap((state) =>
        Array.from({ length: count }, (_, i) => {
          const radius = Math.sqrt(((i * 19) % 43) / 43);
          const angle = i * 2.39996;
          const cx =
            (state === 0 ? 107 : anomaly ? 186 : 220) +
            Math.cos(angle) * radius * (anomaly ? 45 : 30);
          const cy = (state === 0 ? 113 : 64) + Math.sin(angle) * radius * 21;
          return (
            <circle
              key={`${state}-${i}`}
              cx={cx}
              cy={cy}
              r="2.2"
              fill={state === 0 ? "#638eff" : "#4ed0c1"}
              opacity=".72"
            />
          );
        }),
      )}
      <line
        x1="157"
        y1="28"
        x2="181"
        y2="151"
        stroke="#c49c62"
        opacity=".5"
        strokeDasharray="4 4"
      />
      <text x="166" y="189" textAnchor="middle">
        I 分量 (a.u.)
      </text>
      <text x="10" y="94" transform="rotate(-90 10 94)" textAnchor="middle">
        Q 分量 (a.u.)
      </text>
    </svg>
  );
}

export default function NodeInspector({
  workflow,
  node,
  onAction,
  onChat,
  onPolicy,
  canAction = () => true,
  canEditPolicy = true,
  onOpenParameters,
}: NodeInspectorProps) {
  const [tab, setTab] = useState<InspectorTab>("observations");
  useEffect(() => {
    setTab("observations");
  }, [workflow.id]);
  const model = useMemo(
    () => getModel(node, workflow),
    [node, workflow],
  );
  const ready = node.status === "success" || node.status === "attention";
  const hasData =
    ready ||
    ((node.status === "running" || node.status === "abandoned") &&
      node.progress > 0);
  const fraction = ready ? 1 : Math.max(0, Math.min(1, node.progress / 100));
  const currentNode =
    workflow.nodes.find((item) => item.status === "attention") ||
    workflow.nodes.find((item) => item.status === "running");
  const repeatedName =
    workflow.nodes.filter((item) => item.name === node.name).length > 1;
  const nodeEvents = workflow.events
    .filter((event) =>
      repeatedName
        ? node.id !== node.name && event.message.includes(node.id)
        : event.message.includes(node.name),
    )
    .slice(-5);
  const selectedIsCurrent = currentNode?.id === node.id;
  const terminal = ["completed", "failed", "aborted"].includes(workflow.status);
  const canAbort = ["queued", "running", "paused", "attention"].includes(
    workflow.status,
  );
  const nodeIndex = workflow.nodes.findIndex((item) => item.id === node.id);
  const metric =
    model.isIQ || model.isXEB
      ? `${model.fidelity.toFixed(2)}%`
      : model.isDecay
        ? `${model.tau.toFixed(2)} μs`
        : model.isSpectrum
          ? node.name === "ReadoutFreqDip"
            ? "6.742 GHz"
            : "4.982 GHz"
          : model.isRabi
            ? "0.217 a.u."
            : "60 ns";
  const metricLabel = model.isIQ
    ? "模拟判别保真度"
    : model.isXEB
      ? "模拟门保真度"
      : model.isDecay
        ? "模拟相干参数"
        : model.isSpectrum
          ? "模拟中心频率"
          : model.isRabi
            ? "模拟最优幅度"
            : "演示脉冲宽度";
  const isFailedThreshold = hasData && model.anomaly;
  const qualityLabel = !hasData
    ? "等待采样"
    : !ready
      ? "采样未完成"
      : isFailedThreshold
        ? "低于演示阈值"
        : "达到演示阈值";
  const observationRows = model.isIQ
    ? [
        ["|0⟩ · I 均值", "−0.520", "a.u."],
        ["|0⟩ · Q 均值", "−0.360", "a.u."],
        ["|1⟩ · I 均值", model.anomaly ? "0.180" : "0.480", "a.u."],
        ["|1⟩ · Q 均值", "+0.420", "a.u."],
        [
          "代表性散点",
          String(Math.max(1, Math.floor(44 * fraction)) * 2),
          "点",
        ],
      ]
    : model.points
        .slice(0, Math.max(1, Math.ceil(model.points.length * fraction)))
        .filter((_, index) => index % 4 === 0)
        .map((point) => [
          point.x.toFixed(model.isSpectrum ? 3 : 2),
          point.value.toFixed(4),
          "a.u.",
        ]);

  function renderEmpty() {
    return (
      <div className="ni-empty">
        <div>
          <Database size={25} />
        </div>
        <strong>
          {node.status === "abandoned"
            ? "本节点未产生观测数据"
            : "等待节点开始采样"}
        </strong>
        <p>
          {node.status === "pending"
            ? "此节点尚未执行。开始当前流程并运行到该节点后，才会显示对应模拟观测。"
            : node.status === "abandoned"
              ? "流程在采样前已终止，此处不生成虚构的已执行结果。"
              : "当前模拟节点已启动，首批采样完成后显示数据。"}
        </p>
        <span>当前进度 {Math.round(node.progress)}% · 演示模式</span>
      </div>
    );
  }

  return (
    <aside className="ni-inspector" aria-label="实验节点详情">
      <header className="ni-heading">
        <div>
          <FlaskConical size={16} />
          <span>节点详情</span>
        </div>
        <span className="ni-demo">模拟环境</span>
      </header>
      <div className="ni-node-intro">
        <div className="ni-node-kicker">
          <span>
            节点 {String(nodeIndex + 1).padStart(2, "0")} /{" "}
            {workflow.nodes.length}
          </span>
          <span className={`ni-node-status ni-node-status-${node.status}`}>
            <i />
            {workflow.status === "paused" && node.status === "running"
              ? "已暂停"
              : nodeStatusLabels[node.status]}
          </span>
        </div>
        <h2>{node.label}</h2>
        <p className="ni-node-name">{node.name}</p>
        <div className="ni-node-context">
          <span>
            <i />
            {workflow.machine}
          </span>
          <span>{node.qubits || workflow.target}</span>
          <span>第 {node.attempt || "—"} 轮</span>
        </div>
        <div className="ni-progress-line">
          <i
            style={{ width: `${Math.max(0, Math.min(100, node.progress))}%` }}
          />
        </div>
      </div>
      <div className="ni-tabs" role="tablist" aria-label="节点详情分类">
        {tabs.map((item) => (
          <button
            key={item.key}
            type="button"
            role={item.key === "inputs" ? undefined : "tab"}
            aria-label={item.key === "inputs" ? "在浮动侧栏打开参数" : undefined}
            aria-selected={item.key === "inputs" ? undefined : tab === item.key}
            aria-controls={item.key === "inputs" ? "parameter-drawer" : `ni-panel-${node.id}`}
            className={tab === item.key ? "ni-tab-active" : ""}
            onClick={() => item.key === "inputs" ? onOpenParameters() : setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="ni-content" role="tabpanel" id={`ni-panel-${node.id}`}>
        <button className="ni-open-parameters" onClick={onOpenParameters}><SlidersHorizontal size={15}/><span>在侧栏查看实时参数</span><ChevronRight size={14}/></button>
        {!selectedIsCurrent && currentNode && (
          <div className="ni-history-note">
            <Clock3 size={12} />
            <span>
              正在查看
              {node.status === "success"
                ? "已完成"
                : node.status === "pending"
                  ? "尚未执行"
                  : "其他"}
              节点。当前流程节点：<strong>{currentNode.name}</strong>
            </span>
          </div>
        )}
        {tab === "goal" && (
          <>
            <div className="ni-section-title">
              <GitBranch size={14} />
              <h3>节点目标</h3>
            </div>
            <p className="ni-prose">{node.detail.split(" · ")[0]}。</p>
            <div className="ni-definition-list">
              <div>
                <span>执行对象</span>
                <strong>
                  {workflow.machine} / {node.qubits || workflow.target}
                </strong>
              </div>
              <div>
                <span>实验类型</span>
                <strong>
                  {workflow.experiment === "two"
                    ? "双比特 CZ 校准"
                    : "单比特校准"}
                </strong>
              </div>
              <div>
                <span>流程位置</span>
                <strong>
                  第 {nodeIndex + 1} / {workflow.nodes.length} 个节点
                </strong>
              </div>
              <div>
                <span>前置依赖</span>
                <strong>
                  {nodeIndex > 0
                    ? workflow.nodes[nodeIndex - 1].name
                    : "无 · 流程入口"}
                </strong>
              </div>
              <div>
                <span>后续节点</span>
                <strong>
                  {workflow.nodes[nodeIndex + 1]?.name || "流程完成"}
                </strong>
              </div>
            </div>
            <div className="ni-info-box">
              <CircleHelp size={14} />
              <p>
                T1Idle、T2CPMG、RB1QCal
                在参考流程中为并列性能检查，本演示按共享测控资源串行执行。所有节点均在浏览器内模拟。
              </p>
            </div>
          </>
        )}
        
        {tab === "observations" && (
          <>
            {!hasData ? (
              renderEmpty()
            ) : (
              <>
                <div className="ni-section-title">
                  <Activity size={14} />
                  <h3>{model.isIQ ? "IQ 分布观测" : "采样曲线"}</h3>
                  <span className="ni-demo">模拟数据</span>
                </div>
                <div className="ni-chart-box">
                  <div className="ni-chart-legend">
                    <span>
                      <i />
                      {model.isIQ ? "|0⟩ 采样" : "模拟采样"}
                    </span>
                    <span>
                      <i />
                      {model.isIQ ? "|1⟩ 采样" : "生成曲线"}
                    </span>
                  </div>
                  {model.isIQ ? (
                    <IQScatter fraction={fraction} anomaly={model.anomaly} />
                  ) : (
                    <MiniCurve model={model} fraction={fraction} />
                  )}
                  <div className="ni-chart-caption">
                    {model.isIQ
                      ? "代表性子采样 · IQ 归一化坐标"
                      : `${Math.max(1, Math.ceil(model.points.length * fraction))} / ${model.points.length} 个演示采样点`}
                    <span>{ready ? "该节点记录" : "部分模拟数据"}</span>
                  </div>
                </div>
                <div className="ni-metric-card">
                  <span>{metricLabel}</span>
                  <strong>
                    {ready ? metric : "采样中"}
                    {ready && <small>模拟</small>}
                  </strong>
                  <span
                    className={isFailedThreshold ? "ni-warning" : "ni-teal"}
                  >
                    {qualityLabel}
                  </span>
                </div>
                <div className="ni-section-title ni-section-title-small">
                  <h3>数据摘录</h3>
                  <span>{model.isIQ ? "归一化均值" : "采样子集"}</span>
                </div>
                <table className="ni-data-table">
                  <thead>
                    <tr>
                      <th>{model.isIQ ? "观测项" : model.xLabel}</th>
                      <th>{model.isIQ ? "模拟值" : "响应值"}</th>
                      <th>单位</th>
                    </tr>
                  </thead>
                  <tbody>
                    {observationRows.map((row, i) => (
                      <tr key={i}>
                        <td>{row[0]}</td>
                        <td>{row[1]}</td>
                        <td>{row[2]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="ni-footnote">
                  {ready
                    ? "这是所选节点的模拟快照；切换节点会切换对应数据。"
                    : "采样尚未完成，当前数据不构成最终拟合结果。"}{" "}
                  数据与图形由前端规则生成。
                </p>
              </>
            )}
          </>
        )}
        {tab === "fit" && (
          <>
            {!hasData ? (
              renderEmpty()
            ) : !ready ? (
              <div className="ni-empty">
                <div>
                  <Activity size={25} />
                </div>
                <strong>等待采样完成</strong>
                <p>
                  当前节点进度 {Math.round(node.progress)}
                  %，完整模拟参数会在采样结束后展示。
                </p>
              </div>
            ) : (
              <>
                <div className="ni-section-title">
                  <Activity size={14} />
                  <h3>{model.isIQ ? "概率判别参数" : "模拟拟合参数"}</h3>
                  <span className="ni-demo">展示值</span>
                </div>
                <div className="ni-formula">
                  {model.isIQ ? "P(state | I, Q) · 二分类判别" : model.model}
                </div>
                <div className="ni-definition-list">
                  <div>
                    <span>{metricLabel}</span>
                    <strong>{metric}</strong>
                  </div>
                  {model.isIQ ? (
                    <>
                      <div>
                        <span>P(0|0)</span>
                        <strong>{(model.fidelity + 0.35).toFixed(2)}%</strong>
                      </div>
                      <div>
                        <span>P(1|1)</span>
                        <strong>{(model.fidelity - 0.35).toFixed(2)}%</strong>
                      </div>
                      <div>
                        <span>判别阈值</span>
                        <strong>0.500</strong>
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <span>展示拟合优度 R²</span>
                        <strong>{model.anomaly ? "0.9480" : "0.9985"}</strong>
                      </div>
                      <div>
                        <span>展示振幅 A</span>
                        <strong>
                          {model.isRabi
                            ? "0.4500"
                            : model.isSpectrum
                              ? "0.6200"
                              : model.isDecay || model.isXEB
                                ? "0.9600"
                                : "0.3500"}
                        </strong>
                      </div>
                      <div>
                        <span>展示偏置 B</span>
                        <strong>
                          {model.isRabi
                            ? "0.5000"
                            : model.isSpectrum
                              ? "0.8500"
                              : model.isDecay
                                ? "0.0200"
                                : model.isXEB
                                  ? "0.0000"
                                  : "0.5000"}
                        </strong>
                      </div>
                    </>
                  )}
                  <div>
                    <span>数据来源</span>
                    <strong>前端模拟生成</strong>
                  </div>
                </div>
                <div className="ni-info-box">
                  <CircleHelp size={14} />
                  <p>
                    此处参数是预设演示值，没有运行数值拟合或机器学习服务。展示误差和拟合优度不得用于真实设备判断。
                  </p>
                </div>
              </>
            )}
          </>
        )}
        {tab === "decision" && (
          <>
            <div className="ni-section-title">
              <ShieldCheck size={14} />
              <h3>所选节点判定</h3>
            </div>
            <div
              className={`ni-decision-card ${isFailedThreshold ? "ni-decision-warning" : ""}`}
            >
              {!hasData ? (
                <Clock3 size={20} />
              ) : isFailedThreshold ? (
                <TriangleAlert size={20} />
              ) : (
                <Check size={20} />
              )}
              <strong>{qualityLabel}</strong>
              <p>
                {!hasData
                  ? "节点未完成采样，尚无可审批的结果。"
                  : !ready
                    ? "模拟采样进行中，最终判定将在节点完成后产生。"
                    : model.anomaly
                      ? "模拟保真度 94.2% 低于 98% 演示阈值。请复核当前流程节点后选择批准或重试。"
                      : workflow.policy === "strict" &&
                          selectedIsCurrent &&
                          node.status === "attention"
                        ? "当前采用严格审批策略，需要人工确认后继续。"
                        : "所选节点的演示结果满足当前预设条件。"}
              </p>
            </div>
            <div className="ni-definition-list">
              <div>
                <span>当前决策策略</span>
                <strong>{policyLabels[workflow.policy]}</strong>
              </div>
              <div>
                <span>比对基准</span>
                <strong>
                  {model.isIQ || model.isXEB
                    ? "保真度 ≥ 98%"
                    : model.isDecay
                      ? "相干时间 ≥ 30 μs"
                      : "固定演示质量检查"}
                </strong>
              </div>
              <div>
                <span>审批对象</span>
                <strong>{currentNode?.name || "当前无待处理节点"}</strong>
              </div>
            </div>
            <p className="ni-footnote">
              审批与重试作用于当前流程待处理节点，不能回写所选历史节点。更改策略不自动处理已挂起的审批。
            </p>
          </>
        )}
        {tab === "evidence" && (
          <>
            <div className="ni-section-title">
              <FileCheck2 size={14} />
              <h3>可追溯记录</h3>
              <span>本地演示</span>
            </div>
            <div className="ni-evidence-card">
              <span>工作流标识</span>
              <code>{workflow.id}</code>
              <span>执行实例</span>
              <code>
                {node.id} / attempt-{node.attempt || 0}
              </code>
              <span>节点状态</span>
              <p>
                {nodeStatusLabels[node.status]} · {Math.round(node.progress)}%
              </p>
            </div>
            <div className="ni-section-title ni-section-title-small">
              <h3>相关日志</h3>
            </div>
            <div className="ni-evidence-events">
              {nodeEvents.map((event, index) => (
                <div key={`${event.time}-${index}`}>
                  <i />
                  <time>
                    {new Date(event.time).toLocaleTimeString("zh-CN", {
                      timeZone: "Asia/Shanghai",
                      hour12: false,
                    })}
                  </time>
                  <p>{event.message}</p>
                </div>
              ))}
              {!nodeEvents.length && (
                <p className="ni-prose">
                  {repeatedName
                    ? "同名实验的运行日志未提供独立实例标识，因此不将这些日志归属到本次初测或复测。当前状态以所选节点记录为准。"
                    : "当前没有该执行实例的独立日志。种子示例的历史状态仅作为模拟快照。"}
                </p>
              )}
            </div>
            <div className="ni-info-box">
              <Database size={14} />
              <p>
                实验数据来自确定性前端规则，状态来自当前工作流。重试和复测使用独立执行轮次；没有真实硬件测量记录。
              </p>
            </div>
          </>
        )}
      </div>
      <footer className="ni-controls">
        <div className="ni-policy-heading">
          <ShieldCheck size={13} />
          <span>流程决策策略</span>
        </div>
        <select
          aria-label="流程决策策略"
          value={workflow.policy}
          disabled={terminal || !canEditPolicy}
          title={!canEditPolicy ? "需团队负责人设置审批策略" : undefined}
          onChange={(event) => { if (canEditPolicy) onPolicy(event.target.value as Policy); }}
        >
          <option value="strict">严格审批 · 每步确认</option>
          <option value="threshold">阈值审批 · 异常确认</option>
          <option value="autopilot">自动执行 · 自动重试</option>
        </select>
        <p className="ni-policy-description">
          {policyDetails[workflow.policy]}
          {!canEditPolicy && <span className="ni-permission-note">需团队负责人设置审批策略</span>}
        </p>
        <div className="ni-current-action-note">
          <span>操作对象</span>
          <strong>当前流程待处理节点</strong>
          {currentNode && <small>{currentNode.name}</small>}
        </div>
        <div className="ni-action-buttons">
          {workflow.status === "attention" && (
            <>
              <button
                className="ni-primary"
                disabled={!canAction("approve")}
                title={!canAction("approve") ? "需团队负责人审批" : undefined}
                onClick={() => { if (canAction("approve")) onAction("approve"); }}
              >
                <Check size={14} />
                批准并继续
              </button>
              <button className="ni-retry" disabled={!canAction("retry")}
                title={!canAction("retry") ? "需团队负责人审批或退回重试" : undefined}
                onClick={() => { if (canAction("retry")) onAction("retry"); }}>
                <RotateCcw size={13} />
                拒绝并重试
              </button>
            </>
          )}
          {workflow.status === "failed" && (
            <button className="ni-primary" disabled={!canAction("retry")}
                title={!canAction("retry") ? "仅可操作本人任务，或联系团队负责人" : undefined}
                onClick={() => { if (canAction("retry")) onAction("retry"); }}>
              <RotateCcw size={14} />
              重试当前失败节点
            </button>
          )}
          {workflow.status === "running" && (
            <button className="ni-primary" disabled={!canAction("pause")}
                title={!canAction("pause") ? "仅可操作本人任务，或联系团队负责人" : undefined}
                onClick={() => { if (canAction("pause")) onAction("pause"); }}>
              <Pause size={14} />
              暂停当前流程
            </button>
          )}
          {workflow.status === "paused" && (
            <button className="ni-primary" disabled={!canAction("resume")}
                title={!canAction("resume") ? "仅可操作本人任务，或联系团队负责人" : undefined}
                onClick={() => { if (canAction("resume")) onAction("resume"); }}>
              <Play size={14} />
              恢复当前流程
            </button>
          )}
          {["draft", "queued"].includes(workflow.status) && (
            <button className="ni-primary" disabled={!canAction("start")}
                title={!canAction("start") ? "仅可操作本人任务，或联系团队负责人" : undefined}
                onClick={() => { if (canAction("start")) onAction("start"); }}>
              <Play size={14} />
              启动模拟流程
            </button>
          )}
          {workflow.status === "completed" && (
            <div className="ni-terminal-note">
              <Check size={14} />
              当前流程已完成
            </div>
          )}
          {workflow.status === "aborted" && (
            <div className="ni-terminal-note">
              <Square size={12} />
              当前流程已终止
            </div>
          )}
          {canAbort && (
            <button
              className="ni-stop"
              aria-label="终止当前流程"
              disabled={!canAction("abort")}
                title={!canAction("abort") ? "仅可操作本人任务，或联系团队负责人" : undefined}
                onClick={() => { if (canAction("abort")) onAction("abort"); }}
            >
              <Square size={12} />
              终止
            </button>
          )}
        </div>
        {workflow.status === "attention" && !canAction("approve") && <p className="ni-permission-note">需团队负责人审批；当前节点仍可查看参数与记录。</p>}
        {!['completed', 'aborted', 'attention'].includes(workflow.status) && !canAction(workflow.status === 'running' ? 'pause' : workflow.status === 'paused' ? 'resume' : workflow.status === 'failed' ? 'retry' : 'start') && <p className="ni-permission-note">仅可操作本人任务，或联系团队负责人。</p>}
        <button className="ni-chat-action" onClick={onChat}>
          <MessageSquare size={13} />
          <span>打开任务对话</span>
          <ArrowRight size={13} />
        </button>
        <div className="ni-footer-note">
          <ChevronRight size={10} />
          全部操作仅影响本地演示状态
        </div>
      </footer>
    </aside>
  );
}
