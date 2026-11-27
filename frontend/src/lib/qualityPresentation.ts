const phrases:Record<string,string>={
 'Paired drive response candidate does not repeat away from scan boundaries':'尚未确认可重复的谱峰，请查看各次采集结果',
 'No repeat-validated interior response peak':'没有找到经重复采集确认的内部响应峰',
};
const metrics:Record<string,string>={peak_snr:'峰值信噪比',contrast_snr:'对比度信噪比',cycles:'振荡周期数',r_squared:'拟合决定系数（R²）',coverage:'数据覆盖率',reference_drift_fraction:'基准漂移比例'};
const extraMetricLabels:Record<string,string>={
 amplitude_oscillation_frequency:'幅度轴振荡频率 / (a.u.)⁻¹', relative_stderr:'相对标准误差',
 rabi_frequency_hz:'时间 Rabi 振荡频率 / Hz', assignment_fidelity:'读出指派准确率',
 prepared_label_accuracy_0:'制备 0 后判为 0 的比例', prepared_label_accuracy_1:'制备 1 后判为 1 的比例',
 readout_snr_dprime:'读出区分度（d′）', resolved_power_points:'信噪比达到 5 的功率点数', scan_coverage:'扫描覆盖率',
};
export function qualityMetricLabel(key:string):string {
 return Object.hasOwn(metrics,key) ? metrics[key] : Object.hasOwn(extraMetricLabels,key) ? extraMetricLabels[key] : key;
}
type FitGroup = 'signal' | 'model' | 'repeat';
const fitReasons = new Map<string, [FitGroup, string]>([
 ['Fitted oscillation contains fewer than 1.5 cycles.', ['signal', '候选拟合不足 1.5 个振荡周期']],
 ['Oscillation amplitude is not resolved relative to residual/shot noise.', ['signal', '振幅与残差或采样噪声难以区分']],
 ['Fit reached a frequency/time optimization boundary.', ['model', '拟合触及频率或时间的搜索边界']],
 ['95% model interval is too wide relative to the fitted parameter.', ['model', '参数的 95% 拟合区间过宽']],
 ['Insufficient BIC improvement over constant/linear-trend models (<10).', ['model', '相对常数或线性趋势模型，BIC 改善不足 10']],
 ['At least two separately acquired, valid repeat fits are required.', ['repeat', '需至少两组独立采集，且各组拟合均有效']],
 ['需要独立采集复测', ['repeat', '尚未完成独立采集复测']],
]);
function missingMetric(reason: string) {
 const match = /^缺少指标\s+(\w+)$/.exec(reason);
 return match && Object.hasOwn(metrics, match[1]) ? metrics[match[1]] : undefined;
}
const numeric='[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?';
const threshold=new RegExp(`(\\w+)=(${numeric}) 不满足 \\{['"](min|max)['"]: (${numeric})\\}`,'g');
/** Display only: exact reasons and numerical evidence remain in the receipt. */
export function qualityExplanation(raw:string):string{
 let text=raw.split(/([;；])/).map(part=>{
  const reason=part.trim();
  const metric=missingMetric(reason);
  const label=metric ? '缺少有效指标：'+metric : fitReasons.get(reason)?.[1];
  return label ? part.replace(reason,()=>label) : part;
 }).join('');
 for(const [source,label] of Object.entries(phrases))text=text.split(source).join(label);
 text=text.replace(/(独立复测缺少同一候选参数[：:]\s*)drive_hz(?=$|[;；。\s])/g,'$1驱动频率');
 return text.replace(threshold,(original,key,actual,bound,limit)=>{
  if(!Object.hasOwn(metrics,key)||!Number.isFinite(Number(actual))||!Number.isFinite(Number(limit)))return original;
  const rounded=Math.abs(Number(actual))>=.01?Number(actual).toFixed(2):Number(actual).toPrecision(3);
  const value=Number(rounded)===Number(limit) ? actual : rounded;
  return `${metrics[key]} ${value} ${bound==='min'?'低于':'高于'} ${Number(limit)}`;
 });
}

export interface RabiFitEvidence {
 schema:'qcal-rabi-fit-display/1'; stage:'measure'|'verify'; requiredValid:number;
 captures:{index:number;valid:boolean;cycles?:number}[];
}

function captureLines(evidence?:RabiFitEvidence):string[] {
 if (evidence?.schema!=='qcal-rabi-fit-display/1' || !['measure','verify'].includes(evidence.stage)) return [];
 const rows=evidence.captures;
 if (!Array.isArray(rows) || !rows.length || rows.length>32 || evidence.requiredValid!==Math.max(2,rows.length)) return [];
 if (!rows.every((r,i)=>r && r.index===i+1 && typeof r.valid==='boolean' && (!r.valid || (typeof r.cycles==='number' && Number.isFinite(r.cycles) && r.cycles>0)))) return [];
 const good=rows.filter(r=>r.valid);
 const failed=rows.filter(r=>!r.valid);
 if (!failed.length) return [];
 const stage=evidence.stage==='measure'?'首测':'独立复测';
 const head=`${stage}共 ${rows.length} 组采集，${good.length} 组得到有效拟合，还缺 ${evidence.requiredValid-good.length} 组有效拟合。`;
 const details=good.length<=4?good.map(r=>`第 ${r.index} 组的有效拟合覆盖 ${Number(r.cycles!.toFixed(2))} 个周期`):[];
 if (failed.length<=4) details.push(`第 ${failed.map(r=>r.index).join('、')} 组未得到有效拟合`);
 return [head,...(details.length?[details.join('。')+'。']:[])];
}

/** Native rejection strings are the union across attempted candidate models. */
export function qualityDecisionPoints(raw: string, evidence?:RabiFitEvidence): string[] {
 const parts = raw.split(/[;；]/).map(part=>part.trim()).filter(Boolean);
 if (!parts.some(part=>fitReasons.has(part)||missingMetric(part))) return [qualityExplanation(raw)];
 const groups: Record<FitGroup, Set<string>> = {signal:new Set(), model:new Set(), repeat:new Set()};
 const missing = new Set<string>();
 const other: string[] = [];
 for (const part of parts) {
  const fit = fitReasons.get(part);
  const metric = missingMetric(part);
  if (fit) groups[fit[0]].add(fit[1]);
  else if (metric) missing.add(metric);
  else other.push(qualityExplanation(part));
 }
 const capture = captureLines(evidence);
 const screening = [...groups.signal, ...groups.model];
 const repeated = [...groups.repeat];
 if(capture.length) {
  const rule = fitReasons.get('At least two separately acquired, valid repeat fits are required.')![1];
  const index = repeated.indexOf(rule);
  if(index!==-1) repeated.splice(index,1);
 }
 return [
  ...capture,
  ...(screening.length?['不同拟合候选的筛除记录包括'+screening.join('、')+'。']:[]),
  ...(!capture.length && screening.length && groups.repeat.has(fitReasons.get('At least two separately acquired, valid repeat fits are required.')![1])
    ? ['本条记录未提供逐组拟合结果，无法确定还缺几组有效拟合。']:[]),
  ...(missing.size ? ['缺少有效指标：'+Array.from(missing).join('、')+'。'] : []),
  ...(repeated.length?[repeated.join('。')+'。']:[]), ...other,
 ];
}
