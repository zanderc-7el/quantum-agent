import { useEffect, useRef, useState } from "react";
import { Check, Pencil, Save, X } from "lucide-react";
import type { ExperimentNode, ParameterBounds, Workflow } from "../types";
import type { RemoteGateway } from "../lib/remoteGateway";
import { buildManualParameterFields, displayParameterValue, validateManualParameterDraft } from "../lib/manualParameters";

export default function NodeParameterEditor({ gateway, workflow, node, bounds, parameters, canEdit, busy, onBusy, onDirtyChange }: {
  gateway: RemoteGateway; workflow: Workflow; node: ExperimentNode; bounds?: ParameterBounds; parameters?: Record<string, unknown>;
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
  const saving = useRef(false);
  const actual = parameters ?? node.parameters ?? {};
  const fields = buildManualParameterFields(bounds, node.editableParameterKeys);
  useEffect(() => { setEditing(false); setValues({}); setError(""); setNotice(""); }, [workflow.id, node.id]);
  const begin = () => {
    const displayed = Object.fromEntries(fields.map(field => [field.key, displayParameterValue(actual[field.key], field)]));
    setValues(displayed); setInitialValues(displayed);
    setBaseline({ ...actual }); setRevision(workflow.revision ?? 0); setError(""); setNotice(""); setEditing(true);
  };
  const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== initialValues[key]));
  const dirty = editing && Object.keys(changed).length > 0;
  useEffect(() => { onDirtyChange?.(dirty); return () => onDirtyChange?.(false); }, [dirty, onDirtyChange]);
  const stale = editing && revision !== (workflow.revision ?? 0);
  const save = async () => {
    if (saving.current || busy || !canEdit || stale || !bounds) return;
    setError("");
    try {
      const next = validateManualParameterDraft({ revision, fields, baseline, initialValues, values }, workflow.revision ?? 0);
      saving.current = true; onBusy(true);
      await gateway.parameters(workflow.id, node.id, revision, next);
      setEditing(false); setNotice("参数已保存，下次运行生效。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "参数保存失败"); }
    finally { if (saving.current) { saving.current = false; onBusy(false); } }
  };
  return <section className="rp-parameter-editor" aria-label="节点参数编辑">
    {!editing ? <><button className="secondary" disabled={!canEdit || busy || !fields.length} onClick={begin}><Pencil size={13} />编辑本节点参数</button>{!canEdit && <p className="rp-muted">当前状态不可编辑</p>}{canEdit && !fields.length && <p className="rp-muted">此节点无可编辑参数</p>}</> : <>
      <div className="rp-info">保存后，本节点及下游结果待复测；下次运行生效。</div>
      <div className="rp-parameter-fields">{fields.map(field => { const { key, rule } = field; const change = (value: string) => { setValues(current => ({ ...current, [key]: value })); setError(""); }; return <label key={key}><span>{field.label}{field.unit ? ` (${field.unit})` : ""}</span>{rule.enum ? <select aria-label={`参数 ${key}`} value={values[key] ?? ""} disabled={busy || !canEdit} onChange={event => change(event.target.value)}><option value="" disabled>选择参数值</option>{rule.enum.map(value => { const display = displayParameterValue(value, field); return <option key={value} value={display}>{display}</option>; })}</select> : <input aria-label={`参数 ${key}`} inputMode="decimal" value={values[key] ?? ""} placeholder="未设置" disabled={busy || !canEdit} onChange={event => change(event.target.value)} />}<small>{field.min} 至 {field.max}{field.unit ? ` ${field.unit}` : ""}{rule.integer ? " · 整数" : ""}</small></label>; })}</div>
      {stale && <p className="rp-error" role="alert">参数已更新，请取消后重新编辑。</p>}
      <div className="rp-action-row"><button className="secondary" disabled={busy} onClick={() => { setEditing(false); setError(""); }}><X size={13} />取消</button><button className="primary" disabled={busy || !canEdit || stale || !Object.keys(changed).length} onClick={() => void save()}><Save size={13} />{saving.current ? "正在保存…" : "保存参数"}</button></div>
    </>}
    {error && <p className="rp-error" role="alert">{error}</p>}
    {notice && <p className="rp-parameter-saved" role="status"><Check size={13} />{notice}</p>}
  </section>;
}
