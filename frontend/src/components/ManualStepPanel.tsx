import { Check, Play, RotateCcw } from "lucide-react";
import type { ExperimentNode, Policy, RemoteAction, Workflow } from "../types";
import { isManualWorkflow, manualStepControls } from "../lib/workflowControls";
import "./manual-steps.css";

export function ExecutionModeSelector({ policy, onChange, disabled = false }: { policy: Policy; onChange: (policy: Policy) => void; disabled?: boolean }) {
  return <div className="rp-execution-mode">
    <div role="group" aria-label="执行方式" className="rp-mode-options">
      <button type="button" aria-pressed={policy === "strict"} disabled={disabled} onClick={() => onChange("strict")}>人工逐步</button>
      <button type="button" aria-pressed={policy !== "strict"} disabled={disabled} onClick={() => onChange("autopilot")}>智能运行</button>
    </div>
    {policy !== "strict" && <select aria-label="智能运行策略" disabled={disabled} value={policy} onChange={event => onChange(event.target.value as Policy)}><option value="autopilot">智能运行</option><option value="threshold">异常时决策</option></select>}
  </div>;
}

export default function ManualStepPanel({ workflow, node, onSelect, onAction, busy, parameterDirty }: {
  workflow: Workflow; node: ExperimentNode; onSelect: (nodeId: string) => void;
  onAction: (action: RemoteAction, nodeId?: string) => Promise<void>; busy: boolean; parameterDirty: boolean;
}) {
  if (!isManualWorkflow(workflow)) return null;
  const controls = manualStepControls(workflow, node);
  const number = workflow.nodes.findIndex(item => item.id === node.id) + 1;
  return <section className="rp-manual-steps" aria-label="人工逐步操作">
    <header><div><span className="rp-section-kicker">人工逐步</span><h2>第 {number} 步 · {node.label}</h2></div><div className="rp-action-row">
      {controls.adjust && <button className="secondary" disabled={busy || parameterDirty} onClick={() => void onAction("backtrack", node.id)}><RotateCcw size={15} />重新调整</button>}
      {controls.approve ? <button className="primary" disabled={busy || parameterDirty} onClick={() => void onAction("approve", node.id)}><Check size={15} />确认结果</button> : <button className="primary" disabled={busy || parameterDirty || !controls.run} onClick={() => void onAction("run_step", node.id)}>{node.attempt > 0 ? <RotateCcw size={15} /> : <Play size={15} />}{controls.runLabel}</button>}
    </div></header>
    {(parameterDirty || (controls.reason && !controls.approve)) && <p role="status" className="rp-step-reason">{parameterDirty ? "请先保存或取消参数修改" : controls.reason}</p>}
    <ol aria-label="测量步骤">{workflow.nodes.map((item, index) => {
      const step = manualStepControls(workflow, item);
      return <li key={item.id}><button type="button" aria-current={item.id === node.id ? "step" : undefined} className={item.id === node.id ? "selected" : ""} disabled={parameterDirty && item.id !== node.id} onClick={() => onSelect(item.id)}><span className="rp-step-number">{index + 1}</span><span className="rp-step-name">{item.label}</span><small>{step.label}</small></button></li>;
    })}</ol>
  </section>;
}
