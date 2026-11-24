import { useEffect, useState } from 'react';
import type { WorkspaceGateway } from '../lib/workspaceGateway';
import type { ReportExportJob } from '../lib/remoteGateway';
import { reportReceipts, reportReceiptJob } from '../lib/conversationReports';
import type { ReportReceipt } from '../lib/conversationReports';
import { reportDownloadUrl } from '../lib/reportExportSession';

function Receipt({api,receipt}:{api:WorkspaceGateway;receipt:ReportReceipt}) {
 const [job,setJob]=useState<ReportExportJob>();
 const [error,setError]=useState('');const [refresh,setRefresh]=useState(0);
 useEffect(()=>{
  let live=true;let timer:ReturnType<typeof setTimeout>;
  const poll=async()=>{try{
   const next=reportReceiptJob(receipt,await api.get(`/report-exports/${encodeURIComponent(receipt.exportId)}`));
   if(!next)throw new Error('报告回执与当前任务不一致');
   if(!live)return;setJob(next);setError('');
   if(['queued','processing'].includes(next.status))timer=setTimeout(poll,2000);
  }catch(cause){if(live){setError(cause instanceof Error?cause.message:String(cause));timer=setTimeout(poll,5000);}}};
  void poll();return()=>{live=false;clearTimeout(timer);};
 },[api,receipt.exportId,receipt.workflowId,refresh]);
 const state=job?.status??receipt.state;
 const label=({queued:'报告排队中',processing:'正在生成 PPT',completed:'PPT 已生成',failed:'报告生成失败',interrupted:'报告生成已中断'} as Record<string,string>)[state]??'正在核查报告';
 const url=reportDownloadUrl(job);
 return <section className="wc-report-receipt" aria-label="报告导出回执"><strong role="status">{label}</strong>{url&&<a className="secondary rp-link-button" href={url} download>下载国基量子 PPT</a>}{job?.error?.message&&<p role="alert">{job.error.message}</p>}{error&&<p role="alert">{error}<button type="button" className="secondary" onClick={()=>setRefresh(v=>v+1)}>刷新状态</button></p>}</section>;
}
export default function ConversationReports({api,value}:{api:WorkspaceGateway;value:unknown}) {
 const receipts=reportReceipts(value);
 return <>{receipts.map(receipt=><Receipt key={receipt.exportId} api={api} receipt={receipt}/>)}</>;
}
