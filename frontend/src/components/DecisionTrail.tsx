import {
  Check,
  Clock3,
  GitBranch,
  RotateCcw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import type { Workflow } from "../types";
import {
  buildDecisionPath,
  type DecisionPathItem,
} from "../lib/decisionTrail";
import { describeReplayItem } from "../lib/remoteInsights";
import type { WorkflowAudit, WorkflowReplay } from "../lib/remoteInsights";
import "./decision-trail.css";

interface DecisionTrailProps {
  workflow: Workflow;
  audit: WorkflowAudit | null;
  replay: WorkflowReplay | null;
  loading: boolean;
  error: string | null;
  replayBusy: boolean;
  replayError: string | null;
  onReplay: () => void;
  selectedNodeId: string;
  onSelectNode: (id: string) => void;
}

function timeOf(iso: string): string {
  return iso.length >= 19 ? iso.slice(11, 19) : iso;
}

function StepIcon({ tone }: { tone: DecisionPathItem["tone"] }) {
  if (tone === "pass") return <Check size={11} strokeWidth={2.4} />;
  if (tone === "fail") return <TriangleAlert size={11} />;
  return <Clock3 size={11} />;
}

export default function DecisionTrail({
  workflow,
  audit,
  replay,
  loading,
  error,
  replayBusy,
  replayError,
  onReplay,
  selectedNodeId,
  onSelectNode,
}: DecisionTrailProps) {
  const path = buildDecisionPath(audit);
  const nodeFor = (nodeId: string) =>
    workflow.nodes.find(
      (item) => item.id === nodeId || item.name === nodeId,
    );
  const drift =
    replay?.items.filter(
      (item) => item.changed || item.targetChanged === true,
    ) ?? [];

  return (
    <section className="dt-root" aria-label="服务端决策轨迹">
      <div className="dt-head">
        <div className="dt-title">
          <h3>
            <GitBranch size={14} />
            决策轨迹
          </h3>
          <small>服务端审计 · 只读 · 不改变冻结判定</small>
        </div>
        <div className="dt-actions">
          {audit && (
            <span className="dt-badge">
              Skill {audit.skill.version}
            </span>
          )}
          {audit?.device && (
            <span className="dt-badge">
              {audit.device.deviceId} · {audit.device.adapter}
            </span>
          )}
          {audit && (
            <span className="dt-badge">
              {audit.autonomy.level} {audit.autonomy.name}
            </span>
          )}
          <button
            type="button"
            className="dt-replay-button"
            onClick={onReplay}
            disabled={replayBusy}
            title="用当前 Skill 重新判定已记录测量（只读）"
          >
            <RotateCcw size={13} />
            {replayBusy ? "重放中…" : "重放校验"}
          </button>
        </div>
      </div>

      {loading && !audit && (
        <p className="dt-note">正在读取服务端审计…</p>
      )}
      {error && (
        <p className="dt-error" role="alert">
          {error}
        </p>
      )}

      {audit && (
        <>
          <p className="dt-counts">
            <span>测量 {audit.counts.results}</span>
            <span>命令 {audit.counts.actions}</span>
            <span>事件 {audit.counts.events}</span>
            <span
              className={
                audit.counts.escalations ? "dt-count-warning" : undefined
              }
            >
              升级 {audit.counts.escalations ?? 0}
            </span>
            {replay && (
              <span
                className={
                  replay.summary.changed || replay.summary.targetChanged
                    ? "dt-count-warning"
                    : undefined
                }
              >
                重放漂移 {replay.summary.changed}/{replay.summary.total}
                {replay.summary.targetChanged
                  ? ` · 目标漂移 ${replay.summary.targetChanged}`
                  : ""}
              </span>
            )}
            <span className="dt-counts-hint">
              显示最近 {Math.min(14, path.length)} 条测量与命令
            </span>
          </p>

          {path.length ? (
            <ol className="dt-path">
              {path.map((item) => {
                const node = item.nodeId ? nodeFor(item.nodeId) : undefined;
                const body = (
                  <>
                    <span className={`dt-step-icon dt-step-icon--${item.tone}`}>
                      <StepIcon tone={item.tone} />
                    </span>
                    <span className="dt-step-main">
                      <span className="dt-step-top">
                        <strong>
                          {node ? node.label : item.nodeId || "工作流"}
                        </strong>
                        <span
                          className={`dt-step-label dt-step-label--${item.tone}`}
                        >
                          {item.label}
                        </span>
                        <time>{timeOf(item.time)}</time>
                      </span>
                      <span className="dt-step-detail">{item.detail}</span>
                    </span>
                  </>
                );
                return (
                  <li
                    key={item.key}
                    className={`dt-step dt-step--${item.tone}`}
                  >
                    {node ? (
                      <button
                        type="button"
                        className={`dt-step-button${node.id === selectedNodeId ? " dt-step-selected" : ""}`}
                        onClick={() => onSelectNode(node.id)}
                        aria-pressed={node.id === selectedNodeId}
                        title={`在蓝图中查看 ${node.name}`}
                      >
                        {body}
                      </button>
                    ) : (
                      <span className="dt-step-button dt-step-static">
                        {body}
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className="dt-note">
              该任务还没有测量或命令记录。运行后，验收判定与人工决策会出现在这里。
            </p>
          )}
        </>
      )}

      {replayError && (
        <p className="dt-error" role="alert">
          {replayError}
        </p>
      )}

      {replay && (
        <div className="dt-replay">
          <span className="dt-replay-title">
            <ShieldCheck size={13} />
            重放校验 · Skill {replay.skill.version}
          </span>
          {drift.length ? (
            <ul>
              {drift.map((item) => (
                <li key={`${item.nodeId}-${item.attempt}`}>
                  <TriangleAlert size={12} />
                  <span>{describeReplayItem(item).detail}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="dt-note">
              共重放 {replay.summary.total} 条测量，当前 Skill
              与冻结判定一致，未发现判定或设计目标漂移。
            </p>
          )}
        </div>
      )}
    </section>
  );
}
