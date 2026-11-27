import {useEffect,useRef,useState} from 'react';
import type {WorkspaceGateway} from '../lib/workspaceGateway';
import {stabilityReportPath,type StabilityReportFilter,type StabilityRangeReport} from '../lib/stabilityReport';

const local=(value?:string)=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+'（北京时间）':'未记录';
const value=(n:number|null|undefined,scale=1)=>typeof n==='number'&&Number.isFinite(n)?new Intl.NumberFormat('en-US',{maximumSignificantDigits:8,useGrouping:false}).format(n*scale):'—';
const states:Record<string,string>={completed:'完成',failed:'失败',interrupted:'采集中断',running:'采集中',queued:'等待执行',paused:'暂停'};

export default function StabilityReportPanel({gateway,resourceId}:{gateway:WorkspaceGateway;resourceId?:string}){
 const [draft,setDraft]=useState<StabilityReportFilter>({resourceId});
 const [applied,setApplied]=useState<StabilityReportFilter>({resourceId});
 const [report,setReport]=useState<StabilityRangeReport|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const generation=useRef(0);
 const load=async(filters:StabilityReportFilter)=>{const id=++generation.current;setBusy(true);setError('');try{const next=await gateway.get<StabilityRangeReport>(stabilityReportPath(filters));if(id===generation.current){setReport(next);setApplied({...filters});}}catch(e){if(id===generation.current)setError(e instanceof Error?e.message:String(e));}finally{if(id===generation.current)setBusy(false);}};
 useEffect(()=>{setDraft({resourceId});setReport(null);void load({resourceId});return()=>{generation.current++;};},[gateway,resourceId]);
 const field=(key:keyof StabilityReportFilter,v:string)=>setDraft(old=>({...old,[key]:v||undefined}));
 const label=(key:string,id:string)=>report?.choiceLabels?.[key]?.[id]??('记录 '+id.slice(-8));
 return <section className="science-card" aria-label="按观察时段归集稳定性"><h4>按观察时段归集与报告</h4>
 <p>新计划按首测采集完成时间筛选，尚无采集时间及旧计划按入队时间列出。日期含结束当日（北京时间）。相同已发布芯片及接线的记录可归集；版图未确认的同名比特按来源任务隔离。只读取已有轮次。</p>
 <form onSubmit={e=>{e.preventDefault();void load({...draft});}}><div className="science-form">
 <label className="science-field"><span>开始日期</span><input aria-label="报告开始日期" type="date" value={draft.startDate??''} onChange={e=>field('startDate',e.target.value)}/></label>
 <label className="science-field"><span>结束日期</span><input aria-label="报告结束日期" type="date" value={draft.endDate??''} onChange={e=>field('endDate',e.target.value)}/></label>
 <label className="science-field"><span>芯片版本</span><select aria-label="报告芯片版本" value={draft.chipVersionId??''} onChange={e=>field('chipVersionId',e.target.value)}><option value="">全部版本（分别列出）</option>{report?.choices.chipVersionIds.map(v=><option key={v} value={v}>{label("chipVersionId",v)}</option>)}</select></label>
 <label className="science-field"><span>比特</span><select aria-label="报告比特" value={draft.resourceId??''} onChange={e=>field('resourceId',e.target.value)}><option value="">全部比特</option>{[...new Set([resourceId,...(report?.choices.resourceIds??[])].filter((v):v is string=>Boolean(v)))].map(v=><option key={v}>{v}</option>)}</select></label>
 <label className="science-field"><span>来源标定任务</span><select aria-label="报告来源任务" value={draft.sourceWorkflowId??''} onChange={e=>field('sourceWorkflowId',e.target.value)}><option value="">全部来源（按身份隔离）</option>{report?.choices.sourceWorkflowIds.map(v=><option key={v} value={v}>{label("sourceWorkflowId",v)}</option>)}</select></label>
 </div><button disabled={busy}>{busy?'正在读取…':'按范围查看'}</button></form>
 {error&&<p role="alert">{error}</p>}
 {report&&<><div className="science-actions"><a href={'/api'+stabilityReportPath(applied,'html')} target="_blank" rel="noreferrer">打开可读稳定性报告</a><a href={'/api'+stabilityReportPath(applied,'download')} download>下载当前范围 HTML</a></div>
 <p>当前显示快照：{local(report.capturedAt)} · {report.summary.rounds} 轮；有效定频 {report.summary.validFrequencyRounds} 轮，同轮 T₁ {report.summary.validT1Rounds} 轮。导出沿用已应用的筛选，并捕获下载时的最新记录。</p>
 {report.groups.length===0?<p>暂无周期记录</p>:report.groups.map(g=><div key={g.id}><h4>{g.resourceId} · {g.chipVersionName??'版图未确认'} · 接线 {g.deploymentVersionName??'未确认'}</h4>{g.sourceWorkflowId&&<p>来源任务：{g.sourceWorkflowName??label("sourceWorkflowId",g.sourceWorkflowId)}（独立归集）</p>}<div className="science-table-wrap"><table><thead><tr><th>计划/轮次/时间基准</th><th>状态</th><th>频率 / GHz</th><th>相邻差 / Hz</th><th>对参考差 / Hz</th><th>T₁ / μs</th><th>来源/异常</th></tr></thead><tbody>{g.rows.map(r=><tr key={r.scheduleId+'/'+r.number}><td>{r.scheduleName} / {r.number}<small>{r.timeBasis??'入队时间（旧计划）'}：{local(r.displayAt??r.startedAt)}</small></td><td>{states[r.state]??r.state}</td><td>{r.frequencyHz==null?'未获得有效频率':value(r.frequencyHz,1e-9)}<small>{local(r.frequencyEvidence?.acquiredAt)}</small></td><td>{value(r.adjacentDeltaHz)}</td><td>{value(r.referenceDeltaHz)}</td><td>{r.t1Seconds==null?'本轮未测或待核查':value(r.t1Seconds,1e6)}<small>{local(r.t1Evidence?.acquiredAt)}</small></td><td>{r.error}{r.evidenceIssues.join('；')}<details><summary>同轮来源</summary><pre>{JSON.stringify({frequency:r.frequencyEvidence,t1:r.t1Evidence},null,2)}</pre></details></td></tr>)}</tbody></table></div></div>)}
 <details><summary>报告快照摘要</summary><p>{report.snapshotSha256}</p><pre>{JSON.stringify(report.filters,null,2)}</pre></details></>}
 </section>;
}

