import { useEffect, useId, useRef, useState } from "react";
import { Check, Pencil, Save, X } from "lucide-react";
import type { ExperimentNode, ParameterBounds, Workflow } from "../types";
import type { RemoteGateway } from "../lib/remoteGateway";
import { buildManualParameterFields, displayParameterValue, validateManualParameterDraft } from "../lib/manualParameters";
import { manualPreviewChanges } from "../lib/manualPulsePreview";
import ManualPulsePreview from "./ManualPulsePreview";

export default function NodeParameterEditor({ gateway, workflow, node, bounds, parameters, detailRevision, canEdit, busy, onBusy, onDirtyChange }: {
  gateway: RemoteGateway; workflow: Workflow; node: ExperimentNode; bounds?: ParameterBounds; parameters?: Record<string, unknown>; detailRevision?: number;
  canEdit: boolean; busy: boolean; onBusy: (value: boolean) => void;
  onDirtyChange?: (value: boolean) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [baseline, setBaseline] = useState<Record<string, unknown>>({});
  const [initialValues, setInitialValues] = useState<Record<string, string>>({});
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editKey, setEditKey] = useState("");
  const [previewSnapshot, setPreviewSnapshot] = useState<{ key: string; revision: number; request: number }>();
  const saving = useRef(false);
  const formId = useId();
  const identity = JSON.stringify([workflow.id, node.id]);
  const selectedIdentity = useRef(identity);
  selectedIdentity.current = identity;
  const currentRevision = workflow.revision ?? 0;
  const activeEditing = editing && editKey === identity;
  const snapshotCurrent = !!bounds && !!parameters && detailRevision === currentRevision;
  const actual = parameters ?? {};
  const fields = buildManualParameterFields(bounds, node.editableParameterKeys);
  useEffect(() => { setEditing(false); setValues({}); setError(""); setNotice(""); setPreviewSnapshot(undefined); }, [identity]);
  const begin = () => {
    if (busy || saving.current || !canEdit || !snapshotCurrent || !fields.length) return;
    const displayed = Object.fromEntries(fields.map(field => [field.key, displayParameterValue(actual[field.key], field)]));
    setValues(displayed); setInitialValues(displayed);
    setBaseline({ ...actual }); setRevision(currentRevision); setEditKey(identity); setError(""); setNotice(""); setEditing(true);
  };
  const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== initialValues[key]));
  const dirty = activeEditing && Object.keys(changed).length > 0;
  useEffect(() => { onDirtyChange?.(dirty); return () => onDirtyChange?.(false); }, [dirty, onDirtyChange]);
  const stale = activeEditing && revision !== currentRevision;
  const previewOpen = previewSnapshot?.key === identity;
  // A read-only view deliberately keeps its opening revision through SSE progress.
  // Editing instead uses the same revision-bound draft and parser as saving.
  let previewChanges: Record<string, number | number[]> = {}, previewProblem = "";
  if (activeEditing) {
    try {
      previewChanges = manualPreviewChanges({ revision, fields, baseline, initialValues, values }, currentRevision,
        !canEdit ? "当前状态不可编辑；取消草稿后可查看已保存参数时序。" : !snapshotCurrent && !stale ? "正在读取当前配置，暂不能预览草稿。" : "");
    } catch (cause) { previewProblem = cause instanceof Error ? cause.message : "草稿参数无效"; }
  }
  const refreshPreview = () => setPreviewSnapshot(current => ({ key: identity, revision: currentRevision, request: (current?.request ?? 0) + 1 }));
  const save = async () => {
    if (saving.current || busy || !canEdit || stale || !snapshotCurrent || !activeEditing) return;
    setError("");
    try {
      const next = validateManualParameterDraft({ revision, fields, baseline, initialValues, values }, currentRevision);
      saving.current = true; onBusy(true);
      await gateway.parameters(workflow.id, node.id, revision, next);
      if (selectedIdentity.current === identity) { setEditing(false); setPreviewSnapshot(undefined); setNotice("参数已保存，下次运行生效。"); }
    } catch (cause) { if (selectedIdentity.current === identity) setError(cause instanceof Error ? cause.message : "参数保存失败"); }
    finally { if (saving.current) { saving.current = false; onBusy(false); } }
  };
  return <section className="rp-parameter-editor" aria-label="节点参数编辑">
    {!activeEditing ? <><button className="secondary" disabled={!canEdit || busy || !fields.length || !snapshotCurrent} onClick={begin}><Pencil size={13} />编辑本节点参数</button>{!canEdit && <p className="rp-muted">当前状态不可编辑，可按需查看已保存参数时序。</p>}{canEdit && !snapshotCurrent && <p className="rp-muted">正在读取当前版本配置，暂不能编辑。</p>}{canEdit && snapshotCurrent && !fields.length && <p className="rp-muted">此节点无可编辑参数</p>}</> : <>
      <div className="rp-info">保存后，本节点及下游结果待复测；下次运行生效。</div>
      <div className="rp-parameter-fields">{fields.map(field => { const { key, rule } = field; const change = (value: string) => { setValues(current => ({ ...current, [key]: value })); setError(""); }; return <label key={key}><span>{field.label}{field.unit ? ` (${field.unit})` : ""}</span>{rule.enum ? <select id={`${formId}-${key}`} aria-label={`参数 ${key}`} value={values[key] ?? ""} disabled={busy || !canEdit || stale} onChange={event => change(event.target.value)}><option value="" disabled>选择参数值</option>{rule.enum.map(value => { const display = displayParameterValue(value, field); return <option key={value} value={display}>{display}</option>; })}</select> : <input id={`${formId}-${key}`} aria-label={`参数 ${key}`} inputMode="decimal" value={values[key] ?? ""} placeholder="未设置" disabled={busy || !canEdit || stale} onChange={event => change(event.target.value)} />}<small>{field.min} 至 {field.max}{field.unit ? ` ${field.unit}` : ""}{rule.integer ? " · 整数" : ""}</small></label>; })}</div>
      {stale && <p className="rp-error" role="alert">参数已更新，请取消后重新编辑。</p>}
      <div className="rp-action-row"><button className="secondary" disabled={busy} onClick={() => { setEditing(false); setPreviewSnapshot(undefined); setError(""); }}><X size={13} />取消</button><button className="primary" disabled={busy || !canEdit || stale || !snapshotCurrent || !Object.keys(changed).length} onClick={() => void save()}><Save size={13} />{saving.current ? "正在保存…" : "保存参数"}</button></div>
    </>}
    <div className="rp-action-row"><button className="secondary" type="button" aria-expanded={previewOpen} onClick={() => previewOpen ? setPreviewSnapshot(undefined) : refreshPreview()}>{previewOpen ? "收起参数时序" : "展开参数时序"}</button>{previewOpen && !activeEditing && <button className="secondary" type="button" onClick={refreshPreview}>刷新参数时序</button>}</div>
    {previewOpen && <>
      {!activeEditing && <p className="rp-muted">显示快照版本 {previewSnapshot.revision}；当前版本 {currentRevision}。进度更新不会自动刷新时序；需要最新配置时请刷新。此图是参数执行计划，不是本轮实测波形。</p>}
      <ManualPulsePreview key={`${identity}:${previewSnapshot.request}`} gateway={gateway} workflowId={workflow.id} nodeId={node.id} resourceId={node.resourceId ?? workflow.target} procedureId={node.procedureId ?? node.name}
        revision={activeEditing ? revision : previewSnapshot.revision} changes={previewChanges} fields={activeEditing ? fields : []} problem={previewProblem} dirty={dirty}
        onFocusField={key => { const input = document.getElementById(`${formId}-${key}`); input?.focus(); input?.scrollIntoView({ block: "nearest" }); }} />
    </>}
    {editKey === identity && error && <p className="rp-error" role="alert">{error}</p>}
    {editKey === identity && notice && <p className="rp-parameter-saved" role="status"><Check size={13} />{notice}</p>}
  </section>;
}
