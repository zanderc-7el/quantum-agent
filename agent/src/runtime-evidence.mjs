import {createHash} from 'node:crypto';

const record=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const finite=x=>typeof x==='number'&&Number.isFinite(x);
const secret=/api.?key|token|password|secret|authorization|cookie|credential/i;
const canonical=x=>JSON.stringify(x,(_key,value)=>record(value)?Object.fromEntries(Object.keys(value).sort().map(key=>[key,value[key]])):value);
const digest=x=>createHash('sha256').update(canonical(x)).digest('hex');
const pick=(x,keys)=>Object.fromEntries(keys.filter(key=>x?.[key]!==undefined).map(key=>[key,x[key]]));
const scalar=x=>finite(x)||typeof x==='boolean'||x===null||typeof x==='string'&&x.length<=160;
function scalars(value,limit=12){
 const all=record(value)?Object.entries(value):[];
 const rows=all.filter(([key,v])=>key.length<=80&&!secret.test(key)&&scalar(v)).slice(0,limit);
 return {values:Object.fromEntries(rows),complete:rows.length===all.length};
}

function timestamp(source){
 if(typeof source!=='string')return {status:'not_recorded'};
 // Never let the host machine's timezone reinterpret a timezone-less value.
 if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(source)||!Number.isFinite(Date.parse(source)))
  return {status:'timezone_missing_or_invalid'};
 const date=new Date(source),parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{
  timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',
 }).formatToParts(date).map(p=>[p.type,p.value]));
 return {source,utc:date.toISOString(),local:`${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} +08:00`,
  timeZone:'Asia/Shanghai',precision:'display_seconds_original_preserved'};
}

function groups(result,limit){
 const a=result.analysisQuality??{};let rows=[],total=0,valid=false;
 if(['s21','s21_power'].includes(a.kind)&&Array.isArray(a.powers_waveform_scale)&&Array.isArray(a.repeat_centers_hz)){
  total=a.powers_waveform_scale.length;
  valid=total===a.repeat_centers_hz.length&&a.powers_waveform_scale.every(finite)&&a.repeat_centers_hz.every(finite);
  if(valid)rows=a.powers_waveform_scale.map((power,i)=>({axes:{waveformScale:power},centerHz:a.repeat_centers_hz[i]}));
 }else if(Array.isArray(a.groups)){
  total=a.groups.length;
  valid=a.groups.every(g=>record(g.axes)&&Object.keys(g.axes).length>0&&Object.keys(g.axes).length<=8&&scalars(g.axes,8).complete&&finite(g.center_hz));
  if(valid)rows=a.groups.map(g=>({axes:g.axes,centerHz:g.center_hz}));
 }
 const unique=new Set(rows.map(r=>canonical(r.axes))).size===rows.length;
 return {status:valid&&unique?'available':'unavailable_or_ambiguous',meaning:['s21','s21_power'].includes(a.kind)?
  'within_operation_mean_of_captures_per_axes':'recorded_group_center_per_axes',
  total,complete:valid&&unique&&total<=limit,rows:valid&&unique?rows.slice(0,limit):[]};
}

function stage(result,field,groupLimit){
 const metrics=scalars(result.metrics),a=result.analysisQuality??{},q=result.quality??{};
 return {sourceField:field,resultSha256:digest(result),
  operationId:result.provenance?.operationId??result.sourceRunId??null,
  sourceRunId:result.sourceRunId??null,acquiredAt:timestamp(result.acquiredAt),
  ...pick(result,['dataKind','partial','stale','verificationPending']),
  freshAcquisition:result.provenance?.freshAcquisition===true,
  metrics:metrics.values,metricsComplete:metrics.complete,
  quality:pick(q,['status','criteria','reasons']),analysisAccepted:a.accepted===true,
  sourceHashes:pick(a,['raw_sha256','source_sha256']),
  groupCenters:groups(result,groupLimit),
  ...(['s21','s21_power'].includes(a.kind)?{metricSemantics:{contrast_snr:'minimum_over_all_power_capture_pairs',
   resolved_power_points:'count_of_capture_pairs_with_snr_at_least_5_not_unique_power_count',
   perGroupSnr:'not_exposed_in_result_not_evidence_of_absence'}}:{})};
}

function candidateComparison(result){
 const selection=result.selectionResult?.candidateParameters,verification=result.verificationCandidateParameters;
 if(!record(selection))return {status:'selection_candidates_not_recorded',executionRecorded:false,fields:[]};
 const keys=Object.keys(selection);
 if(!keys.length)return {status:'not_applicable_empty_selection',executionRecorded:false,fields:[]};
 const fields=keys.filter(k=>!secret.test(k)&&k.length<=80&&finite(selection[k])&&finite(verification?.[k])&&finite(verification[k]-selection[k]))
  .slice(0,12).map(key=>({key,selection:selection[key],verification:verification[key],delta:verification[key]-selection[key]}));
 // Numeric values are not a per-check execution receipt. Do not infer a pass
 // from proximity, a generic quality pass, or a function existing in source.
 return {status:'values_available_execution_not_recorded',executionRecorded:false,
  selectionSource:'node.result.selectionResult.candidateParameters',verificationSource:'node.result.verificationCandidateParameters',
  complete:fields.length===keys.length,fields};
}

function compareGroups(stages){
 const empty={meaning:'descriptive_verify_minus_selection_same_axes_not_acceptance_gate',independence:'not_authenticated_by_summary',rows:[]};
 if(!stages.selection||!stages.verification)return {status:'requires_selection_and_verification',...empty};
 const s=stages.selection.groupCenters,v=stages.verification.groupCenters;
 if(!s.complete||!v.complete||s.total!==v.total||!s.total)return {status:'unavailable_ambiguous_or_incomplete_axes',...empty};
 const byAxes=new Map(s.rows.map(row=>[canonical(row.axes),row]));
 if(v.rows.some(row=>!byAxes.has(canonical(row.axes))))return {status:'unavailable_ambiguous_or_incomplete_axes',...empty};
 const rows=v.rows.map(row=>({axes:row.axes,selectionCenterHz:byAxes.get(canonical(row.axes)).centerHz,
  verificationCenterHz:row.centerHz,deltaHz:row.centerHz-byAxes.get(canonical(row.axes)).centerHz}));
 if(rows.some(row=>!finite(row.deltaHz)))return {status:'nonfinite_difference',...empty};
 return {...empty,status:'available',complete:true,maxAbsDeltaHz:Math.max(...rows.map(row=>Math.abs(row.deltaHz))),rows};
}

export function evidenceSummary(detail){
 const node=detail.node,result=node?.result;
 if(!record(result))return null;
 const source={schema:'qcal-agent-evidence/1',resultSha256:digest(result),digestFormat:'sha256_sorted_key_json_utf8_ecmascript',
  nodeId:node.id,attemptId:typeof node.latestAttemptId==='string'&&node.latestAttemptId.length<=160?node.latestAttemptId:null,humanAcceptedAt:timestamp(node.humanAcceptedAt),
  interpretation:'recorded_snapshot_only_omission_is_not_absence'};
 // Reduce whole grouped rows, never clip a digest, number, axes, or comparison.
 // This reserved summary is separate from the legacy detail preview budget.
 for(const limit of [12,6,3,0]){
  const stages=record(result.selectionResult)?{selection:stage(result.selectionResult,'node.result.selectionResult',limit),verification:stage(result,'node.result',limit)}:
   {acquisition:stage(result,'node.result',limit)};
  const summary={...source,stages,candidateComparison:candidateComparison(result),groupComparison:compareGroups(stages)};
  if(JSON.stringify(summary).length<=6500)return summary;
 }
 // Unexpected oversized quality text must not displace all source identity.
 return {...source,status:'detail_exceeds_summary_budget',stagesOmitted:true};
}
