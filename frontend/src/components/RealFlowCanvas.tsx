import { abortedNodePresentation } from "../lib/abortedNodePresentation";
import { useId, useState } from "react";
import { GitBranch, Minus, Plus } from "lucide-react";
import type { ExperimentNode, Workflow } from "../types";
import { buildFlowGeometry } from "../lib/flowGeometry";
import { staleNodeLabel } from "../lib/workflowResources";
import "./flow-canvas.css";

const states: Record<string, string> = { success: "已执行", pending: "待执行", running: "运行中", attention: "待决策", abandoned: "已终止" };
function stateLabel(node: ExperimentNode, workflow: Workflow) {
  return abortedNodePresentation(workflow, node)?.label ?? (node.engineState === "historical" ? "历史已载入"
    : workflow.engineState === "replay_completed" && node.attempt === 0 ? "无历史记录"
      : node.engineState === "stale" ? staleNodeLabel(node) : states[node.status]);
}

export default function RealFlowCanvas({ workflow, selectedNodeId, onSelectNode }: {
  workflow: Workflow;
  selectedNodeId: string;
  onSelectNode: (id: string) => void;
}) {
  const [zoom, setZoom] = useState(0.8);
  const marker = useId().replace(/:/g, "");
  const dependencies = workflow.nodes.flatMap(node => (node.dependsOn ?? []).map(from => ({ from, to: node.id })));
  const explicitBacktracks = (workflow.edges ?? []).filter(edge => edge.kind === "rollback" || edge.kind === "backtrack");
  const geometry = buildFlowGeometry(workflow.nodes, [...dependencies, ...explicitBacktracks]);
  const nodesById = new Map(workflow.nodes.map(node => [node.id, node]));
  const showActive = ["running", "attention", "paused"].includes(workflow.status);
  return <section className="fc-root rp-dynamic-flow" aria-label="实际实验工作流蓝图">
    <div className="fc-toolbar">
      <div className="fc-stage-control">
        <span className="fc-title"><GitBranch size={16} />实验流程与依赖</span>
        <span className="fc-stage-count">{workflow.nodes.length} 个节点 · 点击查看详情</span>
      </div>
      <div className="fc-zoom-controls">
        <button onClick={() => setZoom(value => Math.max(0.4, value - 0.1))} aria-label="缩小蓝图"><Minus size={13} /></button>
        <span>{Math.round(zoom * 100)}%</span>
        <button onClick={() => setZoom(value => Math.min(1.4, value + 0.1))} aria-label="放大蓝图"><Plus size={13} /></button>
      </div>
    </div>
    <div className="fc-viewport" tabIndex={0}>
      <div className="fc-scale-space" style={{ width: geometry.width * zoom, height: geometry.height * zoom }}>
        <div className="fc-canvas" style={{ width: geometry.width, height: geometry.height, transform: `scale(${zoom})` }}>
          <svg className="fc-connections" width={geometry.width} height={geometry.height} aria-hidden="true">
            <defs><marker id={marker} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="context-stroke" /></marker></defs>
            {geometry.routes.map((route, index) => {
              const active = showActive && route.edge.to === workflow.currentNodeId;
              const label = (route.label ?? "").length > 25 ? route.label!.slice(0, 24) + "…" : route.label;
              const labelWidth = [...label ?? ""].reduce((sum, char) => sum + (char.charCodeAt(0) > 127 ? 9 : 5.4), 12);
              return <g key={`${route.edge.from}-${route.edge.to}-${index}`}>
                <path d={route.path} className={`fc-edge${route.rollback ? " fc-edge--loop" : ""}${active ? " fc-edge--active" : ""}`} markerEnd={`url(#${marker})`}>
                  <title>{route.edge.from} → {route.edge.to} · {route.rollback ? "回退" : "依赖"}</title>
                </path>
                {route.labelPoint && label && <g className={`rp-edge-label${route.rollback ? " rollback" : ""}`} transform={`translate(${route.labelPoint.x},${route.labelPoint.y})`}>
                  <rect x={-labelWidth / 2} y={-10} width={labelWidth} height={15} rx={3} />
                  <text textAnchor="middle" y={1}>{label}</text>
                </g>}
              </g>;
            })}
          </svg>
          {geometry.cards.map((card, index) => {
            const node = nodesById.get(card.id)!;
            const status = abortedNodePresentation(workflow, node)?.status ?? node.status;
            return <button key={node.id}
              className={`rp-flow-node ${status}${node.engineState === "historical" ? " historical" : ""}${node.id === selectedNodeId ? " selected" : ""}${node.engineState === "stale" ? " stale" : ""}`}
              style={{ left: card.x, top: card.y, width: card.width, height: card.height }}
              onClick={() => onSelectNode(node.id)} aria-pressed={node.id === selectedNodeId}>
              <span className="rp-flow-kicker"><span>{String(index + 1).padStart(2, "0")} · {node.ownerId || workflow.target}</span><i className={`dot dot-${node.engineState === "historical" ? "historical" : status}`} /></span>
              <strong title={node.label}>{node.label}</strong>
              <span className="rp-flow-foot"><small>{stateLabel(node, workflow)}</small><small>第 {node.attempt || 0} 轮</small></span>
              {workflow.status !== "aborted" && node.status === "running" && <div className="rp-flow-progress"><i style={{ width: `${node.progress}%` }} /></div>}
            </button>;
          })}
        </div>
      </div>
    </div>
    <div className="rp-flow-legend">
      <span><i className="dot dot-running" />运行中</span><span><i className="dot dot-success" />已验证</span>
      <span><i className="dot dot-historical" />历史已载入</span><span><i className="dot dot-attention" />待决策</span>
      <span>实线箭头：依赖 · 虚线箭头：回退</span>
    </div>
  </section>;
}
