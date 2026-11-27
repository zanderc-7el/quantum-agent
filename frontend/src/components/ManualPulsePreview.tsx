import {useEffect,useRef,useState} from 'react';
import type {RemoteGateway} from '../lib/remoteGateway';
import type {ManualParameterField} from '../lib/manualParameters';
import {displayParameterValue} from '../lib/manualParameters';
import {pulseEditKey} from '../lib/manualPulsePreview';
import type {ManualPulsePreviewResult} from '../lib/manualPulsePreview';
import './manual-pulse-preview.css';

type Props={gateway:RemoteGateway;workflowId:string;nodeId:string;resourceId:string;procedureId:string;revision:number;
 changes:Record<string,number|number[]>;fields:ManualParameterField[];problem?:string;dirty:boolean;onFocusField:(key:string)=>void};
const fmt=(v:number|undefined|null,scale=1)=>typeof v==='number'&&Number.isFinite(v)?new Intl.NumberFormat('en-US',{maximumSignificantDigits:8,useGrouping:false}).format(v*scale):'未提供';
const axisLabel=(name:string)=>({drive_amplitude:'驱动幅度',drive_duration_s:'驱动时长',readout_frequency_hz:'读出频率',readout_amplitude:'读出幅度',drive_frequency_hz:'驱动频率',drive_state:'驱动开关',prepared_state:'制备态',delay_s:'等待时间',half_delay_a_s:'前半等待',half_delay_b_s:'后半等待',virtual_phase_rad:'虚拟相位',first_half_gap:'首段等待'}[name]??'扫描维度');
const axisValue=(name:string,value:number|string)=>typeof value==='string'?({on:'开启',off:'关闭',ground:'基态',excited:'激发态'}[value]??value):name.endsWith('_hz')?`${fmt(value,1e-9)} GHz`:name.endsWith('_s')||name.includes('gap')?`${fmt(value,1e9)} ns`:name.endsWith('_rad')?`${fmt(value)} rad`:fmt(value);

export default function ManualPulsePreview(props:Props){
 const [selection,setSelection]=useState<{draft:string;indices:number[]}>();
 const [receipt,setReceipt]=useState<{key:string;result?:ManualPulsePreviewResult;error?:string}>();
 const [pulseId,setPulseId]=useState('');const serial=useRef(0);
 const draftKey=JSON.stringify([props.workflowId,props.nodeId,props.revision,props.changes]);
 const indices=selection?.draft===draftKey?selection.indices:undefined;
 const requestKey=JSON.stringify([draftKey,indices,props.problem]);
 useEffect(()=>{
   const generation=++serial.current;
   if(props.problem)return;
   const timer=setTimeout(()=>{
     if(!props.gateway.pulsePreview){setReceipt({key:requestKey,error:'当前连接未提供只读时序预览。'});return;}
     void props.gateway.pulsePreview(props.workflowId,props.nodeId,props.revision,props.changes,indices)
       .then(result=>{if(serial.current===generation)setReceipt(result.workflowId===props.workflowId&&result.nodeId===props.nodeId&&result.workflowRevision===props.revision
         ?{key:requestKey,result}:{key:requestKey,error:'预览来源或版本与当前草稿不一致，请重新读取配置。'});})
       .catch(error=>{if(serial.current===generation)setReceipt({key:requestKey,error:error instanceof Error?error.message:String(error)});});
   },180);
   return()=>{clearTimeout(timer);serial.current++;};
 },[requestKey,props.gateway]);
 const shown=receipt?.key===requestKey?receipt:undefined;
 const result=shown?.result;
 const selected=result?.segments.find(p=>p.id===pulseId)??result?.segments.find(p=>p.channel!=='Wait');
 const origin=result?.readoutStartS??0;
 const left=result?.segments.reduce((a,p)=>Math.min(a,p.startS-origin),0)??0;
 const right=result?.segments.reduce((a,p)=>Math.max(a,p.startS+p.durationS-origin),0)??0;
 const width=right-left||1e-9;
 const x=(seconds:number)=>112+((seconds-origin)-left)/width*660;
 const channels=['XY','Wait','Read in','Read out'];
 const focus=(property:'amplitude'|'frequency'|'duration')=>{const key=selected&&pulseEditKey(selected.channel,property,props.procedureId);return key&&props.fields.some(f=>f.key===key)?key:undefined;};
 const normalization=result?Object.entries(result.normalizedChanges).filter(([k,v])=>k in result.requestedChanges&&JSON.stringify(v)!==JSON.stringify(result.requestedChanges[k])):[];
 return <section className="mpulse" aria-label="当前节点参数时序预览"><header><h3>当前参数时序</h3><span>{props.resourceId} · 版本 {props.revision} · {props.dirty?'未保存草稿':'已保存配置'}</span></header>
 {props.problem?<p role="status">{props.problem}</p>:shown?.error?<p role="alert">预览不可生成：{shown.error}</p>:!result?<p role="status">正在读取只读执行计划…</p>:result.status!=='ready'?<p>{result.reason}</p>:<>
 <p>计划逻辑边沿，以读出起始沿为 0 ns（计划起点 {fmt(origin,1e6)} μs）。修改参数只更新预览；保存配置后仍需提交测量。</p>
 {normalization.length>0&&<p className="mpulse-normalization">预览已按执行器采样网格规范化：{normalization.map(([key,value])=>{const field=props.fields.find(f=>f.key===key);return field?`${field.label} ${displayParameterValue(result.requestedChanges[key],field)} → ${displayParameterValue(value,field)} ${field.unit}`:key;}).join('；')}。保存时使用相同规范化。</p>}
 <div className="mpulse-scans">{result.axes.filter((_,i)=>result.sweepMode!=='zip'||i===0).map((axis,i)=><label key={axis.binding}>{result.sweepMode==='zip'?'联动扫描点':axisLabel(axis.name)}<select aria-label={`时序扫描维度 ${i+1}`} value={axis.index} onChange={event=>{const next=result.axes.map(a=>a.index);if(result.sweepMode==='zip')next.fill(Number(event.target.value));else next[i]=Number(event.target.value);setSelection({draft:draftKey,indices:next});}}>{axis.values.map((v,j)=><option key={j} value={j}>第 {j+1} 点 · {axisValue(axis.name,v)}</option>)}</select></label>)}</div>
 <svg viewBox="0 0 820 245" role="img" aria-label="当前实际计划的脉冲与采集边沿">
 {channels.map((channel,i)=><g key={channel}><text x="6" y={43+i*43}>{channel==='Wait'?'等待':channel==='Read in'?'读出发射':channel==='Read out'?'ADC采集':channel}</text><line x1="112" x2="772" y1={48+i*43} y2={48+i*43}/></g>)}
 {result.segments.map(segment=>{const y=27+channels.indexOf(segment.channel)*43;return <g key={segment.id} role="button" tabIndex={0} aria-label={`查看 ${segment.label} ${segment.id}`} onClick={()=>setPulseId(segment.id)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setPulseId(segment.id);}}}><rect className={`${selected?.id===segment.id?'selected':''} ${segment.amplitude===0?'zero':''}`} x={x(segment.startS)} y={y} width={Math.max(1.5,segment.durationS/width*660)} height="23" rx="2"/><title>{segment.label} · {fmt(segment.startS-origin,1e9)}–{fmt(segment.startS+segment.durationS-origin,1e9)} ns</title></g>;})}
 {[0,.25,.5,.75,1].map(t=><text key={t} x={112+t*660} y="226" textAnchor="middle">{fmt(left+t*width,1e9)} ns</text>)}
 </svg>
 <div className="mpulse-selectors">{result.segments.map((s,i)=><button type="button" key={s.id} aria-pressed={s.id===selected?.id} onClick={()=>setPulseId(s.id)}>{s.channel==='XY'?`XY ${i+1}`:s.label}</button>)}</div>
 {selected&&<div className="mpulse-values"><h4>{selected.label} · {fmt(selected.startS-origin,1e9)}–{fmt(selected.startS+selected.durationS-origin,1e9)} ns</h4><dl>{([
   ['amplitude','幅度',selected.amplitude==null?'未提供':`${fmt(selected.amplitude)} a.u.`],
   ['frequency','频率',selected.frequencyHz==null?'未提供':`${fmt(selected.frequencyHz,1e-9)} GHz`],
   ['duration','脉宽 / 窗口',`${fmt(selected.envelopeDurationS??selected.durationS,1e9)} ns`],
 ] as const).map(([property,label,value])=><div key={property}><dt>{label}</dt><dd>{value}{focus(property)&&<button type="button" onClick={()=>props.onFocusField(focus(property)!)}>定位{props.fields.find(f=>f.key===focus(property))?.label}</button>}</dd></div>)}<div><dt>初相</dt><dd>{selected.phaseRad==null?'未提供':`${fmt(selected.phaseRad)} rad`}<small>{selected.phaseReference??'采集窗口或等待段没有发射初相'}</small></dd></div></dl>{selected.bufferS!==undefined&&<p>原生缓冲总长 {fmt(selected.bufferS,1e9)} ns（脉冲两端各半）；选中块总长 {fmt(selected.durationS,1e9)} ns。</p>}{selected.channel==='Read in'&&<p>上方为当前读出分段。总读出脉宽 {fmt(result.readoutDurationS,1e9)} ns，起始幅度 {fmt(result.readoutAmplitude)} a.u.；前沿与保持段按执行计划分配。</p>}</div>}
 <p>XY 触发延时：{fmt(result.xyTriggerDelayS,1e9)} ns。CLK 参考未提供；Z 保持站端既有值，未读取其数值。</p>
 <details><summary>预览范围与来源</summary>{result.limitations?.map(text=><p key={text}>{text}</p>)}<pre>{JSON.stringify({workflowId:result.workflowId,nodeId:result.nodeId,revision:result.workflowRevision,planSha256:result.planSha256,normalizedChanges:result.normalizedChanges,variant:result.variant},null,2)}</pre></details>
 </>}
 </section>;
}
