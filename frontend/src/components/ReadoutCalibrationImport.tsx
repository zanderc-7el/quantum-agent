import {useEffect,useRef,useState} from 'react';
import type {WorkspaceGateway} from '../lib/workspaceGateway';
import {createScienceGateway,type ScienceRecord} from '../lib/scienceGateway';

export default function ReadoutCalibrationImport({gateway,source,seriesId,records,value,onChange,refresh,onLoad}:{
 gateway:WorkspaceGateway;source?:ScienceRecord;seriesId:string;records:ScienceRecord[];value:string;
 onChange:(id:string)=>void;refresh:()=>Promise<unknown>;onLoad?:(id:string)=>void;
}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');const version=useRef(0);
 const sourceKey=`${source?.id??''}:${seriesId}`;const current=useRef(sourceKey);if(current.current!==sourceKey){current.current=sourceKey;version.current++;}
 useEffect(()=>{setBusy(false);setError('');},[sourceKey]);
 useEffect(()=>()=>{version.current++;},[]);
 const template=async()=>{if(!source)return;setBusy(true);setError('');const active=++version.current;try{
  const data=await createScienceGateway(gateway).readoutCalibrationTemplate(source.id,seriesId);
  if(active!==version.current)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download='读出标定格式_待补齐.json';link.click();URL.revokeObjectURL(url);
 }catch(e){if(active===version.current)setError(e instanceof Error?e.message:String(e));}finally{if(active===version.current)setBusy(false);}};
 const upload=async(file?:File)=>{if(!file||!source)return;setBusy(true);setError('');const active=++version.current;try{
  if(file.size>262144)throw new Error('标定 JSON 不得超过 256 KiB');
  const bytes=new Uint8Array(await file.arrayBuffer());if(active!==version.current)return;
  const documentBase64=btoa(Array.from(bytes,b=>String.fromCharCode(b)).join(''));
  const record=await createScienceGateway(gateway).save('readout_calibration',{sourceRecordId:source.id,sourceSeriesId:seriesId,documentBase64,fileName:file.name},source.scope);
  await refresh();if(active===version.current)onChange(record.id);
 }catch(e){if(active===version.current)setError(e instanceof Error?e.message:String(e));}finally{if(active===version.current)setBusy(false);}};
 const selected=records.find(r=>r.id===value);const applicable=records.filter(r=>r.kind==='readout_calibration'&&r.input?.sourceRecordId===source?.id&&r.input?.sourceSeriesId===seriesId);
 useEffect(()=>{if(value&&selected?.summary)onLoad?.(value);},[value,selected?.summary,onLoad]);
 return <div className="science-card"><h4>读出标定</h4><div className="science-form"><label className="science-field"><span>外部标定记录</span><select aria-label="外部读出标定" value={value} onChange={e=>onChange(e.target.value)}><option value="">未选择 · 仅显示概率模型</option>{applicable.map(r=><option key={r.id} value={r.id}>{r.result?.fileName??r.label} · 外部提供</option>)}</select></label></div>
  <div className="science-actions"><button disabled={source?.dataKind!=='real'||busy} onClick={()=>void template()}>下载标定格式</button><label>导入标定 JSON<input aria-label="导入读出标定 JSON" type="file" accept=".json,application/json" disabled={source?.dataKind!=='real'||busy} onChange={e=>{void upload(e.target.files?.[0]);e.target.value='';}}/></label></div>
  <p className="science-note">外部标定须覆盖实测时间，并匹配读出条件、单位和 I/Q 投影。制备标签及拟合峰谷不可充当纯态标定。</p>
  {selected&&<p>外部提供的标定 · 未经本平台独立验收；对照结果以该标定有效为条件。</p>}
  {selected?.result?.document&&<details><summary>标定来源与适用时间</summary><dl><dt>来源</dt><dd>{selected.result.document.source.origin} · {selected.result.document.source.document}</dd><dt>有效期</dt><dd>{selected.result.document.source.validFrom} 至 {selected.result.document.source.validUntil}</dd><dt>文件 SHA-256</dt><dd>{selected.result.documentSha256}</dd></dl><pre>{JSON.stringify(selected.result.document,null,2)}</pre></details>}
  {error&&<p role="alert">{error}</p>}
 </div>;
}
