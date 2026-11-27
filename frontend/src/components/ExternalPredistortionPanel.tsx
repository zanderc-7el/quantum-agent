import {useEffect,useRef,useState} from 'react';
import type {ComponentType} from 'react';
import type {WorkspaceGateway} from '../lib/workspaceGateway';
import {createScienceGateway} from '../lib/scienceGateway';
import type {ScienceInput,ScienceRecord,ScienceResult,ScienceScope} from '../lib/scienceGateway';
import {finite,formatScience as fmt,resultPlots} from '../lib/scienceModels';
import type {PlotSeries} from '../lib/scienceModels';
import {scienceRecordTimes} from '../lib/scienceRecordLabel';
import {readExternalScienceJSON} from '../lib/scienceExternalFile';

export type ExternalComparisonPlot=ComponentType<{series:PlotSeries[];label:string}>;
const MAX_FILE_BYTES=512*1024;
const errorText=(error:unknown)=>error instanceof Error?error.message:String(error);
function download(bytes:BlobPart,fileName:string){
 const url=URL.createObjectURL(new Blob([bytes],{type:'application/json'}));
 const link=document.createElement('a');link.href=url;link.download=fileName;link.click();URL.revokeObjectURL(url);
}
export function ExternalPredistortionResult({result,Plot}:{result:ScienceResult;Plot:ExternalComparisonPlot}){
 const fixture=result.dataOrigin==='test_fixture';const origin=fixture?'test_fixture':'external_measurement';
 const timeSeries=resultPlots(result).map((trace,index)=>({...trace,source:fixture?origin:result.series?.[index]?.id==='target'?'provided_target':result.series?.[index]?.id.endsWith('_error')?'external_comparison':origin}));
 const waveform=timeSeries.filter((_,index)=>['target','baseline','compensated'].includes(result.series?.[index]?.id??''));
 const errors=timeSeries.filter((_,index)=>['baseline_error','compensated_error'].includes(result.series?.[index]?.id??''));
 const transfer=result.transfer,metrics=result.metrics??{},unit=result.series?.[0]?.unit??'';
 const reductionNote=({baseline_zero:'基线误差为零，比例不适用。',numeric_range:'比例计算超出数值范围。'} as Record<string,string>)[String(metrics.rmseReductionPercentUnavailableReason??'')];
 const context=result.context??{};
 const quantity=({voltage:'电压',current:'电流',iq_projection:'IQ 投影',measured_signal:'采集信号'} as Record<string,string>)[context.quantity]??context.quantity;
 const contextRows=[['通道',context.channelId??context.channel],['测点',context.measurementPoint],['物理量',quantity]]
  .filter((row):row is [string,string]=>typeof row[1]==='string'&&!!row[1]);
 return <>
  <div className="science-source"><span>外部提供资料</span><span>{fixture?'测试夹具 · 非实测':'外部测量文件'}</span><span>未作本机采集或校准验收</span></div>
  <p>{result.fileName}{contextRows.map(([label,value])=>` · ${label}：${value}`).join('')}</p>
  <div className="science-metrics"><div><small>参与比较样点</small><strong>{fmt(metrics.sampleCount)}</strong></div><div><small>均方根误差降低</small><strong>{fmt(metrics.rmseReductionPercent)}{finite(metrics.rmseReductionPercent)?'%':''}</strong>{!finite(metrics.rmseReductionPercent)&&reductionNote&&<small>{reductionNote}</small>}</div></div>
  <div className="science-table-wrap"><table><thead><tr><th>指标{unit&&` / ${unit}`}</th><th>补偿前</th><th>补偿后</th></tr></thead><tbody>{[
   ['均方根误差','baselineRmse','compensatedRmse'],['最大绝对误差','baselineMaxAbsError','compensatedMaxAbsError'],
   ['过冲','baselineOvershoot','compensatedOvershoot'],['下冲','baselineUndershoot','compensatedUndershoot'],
  ].map(([label,before,after])=><tr key={before}><th>{label}</th><td>{fmt(metrics[before])}</td><td>{fmt(metrics[after])}</td></tr>)}</tbody></table></div>
  {waveform.length>0&&<Plot label="同测点目标与补偿前后响应" series={waveform}/>}
  {errors.length>0&&<Plot label="补偿前后波形误差" series={errors}/>}
  {transfer&&<div className="science-two-columns"><Plot label="外部频响 |H(f)|" series={[{name:'幅度',x:transfer.frequencyHz??[],y:transfer.magnitude??[],xUnit:'Hz',yUnit:transfer.unit??'ratio',source:origin}]}/><Plot label="外部频响相位" series={[{name:'相位',x:transfer.frequencyHz??[],y:(transfer.phaseRad??[]).map((value:unknown)=>finite(value)?value*180/Math.PI:null),xUnit:'Hz',yUnit:'°',source:origin}]}/></div>}
  <details className="science-details"><summary>文件、测点与采集来源</summary><pre>{JSON.stringify({fileName:result.fileName,documentSha256:result.documentSha256,dataOrigin:result.dataOrigin,context:result.context,windowS:result.windowS,calibrationVersion:result.calibrationVersion,sourceSummary:result.sourceSummary,metricDefinitions:result.metricDefinitions,qualification:result.qualification,physicalAccepted:result.physicalAccepted},null,2)}</pre></details>
 </>;
}

type PendingComparison = {
 input:ScienceInput;
 result:ScienceResult;
 operationId:string;
 scope:ScienceScope;
 scopeKey:string;
};
function validScope(scope:ScienceScope):boolean {
 return !!scope.resourceId && !!(scope.workflowId || scope.chipVersionId && scope.deploymentVersionId);
}
function scopeMatches(record:ScienceRecord,scope:ScienceScope):boolean {
 return Object.entries(scope).every(([field,value])=>!value || record.scope?.[field as keyof ScienceScope]===value);
}
function invalidEvidence(record?:ScienceRecord):boolean {
 return record?.evidenceIntact===false || record?.validity==='stale';
}
function recordTitle(record:ScienceRecord):string {
 const name=record.result?.fileName??record.label??'外部预补偿对照';
 return `${record.scope.resourceId?record.scope.resourceId+' · ':''}${name}`;
}

export default function ExternalPredistortionPanel({gateway,records,scope:baseScope,resourceIds=[],refresh,loadRecord,Plot}:{
 gateway:WorkspaceGateway;
 records:ScienceRecord[];
 scope:ScienceScope;
 resourceIds?:string[];
 refresh:()=>Promise<unknown>;
 loadRecord:(id:string,retry?:boolean)=>void;
 Plot:ExternalComparisonPlot;
}){
 const [pickedResource,setPickedResource]=useState('');
 const resourceId=baseScope.resourceId??(resourceIds.length===1?resourceIds[0]:resourceIds.includes(pickedResource)?pickedResource:undefined);
 const scope={...baseScope,...(resourceId?{resourceId}:{})};
 const key=JSON.stringify(scope);
 const canImport=validScope(scope);
 const [selectedId,setSelectedId]=useState('');
 const [saved,setSaved]=useState<ScienceRecord>();
 const [draft,setDraft]=useState<PendingComparison>();
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState('');
 const generation=useRef(0);
 const locked=useRef(false);
 const activeKey=useRef(key);
 const pending=useRef<PendingComparison|undefined>(undefined);
 const currentScope=useRef(scope);
 currentScope.current=scope;
 if(activeKey.current!==key){
  activeKey.current=key;
  generation.current++;
  locked.current=false;
  pending.current=undefined;
 }
 function replaceDraft(next?:PendingComparison){pending.current=next;setDraft(next);}
 useEffect(()=>{
  setSelectedId('');setSaved(undefined);replaceDraft();setBusy(false);setError('');
 },[key]);
 useEffect(()=>()=>{generation.current++;locked.current=false;pending.current=undefined;},[]);

 const awaitingResource=resourceIds.length>1&&!resourceId;
 const eligible=records.filter(record=>record.kind==='predistortion_comparison'&&!awaitingResource&&scopeMatches(record,scope));
 const selected=eligible.find(record=>record.id===selectedId);
 const localSaved=saved&&!awaitingResource&&scopeMatches(saved,scope)?saved:undefined;
 const current=selected?.summary!==true?selected:localSaved?.id===selectedId?localSaved:undefined;
 const record=current??(localSaved?.id===selectedId?localSaved:undefined);
 const pendingDraft=draft?.scopeKey===key?draft:undefined;
 const invalid=invalidEvidence(selected)||invalidEvidence(record);
 const result=pendingDraft?.result??record?.result;
 useEffect(()=>{
  if(selectedId&&selected?.summary===true)loadRecord(selectedId);
 },[selectedId,selected?.summary,loadRecord]);
 const api=createScienceGateway(gateway);

 function begin():number|undefined {
  if(locked.current)return;
  locked.current=true;setBusy(true);setError('');
  return ++generation.current;
 }
 function finish(token:number){
  if(token!==generation.current)return;
  locked.current=false;setBusy(false);
 }
 async function template(){
  const requestedScope={...currentScope.current};
  if(!validScope(requestedScope))return;
  const token=begin();if(token===undefined)return;
  try{
   const value=await api.channelComparisonTemplate(requestedScope);
   if(token===generation.current)download(JSON.stringify(value,null,2),'预补偿外部数据格式_待填写.json');
  }catch(error){
   if(token===generation.current)setError(errorText(error));
  }finally{finish(token);}
 }
 async function upload(file?:File){
  const requestedScope={...currentScope.current};
  if(!file||!validScope(requestedScope))return;
  const token=begin();if(token===undefined)return;
  replaceDraft();setSelectedId('');setSaved(undefined);
  try{
   const input=await readExternalScienceJSON(file,MAX_FILE_BYTES);
   if(token!==generation.current)return;
   const result=await api.channelComparisonPreview(input,requestedScope);
   if(token===generation.current){
    replaceDraft({input,result,operationId:crypto.randomUUID(),scope:requestedScope,scopeKey:JSON.stringify(requestedScope)});
   }
  }catch(error){
   if(token===generation.current)setError(errorText(error));
  }finally{finish(token);}
 }
 async function save(){
  // Read the current pending file, not a stale button closure after switching
  // records or targets. Retain its operation ID if the response is uncertain.
  const snapshot=pending.current;
  if(!snapshot||snapshot.scopeKey!==activeKey.current||!validScope(snapshot.scope))return;
  const token=begin();if(token===undefined)return;
  try{
   const value=await api.save('predistortion_comparison',snapshot.input,snapshot.scope,{operationId:snapshot.operationId});
   if(token!==generation.current)return;
   setSaved(value);setSelectedId(value.id);replaceDraft();
   try{await refresh();}
   catch(error){if(token===generation.current)setError(`已保存；列表刷新失败：${errorText(error)}`);}
  }catch(error){
   if(token===generation.current)setError(errorText(error));
  }finally{finish(token);}
 }
 function openRecord(id:string){
  if(locked.current)return;
  setSelectedId(eligible.some(record=>record.id===id)||localSaved?.id===id?id:'');
  replaceDraft();setError('');
 }
 function chooseResource(id:string){
  if(locked.current)return;
  const requestedScope={...baseScope,...(resourceIds.includes(id)?{resourceId:id}:{})};
  currentScope.current=requestedScope;
  activeKey.current=JSON.stringify(requestedScope);
  generation.current++;
  replaceDraft();setSelectedId('');setSaved(undefined);setError('');setPickedResource(id);
 }
 function downloadOriginal(){
  if(!record)return;
  try{
   const input=record.input;
   const bytes=typeof input.documentBase64==='string'
    ?Uint8Array.from(atob(input.documentBase64),value=>value.charCodeAt(0))
    :input.documentText;
   if(typeof bytes!=='string'&&!(bytes instanceof Uint8Array))throw new Error('原始文件未归档。');
   const original=typeof bytes==='string'?bytes:new Uint8Array(bytes).buffer;
   download(original,input.fileName??record.result?.fileName??'外部预补偿数据.json');
  }catch(error){setError(errorText(error));}
 }
 const hasOriginal=typeof record?.input?.documentBase64==='string'||typeof record?.input?.documentText==='string';
 return <article className="science-card">
  <h3>外部频响与补偿验证</h3>
  {resourceIds.length>1&&!baseScope.resourceId&&<label className="science-field">
   <span>关联比特</span>
   <select aria-label="外部数据关联比特" disabled={busy} value={resourceId??''} onChange={e=>chooseResource(e.target.value)}>
    <option value="">选择比特</option>{resourceIds.map(id=><option key={id} value={id}>{id}</option>)}
   </select>
  </label>}
  <div className="science-form">
   <label className="science-field"><span>已保存的外部比较</span>
    <select aria-label="外部预补偿比较记录" disabled={busy} value={selectedId} onChange={e=>openRecord(e.target.value)}>
     <option value="">选择保存记录</option>
     {eligible.map(record=><option key={record.id} value={record.id}>{recordTitle(record)} · {scienceRecordTimes(record)}</option>)}
     {localSaved&&!eligible.some(record=>record.id===localSaved.id)&&<option value={localSaved.id}>{recordTitle(localSaved)}</option>}
    </select>
   </label>
   <label className="science-field"><span>导入外部数据 JSON</span>
    <input aria-label="导入预补偿外部 JSON" type="file" accept=".json,application/json" disabled={busy||!canImport}
     onChange={e=>{void upload(e.target.files?.[0]);e.target.value='';}}/>
   </label>
  </div>
  <div className="science-actions">
   <button disabled={busy||!canImport} onClick={()=>void template()}>下载外部数据格式</button>
   <button className="science-primary" disabled={busy||!pendingDraft||!canImport} onClick={()=>void save()}>保存比较</button>
   {hasOriginal&&<button disabled={busy} onClick={downloadOriginal}>下载原始文件</button>}
  </div>
  {!canImport&&<p>先选择一个比特任务。</p>}
  {busy&&<p role="status">正在处理…</p>}
  {error&&<p role="alert" className="science-error">{error}</p>}
  {selected?.summary===true&&!record&&<p>正在载入记录… <button disabled={busy} onClick={()=>loadRecord(selectedId,true)}>重新载入</button></p>}
  {invalid?<p role="alert" className="science-error">记录来源校验未通过。</p>
   :result?<ExternalPredistortionResult result={result} Plot={Plot}/>
   :!busy&&<p>导入同测点的频响、目标波形和补偿前后记录。</p>}
  {record&&<details className="science-details"><summary>归档记录</summary>
   <pre>{JSON.stringify({id:record.id,createdAt:record.createdAt,scope:record.scope,provenance:record.provenance},null,2)}</pre>
  </details>}
 </article>;
}
