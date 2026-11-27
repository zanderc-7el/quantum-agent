import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRuntimeTools} from '../src/runtime-tools.mjs';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

const fixtureUrl=new URL('./fixtures/workspace-s21-evidence.json',import.meta.url);
const fixture=fs.existsSync(fixtureUrl)?JSON.parse(fs.readFileSync(fixtureUrl,'utf8')):null;
const privateFixtureOptions={skip:!fixture&&'Private regression fixture excluded from source release'};
const root='D:/fixture/platform',workflowId='wf-fixture-s21',scope={scope:{kind:'workspace'}};
function runtime(detail=structuredClone(fixture.detail)){
 const requests=[],nodeId=detail.node.id;
 const tools=createRuntimeTools({root,request:async(url,options)=>{
  requests.push({url:String(url),...options});
  return new Response(JSON.stringify(new URL(url).pathname==='/api/health'?{projectId:'qcal-local-f8-platform',projectRoot:root}:detail));
 }});
 return {tools,requests,read:()=>tools.execute('inspect_workflow',{workflowId,nodeId},scope)};
}

test('real S21 evidence survives large detail with original result digest, stages and explicit timezone',privateFixtureOptions,async()=>{
 const f=runtime(),value=await f.read(),s=value.data.evidenceSummary;
 assert.ok(s,'node evidence summary is required before the bounded legacy detail');
 assert.equal(s.resultSha256,fixture.source.fixtureResultCanonicalSha256);
 assert.equal(s.stages.selection.metrics.contrast_snr,5.997815885453773);
 assert.equal(s.stages.verification.metrics.contrast_snr,7.062638567935815);
 assert.equal(s.stages.selection.operationId,'attempt-fixture-s21/measure');
 assert.equal(s.stages.verification.operationId,'attempt-fixture-s21/verify');
 assert.equal(s.stages.verification.acquiredAt.local,'2026-11-26 02:56:46 +08:00');
 assert.equal(s.humanAcceptedAt.local,'2026-11-26 02:59:43 +08:00');
 assert.equal(s.humanAcceptedAt.timeZone,'Asia/Shanghai');
 assert.equal(s.stages.verification.groupCenters.rows.length,5);
 assert.equal(s.stages.verification.groupCenters.rows[0].axes.waveformScale,.8);
 assert.equal(s.stages.verification.groupCenters.meaning,'within_operation_mean_of_captures_per_axes');
 assert.equal(s.stages.verification.metricSemantics.contrast_snr,'minimum_over_all_power_capture_pairs');
 assert.equal(s.stages.verification.metricSemantics.perGroupSnr,'not_exposed_in_result_not_evidence_of_absence');
 assert.ok(value.truncated);assert.ok(JSON.stringify(value).length<12000);
 assert.ok(f.requests.every(r=>r.method==='GET'));
});

test('power groups align by equal axes rather than row order and empty candidates never claim a repeat gate',privateFixtureOptions,async()=>{
 const s=(await runtime().read()).data.evidenceSummary;
 assert.ok(s,'summary missing');
 assert.equal(s.candidateComparison.status,'not_applicable_empty_selection');
 assert.equal(s.candidateComparison.executionRecorded,false);
 assert.deepEqual(s.candidateComparison.fields,[]);
 const c=s.groupComparison;
 assert.equal(c.meaning,'descriptive_verify_minus_selection_same_axes_not_acceptance_gate');
 assert.equal(c.complete,true);assert.equal(c.rows.length,5);
 assert.ok(Math.abs(c.maxAbsDeltaHz-66666.66666603088)<1e-6);
 assert.equal(c.rows.find(r=>r.axes.waveformScale===.8).deltaHz,0);
 assert.ok(Math.abs(c.rows.find(r=>r.axes.waveformScale===.2).deltaHz+66666.66666603088)<1e-6);
});

test('nonempty candidates do not fabricate execution, and malformed or duplicate axes cannot become comparisons',privateFixtureOptions,async()=>{
 const detail=structuredClone(fixture.detail),r=detail.node.result;
 r.selectionResult.candidateParameters={drive_hz:100};r.candidateParameters={drive_hz:100};r.verificationCandidateParameters={drive_hz:103};
 r.analysisQuality.powers_waveform_scale=[.8,.8,.6,.4,.2];
 const s=(await runtime(detail).read()).data.evidenceSummary;
 assert.ok(s,'summary missing');
 assert.equal(s.candidateComparison.status,'values_available_execution_not_recorded');
 assert.equal(s.candidateComparison.executionRecorded,false);
 assert.deepEqual(s.candidateComparison.fields,[{key:'drive_hz',selection:100,verification:103,delta:3}]);
 assert.equal(s.groupComparison.status,'unavailable_ambiguous_or_incomplete_axes');
 assert.deepEqual(s.groupComparison.rows,[]);
 delete r.selectionResult;r.acquiredAt='2026-11-26T02:59:43';
 const solo=(await runtime(detail).read()).data.evidenceSummary;
 assert.equal(solo.stages.verification,undefined);assert.equal(solo.stages.acquisition.acquiredAt.status,'timezone_missing_or_invalid');
 assert.equal(solo.groupComparison.status,'requires_selection_and_verification');
});

test('large metrics and grouped evidence are explicitly incomplete and cannot truncate a digest or partial row into a fact',privateFixtureOptions,async()=>{
 const detail=structuredClone(fixture.detail),r=detail.node.result;
 r.analysisQuality.powers_waveform_scale=Array.from({length:100},(_,i)=>i/100);
 r.analysisQuality.repeat_centers_hz=Array.from({length:100},(_,i)=>1e9+i);
 r.metrics=Object.fromEntries(Array.from({length:200},(_,i)=>['value'+i,i]));
 const value=await runtime(detail).read(),s=value.data.evidenceSummary;
 assert.ok(s,'summary missing');assert.equal(s.resultSha256.length,64);
 assert.equal(s.stages.verification.metricsComplete,false);
 assert.equal(s.stages.verification.groupCenters.complete,false);
 assert.equal(s.stages.verification.groupCenters.total,100);
 assert.ok(s.stages.verification.groupCenters.rows.every(r=>Object.keys(r).length===2));
 assert.ok(JSON.stringify(value).length<12000);
});

test('workspace prompt treats omitted data as unknown and execution claims require recorded evidence',privateFixtureOptions,async()=>{
 const f=runtime(),store=new Store(':memory:');let calls=0,prompt;
 try{
  const brain=new Brain({configured:true,protocol:'openai_chat_completions',model:'stub',timeoutMs:2000},store,async(_,messages)=>{
   prompt=messages[0].content;
   return calls++===0?JSON.stringify({type:'tool_call',tool:'inspect_workflow',arguments:{workflowId,nodeId:fixture.detail.node.id}}):JSON.stringify({type:'final',content:JSON.stringify({message:'已按来源核对。',proposals:[]})});
  },{runtime:f.tools});
  const out=await brain.run('workspace',{message:'只读解释通过条件',context:{...scope,intent:'discuss'}});
  assert.equal(out.status,'ok');assert.deepEqual(out.proposals,[]);
  assert.match(prompt,/evidenceSummary/);assert.match(prompt,/Asia\/Shanghai/);
  assert.match(prompt,/同一.*axes/);assert.match(prompt,/空.*candidate/);
  const s=store.events(out.sessionId).find(e=>e.event_type==='runtime_tool_result').payload.result.data.evidenceSummary;
  assert.equal(s.stages.verification.metrics.contrast_snr,7.062638567935815);
 }finally{store.close();}
});

test('evidence redaction preserves valid JSON, extreme values remain unknown, and oversized quality stays bounded',privateFixtureOptions,async()=>{
 const detail=structuredClone(fixture.detail),r=detail.node.result;
 r.metrics.debug='Authorization: Bearer PRIVATE_SENTINEL';
 r.quality.criteria.password={value:'PRIVATE_SENTINEL'};
 r.quality.reasons=['token=PRIVATE_SENTINEL'];
 let out=await runtime(detail).read();
 assert.doesNotMatch(JSON.stringify(out),/PRIVATE_SENTINEL/);
 assert.equal(out.data.evidenceSummary.stages.verification.metrics.contrast_snr,7.062638567935815);
 r.selectionResult.candidateParameters={drive_hz:-1e308};r.verificationCandidateParameters={drive_hz:1e308};
 r.quality.reasons=Array(50).fill('x'.repeat(20000));
 detail.node.latestAttemptId='x'.repeat(20000);
 out=await runtime(detail).read();
 assert.equal(out.data.evidenceSummary.status,'detail_exceeds_summary_budget');
 assert.equal(out.data.evidenceSummary.resultSha256.length,64);
 assert.ok(JSON.stringify(out).length<12000);
});
