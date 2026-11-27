import type {ScienceRecord} from './scienceGateway.ts';

export function scienceGateCoefficientLabel(pulse?:Record<string,unknown>){
 const empirical=pulse?.dragMode==='empirical_beta';
 const coefficient=empirical?pulse.betaSeconds:pulse?.lambda1;
 const label=empirical?'β':'λ';
 if(typeof coefficient!=='number'||!Number.isFinite(coefficient))return `${label} 未记录`;
 const value=Number((coefficient*(empirical?1e9:1)).toPrecision(8));
 return `${label} ${String(value).replace('-','−')}${empirical?' ns':''}`;
}

export function sameHalfGateEvidence(scan?:ScienceRecord,record?:ScienceRecord){
 const current=(r?:ScienceRecord)=>r?.kind==='measurement'&&r.dataKind==='real'&&r.summary!==true&&r.evidenceCurrent===true&&r.evidenceIntact===true&&r.provenance?.independentlyVerified===true&&r.result?.quality?.status==='passed';
 if(!current(scan)||!current(record))return {ready:false,reason:'请选择当前有效、已独立验收的半门扫描与 IRB 采集记录。'};
 if(scan!.result?.procedureId!=='sq_drag_half_scan'||record!.result?.procedureId!=='sq_drag_irb')return {ready:false,reason:'π 门诊断或旧 RB 不能替代同一 X/2 DRAG 门表征。'};
 const gate=record!.result?.gateCharacterization,pulse=record!.result?.targetPulseIdentity;
 if(!gate?.reportable||!pulse?.sha256||pulse.sha256!==scan!.result?.targetPulseIdentity?.sha256||!gate.sourceReferences?.some((r:any)=>r.role==='drag'&&r.recordId===scan!.id))return {ready:false,reason:'IRB 未绑定所选半门扫描的完整脉冲身份。'};
 return {ready:true,reason:'同一 X/2 门 · 独立条件 IRB 表征',gate,pulse};
}
