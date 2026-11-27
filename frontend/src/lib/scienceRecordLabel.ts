import type {ScienceRecord} from './scienceGateway';
import {acquiredTime,recordStoredTime} from './scienceRecordTime.ts';

const methods:Record<string,string>={
 ro_resonator_survey:'读取腔定频',ro_power_shift:'S21 功率扫描',ro_s21_ge:'两态腔响应',
 ro_iq01:'读出状态区分',ro_optimize:'读出参数优化',sq_spectroscopy:'比特定频',
 sq_rabi_amplitude:'幅度 Rabi',sq_rabi_time:'时间 Rabi',sq_rabi_frequency:'Rabi 频率扫描',
 sq_t1:'T₁ 能量弛豫',sq_ramsey:'Ramsey · T₂*',sq_echo:'Spin Echo · T₂',sq_cpmg:'CPMG · T₂(N)',
 sq_allxy:'AllXY 诊断',sq_rb:'单比特 RB',sq_drag_irb:'交错 RB',
 sq_shape_rabi_amplitude:'成形脉冲幅度 Rabi',sq_shape_rabi_amplitude_repeat5:'成形脉冲幅度 Rabi（五脉冲）',sq_shape_half_verify:'半 π 脉冲验证',
 sq_drag_scan:'DRAG 系数扫描',sq_drag_half_scan:'半 π DRAG 扫描',sq_drag_verify:'DRAG 独立验证',
 sq_shelve_spectrum12:'1→2 条件谱',sq_shelve_frequency_verify:'1→2 频率验证',
 sq_shelve_rabi12:'1→2 Rabi',sq_shelve_pi12_verify:'π₁₂ 验证',sq_shelve_readout_compare:'SHELVE 读出对照',
 ro_flux_scan:'磁通扫描',sq_flux_point_recheck:'磁通点位复测',
};
const kinds:Record<string,string>={measurement:'采集记录',simulation:'模型预测',bloch:'布洛赫模型',
 pulse:'脉冲时序',predistortion:'预补偿',predistortion_comparison:'外部预补偿对照',comparison:'仿真实测对照',drag:'DRAG 模型',
 shelve:'SHELVE 分析',working_point:'点位',flux:'磁通记录',readout_calibration:'外部读出标定'};
const text=(value:unknown):string|undefined=>typeof value==='string'&&value.trim()?value.trim():undefined;
const meaningful=(value:unknown):string|undefined=>{
 const label=text(value);return label&&!Object.values(kinds).includes(label)&&!['同点模拟','记录'].includes(label)?label:undefined;
};

export function scienceRecordLabel(record:ScienceRecord):string{
 const procedure=text(record.result?.procedureId)??text(record.provenance?.procedureId)??text(record.result?.experimentBinding?.procedureId);
 // A blueprint owner can contain both Rabi methods. Prefer the actual method
 // over that shared node label; the summary already carries its procedure ID.
 const method=(procedure?methods[procedure]:undefined)??meaningful(record.result?.methodLabel)
  ??meaningful(record.result?.nodeLabel)??meaningful(record.provenance?.nodeLabel)??meaningful(record.label);
 const kind=kinds[record.kind]??'记录',resource=text(record.scope?.resourceId);
 return [resource,method??kind,method&&record.kind!=='measurement'?kind:undefined].filter(Boolean).join(' · ');
}

export function scienceRecordTimes(record:ScienceRecord):string{
 // createdAt is when this archive entry was saved, never the acquisition time.
 return [record.kind==='measurement'?acquiredTime(record.result??{}):undefined,recordStoredTime(record)].filter(Boolean).join('；');
}
