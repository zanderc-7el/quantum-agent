import type {ScienceInput,ScienceRecord} from './scienceGateway';

const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
export const blochDefaults=():ScienceInput=>({sequence:'x',initialState:'0',durationNs:50,points:201,rabiHz:1e7,detuningHz:0,t1Ns:50000,t2Ns:30000,piPulses:4,phaseDeg:0,analysisPhaseHz:0,analysisPhaseDeg:0,readoutSign:1,refocusAxis:'x'});
export interface BlochReplay {
 input:ScienceInput;sourceFields:Record<string,string>;missingFields:string[];warnings:string[];
 source:{recordId:string;procedureId?:string;acquiredAt?:string;resourceId?:string;quality?:string};
}
/** Metadata projection for a model only. Never grants calibration or measurement acceptance. */
export function deriveBlochReplay(record:ScienceRecord):BlochReplay {
 const result=record.result??{},p=result.parameters??{},procedure=result.procedureId;
 const out:BlochReplay={input:{},sourceFields:{},missingFields:[],warnings:[],source:{recordId:record.id,procedureId:procedure,acquiredAt:result.acquiredAt,resourceId:record.scope.resourceId,quality:result.quality?.status}};
 const put=(key:string,value:any,label:string)=>{out.input[key]=value;out.sourceFields[key]=label;};
 const sequence=({sq_rabi_time:'x',sq_t1:'t1',sq_ramsey:'ramsey',sq_echo:'echo',sq_cpmg:'cpmg'} as Record<string,string>)[procedure??''];
 if(record.kind!=='measurement'||(record.dataKind??result.dataKind)!=='real'||record.summary===true||record.evidenceIntact===false||!sequence||!record.reference?.attemptId||record.reference.workflowId!==record.scope.workflowId){
  out.warnings.push('该记录缺少可带入的完整测量来源；仅可独立查看人工模型。');return out;
 }
 put('sequence',sequence,'归档实验');put('initialState',sequence==='x'?'0':sequence==='t1'?'1':'+x','已支持序列的模型初态');
 const trace=(result.sourceSeries??result.series??[])[0];
 const xs=trace?.x??result.axes?.x?.values;
 const scale=({s:1e9,us:1000,'μs':1000,'µs':1000,ns:1} as Record<string,number>)[trace?.xUnit??result.axes?.x?.unit??''];
 if(scale&&Array.isArray(xs)&&xs.length>=2&&xs.length<=4096&&xs.every((x,i)=>finite(x)&&x>=0&&(i===0||x>xs[i-1]))&&xs[xs.length-1]*scale>0&&xs[xs.length-1]*scale<=1e10){
  put('timesNs',xs.map(x=>x*scale),'归档扫描点');put('durationNs',xs[xs.length-1]*scale,'归档时间范围');
 }else out.missingFields.push('有效时间轴与单位');
 if(sequence==='cpmg'){
  if(Number.isInteger(p.pi_pulses)&&p.pi_pulses>=1&&p.pi_pulses<=64)put('piPulses',p.pi_pulses,'归档 π 脉冲数');
  else out.missingFields.push('π 脉冲数 N');
 }
 if(['ramsey','echo','cpmg'].includes(sequence)){
  if(p.pulse_compatibility==='f8_validated_short_half_dc_buff2ns_v1'&&(sequence==='cpmg'||finite(p.rotation_hz))){
   // Same model-coordinate convention as ScienceService._simulation. The
   // virtual analysis rotation is not free-evolution detuning.
   put('readoutSign',-1,'已核对原生末端 π/2 投影');put('analysisPhaseDeg',0,'原生分析相位约定');
   put('analysisPhaseHz',sequence==='cpmg'?0:-p.rotation_hz,'归档末端分析相位扫描');
   put('refocusAxis',sequence==='echo'?'y':'x','原生旋转轴的模型坐标映射');
  }else{out.missingFields.push('已核对的制备与分析相位');out.warnings.push('原记录的相位约定不完整，当前相位使用人工模型设定，不代表同序列重放。');}
 }
 const fitField=sequence==='t1'?['t1_s','t1Ns','T₁']:sequence==='x'?['rabi_frequency_hz','rabiHz','Rabi 频率']:['t2_s','t2Ns',sequence==='ramsey'?'T₂*':sequence==='echo'?'回波 T₂':'CPMG T₂(N)'];
 const value=result.metrics?.[fitField[0]],converted=finite(value)?value*(fitField[1].endsWith('Ns')?1e9:1):NaN;
 const inRange=finite(converted)&&converted>=(fitField[1].endsWith('Ns')?.001:0)&&converted<=(fitField[1].endsWith('Ns')?1e16:1e10);
 if(result.quality?.status==='passed'&&!result.partial&&finite(value)&&value>0&&inRange){put(fitField[1],converted,'该历史记录通过的 '+fitField[2]+' 拟合');}
 else out.missingFields.push('通过验收的 '+fitField[2]+' 拟合');
 if(result.quality?.status!=='passed')out.warnings.push('该采集未通过验收；未把其拟合值带入模型，原失败状态保留。');
 if(finite(out.input.t1Ns)&&!('t2Ns' in out.input)&&blochDefaults().t2Ns>2*out.input.t1Ns){
  out.input.t2Ns=2*out.input.t1Ns;
  out.warnings.push('原记录未提供 T₂；模型暂取 2T₁ 上限，可手动修改。这不是实测 T₂。');
 }
 if(record.validity==='stale'||record.evidenceCurrent===false)out.warnings.push('此为历史失效工况回放，不代表当前有效校准。');
 return out;
}
