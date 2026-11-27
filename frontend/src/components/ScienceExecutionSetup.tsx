import {useEffect,useMemo,useRef,useState} from 'react';
import type {CreateWorkflowInput,ScienceExecutionCatalog,ScienceExecutionScope,Workflow} from '../types';
import type {Configuration,WorkspaceGateway} from '../lib/workspaceGateway';
import type {ScienceRecord,ScienceManualContext} from '../lib/scienceGateway';
import {createScienceGateway} from '../lib/scienceGateway';
import {scienceCatalogPath,scienceDragModeLabels,scienceExecutionInput,scienceInheritDragSource,scienceMethodForValues,scienceParameterPresentation,scienceParameterValues,scienceRoleLabels,scienceSourceChoices,scienceSourceLabel} from '../lib/scienceExecutionDraft';
import {isConfirmedExecutionQubit,topologyExecutionTarget} from '../lib/topologyExecutionTarget';
import ChipTopology from './ChipTopology';

export interface ScienceExecutionSetupProps {
 api:WorkspaceGateway;chips:Configuration[];deployments:Configuration[];name:string;disabled:boolean;
 workflows?:Workflow[];executionProfile?:'science-v3'|'science-v3-irb'|'science-shape-repeat5'|'science-flux';
 initialWorkingPoint?:ScienceManualContext;
 onNameChange(name:string):void;onDraftChange(input:CreateWorkflowInput|undefined):void;
}
export default function ScienceExecutionSetup({api,chips,deployments,workflows=[],executionProfile='science-v3',initialWorkingPoint,name,disabled,onNameChange,onDraftChange}:ScienceExecutionSetupProps){
 const [identityMode,setIdentityMode]=useState<'published'|'observed'>(initialWorkingPoint&&!initialWorkingPoint.chipVersionId?'observed':'published');
 const [scope,setScope]=useState<ScienceExecutionScope>(initialWorkingPoint?{resourceId:initialWorkingPoint.resourceId??'',...(initialWorkingPoint.chipVersionId?{chipVersionId:initialWorkingPoint.chipVersionId,deploymentVersionId:initialWorkingPoint.deploymentVersionId}:{sourceWorkflowId:initialWorkingPoint.workflowId})}:{chipVersionId:'',deploymentVersionId:'',resourceId:''});
 const [catalog,setCatalog]=useState<ScienceExecutionCatalog>();const [records,setRecords]=useState<ScienceRecord[]>([]);
 const [procedureId,setProcedureId]=useState('');const [values,setValues]=useState<Record<string,string>>({});
 const [references,setReferences]=useState<Record<string,string>>({});const [loading,setLoading]=useState(false);const [error,setError]=useState('');const [refresh,setRefresh]=useState(0);
 const [workingPointId,setWorkingPointId]=useState(initialWorkingPoint?.recordId??'');
 const [nextCursor,setNextCursor]=useState<string|null>(null);const generation=useRef(0);
 const chip=chips.flatMap(c=>c.versions??[]).find(v=>v.id===scope.chipVersionId);
 const deployment=deployments.flatMap(c=>c.versions??[]).find(v=>v.id===scope.deploymentVersionId);
 const confirmedResource=(id:string)=>chip?.qubits?.filter(q=>q.id===id).length===1&&isConfirmedExecutionQubit(chip.qubits.find(q=>q.id===id));
 const validResource=identityMode==='observed'||confirmedResource(scope.resourceId);
 const path=validResource?scienceCatalogPath(scope,executionProfile):undefined;
 const sourceWorkflows=workflows.filter(w=>w.mode==='real'&&(('observedResourceBinding' in w)||w.nodes.some(n=>['ro_iq01','ro_optimize','ro_resonator_survey'].includes(n.procedureId??'')&&n.engineState==='verified')));
 const sourceWorkflow=sourceWorkflows.find(w=>w.id===scope.sourceWorkflowId);
 const observedBinding=catalog?.configurationBinding?.observedResourceBinding??catalog?.observedResourceBinding;
 useEffect(()=>{
  let current=true;generation.current++;setCatalog(undefined);setRecords([]);setNextCursor(null);setProcedureId('');setValues({});setReferences({});setError('');
  if(!path){setLoading(false);return ()=>{current=false;};}
  setLoading(true);
  void Promise.all([api.get<ScienceExecutionCatalog>(path),createScienceGateway(api).recordsPage(),initialWorkingPoint?createScienceGateway(api).record(initialWorkingPoint.recordId):Promise.resolve(undefined)]).then(([next,sources,point])=>{
   if(current){setCatalog(next);setRecords(point?[...sources.records.filter(r=>r.id!==point.id),point]:sources.records);setNextCursor(sources.hasMore?sources.nextCursor:null);
    if(point&&initialWorkingPoint?.resourceId===scope.resourceId){const method=next.manualMethods.find(m=>m.procedureId==='sq_flux_point_recheck');if(method){setProcedureId(method.procedureId);setValues(scienceParameterValues(method));setReferences({flux_scan:point.sourceRecordId??''});}}
   }
  }).catch(cause=>{if(current)setError(cause instanceof Error?cause.message:String(cause));}).finally(()=>{if(current)setLoading(false);});
  return ()=>{current=false;};
 },[api,path,refresh]);
 const originalMethod=catalog?.manualMethods?.find(item=>item.procedureId===procedureId);
 const method=originalMethod?scienceMethodForValues(originalMethod,values):undefined;
 const dragSource=records.find(record=>record.id===references.drag);
 let inheritedKeys:string[]=[];
 if(originalMethod?.procedureId==='sq_drag_irb'&&originalMethod.parameterKeysByMode&&dragSource){try{inheritedKeys=scienceInheritDragSource(originalMethod,values,dragSource).lockedKeys;}catch{/* Full source loading reports errors below. */}}
 const validation=useMemo(()=>{try{if(!validResource)throw new Error('请选择当前芯片版本中已确认实测身份的比特');const draft=scienceExecutionInput({executionProfile,name,scope,catalog,procedureId,values,references,records,workingPointId});if(identityMode==='published'){if(!deployment)throw new Error('请选择当前已发布的接线版本');draft.machine=(deployment as {machine?:string}).machine||deployment.name||'已发布接线';}return {draft,message:''};}catch(cause){return {draft:undefined,message:cause instanceof Error?cause.message:String(cause)};}},[name,scope,catalog,procedureId,values,references,records,executionProfile,workingPointId,validResource,deployment,identityMode]);
 useEffect(()=>{onDraftChange(validation.draft);},[validation.draft,onDraftChange]);
 const changeScope=(next:ScienceExecutionScope)=>{setScope(next);setReferences({});setProcedureId('');setValues({});onDraftChange(undefined);};
 const chooseResource=(id:string)=>{if(disabled)return;if(identityMode==='published'&&id&&!confirmedResource(id)){setError('此编号的实测身份尚未确认或不属于当前芯片版本，仅供浏览。');return;}setError('');changeScope({...scope,resourceId:id});};
 const loadMore=async()=>{if(!nextCursor||loading)return;const version=generation.current;setLoading(true);setError('');try{const page=await createScienceGateway(api).recordsPage(undefined,nextCursor);if(version===generation.current){setRecords(previous=>[...previous,...page.records.filter(record=>!previous.some(item=>item.id===record.id))]);setNextCursor(page.hasMore?page.nextCursor:null);}}catch(cause){if(version===generation.current)setError(cause instanceof Error?cause.message:String(cause));}finally{if(version===generation.current)setLoading(false);}};
 const locked=disabled||loading;
 const changeParameter=(key:string,value:string)=>{
  const next={...values,[key]:value};
  if(key==='drag_mode'&&originalMethod){
   const selected=scienceMethodForValues(originalMethod,next);
   setValues(Object.fromEntries(Object.entries({...scienceParameterValues({...selected,parameterDefaults:{...selected.parameterDefaults,drag_mode:value}}),...next}).filter(([name])=>name in selected.parameterBounds)));
   setReferences(Object.fromEntries(Object.entries(references).filter(([role])=>role!=='drag'&&selected.scienceSourceRoles.includes(role))));
  }else setValues(next);
  setError('');onDraftChange(undefined);
 };
 const selectSource=async(role:string,id:string)=>{
  setReferences(previous=>({...previous,[role]:id}));onDraftChange(undefined);
  if(role!=='drag'||!id||originalMethod?.procedureId!=='sq_drag_irb'||!originalMethod.parameterKeysByMode)return;
  const version=generation.current;setLoading(true);setError('');
  try{
   const source=await createScienceGateway(api).record(id);
   if(version!==generation.current)return;
   const inherited=scienceInheritDragSource(originalMethod,values,source);
   setRecords(previous=>[...previous.filter(record=>record.id!==id),source]);setValues(inherited.values);
   const selected=scienceMethodForValues(originalMethod,inherited.values);
   setReferences(previous=>Object.fromEntries(Object.entries(previous).filter(([name])=>selected.scienceSourceRoles.includes(name))));
  }catch(cause){if(version===generation.current){setReferences(previous=>({...previous,drag:''}));setError(cause instanceof Error?cause.message:String(cause));}}
  finally{if(version===generation.current)setLoading(false);}
 };
 const selectPoint=async(id:string)=>{const version=generation.current;setWorkingPointId(id);onDraftChange(undefined);if(!id)return;setLoading(true);try{const point=await createScienceGateway(api).record(id);if(version!==generation.current)return;setRecords(rows=>[...rows.filter(r=>r.id!==id),point]);setReferences(refs=>({...refs,flux_scan:point.sourceRecordId??''}));}catch(cause){if(version===generation.current)setError(cause instanceof Error?cause.message:String(cause));}finally{if(version===generation.current)setLoading(false);}};
 return <div aria-label="科学实验配置">
  <div className="mw-tabs" role="group" aria-label="科学资源身份"><button type="button" aria-pressed={identityMode==='published'} disabled={disabled} onClick={()=>{setIdentityMode('published');changeScope({chipVersionId:'',deploymentVersionId:'',resourceId:''});}}>已发布芯片与接线</button><button type="button" aria-pressed={identityMode==='observed'} disabled={disabled} onClick={()=>{setIdentityMode('observed');changeScope({sourceWorkflowId:'',resourceId:''});}}>已观测资源（版图未确认）</button></div>
  <p className="cd-note">{identityMode==='published'?'先选择已发布芯片、已核对接线和目标比特，再加载该配置实际支持的实验。':'从已有真实采集任务建立实测资源绑定。来源读出、设备和接线会重新核对；比特标签不代表已确认版图身份。'}创建草稿后，在实验配置页人工启动采集。</p>
  <div className="mw-object-fields">
   {identityMode==='published'?<><label><span>芯片版本</span><select aria-label="科学实验芯片版本" value={scope.chipVersionId??''} disabled={disabled} onChange={e=>changeScope({chipVersionId:e.target.value,deploymentVersionId:'',resourceId:''})}><option value="">选择已发布芯片</option>{chips.flatMap(c=>(c.versions??[]).map((v,i)=><option key={v.id} value={v.id}>{c.name} · 发布 {i+1}</option>))}</select></label>
   <label><span>接线版本</span><select aria-label="科学实验接线版本" value={scope.deploymentVersionId??''} disabled={disabled||!chip} onChange={e=>changeScope({...scope,deploymentVersionId:e.target.value})}><option value="">选择已核对接线</option>{deployments.flatMap(c=>(c.versions??[]).map((v,i)=><option key={v.id} value={v.id}>{c.name} · 发布 {i+1}</option>))}</select></label></>:<label className="mw-wide"><span>实测资源来源任务</span><select aria-label="实测资源来源任务" value={scope.sourceWorkflowId??''} disabled={disabled} onChange={e=>changeScope({sourceWorkflowId:e.target.value,resourceId:''})}><option value="">选择已记录独立读出复测的任务</option>{sourceWorkflows.map(w=><option key={w.id} value={w.id}>{w.name} · {(w.resourceIds??[w.target]).join('、')} · 实测资源</option>)}</select>{!sourceWorkflows.length&&<small>先完成真实读出采集和独立验收，再刷新任务列表。</small>}</label>}
   {identityMode==='observed'&&executionProfile==='science-flux'&&<label><span>偏置接线版本</span><select aria-label="偏置接线版本" value={scope.biasDeploymentVersionId??''} disabled={disabled||!sourceWorkflow} onChange={e=>changeScope({...scope,biasDeploymentVersionId:e.target.value||undefined})}><option value="">沿用来源已冻结偏置线路（若有）</option>{deployments.flatMap(c=>(c.versions??[]).map((v,i)=><option key={v.id} value={v.id}>{c.name} · 发布 {i+1}</option>))}</select><small>新接线须已发布且与来源的设备、RF/ADC 通道相符；不据此确认芯片版图身份。</small></label>}
   <label><span>目标比特</span><select aria-label="科学实验目标比特" value={validResource?scope.resourceId:''} disabled={disabled||(identityMode==='published'?!chip:!sourceWorkflow)} onChange={e=>chooseResource(e.target.value)}><option value="">选择目标比特</option>{identityMode==='published'?(chip?.qubits??[]).map(q=><option key={q.id} value={q.id} disabled={!confirmedResource(q.id)}>{q.label||q.id}{confirmedResource(q.id)?'':' · 实测身份待核实'}</option>):(sourceWorkflow?.resourceIds??[]).map(id=><option key={id} value={id}>实测资源 {id}／版图未确认</option>)}</select></label>
   <label><span>实验名称</span><input aria-label="科学实验名称" value={name} maxLength={120} disabled={disabled} onChange={e=>onNameChange(e.target.value)} placeholder="输入实验名称"/></label>
   <label className="mw-wide"><span>实验方法</span><select aria-label="科学实验方法" value={procedureId} disabled={locked||!catalog} onChange={e=>{const selected=catalog?.manualMethods.find(m=>m.procedureId===e.target.value);setProcedureId(e.target.value);setReferences({});setValues(selected?scienceParameterValues(selected):{});onDraftChange(undefined);}}><option value="">{loading?'正在读取配置能力…':'选择实验方法'}</option>{catalog?.manualMethods?.map(item=><option key={item.procedureId} value={item.procedureId}>{item.label}{item.capability==='available'?'':' · 暂不可执行'}</option>)}</select></label>
  </div>
  {identityMode==='published'&&chip&&<ChipTopology key={scope.chipVersionId} scope={scope.chipVersionId??''} topology={chip.topology} qubits={chip.qubits} selectedResourceId={validResource?scope.resourceId:''} disabled={disabled} onSelect={id=>{const target=topologyExecutionTarget(chip.topology,chip.qubits??[],id);if(target.resourceId)chooseResource(target.resourceId);else setError(target.reason??'此比特仅供浏览');}}/>}
  {path&&<button type="button" className="secondary" disabled={locked} onClick={()=>setRefresh(n=>n+1)}>刷新能力与来源</button>}
  {error&&<p className="mw-scan-error" role="alert">{error}</p>}
  {identityMode==='observed'&&catalog&&<p role="status" className="cd-note">{catalog.configurationBinding?.identityLabel??`实测资源 ${scope.resourceId}／版图未确认`} · 只复用已核验来源链，未建立版图身份。</p>}
  {catalog&&catalog.preflight?.staticReady!==true&&<p className="mw-scan-error" role="status">配置预检未通过：{catalog.preflight?.reason||'尚未取得有效预检结果'}</p>}
  {executionProfile==='science-flux'&&<p className="cd-note">偏置任务须使用已核实的直流线路。设定电压策略记录真实命令回执，未独立测压，结束时返回声明的设置；测压策略独立测量输出并恢复初始设置。任务结束后不保持所选偏置，未知回执或结束失败需人工处理。</p>}
  {catalog?.preflight?.missingFields&&Object.values(catalog.preflight.missingFields).some(items=>items.length>0)&&<div role="status" className="mw-scan-error">偏置配置尚未完成，不能开始采集。<ul>{Object.entries(catalog.preflight.missingFields).flatMap(([resource,items])=>items.map(field=><li key={resource+field}>{resource}：{field.includes('provider')||field.includes('提供器')?'缺少已审核的硬件寄存器与实测电压回读提供器':field===`biasRoutes.${resource}`?'缺少明确的偏置线路和恢复约定':'偏置线路字段缺失或未通过校验'}</li>))}</ul><details><summary>配置缺项详情</summary><pre>{JSON.stringify(catalog.preflight.missingFields,null,2)}</pre></details></div>}
  {method&&<>
   {method.parameterBounds.drag_mode?.enum&&<label className="science-field"><span>DRAG 系数方案</span><select aria-label="DRAG 系数方案" value={values.drag_mode??'normalized_alpha'} disabled={locked||inheritedKeys.includes('drag_mode')} onChange={e=>changeParameter('drag_mode',e.target.value)}>{method.parameterBounds.drag_mode.enum.map(mode=><option key={String(mode)} value={String(mode)}>{scienceDragModeLabels[String(mode)]??String(mode)}</option>)}</select></label>}
   {values.drag_mode==='empirical_beta'&&<p className="cd-note">β 直接乘包络导数：Q = β · dI/dt。此处填写 ns（1 ns = 10⁻⁹ s）。</p>}
   {inheritedKeys.length>0&&<p className="cd-note">门参数沿用所选扫描的已验收候选；更换门请重新选择来源。</p>}
   {method.procedureId==='sq_drag_half_scan'&&<p className="cd-note">半门扫描必须实际包含零系数；固定 X/2 门扫描和候选复测不等于门保真度。完成后再选择同一半门 IRB。</p>}
   {method.procedureId==='sq_flux_point_recheck'&&<label className="science-field"><span>原生扫描点位</span><select aria-label="原生扫描点位" value={workingPointId} disabled={locked} onChange={e=>void selectPoint(e.target.value)}><option value="">选择已保存的工作点或闲置点</option>{records.filter(r=>r.kind==='working_point'&&r.scope.resourceId===scope.resourceId).map(r=><option key={r.id} value={r.id}>{r.label||'工作点'} · {scienceSourceLabel(r)}</option>)}</select><small>读取完整点位后绑定原扫描；来源、偏置策略及首测与独立复测的命令或测压证据在服务器重新核验。</small></label>}
   {method.capability!=='available'&&<p className="mw-scan-error" role="status">{method.reason||catalog?.capabilities?.procedures?.[method.procedureId]?.reason||'该方法在所选接线下尚不可执行，请核对已发布线路能力。'}</p>}
   <h3>前置实测来源</h3><p className="cd-note">从已加载的 {records.length} 条记录中筛选，{identityMode==='observed'?'属于同一实测资源绑定':'同一芯片、接线和比特'}且记录了独立复测的当前采集。模型不能作为前置标定；开始采集时服务器还会核对原始文件、工作点和脉冲身份。</p>
   {nextCursor&&<button type="button" className="secondary" disabled={locked} onClick={()=>void loadMore()}>加载更早的来源记录</button>}
   <div className="mw-object-fields">{[...method.scienceSourceRoles].sort((a,b)=>Number(b==='drag')-Number(a==='drag')).map(role=>{
    const choices=scienceSourceChoices(records,method,role,scope,observedBinding),selected=choices.find(r=>r.id===references[role]),p=selected?.result?.parameters;
    return <label key={role} className="mw-wide"><span>{scienceRoleLabels[role]??role}</span><select aria-label={`前置来源：${scienceRoleLabels[role]??role}`} value={references[role]??''} disabled={locked} onChange={e=>void selectSource(role,e.target.value)}><option value="">{choices.length?'选择已独立复测的采集':'暂无符合条件的采集记录'}</option>{choices.map(r=><option key={r.id} value={r.id}>{scienceSourceLabel(r)}</option>)}</select>{selected?<small>当前记录 · 独立复测已记录{typeof p?.drive_hz==='number'?` · 驱动 ${(p.drive_hz/1e9).toPrecision(7)} GHz`:''}{typeof p?.readout_hz==='number'?` · 读出 ${(p.readout_hz/1e9).toPrecision(7)} GHz`:''}{typeof p?.flux_bias==='number'?` · 偏置 ${p.flux_bias}`:''} · 执行前复核</small>:<small>先完成对应实验，并在科学工作台保存该采集记录。</small>}</label>;
   })}</div>
   <h3>采集参数</h3><p className="cd-note">范围和初值来自所选配置的执行器。空白参数沿用执行器配置；没有适用值时，控制器会在采集前阻止运行。</p>
   <div className="mw-object-fields">{Object.entries(method.parameterBounds??{}).filter(([key])=>key!=='drag_mode').map(([key,rule])=>{const field=scienceParameterPresentation(key,rule),label=field.label+(field.unit?` / ${field.unit}`:''),fieldLocked=locked||inheritedKeys.includes(key);return <label key={key}><span>{label}</span>{rule.enum?<select aria-label={label} value={values[key]??''} disabled={fieldLocked} onChange={e=>changeParameter(key,e.target.value)}><option value="">沿用执行器配置</option>{rule.enum.map(value=><option key={String(value)} value={String(value)}>{String(value)}</option>)}</select>:<input aria-label={label} inputMode="decimal" value={values[key]??''} disabled={fieldLocked||rule.type==='array'} onChange={e=>changeParameter(key,e.target.value)} placeholder={rule.type==='numberArray'?'明确数值，以逗号分隔':rule.type==='integerArray'?'整数以逗号分隔':rule.type==='array'?'该数组参数暂不支持手动提交':'沿用执行器配置'}/>}<small>{inheritedKeys.includes(key)?'沿用已验收的门参数':rule.enum?'以目录枚举为准':rule.type==='numberArray'?`${rule.required?'必填 · ':''}${rule.minItems} 至 ${rule.maxItems} 个数值，${field.min} 至 ${field.max} ${field.unit}${rule.strictlyIncreasing?'，严格递增':''}`:rule.type==='integerArray'?`${rule.minItems} 至 ${rule.maxItems} 个整数，${rule.min} 至 ${rule.max}，严格递增`:rule.type==='array'?'需执行器提供可提交的标量范围':`${field.min??'—'} 至 ${field.max??'—'} ${field.unit}${rule.integer?' · 整数':''}`}</small></label>;})}</div>
  </>}
  {path&&!loading&&!error&&validation.message&&<p role="status" className="cd-note">{validation.message}</p>}
 </div>;
}
