import { useEffect, useRef, useState } from "react";
import { Download, FileChartColumn } from "lucide-react";
import type { Workflow } from "../types";
import type { RemoteGateway } from "../lib/remoteGateway";
import { ApiError } from "../lib/remoteGateway";
import { exportSessionKey, parseExportSession, recoverExportSession, reportDownloadUrl } from "../lib/reportExportSession";
import type { ExportSession } from "../lib/reportExportSession";

export default function ReportExportButton({ gateway, workflow }: { gateway: RemoteGateway; workflow: Workflow }) {
  const key = exportSessionKey(workflow.id);
  const [session, setSession] = useState<ExportSession | undefined>(() => { try { return parseExportSession(localStorage.getItem(key), workflow.id); } catch { return; } });
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const inFlight = useRef(false), alive = useRef(true);
  const currentSession = useRef(session);
  currentSession.current = session;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const save = (next: ExportSession) => { if (!alive.current) return; currentSession.current = next; setSession(next); try { localStorage.setItem(key, JSON.stringify(next)); } catch { setError("导出任务已提交，但本地界面记录保存失败，请等待完成后下载。"); } };
  useEffect(() => {
    let cancelled = false; let timer: ReturnType<typeof setTimeout>;
    const recover = async () => {
      try {
        const jobs = await gateway.reportExports(workflow.id);
        if (!cancelled && !inFlight.current) {
          const next = recoverExportSession(currentSession.current, jobs, workflow.id);
          if (next && next !== currentSession.current) save(next);
        }
      } catch { /* A failed read never replaces the saved request or sends it again. */ }
      finally { if (!cancelled) timer = setTimeout(recover, 15000); }
    };
    void recover(); return () => { cancelled = true; clearTimeout(timer); };
  }, [gateway, workflow.id]);
  const active = session?.job && ["queued", "processing"].includes(session.job.status);
  useEffect(() => {
    if (!active || !session?.job) return;
    let cancelled = false; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { const job = await gateway.reportExport(session.job!.id); if (cancelled) return; save({ ...session, job }); setError(""); if (["queued", "processing"].includes(job.status)) timer = setTimeout(poll, 2000); }
      catch (cause) { if (cancelled) return; setError(cause instanceof Error ? cause.message : String(cause)); timer = setTimeout(poll, 5000); }
    };
    void poll(); return () => { cancelled = true; clearTimeout(timer); };
  }, [gateway, session?.job?.id, Boolean(active)]);
  const create = async () => {
    if (inFlight.current || active) return;
    const pending: ExportSession = session && !session.job ? session : { workflowId: workflow.id, request: { operationId: crypto.randomUUID(), expectedRevision: workflow.revision ?? 0, format: "pptx", appendixCount: 16 } };
    inFlight.current = true; setSending(true); setError(""); save(pending);
    try { const job = await gateway.createReportExport(workflow.id, pending.request); save({ ...pending, job }); }
    catch (cause) {
      if (!alive.current) return;
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 408) { setSession(undefined); try { localStorage.removeItem(key); } catch { /* message below remains visible */ } }
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { inFlight.current = false; if (alive.current) setSending(false); }
  };
  const url = reportDownloadUrl(session?.job);
  return <div className="report-export-control"><div>{url && <a className="secondary rp-link-button" href={url}><Download size={14} />下载 PPT</a>}<button className="secondary" disabled={sending || Boolean(active)} onClick={() => void create()}><FileChartColumn size={15} />{sending ? "正在提交…" : active ? "正在生成 PPT…" : session && !session.job ? "核查并继续导出" : url ? "重新生成 PPT" : "导出国基量子 PPT"}</button></div>{url && session?.job && <small>报告保存于 {new Date(session.job.createdAt).toLocaleString("zh-CN")}{session.job.revision !== workflow.revision ? "，实验记录已更新，可重新生成。" : ""}</small>}{error && <small role="alert">{error}</small>}{session?.job?.error && <small role="alert">{session.job.error.message}</small>}{session && !session.job && !sending && <small>导出回执待核查，再次点击沿用同一请求。</small>}</div>;
}
