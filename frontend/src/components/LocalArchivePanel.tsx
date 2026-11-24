import { useEffect, useState } from "react";
import { Database } from "lucide-react";
import type { ArchiveItem, ArchiveStatus, RemoteGateway } from "../lib/remoteGateway";

export default function LocalArchivePanel({ gateway, workflowId }: { gateway: RemoteGateway; workflowId?: string }) {
  const [status, setStatus] = useState<ArchiveStatus>();
  const [records, setRecords] = useState<ArchiveItem[]>();
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    let cancelled = false; let timer: ReturnType<typeof setTimeout>;
    const load = async () => { try { const value = await gateway.archiveStatus(); if (!cancelled) { setStatus(value); setError(""); } } catch (cause) { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); } finally { if (!cancelled) timer = setTimeout(load, 15000); } };
    void load(); return () => { cancelled = true; clearTimeout(timer); };
  }, [gateway]);
  useEffect(() => {
    if (!expanded) return;
    let cancelled = false; setRecords(undefined);
    void gateway.archiveList(workflowId).then(value => { if (!cancelled) setRecords(value.items); }).catch(cause => { if (!cancelled) setError(cause.message); });
    return () => { cancelled = true; };
  }, [gateway, expanded, workflowId, status?.local.recordCount]);
  return <details className="local-archive-panel" onToggle={event => setExpanded(event.currentTarget.open)}><summary><Database size={15} /><strong>本地实验数据库</strong><span>{status ? `已存 ${status.local.recordCount} 条记录` : "正在读取…"}</span><small>{status ? status.cloud.configured ? "云端连接已配置" : "云端尚未配置" : "正在读取存储状态"}</small></summary><div>{status?.cloud.message && <p>{status.cloud.message}</p>}{status?.local.latestArchivedAt && <p>最近保存：{new Date(status.local.latestArchivedAt).toLocaleString("zh-CN")}</p>}{error && <p role="alert" className="rp-error">{error}</p>}{records ? records.length ? <table><thead><tr><th>实验记录</th><th>结果</th><th>时间</th></tr></thead><tbody>{records.map(item => <tr key={item.id}><td>{item.nodeLabel ?? item.workflowName ?? "报告"}<small>{item.mode === "replay" ? "历史回放" : "实测"} · {item.kind === "report" ? "报告" : item.kind === "attempt" ? "尝试记录" : item.role === "verification" ? "独立复测" : "采集"}</small></td><td>{({ passed: "当次质量通过", failed: "质量未通过", unverified: "待验证" })[item.qualityStatus ?? "unverified"] ?? "已归档"}</td><td>{new Date(item.acquiredAt ?? item.createdAt).toLocaleString("zh-CN")}</td></tr>)}</tbody></table> : <p>暂无归档记录</p> : expanded && <p>正在读取记录…</p>}<small>显示最近30条{workflowId ? "本任务" : ""}归档记录</small></div></details>;
}
