import type { Catalog, CreateWorkflowInput, Workflow, WorkflowCoverage } from "../types";
import { coverageDescription, defaultCoverage, workingPointScope, workingPointUnavailableReason } from "../lib/workflowCoverage";
import "./workflow-coverage.css";

export function WorkflowCoverageSelect({ catalog, form, onChange }: {
  catalog?: Catalog; form: CreateWorkflowInput; onChange: (coverage: WorkflowCoverage) => void;
}) {
  if (form.mode !== "real") return null;
  const reason = workingPointUnavailableReason(catalog, form);
  const coverage = form.coverage ?? defaultCoverage(form.mode);
  return <div className="rp-coverage-choice">
    <label>测量范围<select aria-label="测量范围" value={coverage} onChange={event => onChange(event.target.value as WorkflowCoverage)}>
      <option value="full">完整测量 · 主步骤与扩展采集</option>
      <option value="main">主步骤 · 原流程八步</option>
      <option value="working_point" disabled={Boolean(reason)}>复刻已有工作点 · 从 Rabi 开始</option>
    </select></label>
    <p>{coverageDescription(coverage)}</p>
    {reason && <p className="rp-muted" role={coverage === "working_point" ? "alert" : undefined}>{reason}</p>}
  </div>;
}

export function WorkingPointScope({ workflow }: { workflow: Workflow }) {
  const display = workingPointScope(workflow);
  if (!display) return null;
  const seed = display.source;
  return <section className="rp-working-point rp-info" aria-label="本次测量范围与参考点">
    <strong>{display.title}</strong>
    <p>{display.scope}</p>
    <p className="rp-working-point-seed">{display.seed}</p>
    {seed ? <p>参考点来源：{seed.sourceId}</p> : <p>参考点来源记录尚未载入。</p>}
    {display.procedures.length > 0 && <p>本次测项（执行顺序见蓝图）：{display.procedures.join("、")}</p>}
    {seed && <details className="rp-provenance">
      <summary>查看参考点来源与初值</summary>
      <p>{seed.meaning}</p>
      <pre>{JSON.stringify({ status: seed.status, sourceId: seed.sourceId, parameters: seed.parameters, sourceFiles: seed.sourceFiles, sourceReport: seed.sourceReport, completionScope: workflow.completionScope }, null, 2)}</pre>
    </details>}
  </section>;
}
