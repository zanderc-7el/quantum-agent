import type { ReportExportJob, ReportExportRequest } from "./remoteGateway.ts";
export interface ExportSession { workflowId: string; request: ReportExportRequest; job?: ReportExportJob; }
export const exportSessionKey = (workflowId: string) => `qcal.report-export.v1:${workflowId}`;
export function parseExportSession(raw: string | null, workflowId: string): ExportSession | undefined {
  try {
    if (!raw) return;
    const value = JSON.parse(raw) as ExportSession;
    if (value.workflowId !== workflowId || !value.request || !/^[a-zA-Z0-9_-]{1,120}$/.test(value.request.operationId) || !Number.isSafeInteger(value.request.expectedRevision) || value.request.expectedRevision < 0 || value.request.format !== "pptx" || ![16,64].includes(value.request.appendixCount)) return;
    if (value.job && (value.job.workflowId !== workflowId || !/^[a-zA-Z0-9_-]{1,120}$/.test(value.job.id) || !["queued","processing","completed","failed","interrupted"].includes(value.job.status))) return;
    return value;
  } catch { return; }
}
export function reportDownloadUrl(job?: ReportExportJob) {
  // Never use a server-provided external URL or arbitrary local path as a link.
  return job?.status === "completed" ? `/api/report-exports/${encodeURIComponent(job.id)}/file` : undefined;
}

export function recoverExportSession(current: ExportSession | undefined, jobs: ReportExportJob[], workflowId: string): ExportSession | undefined {
  // An unanswered POST must retain its original operation ID for reconciliation.
  if (current && !current.job) return current;
  const candidates = jobs.flatMap(job => {
    if (!Number.isFinite(Date.parse(job.createdAt))) return [];
    const session = parseExportSession(JSON.stringify({ workflowId, job, request: {
      operationId: job.operationId, expectedRevision: job.revision, format: "pptx", appendixCount: job.appendixCount,
    } }), workflowId);
    return session ? [session] : [];
  }).sort((a, b) => Date.parse(b.job!.createdAt) - Date.parse(a.job!.createdAt));
  if (current?.job && ["queued", "processing"].includes(current.job.status)) {
    return candidates.find(candidate => candidate.job!.id === current.job!.id) ?? current;
  }
  const latest = candidates[0];
  if (!latest || (current?.job && Date.parse(current.job.createdAt) > Date.parse(latest.job!.createdAt))) return current;
  return JSON.stringify(latest) === JSON.stringify(current) ? current : latest;
}
