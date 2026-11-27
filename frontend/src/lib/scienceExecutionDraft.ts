import type {CreateWorkflowInput, ObservedResourceBinding, ScienceExecutionCatalog, ScienceExecutionMethod, ScienceExecutionScope, ScienceParameterBound} from '../types.ts';
import type {ScienceRecord} from './scienceGateway.ts';

export const scienceRoleLabels:Record<string,string>={cavity_seed:'读取腔实测种子（非分类器标定）',flux_scan:'同线路原生偏置扫描',f01:'比特 0→1 定频',rabi_amplitude:'目标形状幅度 Rabi 来源（与 π 来源相同）',rabi_time:'时间 Rabi 工作点 / 时间窗来源（DC 支持依据）',readout:'读出标定',drag:'同一 X/2 DRAG 系数扫描与复测',pi01:'0→1 π 脉冲',half01:'0→1 半幅门验证',anharmonicity:'非谐性来源',spectrum12:'条件 1→2 定频',pi12:'1→2 π 脉冲'};
const labels:Record<string,string>={bias_values_v:'偏置设定电压列表',readout_frequencies_hz:'读取腔扫描频点',local_readout_frequencies_hz:'点位局部腔扫描频点',drive_frequencies_hz:'可选比特谱频点',spectroscopy_width_s:'比特谱激励时长',pulse_shape:'目标脉冲形状',pulse_length_s:'目标脉冲时长',pulse_sigma_s:'目标高斯宽度',shape_pi_amp:'目标 π 幅度',pulse_phase_rad:'目标脉冲相位',drag_lambda1:'DRAG 系数 λ',anharmonicity_hz:'非谐性',prep01_shape:'制备脉冲形状',prep01_sigma_s:'制备高斯宽度',probe12_shape:'探测脉冲形状',probe12_length_s:'探测脉冲时长',probe12_sigma_s:'探测高斯宽度',probe12_amplitude:'探测脉冲幅度',probe12_phase_rad:'探测脉冲相位',f12_hz:'1→2 频率',shots:'每块采样次数',capture_repeats:'独立采集轮数',acquisitions_per_point:'每点采集块数',points:'扫描点数',amp_start:'幅度起点',amp_stop:'幅度终点',start_hz:'频率起点',stop_hz:'频率终点',lambda_start:'系数起点',lambda_stop:'系数终点',drag_repeats:'DRAG 序列重复次数',drive_hz:'0→1 驱动频率',drive_amp:'制备 π 幅度',drive_length_s:'制备 π 时长',half_pi_amp:'半 π 幅度',readout_hz:'读出频率',readout_amp:'读出幅度',readout_width_s:'读出时长',xy_delay_s:'XY 延迟',xy_attenuation_db:'XY 衰减',xy_filter_mhz:'XY 滤波频率',order_seed:'扫描顺序种子',rb_lengths:'Clifford 序列长度',rb_sequences:'每种长度独立随机序列数',rb_captures:'每个序列采集轮数',reference_captures:'PRE / POST 独立参考轮数',rb_master_seed:'随机序列主种子',rb_bootstrap_replicates:'配对 bootstrap 次数',rb_target:'交错目标门'};
export function scienceCatalogPath(scope:ScienceExecutionScope,executionProfile:'science-v3'|'science-v3-irb'|'science-shape-repeat5'|'science-flux'='science-v3'):string|undefined {
 if(!scope.resourceId?.trim())return undefined;
 const query:Record<string,string>={executionProfile,resourceId:scope.resourceId};
 if(scope.sourceWorkflowId){if(scope.chipVersionId||scope.deploymentVersionId)return undefined;query.sourceWorkflowId=scope.sourceWorkflowId;if(scope.biasDeploymentVersionId){if(executionProfile!=='science-flux')return undefined;query.biasDeploymentVersionId=scope.biasDeploymentVersionId;}}
 else{if(!scope.chipVersionId?.trim()||!scope.deploymentVersionId?.trim())return undefined;query.chipVersionId=scope.chipVersionId;query.deploymentVersionId=scope.deploymentVersionId;}
 return '/catalog?'+new URLSearchParams(query).toString();
}
function shift(value:number,exponent:number){const [mantissa,power='0']=String(value).split('e');return Number(`${mantissa}e${Number(power)+exponent}`);}
export const scienceDragModeLabels:Record<string,string>={normalized_alpha:'已测非谐性 · λ',empirical_beta:'经验导数系数 · β'};
const betaLabels:Record<string,string>={drag_mode:'DRAG 系数方案',drag_beta_s:'导数系数 β',beta_start_s:'β 扫描起点',beta_stop_s:'β 扫描终点',verify_drag_beta_s:'复测导数系数 β'};
export function scienceMethodForValues(method:ScienceExecutionMethod,values:Record<string,string>={}):ScienceExecutionMethod{
 const mode=values.drag_mode||String(method.parameterDefaults?.drag_mode??method.parameterBounds.drag_mode?.default??'normalized_alpha');
 const keys=method.parameterKeysByMode?.[mode];
 return {...method,scienceSourceRoles:method.scienceSourceRolesByMode?.[mode]??method.scienceSourceRoles,
  parameterBounds:keys?Object.fromEntries(Object.entries(method.parameterBounds).filter(([key])=>keys.includes(key))):method.parameterBounds};
}
export function scienceParameterPresentation(key:string,rule:ScienceParameterBound){
 let exponent=0,unit=rule.unit??'';
 if(key in betaLabels&&key.endsWith('_s')&&(!unit||unit==='s')){exponent=-9;unit='ns';}
 else if(!unit&&key.endsWith('_hz')){exponent=key==='anharmonicity_hz'?6:9;unit=exponent===6?'MHz':'GHz';}
 else if(!unit&&key.endsWith('_s')){exponent=key==='readout_width_s'?-6:-9;unit=exponent===-6?'µs':'ns';}
 else if(!unit&&key.endsWith('_v'))unit='V';
 else if(!unit&&key.endsWith('_rad'))unit='rad';
 else if(!unit&&key.endsWith('_db'))unit='dB';
 else if(!unit&&key.endsWith('_mhz'))unit='MHz';
 return {label:rule.label??betaLabels[key]??labels[key]??key,unit,exponent,min:typeof rule.min==='number'?shift(rule.min,-exponent):undefined,max:typeof rule.max==='number'?shift(rule.max,-exponent):undefined};
}
export function scienceParameterValues(method:ScienceExecutionMethod):Record<string,string>{
 method=scienceMethodForValues(method);
 return Object.fromEntries(Object.entries(method.parameterBounds??{}).map(([key,rule])=>{const value=method.parameterDefaults?.[key]??rule.default,exponent=scienceParameterPresentation(key,rule).exponent;return [key,typeof value==='number'?String(shift(value,-exponent)):typeof value==='string'?value:Array.isArray(value)?value.map(v=>shift(v,-exponent)).join(', '):''];}));
}
export function scienceParameters(method:ScienceExecutionMethod,values:Record<string,string>):Record<string,number|string|number[]>{
 const result:Record<string,number|string|number[]>={};
 if(Object.keys(values).some(key=>!Object.hasOwn(method.parameterBounds,key)))throw new Error('参数已不属于当前方法，请重新选择');
 method=scienceMethodForValues(method,values);
 for(const [key,rule] of Object.entries(method.parameterBounds??{})){
  const text=(values[key]??'').trim(),field=scienceParameterPresentation(key,rule);
  if(!text&&rule.required)throw new Error(`请明确填写${field.label}，不会自动填入偏置或扫描频点`);
  if(rule.type==='numberArray'){
   if(!text)continue;
   const numeric='[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?';
   if(!new RegExp(`^${numeric}(?:\\s*[,，]\\s*${numeric}|\\s+${numeric})*$`).test(text))throw new Error(`${field.label}请填有限数值，以逗号分隔`);
   const parts=text.split(/[,，\s]+/),array=parts.map(v=>shift(Number(v),field.exponent));
   if(rule.minItems==null||rule.maxItems==null||rule.min==null||rule.max==null||parts.length<rule.minItems||parts.length>rule.maxItems||array.some(v=>!Number.isFinite(v)||v<rule.min!||v>rule.max!))throw new Error(`${field.label}须填写 ${rule.minItems} 至 ${rule.maxItems} 个范围内数值`);
   if(rule.unique&&new Set(array).size!==array.length||rule.strictlyIncreasing&&array.some((v,i)=>i>0&&v<=array[i-1]))throw new Error(`${field.label}不能重复${rule.strictlyIncreasing?'，且须严格递增':''}`);
   result[key]=array;continue;
  }
  if(rule.type==='integerArray'){
   if(!text)continue;
   const parts=text.split(/[,，\s]+/).filter(Boolean);
   if(rule.minItems==null||rule.maxItems==null||rule.min==null||rule.max==null||parts.length<rule.minItems||parts.length>rule.maxItems||parts.some(v=>!/^\d+$/.test(v)))throw new Error(`${field.label}需填写 ${rule.minItems} 至 ${rule.maxItems} 个整数，以逗号分隔`);
   const array=parts.map(Number);
   if(array.some(v=>!Number.isSafeInteger(v)||v<rule.min!||v>rule.max!)||rule.strictlyIncreasing&&array.some((v,i)=>i>0&&v<=array[i-1]))throw new Error(`${field.label}需在 ${rule.min} 至 ${rule.max} 内严格递增`);
   result[key]=array;continue;
  }
  if(rule.type==='array')throw new Error(`${field.label}数组参数尚不支持手动提交`);
  if(rule.enum){if(!text)continue;const found=rule.enum.find(v=>String(v)===text);if(found===undefined)throw new Error(`${field.label}请选择目录允许的值`);result[key]=found;continue;}
  if(typeof rule.min!=='number'||typeof rule.max!=='number'||!Number.isFinite(rule.min)||!Number.isFinite(rule.max)||rule.min>rule.max)throw new Error(`${field.label}参数范围不支持，请刷新目录`);
  if(!text)continue;
  if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text))throw new Error(`${field.label}请输入有限数值`);
  const value=shift(Number(text),field.exponent);
  if(!Number.isFinite(value)||value<rule.min||value>rule.max||rule.integer&&!Number.isInteger(value))throw new Error(`${field.label}需在 ${field.min} 至 ${field.max} ${field.unit}${rule.integer?'，且为整数':''}`);
  result[key]=value;
 }
 for(const [start,stop] of [['amp_start','amp_stop'],['start_hz','stop_hz'],['lambda_start','lambda_stop'],['beta_start_s','beta_stop_s']])if(typeof result[start]==='number'&&typeof result[stop]==='number'&&result[start]>=result[stop])throw new Error('扫描终点必须大于起点');
 return result;
}
// Full-pi calibration stays in parameters; the identity amplitude is already halved.
const dragIdentityKeys=['drag_mode','drag_beta_s','drag_lambda1','anharmonicity_hz','pulse_shape','pulse_length_s','pulse_sigma_s','shape_pi_amp','pulse_phase_rad','drive_hz','readout_hz','readout_amp','readout_width_s','xy_delay_s','xy_attenuation_db','xy_filter_mhz'];
export function scienceInheritDragSource(method:ScienceExecutionMethod,values:Record<string,string>,source:ScienceRecord){
 const pulse=source.result?.targetPulseIdentity?.identity;
 if(source.summary===true||!pulse||!source.result?.targetPulseIdentity?.sha256||source.result?.procedureId!=='sq_drag_half_scan')throw new Error('请加载完整的半门扫描与复测记录');
 const mode=pulse.dragMode??'normalized_alpha';
 if(!['normalized_alpha','empirical_beta'].includes(mode))throw new Error('扫描记录的系数方案不受支持');
 const parameters={...source.result?.parameters,drag_mode:mode};
 if(mode==='empirical_beta'){
  if(typeof pulse.betaSeconds!=='number'||!Number.isFinite(pulse.betaSeconds))throw new Error('扫描记录缺少已验收的 β');
  parameters.drag_beta_s=pulse.betaSeconds;
 }else{
  if(typeof pulse.lambda1!=='number'||typeof pulse.anharmonicityHz!=='number')throw new Error('扫描记录缺少已验收的 λ 或非谐性');
  parameters.drag_lambda1=pulse.lambda1;parameters.anharmonicity_hz=pulse.anharmonicityHz;
 }
 const selected=scienceMethodForValues(method,{drag_mode:mode});
 const next:Record<string,string>={...scienceParameterValues({...selected,parameterDefaults:{...method.parameterDefaults,drag_mode:mode}}),...values,drag_mode:mode};
 const lockedKeys:string[]=[];
 for(const key of dragIdentityKeys){
  const rule=selected.parameterBounds[key],value=parameters[key];
  if(!rule)continue;
  const optionalSigma=key==='pulse_sigma_s'&&parameters.pulse_shape!=='Gaussian';
  if(!optionalSigma&&(value==null||value===''||typeof value==='number'&&!Number.isFinite(value)))throw new Error(`扫描记录缺少${scienceParameterPresentation(key,rule).label}，请重新选择完整来源`);
  lockedKeys.push(key);
  next[key]=typeof value==='number'?String(shift(value,-scienceParameterPresentation(key,rule).exponent)):typeof value==='string'?value:'';
 }
 return {values:Object.fromEntries(Object.entries(next).filter(([key])=>key in selected.parameterBounds)),lockedKeys};
}
export function scienceSourceChoices(records:ScienceRecord[],method:ScienceExecutionMethod,role:string,scope:ScienceExecutionScope,binding?:ObservedResourceBinding){
 const procedures=method.scienceSourceMethods?.[role]??[];
 return records.filter(record=>record.kind==='measurement'&&record.dataKind==='real'&&!!record.reference
  &&record.scope.resourceId===scope.resourceId
  &&(scope.sourceWorkflowId?!!binding&&binding.resourceId===scope.resourceId&&binding.identityClaim==='measured_resource_not_design_identity'&&binding.physicalDesignId===null
    &&(record.scope.workflowId===binding.sourceWorkflowId||record.scope.observedResourceBindingSha256===binding.sha256)
    :!!scope.chipVersionId&&!!scope.deploymentVersionId&&record.scope.chipVersionId===scope.chipVersionId&&record.scope.deploymentVersionId===scope.deploymentVersionId)
  &&record.evidenceCurrent===true&&record.evidenceIntact===true&&record.provenance?.independentlyVerified===true
  &&record.result?.quality?.status==='passed'&&!record.result?.partial
  &&procedures.includes(record.result?.procedureId??record.provenance?.procedureId));
}
export function scienceSourceLabel(record:ScienceRecord){
 const date=record.createdAt?new Date(record.createdAt).toLocaleString('zh-CN',{hour12:false}):'采集时间未提供';
 return `${record.label||record.result?.methodLabel||'采集记录'} · ${record.scope.resourceId??'比特未记录'} · ${date}`;
}
export interface ScienceExecutionDraft {workingPointId?:string;executionProfile?:'science-v3'|'science-v3-irb'|'science-shape-repeat5'|'science-flux';name:string;scope:ScienceExecutionScope;catalog?:ScienceExecutionCatalog;procedureId:string;values:Record<string,string>;references:Record<string,string>;records:ScienceRecord[];}
export function scienceExecutionInput(draft:ScienceExecutionDraft):CreateWorkflowInput {
 const {name,scope,catalog,procedureId,values,references,records,executionProfile='science-v3'}=draft;
 if(!name.trim())throw new Error('请填写实验名称');
 if(!scienceCatalogPath(scope,executionProfile)||catalog?.executionProfile!==executionProfile||Object.entries(scope).some(([key,value])=>value!=null&&value!==''&&catalog.configurationBinding?.[key as keyof ScienceExecutionScope]!==value))throw new Error('请加载所选芯片、接线和比特的实验目录');
 const original=catalog.manualMethods.find(item=>item.procedureId===procedureId);
 if(!original||original.capability!=='available'||catalog.preflight?.staticReady!==true)throw new Error('该方法未通过当前配置预检');
 const method=scienceMethodForValues(original,values);
 if(!Array.isArray(method.scienceSourceRoles)||!method.scienceSourceRoles.length)throw new Error('目录未提供前置来源要求');
 const knownRoles=new Set([...original.scienceSourceRoles,...Object.values(original.scienceSourceRolesByMode??{}).flat()]);
 if(Object.keys(references).some(role=>!knownRoles.has(role)))throw new Error('来源选择已不属于当前实验方法');
 const binding=catalog.configurationBinding.observedResourceBinding??catalog.observedResourceBinding;
 const refs=method.scienceSourceRoles.map(role=>{const id=references[role];if(!scienceSourceChoices(records,method,role,scope,binding).some(r=>r.id===id))throw new Error(`请选择同一配置、当前有效的${scienceRoleLabels[role]??role}来源`);return {role,recordId:id};});
 if(procedureId==='sq_drag_irb'&&original.parameterKeysByMode){
  const source=records.find(record=>record.id===references.drag);
  if(!source)throw new Error('请选择已验收的半门扫描与复测记录');
  const inherited=scienceInheritDragSource(original,values,source);
  const actual=scienceParameters(original,values),expected=scienceParameters(original,inherited.values);
  if(inherited.lockedKeys.some(key=>actual[key]!==expected[key]))throw new Error('门参数必须沿用所选半门扫描与复测记录');
 }
 let workingPointId:string|undefined;
 if(procedureId==='sq_flux_point_recheck'){
  const point=records.find(r=>r.id===draft.workingPointId);
  if(point?.kind!=='working_point'||point.sourceRecordId!==references.flux_scan||point.scope.resourceId!==scope.resourceId||!['qcal-flux-point-evidence/1','qcal-flux-point-evidence/2'].includes(point.result?.fluxPointEvidence?.schema))throw new Error('请选择来自同一原生扫描的完整点位记录');
  workingPointId=point.id;
 }
 return { ...(workingPointId?{manualWorkingPointId:workingPointId}:{}),name:name.trim(),machine:'八号机',qubits:1,target:scope.resourceId,type:'adaptive',policy:'strict',scenario:'normal',mode:'real',coverage:'full',experiment:'single',resourceIds:[scope.resourceId],...(scope.sourceWorkflowId?{sourceWorkflowId:scope.sourceWorkflowId,...(scope.biasDeploymentVersionId?{biasDeploymentVersionId:scope.biasDeploymentVersionId}:{})}:{chipVersionId:scope.chipVersionId!,deploymentVersionId:scope.deploymentVersionId!}),executionProfile,manualProcedureId:procedureId,manualParameters:scienceParameters(original,values),manualScienceReferences:refs};
}
