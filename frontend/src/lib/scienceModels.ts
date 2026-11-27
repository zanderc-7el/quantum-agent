import type { ScienceInput, ScienceManualContext, ScienceRecord, ScienceResult } from './scienceGateway';
import type { Workflow } from '../types';
export function measurementScope(workflow:Workflow,attemptId:string) {
 const node=workflow.nodes.find(n=>n.latestAttemptId===attemptId);
 if(!node?.resourceId)throw new Error('采集记录已更新，请重新选择节点');
 return {workflowId:workflow.id,resourceId:node.resourceId};
}
export interface PlotSeries {name:string;x:number[];y:(number|null)[];xUnit:string;yUnit:string;quantity?:string;source?:string;markerX?:number}
export const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);
export function pulseEdges(input:{xyWidthNs:number;gapNs:number;readoutStartNs:number;readoutWidthNs:number;captureDelayNs:number}) {
 if(Object.values(input).some(n=>!finite(n)||n<0)||input.xyWidthNs===0||input.readoutWidthNs===0)throw new Error('脉宽必须为正值，时间不能为负');
 const {xyWidthNs:w,gapNs:g,readoutStartNs:r,readoutWidthNs:rw,captureDelayNs:d}=input;
 return {xy:[[0,w],[w+g,2*w+g]],readIn:[r,r+rw],readOut:[r+d,r+d+rw]};
}
export function comparisonPoints(p:PlotSeries,m:PlotSeries) {
 if(!p.xUnit||!m.xUnit||p.xUnit!==m.xUnit||!p.yUnit||p.yUnit!==m.yUnit||p.quantity!==m.quantity)throw new Error('坐标单位或观测量不同，不能直接求差');
 const pv=new Map(p.x.map((x,i)=>[x,p.y[i]])),mv=new Map(m.x.map((x,i)=>[x,m.y[i]]));
 return [...new Set([...p.x,...m.x])].sort((a,b)=>a-b).map(x=>{const predicted=finite(pv.get(x))?pv.get(x)!:null, measured=finite(mv.get(x))?mv.get(x)!:null;return {x,predicted,measured,difference:predicted!==null&&measured!==null?measured-predicted:null};});
}
export function projectBloch(v:[number,number,number],azimuth:number):[number,number] {
 const a=v[0]*Math.cos(azimuth)+v[1]*Math.sin(azimuth),b=-v[0]*Math.sin(azimuth)+v[1]*Math.cos(azimuth);
 return [a,-v[2]+.3*b];
}
export function readWorkingPoint(record:ScienceRecord,resourceId?:string):ScienceManualContext {
 if(!record.id||record.kind!=='working_point')throw new Error('请先保存工作点');
 if(record.evidenceIntact===false||record.validity==='stale')throw new Error('工作点来源证据已失效，请重新采集并保存');
 if(resourceId&&record.scope.resourceId!==resourceId)throw new Error('工作点与当前比特不一致');
 const native=['qcal-flux-point-evidence/1','qcal-flux-point-evidence/2'].includes(record.result?.fluxPointEvidence?.schema);
 const p=native?record.result!.point:record.input;
 if(!p.biasUnit||!['V','A','Phi0'].includes(p.biasUnit))throw new Error('偏置单位未提供');
 if(!finite(p.bias)||!finite(p.readoutFrequencyHz)||p.readoutFrequencyHz<=0||!p.fluxChannel)throw new Error('请补全偏置、磁通通道与读出频率');
 if(!p.sourceRecordId)throw new Error('缺少原始扫描记录');
 return {recordId:record.id,...record.scope,...(native?{executionProfile:'science-flux' as const,biasMeaning:p.biasMeaning,measuredOutputV:p.measuredOutputV}:{}),purpose:p.purpose==='idle'?'idle':'work',fluxChannel:p.fluxChannel,bias:p.bias,biasUnit:p.biasUnit,readoutFrequencyHz:p.readoutFrequencyHz,driveFrequencyHz:p.driveFrequencyHz,sourceRecordId:p.sourceRecordId};
}
export function modelInputFromRecord(record:ScienceRecord,modelParameters:ScienceInput):ScienceInput {
 return {...structuredClone(record.result?.parameters??{}),...structuredClone(record.input),...modelParameters,sourceRecordId:record.id,modelParameterSource:'manual',resourceId:record.scope.resourceId};
}
export function measuredModelSequence(record?:ScienceRecord):string|undefined {
 if(record?.dataKind!=='real')return undefined;
 return ({sq_rabi_time:'x',sq_t1:'t1',sq_ramsey:'ramsey',sq_echo:'echo',sq_cpmg:'cpmg'} as Record<string,string>)[record.result?.procedureId??record.provenance?.procedureId??''];
}
export function comparisonModelParameters(record:ScienceRecord,model:ScienceInput):ScienceInput {
 const copy={...model};
 if(record.dataKind==='real'){delete copy.sequence;delete copy.initialState;}
 return copy;
}
export function resultPlots(result?:ScienceResult):PlotSeries[] {
 return (result?.sourceSeries??result?.series??[]).map(s=>({name:s.label??s.id,x:s.x??result?.axes?.x?.values??[],y:s.values??[],xUnit:s.xUnit??result?.axes?.x?.unit??'',yUnit:s.unit??'',quantity:s.quantity,source:result?.source??result?.dataKind}));
}
export function fluxRecheckOverlay(result?:{procedureId?:unknown;scienceFlux?:any;provenance?:any}):PlotSeries[] {
 if(!result||(result.procedureId&&result.procedureId!=='sq_flux_point_recheck'))return [];
 const flux=result.scienceFlux??result.provenance?.scienceFlux;
 const original=flux?.sourcePoint?.originalCut,local=flux?.points?.find((point:ScienceInput)=>point.phase==='local_cavity');
 const valid=(cut:ScienceInput|undefined)=>Array.isArray(cut?.frequenciesHz)&&Array.isArray(cut?.responseMagnitude)&&cut.frequenciesHz.length>1&&cut.frequenciesHz.length===cut.responseMagnitude.length&&cut.frequenciesHz.every(finite)&&cut.responseMagnitude.every(finite);
 if(!valid(original)||!valid(local))return [];
 return [original,local].map((cut,index)=>({name:`${index?'本次同偏置局部腔谱':'来源点原始腔谱'} · 共振候选 ${finite(cut.readoutFrequencyHz)?`${Number((cut.readoutFrequencyHz/1e9).toPrecision(10))} GHz`:'未记录'}`,markerX:finite(cut.readoutFrequencyHz)?cut.readoutFrequencyHz:undefined,x:[...cut.frequenciesHz],y:[...cut.responseMagnitude],xUnit:'Hz',yUnit:'ADC',quantity:'iq_amplitude',source:'acquisition'}));
}
export const formatScience=(value:unknown)=>finite(value)?Math.abs(value)>0&&Math.abs(value)<.0001||Math.abs(value)>=1e7?value.toExponential(4):Number(value.toPrecision(6)).toString():value==null?'未记录':String(value);
