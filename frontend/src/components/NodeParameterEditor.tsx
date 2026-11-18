import { useEffect, useRef, useState } from "react";
import { Check, Pencil, Save, X } from "lucide-react";
import type { ExperimentNode, ParameterBounds, Workflow } from "../types";
import type { RemoteGateway } from "../lib/remoteGateway";
import { numericParameterBounds, validateParameterChanges } from "../lib/parameters";

export default function NodeParameterEditor({ gateway, workflow, node, bounds, parameters, canEdit, busy, onBusy }: {
  gateway: RemoteGateway; workflow: Workflow; node: ExperimentNode; bounds?: ParameterBounds; parameters?: Record<string, unknown>;
  canEdit: boolean; busy: boolean; onBusy: (value: boolean) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [baseline, setBaseline] = useState<Record<string, unknown>>({});
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const saving = useRef(false);
  const actual = parameters ?? node.parameters ?? {};
  const editableBounds = numericParameterBounds(bounds);
  const keys = Object.keys(editableBounds).filter(key => !node.editableParameterKeys || node.editableParameterKeys.includes(key));
  useEffect(() => { setEditing(false); setValues({}); setError(""); setNotice(""); }, [workflow.id, node.id]);
  const begin = () => {
    setValues(Object.fromEntries(keys.map(key => [key, typeof actual[key] === "number" && Number.isFinite(actual[key]) ? String(actual[key]) : ""])));
    setBaseline({ ...actual }); setRevision(workflow.revision ?? 0); setError(""); setNotice(""); setEditing(true);
  };
  const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== (typeof baseline[key] === "number" ? String(baseline[key]) : "")));
  const stale = editing && revision !== (workflow.revision ?? 0);
  const save = async () => {
    if (saving.current || busy || !canEdit || stale || !bounds) return;
    setError("");
    try {
      const next = validateParameterChanges(changed, editableBounds, keys);
      saving.current = true; onBusy(true);
      await gateway.parameters(workflow.id, node.id, revision, next);
      setEditing(false); setNotice("参数已保存。开始或继续运行后才会采集。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "参数保存失败"); }
    finally { if (saving.current) { saving.current = false; onBusy(false); } }
  };
  return <section className="rp-parameter-editor" aria-label="节点参数编辑">
    {!editing ? <><button className="secondary" disabled={!canEdit || busy || !keys.length} onClick={begin}><Pencil size={13} />编辑本节点参数</button>{!canEdit && <p className="rp-muted">草稿、暂停或等待处理时可编辑；运行中请先暂停到检查点。</p>}{canEdit && !keys.length && <p className="rp-muted">后端尚未提供本节点可编辑参数范围。</p>}</> : <>
      <div className="rp-info">保存仅更新“{node.label}”的下次执行参数，不会立即采集。本节点及下游依赖将失效，原始结果保留在执行历史。</div>
      <div className="rp-parameter-fields">{keys.map(key => { const rule = editableBounds[key]; return <label key={key}><span>{rule.label ?? key}{rule.unit ? ` (${rule.unit})` : ""}</span>{rule.enum ? <select aria-label={`参数 ${key}`} value={values[key] ?? ""} disabled={busy || !canEdit} onChange={event => setValues(current => ({ ...current, [key]: event.target.value }))}><option value="" disabled>选择参数值</option>{rule.enum.map(value => <option key={value} value={value}>{value}</option>)}</select> : <input aria-label={`参数 ${key}`} inputMode="decimal" value={values[key] ?? ""} placeholder="未设置" disabled={busy || !canEdit} onChange={event => setValues(current => ({ ...current, [key]: event.target.value }))} />}<small>{rule.min} 至 {rule.max}{rule.integer ? " · 整数" : ""}</small></label>; })}</div>
      {stale && <p className="rp-error" role="alert">任务版本已更新。取消后重新打开编辑，以免覆盖新的参数。</p>}
      <div className="rp-action-row"><button className="secondary" disabled={busy} onClick={() => { setEditing(false); setError(""); }}><X size={13} />取消</button><button className="primary" disabled={busy || !canEdit || stale || !Object.keys(changed).length} onClick={() => void save()}><Save size={13} />{saving.current ? "正在保存…" : "保存参数"}</button></div>
    </>}
    {error && <p className="rp-error" role="alert">{error}</p>}
    {notice && <p className="rp-parameter-saved" role="status"><Check size={13} />{notice}</p>}
  </section>;
}
