import type { ReportExportJob } from './remoteGateway.ts';
export interface ReportReceipt {status:'report_receipt';exportId:string;workflowId:string;state:string;message?:string;downloadUrl?:undefined}
const record=(v:unknown):Record<string,unknown>=>v!==null&&typeof v==='object'?v as Record<string,unknown>:{};
const identifier=(v:unknown):v is string=>typeof v==='string'&&/^[a-zA-Z0-9_-]{1,120}$/.test(v);
export function reportReceipts(value:unknown):ReportReceipt[] {
 const item=record(value),results=Array.isArray(item.toolResults)?item.toolResults:[],traces=Array.isArray(item.actionTrace)?item.actionTrace:[];
 const found=new Map<string,ReportReceipt>();
 for(const raw of [...results,...traces.map(trace=>record(trace).result)]) {
  const receipt=record(raw);
  if(receipt.status!=='report_receipt'||!identifier(receipt.exportId)||!identifier(receipt.workflowId))continue;
  found.set(receipt.exportId,{...receipt,downloadUrl:undefined} as ReportReceipt);
 }
 return [...found.values()];
}
export function reportReceiptJob(receipt:Pick<ReportReceipt,'exportId'|'workflowId'>,value:unknown):ReportExportJob|undefined {
 const job=record(value);
 if(job.id!==receipt.exportId||job.workflowId!==receipt.workflowId||!['queued','processing','completed','failed','interrupted'].includes(String(job.status)))return;
 return job as unknown as ReportExportJob;
}
