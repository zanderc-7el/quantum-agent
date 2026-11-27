import {useEffect,useRef,useState} from 'react';
import type {ComponentType,ReactNode} from 'react';
import type {ScienceRecord} from '../lib/scienceGateway';
import type {WorkspaceGateway} from '../lib/workspaceGateway';
import type {PlotSeries} from '../lib/scienceModels';

interface PulseSamples {
 schema:string;recordId:string;workflowId?:string;attemptId?:string;stage:string;status?:string;reason?:string;currentEvidence:boolean;
 stages:{id:string;label:string}[];
 rows:{rowId:number;condition?:string;stage?:string;capture?:number;axisValue?:number;logicalState?:number}[];
 segments:{id:string;label?:string;channel?:string}[];selected:{rowId:number;segmentId:string}|null;segment?:Record<string,any>;
 samples:null|{timeNs:number[];i:number[];q:number[];rfTimeNs:number[];rf:number[];basebandSampleCount:number;rfSampleCount:number;decimation:string;maxDisplaySamples:number};
 upload?:{status:string;receipts:unknown[];deviceWaveformReadback:boolean};
 artifacts:{id:string;name:string;sha256:string;url?:string}[];
}
interface Props {record:ScienceRecord;gateway:WorkspaceGateway;allowAnyMeasurement?:boolean;showRelatedSources?:boolean;renderResult:(result:ScienceRecord['result'])=>ReactNode;Plot:ComponentType<{series:PlotSeries[];label:string}>}
const methodLabel=(record:ScienceRecord)=>record.result?.procedureId==='sq_drag_irb'?'门表征记录':record.result?.procedureId==='sq_drag_half_scan'?'系数扫描记录':'来源记录';
const roleLabel:Record<string,string>={drag:'系数扫描与复测',pi01:'π 脉冲标定',half01:'半门验证',readout:'独立读出标定',rabi_amplitude:'幅度 Rabi',rabi_time:'时间 Rabi',anharmonicity:'已测非谐性'};
const conditionLabel:Record<string,string>={diagnostic:'系数扫描',verification_candidate:'候选复测',reference:'参考',reference_pre:'前置参考',reference_post:'后置参考',interleaved:'交错序列'};

export default function ScienceAcquisitionEvidence({record,gateway,allowAnyMeasurement=false,showRelatedSources=true,renderResult,Plot}:Props){
 const [related,setRelated]=useState<ScienceRecord>();
 const [samples,setSamples]=useState<PulseSamples>();
 const [busy,setBusy]=useState(false);const [error,setError]=useState('');
 const generation=useRef(0);
 useEffect(()=>()=>{generation.current++;},[]);
 const current=related??record;
 const procedure=current.result?.procedureId;
 const generalMeasurement=allowAnyMeasurement&&current.kind==='measurement'&&current.source==='acquisition'&&current.result?.dataKind==='real'&&!!current.reference;
 const canViewPulse=generalMeasurement||['sq_drag_half_scan','sq_drag_irb'].includes(procedure??'');
 const refs=(current.result?.gateCharacterization?.sourceReferences??current.dependencyProof?.rootReferences??current.provenance?.scienceDependencies?.rootReferences??[]) as {role:string;recordId:string}[];
 const sources=showRelatedSources?refs.filter((ref,index)=>ref.recordId&&ref.recordId!==current.id&&refs.findIndex(other=>other.recordId===ref.recordId)===index):[];
 const reset=()=>{generation.current++;setSamples(undefined);setError('');setBusy(false);};
 const openSource=async(id:string)=>{
  reset();const version=generation.current;setBusy(true);
  try{const next=await gateway.get<ScienceRecord>(`/science/records/${encodeURIComponent(id)}`);if(version===generation.current)setRelated(next);}
  catch(cause){if(version===generation.current)setError(cause instanceof Error?cause.message:String(cause));}
  finally{if(version===generation.current)setBusy(false);}
 };
 const loadSamples=async(selection:{stage?:string;rowId?:number;segmentId?:string}={})=>{
  const version=++generation.current;setBusy(true);setError('');setSamples(undefined);
  const query=new URLSearchParams();
  for(const [key,value] of Object.entries(selection))if(value!==undefined)query.set(key,String(value));
  try{
   const bound=current.readOnlyAttempt;
   if(bound)for(const key of ['workflowId','nodeId','attemptId'])query.set(key,bound[key]);
   const endpoint=bound?'/science/attempt-signal-pulse':`/science/records/${encodeURIComponent(current.id)}/pulse-samples`;
   const next=await gateway.get<PulseSamples>(endpoint+(query.size?'?'+query:''));
   if(version!==generation.current)return;
   if(next.schema!=='qcal-archived-pulse-samples/1'||next.recordId!==current.id)throw new Error('样点响应与所选采集记录不一致');
   if(generalMeasurement&&(next.workflowId!==current.reference?.workflowId||next.attemptId!==current.reference?.attemptId))throw new Error('样点响应与所选任务或尝试不一致');
   setSamples(next);
  }catch(cause){if(version===generation.current)setError(cause instanceof Error?cause.message:String(cause));}
  finally{if(version===generation.current)setBusy(false);}
 };
 const data=samples?.samples;
 const traces:PlotSeries[]=data?[{name:'I',x:data.timeNs,y:data.i,xUnit:'ns',yUnit:'a.u.',source:'compiled'},{name:'Q',x:data.timeNs,y:data.q,xUnit:'ns',yUnit:'a.u.',source:'compiled'}]:[];
 return <section className="science-acquisition-evidence" aria-label="脉冲与关联采集记录">
  <h4>{related?methodLabel(related):'脉冲与关联记录'}</h4>
  <div className="science-actions">
   {related&&<button type="button" onClick={()=>{reset();setRelated(undefined);}}>返回{methodLabel(record)}</button>}
   {canViewPulse&&<button type="button" disabled={busy} onClick={()=>loadSamples()}>查看脉冲样点</button>}
   {sources.map(source=><button key={source.recordId} type="button" disabled={busy} onClick={()=>openSource(source.recordId)}>打开{roleLabel[source.role]??'来源记录'}</button>)}
  </div>
  {related&&renderResult(related.result)}
  {busy&&<p role="status">正在读取归档…</p>}
  {error&&<p className="science-error" role="alert">{error}</p>}
  {samples&&<>
   <div className="science-form">
    <label className="science-field"><span>采集阶段</span><select aria-label="脉冲采集阶段" value={samples.stage} onChange={e=>loadSamples({stage:e.target.value})}>{samples.stages.map(stage=><option key={stage.id} value={stage.id}>{stage.label}</option>)}</select></label>
    {samples.rows.length>0&&<label className="science-field"><span>实际序列行</span><select aria-label="脉冲序列行" value={samples.selected?.rowId} onChange={e=>loadSamples({stage:samples.stage,rowId:Number(e.target.value)})}>{samples.rows.map(row=><option key={row.rowId} value={row.rowId}>行 {row.rowId} · {conditionLabel[row.condition??'']??row.condition??row.stage??'序列'}{row.axisValue===0&&row.condition==='diagnostic'?' · 零基线':''}{row.capture!=null?` · 采集 ${row.capture+1}`:''}</option>)}</select></label>}
    {samples.selected&&<label className="science-field"><span>XY 脉冲分段</span><select aria-label="脉冲分段" value={samples.selected?.segmentId??''} onChange={e=>loadSamples({stage:samples.stage,rowId:samples.selected?.rowId,segmentId:e.target.value})}>{samples.segments.filter(segment=>segment.channel==='xy').map(segment=><option key={segment.id} value={segment.id}>{segment.id} · {segment.label??'XY'}</option>)}</select></label>}
   </div>
   {!samples.currentEvidence&&<p className="science-note">历史归档 · 当前标定已变化。</p>}
   {data?<>
    <Plot series={traces} label={`行 ${samples.selected?.rowId} / ${samples.selected?.segmentId} · 载频前 I/Q`}/>
    <Plot series={[{name:'单路 RF',x:data.rfTimeNs,y:data.rf,xUnit:'ns',yUnit:'a.u.',source:'compiled'}]} label="单路 RF 编译样点"/>
    <p className="science-note">I/Q 为实际实现的载频前样点，含分段相位与修正，不等同于目标主包络。RF 是编译输出，是否存在上传命令记录另列如下。横轴从所选分段起点计时，纵轴为 a.u.，不是片端电压。原档 I/Q {data.basebandSampleCount} 点、RF {data.rfSampleCount} 点；图中按原样点索引抽取，未插值。</p>
   </>:<p className="science-note">{samples.reason??'此归档未提供载频前 I/Q 样点。'}</p>}
   <p className="science-note">{samples.upload?.status==='matched'?'上传命令记录与该行波形哈希匹配。':'未找到该行匹配的上传命令记录。'}未回读设备内部波形。</p>
   <details className="science-details"><summary>行、分段与 SHA 依据</summary><pre>{JSON.stringify({stage:samples.stage,rowId:samples.selected?.rowId,segment:samples.segment,upload:samples.upload,artifacts:samples.artifacts.map(({id,name,sha256})=>({id,name,sha256}))},null,2)}</pre></details>
   <div className="science-actions">{samples.artifacts.map(artifact=><a key={artifact.id} href={`/api/artifacts/${encodeURIComponent(artifact.id)}`} download={artifact.name}>{artifact.name==='compiled_plan.json'?'编译行与分段清单':artifact.name==='uploads.jsonl'?'RF 上传命令依据':artifact.name.endsWith('.npz')?'本行 I/Q 与 RF 原档':artifact.name}</a>)}</div>
  </>}
 </section>;
}
