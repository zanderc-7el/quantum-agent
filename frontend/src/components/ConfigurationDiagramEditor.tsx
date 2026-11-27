import { useEffect, useRef, useState } from 'react';
import type { ChipTopologyData, DiagramImage, SignalComponent, SignalPath, TopologyEdge } from '../lib/configurationDiagrams';
import { isRasterImage, MAX_DIAGRAM_DATA_URL_CHARS, readDiagramImage } from '../lib/configurationDiagrams';
import { DiagramSourceEvidence, DiagramSourceField } from './DiagramSource';
import './configuration-diagrams.css';

type Resource = {id:string;label?:string;physicalId?:string};
type Props = {kind:'topology'|'signalPaths';scope?:string;resources?:Resource[];value?:ChipTopologyData|SignalPath[];onChange(value:ChipTopologyData|SignalPath[]):void};
const componentKinds:Record<string,string>={instrument:'仪器',attenuator:'衰减器',filter:'滤波器',amplifier:'放大器',mixer:'混频器',cable:'线缆',chip:'芯片',adc:'采集器',other:'其他'};
const edgeKinds:Record<string,string>={coupling:'直接耦合',readout:'读出连接',flux:'磁通连接',unknown:'类型待核实'};
const nextId=(prefix:string,rows:readonly {id:string}[])=>{let n=1;while(rows.some(row=>row.id===`${prefix}-${n}`))n++;return `${prefix}-${n}`;};
const optionalText=(value:string)=>value.trim()?value:undefined;
function optionalFields(record:unknown,fields:readonly string[],maximum=2000){
 if(!record||typeof record!=='object')return false;
 const value=record as Record<string,unknown>;
 return fields.every(key=>value[key]===undefined||(typeof value[key]==='string'&&!!value[key].trim()&&value[key].length<=maximum));
}
function validComponent(component:SignalComponent){
 if(!component||typeof component.id!=='string'||!component.id.trim()||typeof component.label!=='string'||!component.label.trim()||!Object.hasOwn(componentKinds,component.type))return false;
 if(!optionalFields(component,['channel','port','fromPort','toPort','model','source']))return false;
 if(component.confirmation!==undefined&&!['pending','confirmed'].includes(component.confirmation))return false;
 return (['temperatureK','attenuationDb','delayNs'] as const).every(key=>component[key]===undefined||(typeof component[key]==='number'&&Number.isFinite(component[key])&&component[key]>=0&&component[key]<={temperatureK:1e5,attenuationDb:200,delayNs:1e9}[key]));
}

function Field({label,value,onChange,type='text',min,max,placeholder}:{label:string;value?:string|number;onChange(value:string):void;type?:string;min?:number;max?:number;placeholder?:string}){
 return <label>{label}<input aria-label={label} type={type} min={min} max={max} step={type==='number'?'any':undefined} value={value??''} placeholder={placeholder} onChange={e=>onChange(e.target.value)}/></label>;
}
function Confirmation({label,value,onChange,source}:{label:string;value?:string;onChange(value:string):void;source?:string}){
 return <label>{label}<select aria-label={label} value={value??'pending'} onChange={e=>onChange(e.target.value)}><option value="pending">待核实</option><option value="confirmed" disabled={!source?.trim()}>已确认（需来源）</option></select></label>;
}
function ImageUpload({image,onChange,label}:{image?:DiagramImage;onChange(image?:DiagramImage):void;label:string}){
 const [error,setError]=useState('');const ticket=useRef(0);const alive=useRef(true);const latestChange=useRef(onChange);latestChange.current=onChange;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;ticket.current++;};},[]);
 return <div className="cd-image-upload"><label>{label}<input aria-label={label} type="file" accept="image/png,image/jpeg,.png,.jpg,.jpeg" onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;const request=++ticket.current;setError('');void readDiagramImage(file).then(next=>{if(alive.current&&request===ticket.current)latestChange.current(next);}).catch(cause=>{if(alive.current&&request===ticket.current)setError(cause instanceof Error?cause.message:String(cause));});}}/></label><small>PNG/JPEG，单图 ≤ 512 KiB；同一配置的图片编码合计 ≤ 700 KiB。保留原始图像字节。</small>{image&&<p>{image.name} · {image.width} × {image.height} px <button type="button" className="secondary" onClick={()=>{ticket.current++;onChange(undefined);}}>移除原图</button></p>}{error&&<p className="wc-error" role="alert">{error}</p>}</div>;
}

export default function ConfigurationDiagramEditor(props: Props) {
 const draft = props.value;
 // Older drafts can contain the original diagram before nodes have been mapped.
 const value = props.kind === 'topology' && !Array.isArray(draft)
  ? {...draft, nodes: draft?.nodes ?? [], edges: draft?.edges ?? []}
  : draft;
 return <Editor key={`${props.kind}:${props.scope ?? ''}`} {...props} value={value}/>;
}
function Editor({kind,value,onChange,resources=[]}:Props){
 const [raw,setRaw]=useState<string>();const [error,setError]=useState('');
 function change(next:ChipTopologyData|SignalPath[]){
  const images=Array.isArray(next)?next.map(p=>p.image):[next.image];
  if(images.reduce((sum,img)=>sum+(img?.dataUrl.length??0),0)>MAX_DIAGRAM_DATA_URL_CHARS){setError('同一配置的原图编码合计超过 700 KiB，请移除重复原图或使用较小图片。');return;}
  setError('');setRaw(undefined);onChange(next);
 }
 function apply(){try{
  const parsed:unknown=JSON.parse(raw??JSON.stringify(value??(kind==='topology'?{nodes:[],edges:[]}:[])));
  if(kind==='topology'){
   const t=parsed as ChipTopologyData;
   if(!t||!Array.isArray(t.nodes)||!Array.isArray(t.edges))throw new Error('拓扑需要 nodes 与 edges 数组');
   if(t.nodes.length>1024||t.edges.length>4096||!optionalFields(t,['source','date']))throw new Error('拓扑来源 / 日期应是文本，节点与连线数量不能超过限制');
   const ids=new Set<string>();
   for(const node of t.nodes){if(!node||!resources.some(r=>r.id===node.resourceId)||ids.has(node.resourceId)||!Number.isFinite(node.x)||!Number.isFinite(node.y)||Math.abs(node.x)>1e6||Math.abs(node.y)>1e6||!optionalFields(node,['designId'],500))throw new Error('节点必须唯一、对应本芯片资源，并具有有限 x/y 坐标和文本设计编号');ids.add(node.resourceId);}
   if(t.edges.some(e=>!e||typeof e.id!=='string'||!e.id.trim()||!ids.has(e.from)||!ids.has(e.to)||e.from===e.to||!edgeKinds[e.kind]||!['pending','confirmed'].includes(e.confirmation)||typeof e.source!=='string'||!e.source.trim()))throw new Error('连线须有唯一编号、不同的本图端点、类型、确认状态和资料来源');
   if(new Set(t.edges.map(e=>e.id)).size!==t.edges.length)throw new Error('连线编号重复');
   if(t.image&&!isRasterImage(t.image))throw new Error('拓扑原图只能是有尺寸的受限 PNG/JPEG');
   change(t);
  }else{
   if(!Array.isArray(parsed)||parsed.some(p=>!p||![p.id,p.label,p.source].every(v=>typeof v==='string'&&v.trim())||!['input','output','bidirectional'].includes(p.direction)||!['pending','confirmed'].includes(p.confirmation)||!Array.isArray(p.components)||!p.components.length||p.components.some((c:SignalComponent)=>!c||!c.id?.trim()||!c.label?.trim()||!componentKinds[c.type])||(p.image&&!isRasterImage(p.image))))throw new Error('线路需要名称、方向、来源、确认状态，以及有名称和类型的部件；原图只支持 PNG/JPEG');
   if(new Set(parsed.map(p=>p.id)).size!==parsed.length)throw new Error('线路编号重复');
   if(parsed.length>256||parsed.some(p=>!optionalFields(p,['station','sourceDate'],500)||p.components.length>100||p.components.some((c:SignalComponent)=>!validComponent(c))||new Set(p.components.map((c:SignalComponent)=>c.id)).size!==p.components.length))throw new Error('部件端口、型号与来源应为文本；温级、衰减与延时应为有效的非负数值；部件编号不能重复');
   change(parsed);
  }
 }catch(cause){setError(cause instanceof Error?cause.message:String(cause));}}
 return <section className="cd-editor" aria-label={kind==='topology'?'拓扑配置表单':'信号路径配置表单'}>
  <h3>{kind==='topology'?'版图与资源对应':'线路与沿途部件'}</h3>
  <p className="cd-note">表单修改会更新上方预览。完成后点击页面“保存草稿”；发布仍需既有配置检查。未知信息保留空白或待核实。</p>
  {kind==='topology'?<TopologyForm value={(value as ChipTopologyData)??{nodes:[],edges:[]}} resources={resources} onChange={change}/>:<PathsForm paths={(value as SignalPath[])??[]} onChange={change}/>}
  {error&&<p className="wc-error" role="alert">{error}</p>}
  <details className="cd-json-editor"><summary>技术详情：JSON 批量导入 / 导出</summary><label>读取 JSON 文件<input aria-label="读取 JSON 文件" type="file" accept=".json,application/json" onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;if(file.size>900*1024){setError('JSON 文件超过 900 KiB，请缩小或移除图片');return;}void file.text().then(setRaw).catch(cause=>setError(String(cause)));}}/></label><textarea aria-label={kind==='topology'?'拓扑 JSON':'信号路径 JSON'} spellCheck={false} value={raw??JSON.stringify(value??(kind==='topology'?{nodes:[],edges:[]}:[]),null,2)} onChange={e=>setRaw(e.target.value)}/><button className="secondary" type="button" onClick={apply}>应用 JSON 到表单</button></details>
 </section>;
}

function TopologyForm({value,resources,onChange}:{value:ChipTopologyData;resources:Resource[];onChange(value:ChipTopologyData):void}){
 const [resourceId,setResourceId]=useState('');const [designId,setDesignId]=useState('');const [point,setPoint]=useState({x:'0',y:'0'});const [zoom,setZoom]=useState(1);
 const available=resources.filter(r=>r.id&&!value.nodes.some(n=>n.resourceId===r.id));const image=isRasterImage(value.image)?value.image:undefined;
 const validPoint=point.x.trim()!==''&&point.y.trim()!==''&&[point.x,point.y].every(v=>Number.isFinite(Number(v))&&Math.abs(Number(v))<=1e6);
 const canAdd=available.some(r=>r.id===resourceId)&&validPoint;
 const patchEdge=(id:string,patch:Partial<TopologyEdge>)=>onChange({...value,edges:value.edges.map(e=>e.id===id?{...e,...patch}:e)});
 return <>
  <div className="cd-form-grid"><DiagramSourceField label="版图来源 / 版本" value={value.source} onChange={source=>onChange({...value,source:optionalText(source)})} placeholder="设计交付文件、页码或资料链接"/><Field label="版图资料日期" placeholder="例如 2026年10月或 2026-10-20" value={value.date} onChange={date=>onChange({...value,date:optionalText(date)})}/></div>
  <ImageUpload image={value.image} label="上传拓扑原图" onChange={image=>onChange({...value,image})}/>{image?.source&&image.source!==value.source&&<DiagramSourceEvidence source={image.source} label="原图来源证据"/>}
  <fieldset className="cd-form-card"><legend>1. 将设计节点对应到实测资源</legend><p className="cd-note">选择资源，填写设计编号；可在原图点击定位，或填写原图像素坐标。不会自动推断耦合。</p>
   <div className="cd-form-grid"><label>映射资源<select aria-label="映射资源" value={available.some(r=>r.id===resourceId)?resourceId:''} onChange={e=>setResourceId(e.target.value)}><option value="">选择本芯片资源</option>{available.map(r=><option key={r.id} value={r.id}>{r.id}{r.physicalId?` · ${r.physicalId}`:''}{r.label&&r.label!==r.id?` · ${r.label}`:''}</option>)}</select></label><Field label="设计编号" value={designId} onChange={setDesignId} placeholder="例如版图标注 D7"/><Field label="节点 X" type="number" value={point.x} onChange={x=>setPoint({...point,x})}/><Field label="节点 Y" type="number" value={point.y} onChange={y=>setPoint({...point,y})}/></div>
   {!resources.some(r=>r.id)&&<p className="cd-missing">先在下方添加本芯片的比特编号，再建立版图对应。</p>}
   {image&&<><label className="cd-zoom">原图缩放<input aria-label="定位原图放大" type="range" min="1" max="4" step=".25" value={zoom} onChange={e=>setZoom(Number(e.target.value))}/></label><div className="cd-viewport"><svg aria-label="拓扑原图定位" role="img" viewBox={`0 0 ${image.width} ${image.height}`} style={{width:`${zoom*100}%`,maxWidth:'none'}} onClick={e=>{const bounds=e.currentTarget.getBoundingClientRect();if(!bounds.width||!bounds.height)return;setPoint({x:String(Math.round(Math.max(0,Math.min(image.width,(e.clientX-bounds.left)/bounds.width*image.width)))),y:String(Math.round(Math.max(0,Math.min(image.height,(e.clientY-bounds.top)/bounds.height*image.height))))});}}><title>点击原图设置节点坐标，也可用上方数字字段输入</title><image href={image.dataUrl} width={image.width} height={image.height}/>{value.nodes.map(n=><g key={n.resourceId}><circle cx={n.x} cy={n.y} r={Math.max(image.width/90,4)} className="cd-mapping-marker"/><text x={n.x} y={n.y-image.width/65} fontSize={Math.max(image.width/60,10)} className="cd-mapping-label">{n.designId?`${n.designId} → `:''}{n.resourceId}</text></g>)}{validPoint&&resourceId&&<circle cx={Number(point.x)} cy={Number(point.y)} r={Math.max(image.width/65,5)} className="cd-mapping-cursor"/>}</svg></div></>}
   <button type="button" className="secondary" disabled={!canAdd} onClick={()=>{if(!canAdd)return;onChange({...value,nodes:[...value.nodes,{resourceId,...(designId.trim()?{designId:designId.trim()}:{}),x:Number(point.x),y:Number(point.y)}]});setResourceId('');setDesignId('');}}>添加节点</button>
  </fieldset>
  {value.nodes.length>0&&<div className="cd-table-scroll"><table className="cd-edit-table"><caption>已对应的节点（删除节点会同时移除与它相连的线）</caption><thead><tr><th>实测资源</th><th>设计编号</th><th>X / px</th><th>Y / px</th><th>操作</th></tr></thead><tbody>{value.nodes.map(n=><tr key={n.resourceId}><th>{n.resourceId}{!resources.some(r=>r.id===n.resourceId)&&<small className="wc-error">资源已删除，请移除节点</small>}</th><td><input aria-label={`${n.resourceId} 设计编号`} value={n.designId??''} onChange={e=>onChange({...value,nodes:value.nodes.map(old=>old.resourceId===n.resourceId?{...old,designId:optionalText(e.target.value)}:old)})}/></td>{(['x','y'] as const).map(axis=><td key={axis}><input aria-label={`${n.resourceId} ${axis.toUpperCase()}`} type="number" step="any" value={n[axis]} onChange={e=>{if(e.target.value.trim()&&Number.isFinite(Number(e.target.value)))onChange({...value,nodes:value.nodes.map(old=>old.resourceId===n.resourceId?{...old,[axis]:Number(e.target.value)}:old)});}}/></td>)}<td><button type="button" className="secondary" onClick={()=>onChange({...value,nodes:value.nodes.filter(old=>old.resourceId!==n.resourceId),edges:value.edges.filter(e=>e.from!==n.resourceId&&e.to!==n.resourceId)})}>删除节点 {n.resourceId}</button></td></tr>)}</tbody></table></div>}
  <fieldset className="cd-form-card"><legend>2. 记录图纸中的连接</legend><p className="cd-note">共用读出总线不代表直接耦合；每条连接分别填写类型和来源。</p>
   {value.edges.map((edge,i)=><div className="cd-form-card" key={edge.id}><strong>连线 {i+1}</strong><div className="cd-form-grid">{(['from','to'] as const).map(side=><label key={side}>{side==='from'?'起点':'终点'}<select aria-label={`连线 ${i+1} ${side==='from'?'起点':'终点'}`} value={edge[side]} onChange={e=>patchEdge(edge.id,{[side]:e.target.value,confirmation:'pending'})}>{value.nodes.map(n=><option key={n.resourceId} value={n.resourceId} disabled={n.resourceId===edge[side==='from'?'to':'from']}>{n.resourceId}</option>)}</select></label>)}<label>连接类型<select aria-label={`连线 ${i+1} 类型`} value={edge.kind} onChange={e=>patchEdge(edge.id,{kind:e.target.value,confirmation:'pending'})}>{Object.entries(edgeKinds).map(([key,name])=><option value={key} key={key}>{name}</option>)}</select></label><DiagramSourceField label={`连线 ${i+1} 来源`} value={edge.source} onChange={source=>patchEdge(edge.id,{source,confirmation:'pending'})}/><Confirmation label={`连线 ${i+1} 确认状态`} value={edge.confirmation} source={edge.source} onChange={confirmation=>patchEdge(edge.id,{confirmation})}/></div>{!edge.source.trim()&&<small className="wc-error">保存前请补充图纸或现场核对来源。</small>}<button type="button" className="secondary" onClick={()=>onChange({...value,edges:value.edges.filter(e=>e.id!==edge.id)})}>删除连线 {i+1}</button></div>)}
   <button type="button" className="secondary" disabled={value.nodes.length<2||value.edges.length>=4096} onClick={()=>{if(value.nodes.length<2)return;onChange({...value,edges:[...value.edges,{id:nextId('edge',value.edges),from:value.nodes[0].resourceId,to:value.nodes[1].resourceId,kind:'unknown',confirmation:'pending',source:value.source??''}]});}}>添加连线</button>
  </fieldset>
 </>;
}

function PathsForm({paths,onChange}:{paths:SignalPath[];onChange(value:SignalPath[]):void}){
 const [selected,setSelected]=useState('');const path=paths.find(p=>p.id===selected)??paths[0];
 const patch=(changes:Partial<SignalPath>)=>{if(path)onChange(paths.map(p=>p.id===path.id?{...p,...changes}:p));};
 const patchComponent=(id:string,changes:Partial<SignalComponent>)=>{if(path)patch({components:path.components.map(c=>c.id===id?{...c,...changes}:c),confirmation:'pending'});};
 const move=(index:number,delta:number)=>{if(!path||index+delta<0||index+delta>=path.components.length)return;const components=[...path.components];[components[index],components[index+delta]]=[components[index+delta],components[index]];patch({components,confirmation:'pending'});};
 return <><div className="cd-path-list">{paths.map(p=><button key={p.id} type="button" aria-pressed={path?.id===p.id} className={path?.id===p.id?'primary':'secondary'} onClick={()=>setSelected(p.id)}>{p.label||'未命名线路'}</button>)}<button type="button" className="secondary" disabled={paths.length>=256} onClick={()=>{const id=nextId('path',paths);onChange([...paths,{id,label:'新线路',direction:'output',confirmation:'pending',source:'',components:[]}]);setSelected(id);}}>添加线路</button></div>
  {path?<div key={path.id} className="cd-form-card"><div className="cd-form-grid"><Field label="线路名称" value={path.label} onChange={label=>patch({label})}/><Field label="机台名称" value={path.station} onChange={station=>patch({station:optionalText(station),confirmation:'pending'})}/><label>信号方向<select aria-label="信号方向" value={path.direction} onChange={e=>patch({direction:e.target.value,confirmation:'pending'})}><option value="output">仪器 → 芯片（输出激励）</option><option value="input">芯片 → 采集器（读出输入）</option><option value="bidirectional">双向</option></select></label><DiagramSourceField label="线路来源" value={path.source} placeholder="OneNote 页名、图纸编号或现场核对记录" onChange={source=>patch({source,confirmation:'pending'})}/><Field label="线路资料日期" type="date" value={path.sourceDate} onChange={sourceDate=>patch({sourceDate:optionalText(sourceDate),confirmation:'pending'})}/><Confirmation label="线路确认状态" value={path.confirmation} source={path.source} onChange={confirmation=>patch({confirmation})}/></div>
   {!path.source.trim()&&<p className="wc-error">保存前请填写线路来源；资料有出入时保留待核实。</p>}
   <ImageUpload image={path.image} label="上传线路原图" onChange={image=>patch({image,confirmation:'pending'})}/>{path.image?.source&&path.image.source!==path.source&&<DiagramSourceEvidence source={path.image.source} label="原图来源证据"/>}<p className="cd-note">原图可在上方线路预览中放大核对。按信号方向添加起点设备、中间部件、终点设备；上移 / 下移可调整顺序。</p>
   {path.components.map((component,index)=><fieldset key={component.id} className="cd-form-card"><legend>部件 {index+1} · {component.label||'未命名'}</legend><div className="cd-form-grid"><Field label={`部件 ${index+1} 名称`} value={component.label} onChange={label=>patchComponent(component.id,{label,confirmation:'pending'})}/><label>类型<select aria-label={`部件 ${index+1} 类型`} value={component.type} onChange={e=>patchComponent(component.id,{type:e.target.value,confirmation:'pending'})}>{Object.entries(componentKinds).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>{(['channel','fromPort','toPort','port','model'] as const).map(key=><Field key={key} label={`部件 ${index+1} ${{channel:'通道',fromPort:'入口端口',toPort:'出口端口',port:'端口别名',model:'型号',source:'来源'}[key]}`} value={component[key]} onChange={v=>patchComponent(component.id,{[key]:optionalText(v),confirmation:'pending'})}/>)}<DiagramSourceField label={`部件 ${index+1} 来源`} value={component.source} onChange={source=>patchComponent(component.id,{source:optionalText(source),confirmation:'pending'})}/>{(['temperatureK','attenuationDb','delayNs'] as const).map(key=><Field key={key} label={`部件 ${index+1} ${{temperatureK:'温级 K',attenuationDb:'衰减 dB',delayNs:'延时 ns'}[key]}`} value={component[key]} type="number" min={0} max={{temperatureK:1e5,attenuationDb:200,delayNs:1e9}[key]} onChange={v=>patchComponent(component.id,{[key]:v.trim()?Number(v):undefined,confirmation:'pending'})}/>)}<Confirmation label={`部件 ${index+1} 确认状态`} value={component.confirmation} source={component.source||path.source} onChange={confirmation=>patchComponent(component.id,{confirmation})}/></div><div className="cd-row-actions"><button type="button" className="secondary" disabled={index===0} onClick={()=>move(index,-1)}>上移部件 {index+1}</button><button type="button" className="secondary" disabled={index===path.components.length-1} onClick={()=>move(index,1)}>下移部件 {index+1}</button><button type="button" className="secondary" onClick={()=>patch({components:path.components.filter(c=>c.id!==component.id),confirmation:'pending'})}>删除部件 {index+1}</button></div></fieldset>)}
   {!path.components.length&&<p className="cd-missing">尚无部件。至少录入一个实际设备或部件后才能保存此线路。</p>}
   <div className="cd-row-actions"><button type="button" className="secondary" disabled={path.components.length>=100} onClick={()=>patch({components:[...path.components,{id:nextId('component',path.components),type:'other',label:'',confirmation:'pending'}],confirmation:'pending'})}>添加部件</button><button type="button" className="secondary" onClick={()=>{onChange(paths.filter(p=>p.id!==path.id));setSelected('');}}>删除线路</button></div>
  </div>:<p className="cd-missing">从一条有来源的读出线路开始录入。</p>}
 </>;
}
