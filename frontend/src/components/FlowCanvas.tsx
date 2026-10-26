import { useEffect, useId, useRef, useState } from "react";
import {
  Check,
  ChevronRight,
  Expand,
  GitBranch,
  Minus,
  Plus,
  RotateCcw,
} from "lucide-react";
import type { ExperimentNode, NodeStatus, Workflow } from "../types";
import "./flow-canvas.css";

interface FlowCanvasProps {
  workflow: Workflow;
  selectedNodeId: string;
  onSelectNode: (id: string) => void;
}

type Stage = "single" | "cz";
type Point = { x: number; y: number };
type Connection = { from: number; to: number; path: string; loop?: boolean };

const WIDTH = 1100;
const HEIGHT = 440;
const MIN_ZOOM = 0.8;
const CARD_WIDTH = 190;
const CARD_HEIGHT = 110;
const SINGLE_IDS = new Set([
  "ReadoutFreqDip",
  "F01Spectroscopy",
  "XYGateAmpRabiImpl",
  "IqToProbability",
  "ReadoutParaSweepByIQProb",
  "IqToProbabilityRetest",
  "T1Idle",
  "T2CPMG",
  "RB1QCal",
]);
const STATUS_LABEL: Record<NodeStatus, string> = {
  success: "已完成",
  running: "运行中",
  pending: "待执行",
  attention: "待决策",
  abandoned: "已终止",
};
const SINGLE_POSITIONS: Point[] = [
  { x: 32, y: 58 },
  { x: 270, y: 58 },
  { x: 508, y: 58 },
  { x: 508, y: 244 },
  { x: 270, y: 244 },
  { x: 32, y: 244 },
  { x: 872, y: 22 },
  { x: 872, y: 165 },
  { x: 872, y: 308 },
];
const CZ_POSITIONS: Point[] = [
  { x: 34, y: 66 },
  { x: 310, y: 66 },
  { x: 586, y: 66 },
  { x: 862, y: 66 },
  { x: 862, y: 272 },
  { x: 586, y: 272 },
  { x: 310, y: 272 },
  { x: 34, y: 272 },
];
const SINGLE_CONNECTIONS: Connection[] = [
  { from: 0, to: 1, path: "M222 113 H270" },
  { from: 1, to: 2, path: "M460 113 H508" },
  { from: 2, to: 3, path: "M603 168 V244" },
  { from: 3, to: 4, path: "M508 299 H460" },
  { from: 4, to: 5, path: "M270 299 H222" },
  {
    from: 5,
    to: 6,
    path: "M127 354 V396 Q127 414 145 414 H786 Q804 414 804 396 V95 Q804 77 822 77 H872",
  },
  { from: 5, to: 7, path: "M804 220 H872" },
  { from: 5, to: 8, path: "M804 363 H872" },
  {
    from: 5,
    to: 3,
    path: "M127 244 V220 Q127 202 145 202 H585 Q603 202 603 220 V244",
    loop: true,
  },
];
const CZ_CONNECTIONS: Connection[] = [
  { from: 0, to: 1, path: "M224 121 H310" },
  { from: 1, to: 2, path: "M500 121 H586" },
  { from: 2, to: 3, path: "M776 121 H862" },
  { from: 3, to: 4, path: "M957 176 V272" },
  { from: 4, to: 5, path: "M862 327 H776" },
  { from: 5, to: 6, path: "M586 327 H500" },
  { from: 6, to: 7, path: "M310 327 H224" },
  {
    from: 7,
    to: 5,
    path: "M129 272 V236 Q129 218 147 218 H663 Q681 218 681 236 V272",
    loop: true,
  },
];

function stageFor(nodeId: string): Stage {
  return SINGLE_IDS.has(nodeId) ? "single" : "cz";
}

function NodeCard({
  node,
  position,
  selected,
  index,
  paused,
  onSelect,
}: {
  node: ExperimentNode;
  position: Point;
  selected: boolean;
  index: number;
  paused: boolean;
  onSelect: () => void;
}) {
  const statusLabel =
    paused && node.status === "running" ? "已暂停" : STATUS_LABEL[node.status];
  return (
    <button
      type="button"
      className={`fc-node fc-node--${node.status}${selected ? " fc-node--selected" : ""}${paused ? " fc-node--paused" : ""}`}
      style={{
        left: position.x,
        top: position.y,
        width: CARD_WIDTH,
        height: CARD_HEIGHT,
      }}
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${node.label}，${node.name}，${statusLabel}，进度 ${Math.round(node.progress)}%`}
      title={`${node.name}\n${node.detail}`}
    >
      <span className="fc-node-topline">
        <span className="fc-node-number">
          {String(index + 1).padStart(2, "0")}
        </span>
        <span className="fc-node-status">
          <span className="fc-status-dot">
            {node.status === "success" ? (
              <Check size={9} strokeWidth={3} />
            ) : null}
          </span>
          {statusLabel}
        </span>
      </span>
      <span className="fc-node-label">{node.label}</span>
      <span className="fc-node-name">{node.name}</span>
      <span className="fc-node-meta">
        <span title={node.qubits}>{node.qubits}</span>
        <span>
          {node.attempt > 1
            ? `重试 ${node.attempt - 1}`
            : `尝试 ${node.attempt}`}
          <span className="fc-node-percent">{Math.round(node.progress)}%</span>
        </span>
      </span>
      <span className="fc-node-progress">
        <span
          style={{ width: `${Math.max(0, Math.min(100, node.progress))}%` }}
        />
      </span>
    </button>
  );
}

export default function FlowCanvas({
  workflow,
  selectedNodeId,
  onSelectNode,
}: FlowCanvasProps) {
  const [stage, setStage] = useState<Stage>(() =>
    workflow.experiment === "two" ? stageFor(selectedNodeId) : "single",
  );
  const [zoom, setZoom] = useState(1);
  const [fitToWidth, setFitToWidth] = useState(true);
  const viewportRef = useRef<HTMLDivElement>(null);
  const markerId = `fc-arrow-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const isTwo = workflow.experiment === "two";
  const visibleStage = isTwo ? stage : "single";
  const nodes = workflow.nodes.filter(
    (node) => (visibleStage === "single") === SINGLE_IDS.has(node.id),
  );
  const positions = visibleStage === "single" ? SINGLE_POSITIONS : CZ_POSITIONS;
  const connections =
    visibleStage === "single" ? SINGLE_CONNECTIONS : CZ_CONNECTIONS;
  const completed = nodes.filter((node) => node.status === "success").length;
  const frameHeight = Math.max(380, HEIGHT * zoom);

  useEffect(() => {
    setStage(
      isTwo && workflow.nodes.some((node) => node.id === selectedNodeId)
        ? stageFor(selectedNodeId)
        : "single",
    );
  }, [isTwo, workflow.id, selectedNodeId]);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element || !fitToWidth) return;
    const resize = () =>
      setZoom(
        Math.min(1, Math.max(MIN_ZOOM, (element.clientWidth - 32) / WIDTH)),
      );
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    return () => observer.disconnect();
  }, [fitToWidth]);

  function chooseStage(nextStage: Stage) {
    setStage(nextStage);
    const stageNodes = workflow.nodes.filter(
      (node) => (nextStage === "single") === SINGLE_IDS.has(node.id),
    );
    const nextNode =
      stageNodes.find(
        (node) => node.status === "attention" || node.status === "running",
      ) ?? stageNodes[0];
    if (nextNode) onSelectNode(nextNode.id);
    viewportRef.current?.scrollTo({ top: 0, left: 0 });
  }

  function changeZoom(delta: number) {
    setFitToWidth(false);
    setZoom((current) =>
      Math.min(
        1.6,
        Math.max(MIN_ZOOM, Math.round((current + delta) * 100) / 100),
      ),
    );
  }

  function fit() {
    const width = viewportRef.current?.clientWidth ?? WIDTH + 32;
    setZoom(Math.min(1, Math.max(MIN_ZOOM, (width - 32) / WIDTH)));
    setFitToWidth(true);
    viewportRef.current?.scrollTo({ top: 0, left: 0 });
  }

  return (
    <section className="fc-root" aria-label="实验工作流蓝图">
      <div className="fc-toolbar">
        <div className="fc-stage-control">
          {isTwo ? (
            <div className="fc-stage-tabs" aria-label="校准阶段">
              <button
                type="button"
                className={visibleStage === "single" ? "fc-stage-active" : ""}
                aria-pressed={visibleStage === "single"}
                onClick={() => chooseStage("single")}
              >
                <span>01</span>单比特前置
              </button>
              <ChevronRight size={13} />
              <button
                type="button"
                className={visibleStage === "cz" ? "fc-stage-active" : ""}
                aria-pressed={visibleStage === "cz"}
                onClick={() => chooseStage("cz")}
              >
                <span>02</span>CZ 校准
              </button>
            </div>
          ) : (
            <span className="fc-title">
              <GitBranch size={16} />
              单比特校准蓝图
            </span>
          )}
          <span className="fc-stage-count">
            <span>{completed}</span> / {nodes.length} 已完成
          </span>
        </div>
        <div className="fc-zoom-controls" aria-label="画布缩放">
          <button
            type="button"
            onClick={() => changeZoom(-0.1)}
            disabled={zoom <= MIN_ZOOM}
            title="缩小画布"
            aria-label="缩小画布"
          >
            <Minus size={15} />
          </button>
          <span aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button
            type="button"
            onClick={() => changeZoom(0.1)}
            disabled={zoom >= 1.6}
            title="放大画布"
            aria-label="放大画布"
          >
            <Plus size={15} />
          </button>
          <span className="fc-control-divider" />
          <button
            type="button"
            onClick={fit}
            title="适应画布宽度"
            aria-label="适应画布宽度"
          >
            <Expand size={15} />
          </button>
        </div>
      </div>
      <div
        className="fc-viewport"
        ref={viewportRef}
        tabIndex={0}
        aria-label="可滚动工作流画布"
      >
        <div
          className="fc-scale-space"
          style={{ width: WIDTH * zoom, height: frameHeight }}
        >
          <div
            className="fc-canvas"
            style={{
              width: WIDTH,
              height: HEIGHT,
              top: (frameHeight - HEIGHT * zoom) / 2,
              transform: `scale(${zoom})`,
            }}
          >
            {visibleStage === "single" ? (
              <>
                <div className="fc-group fc-group--foundation">
                  <span>01 — 频率与驱动标定</span>
                </div>
                <div className="fc-group fc-group--readout">
                  <span>02 — 读取优化与复测</span>
                </div>
                <span className="fc-branch-caption">03 — 质量验证</span>
              </>
            ) : (
              <>
                <div className="fc-group fc-group--cz-scan">
                  <span>01 — 频率依赖 · 交换扫描 · 耦合精调</span>
                </div>
                <div className="fc-group fc-group--cz-validate">
                  <span>02 — 失谐精调 · 基准评估 · 优化复测</span>
                </div>
                <span className="fc-parameter-note">CZ_width = 60 ns</span>
              </>
            )}
            <svg
              className="fc-connections"
              width={WIDTH}
              height={HEIGHT}
              viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
              aria-hidden="true"
            >
              <defs>
                <marker
                  id={markerId}
                  markerWidth="8"
                  markerHeight="8"
                  refX="7"
                  refY="4"
                  orient="auto"
                  markerUnits="userSpaceOnUse"
                >
                  <path d="M0 0 L8 4 L0 8 Z" fill="context-stroke" />
                </marker>
              </defs>
              {connections.map((connection) => {
                const source = nodes[connection.from];
                const destination = nodes[connection.to];
                if (!source || !destination) return null;
                const complete =
                  source.status === "success" &&
                  destination.status === "success";
                const active =
                  source.status === "success" &&
                  ["running", "attention"].includes(destination.status);
                return (
                  <path
                    key={`${connection.from}-${connection.to}`}
                    d={connection.path}
                    markerEnd={`url(#${markerId})`}
                    className={`fc-edge${connection.loop ? " fc-edge--loop" : ""}${complete ? " fc-edge--complete" : ""}${active ? " fc-edge--active" : ""}`}
                  />
                );
              })}
            </svg>
            <div
              className={`fc-loop-note${visibleStage === "cz" ? " fc-loop-note--cz" : ""}`}
            >
              <RotateCcw size={11} />
              优化后复测
            </div>
            {nodes.map((node, index) =>
              positions[index] ? (
                <NodeCard
                  key={node.id}
                  node={node}
                  position={positions[index]}
                  selected={node.id === selectedNodeId}
                  paused={workflow.status === "paused"}
                  index={workflow.nodes.findIndex(
                    (item) => item.id === node.id,
                  )}
                  onSelect={() => onSelectNode(node.id)}
                />
              ) : null,
            )}
          </div>
        </div>
      </div>
      <div className="fc-footer">
        <span>
          <span className="fc-legend-line" />
          执行依赖
          <span className="fc-legend-line fc-legend-line--dashed" />
          复测回环
        </span>
        <span>
          {visibleStage === "single"
            ? "末端三分支 · 演示按共享资源顺序调度"
            : "单比特校准完成后进入 CZ 阶段 · 演示数据"}
        </span>
      </div>
    </section>
  );
}
