import { useEffect, useRef, useState } from 'react';
import type { Workflow } from '../types';
import type { WorkspaceGateway } from '../lib/workspaceGateway';
import type { ScienceResult } from '../lib/scienceGateway';

type Props={workflow?:Workflow;gateway:WorkspaceGateway;kind:'pulse'|'drag'|'shelve';renderResult:(result:ScienceResult)=>React.ReactNode};
export default function NativePulseCompiler({workflow,gateway,kind,renderResult}:Props){
 const [nodeId,setNodeId]=useState(workflow?.currentNodeId??workflow?.nodes[0]?.id??'');
 const [values,setValues]=useState<Record<string,string>>({shape:kind==='drag'?'CosPulse':'DC',phaseRad:'0',gapNs:'300',readoutDelayNs:'0',adcDelayNs:'0',lambda1:'0'});
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const [result,setResult]=useState<ScienceResult>();const version=useRef(0);
 useEffect(()=>()=>{version.current++;},[]);
 const update=(key:string,value:string)=>{version.current++;setValues({...values,[key]:value});setResult(undefined);setBusy(false);setError('');};
 const node=workflow?.nodes.find(n=>n.id===nodeId);
 const empiricalGate=node?.parameters?.drag_mode==='empirical_beta';
 const compile=async()=>{if(!workflow||!node||empiricalGate)return;const current=++version.current;setBusy(true);setError('');try{
  const input:Record<string,string|number>={};
  for(const [key,value] of Object.entries(values)){if(value==='')continue;if(key==='shape'||key==='anharmonicitySource'){input[key]=value;continue;}const number=Number(value);if(!Number.isFinite(number))throw new Error('请填写有效的脉冲参数');input[key]=number;}
  const response=await gateway.write<ScienceResult>('/native-preview',{kind,nodeId:node.id,scope:{workflowId:workflow.id,resourceId:node.resourceId},input});
  if(version.current===current)setResult(response);
 }catch(e){if(version.current===current)setError(e instanceof Error?e.message:String(e));}finally{if(version.current===current)setBusy(false);}};
 const field=(key:string,label:string,unit:string,placeholder?:string)=><label className="science-field" key={key}><span>{label}<small>{unit}</small></span><input type="number" step="any" value={values[key]??''} placeholder={placeholder} onChange={e=>update(key,e.target.value)}/></label>;
 return <article className="science-card"><h3>原生样点编译</h3><p>读取所选步骤的参数，用 ArcLight 原生脉冲代码生成样点。</p>
 {!workflow?<div className="science-empty">选择任务后编译</div>:<><div className="science-form"><label className="science-field"><span>参数来源步骤</span><select value={nodeId} onChange={e=>{version.current++;setNodeId(e.target.value);setResult(undefined);setBusy(false);}}>{workflow.nodes.map(n=><option key={n.id} value={n.id}>{n.resourceId} · {n.label}</option>)}</select></label><label className="science-field"><span>包络</span><select value={values.shape} onChange={e=>update('shape',e.target.value)}><option value="DC">矩形</option><option value="Gaussian">高斯</option><option value="CosPulse">余弦</option></select></label>
 {field('amplitude','XY 幅度','a.u.','使用任务参数')}{field('durationNs','XY 脉宽','ns','使用任务参数')}{field('frequencyHz','XY 频率','Hz','使用任务参数')}{field('phaseRad','XY 初相','rad')}{values.shape==='Gaussian'&&field('sigmaNs','高斯 σ','ns')}
 {field('gapNs','XY 至读出间隔','ns')}{field('readoutWidthNs','读出时长','ns','使用任务参数')}{field('readoutAmplitude','读出幅度','a.u.','使用任务参数')}{field('readoutFrequencyHz','读出频率','Hz','使用任务参数')}{field('readoutDelayNs','读出整体延迟','ns')}{field('adcDelayNs','采集相对读出延迟','ns')}
 {kind==='drag'&&!empiricalGate&&<>{field('lambda1','DRAG 系数 λ','')}{field('anharmonicityHz','带符号非谐性 Δ','Hz')}<label className="science-field"><span>非谐性来源</span><input value={values.anharmonicitySource??''} onChange={e=>update('anharmonicitySource',e.target.value)} placeholder="测量记录或人工假设"/></label></>}
 {kind==='shelve'&&<>{field('f12Hz','1→2 驱动频率','Hz')}{field('probeDurationNs','1→2 脉宽','ns')}{field('probeAmplitude','1→2 幅度','a.u.')}</>}
 </div>{empiricalGate&&<p className="science-note">此编译入口仅支持已测非谐性方案，尚不支持经验 β。</p>}<div className="science-actions"><button className="science-primary" disabled={busy||!node||empiricalGate} onClick={()=>void compile()}>{busy?'正在编译…':'编译原生样点'}</button><small>仅编译，未连接仪器</small></div>{error&&<div className="science-error" role="alert">{error}</div>}
 {result&&<>{renderResult(result)}<details className="science-details"><summary>实际参数与来源</summary><table><thead><tr><th>参数</th><th>值</th><th>来源</th></tr></thead><tbody>{Object.entries(result.input??{}).map(([key,value])=><tr key={key}><td>{key}</td><td>{String(value??'未使用')}</td><td>{{explicit_compile_input:'本次填写',server_workflow_parameter_seed:'任务参数',native_compile_template_default:'编译模板'}[result.sourceEvidence?.parameterSources?.[key] as string]??'未记录'}</td></tr>)}</tbody></table></details><div className="science-actions">{(result.artifacts??[]).map((a:any)=><a key={a.id} href={a.url??`/api/artifacts/${encodeURIComponent(a.id)}`} download>{a.name}</a>)}</div></>}
 </>}
 </article>;
}
