import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createSourceTools} from '../src/source-tools.mjs';
import {createRuntimeTools} from '../src/runtime-tools.mjs';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

async function sourceFixture(t){
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'qcal-evidence-'));
 t.after(()=>fs.rm(root,{recursive:true,force:true}));
 const put=async(name,content)=>{const target=path.join(root,name);await fs.mkdir(path.dirname(target),{recursive:true});await fs.writeFile(target,content);};
 return {put,tools:createSourceTools({root})};
}

test('zero phrase matches disclose literal semantics and useful scoped keyword followups',async t=>{
 const {put,tools}=await sourceFixture(t);
 await put('backend/gates.py','frequency_tolerance_hz = 200000\n');
 const miss=await tools.execute('search_source',{query:'frequency tolerance threshold',pathPrefix:'backend'});
 assert.equal(miss.matches.length,0);
 assert.equal(miss.query.mode,'literal_substring');assert.equal(miss.query.caseSensitive,true);
 assert.equal(miss.query.text,'frequency tolerance threshold');
 assert.equal(miss.coverage.pathPrefix,'backend');assert.equal(miss.coverage.uploadedDocumentsIncluded,false);
 assert.equal(miss.coverage.searchedFiles,1);assert.equal(miss.coverage.boundedScanFinished,true);
 assert.equal(miss.interpretation,'no_literal_matches_in_searched_source_not_proof_of_absence');
 assert.deepEqual(miss.followup.keywordCandidates,['frequency','tolerance','threshold']);
 const found=await tools.execute('search_source',{query:miss.followup.keywordCandidates[1],pathPrefix:miss.coverage.pathPrefix});
 assert.equal(found.matches[0].path,'backend/gates.py');assert.equal(found.matches[0].line,1);
 assert.equal(found.interpretation,'literal_matches_require_context_and_applicability_check');
 assert.equal((await tools.execute('search_source',{query:'TOLERANCE',pathPrefix:'backend'})).matches.length,0);
});

test('excluded and bounded source coverage never claims whole-workspace absence',async t=>{
 const {put,tools}=await sourceFixture(t);
 await put('backend/safe.py','ordinary code');
 await put('backend/large.py','x'.repeat(600*1024));
 await put('backend/withvalue.py','password = "private-sentinel"\nthreshold = 1');
 await put('data/uploads/document.md','threshold = 2');
 const result=await tools.execute('search_source',{query:'threshold'});
 assert.equal(result.matches.length,0);assert.equal(result.coverage.searchedFiles,1);
 assert.equal(result.coverage.skippedFiles,2);assert.equal(result.coverage.boundedScanFinished,false);
 assert.equal(result.coverage.scope,'allowlisted_project_source_only');
 assert.equal(result.coverage.uploadedDocumentsIncluded,false);
 assert.match(result.followup.documents,/list_documents.*read_document/);
 assert.doesNotMatch(JSON.stringify(result),/private-sentinel|withvalue/);
});

test('query evidence remains bounded and preserves literal syntax and forbidden-path rejection',async t=>{
 const {put,tools}=await sourceFixture(t);
 await put('backend/wide.py','needle.*()'+'"\\'.repeat(30000));
 const result=await tools.execute('search_source',{query:'needle.*()'});
 assert.equal(result.matches.length,1);assert.equal(result.truncated,true);
 assert.equal(result.coverage.boundedScanFinished,false);assert.ok(JSON.stringify(result).length<=12000);
 assert.deepEqual(result.followup.keywordCandidates,[]);
 for(const prefix of ['../backend','data/uploads','D:/qoskit-workspace'])
  await assert.rejects(tools.execute('search_source',{query:'needle',pathPrefix:prefix}),/source_path_not_allowed/);
});

function runtimeFixture(parameters){
 const root='D:/allowed/platform';
 const node={id:'n1',status:'paused',parameters,latestAttemptId:'attempt1'};
 const values={'/api/health':{projectId:'qcal-local-f8-platform',projectRoot:root},
 '/api/workflows/wf-one':{id:'wf-one',status:'paused',revision:9,currentNodeId:'n1',nodes:[node]},
 '/api/workflows/wf-one/nodes/n1':{node,parameters,attempts:[{id:'attempt1',parameters}]}};
 return createRuntimeTools({root,request:async url=>new Response(JSON.stringify(values[new URL(url).pathname]))});
}

test('frequency unit conversion preserves source decimals without Q11-specific values or display rounding',async()=>{
 const parameters={readout_hz:7009676666.666666,drive_hz:4862690000,rotation_hz:-1250000.125,small_hz:1e-7,huge_hz:1e21,amplitude:0.99,secret_hz:1234};
 const value=await runtimeFixture(parameters).execute('inspect_workflow',{workflowId:'wf-one'},{scope:{kind:'workspace'}});
 const units=value.data.currentEffectiveParameters.frequencyUnits;
 assert.equal(units.basis,'decimal_scaling_of_returned_Hz_value');
 assert.equal(units.measurementPrecisionAdded,false);
 assert.deepEqual(units.values.readout_hz,{Hz:'7009676666.666666',MHz:'7009.676666666666',GHz:'7.009676666666666'});
 assert.equal(units.values.drive_hz.MHz,'4862.69');
 assert.equal(units.values.rotation_hz.MHz,'-1.250000125');
 assert.equal(units.values.small_hz.MHz,'0.0000000000001');
 assert.equal(units.values.huge_hz.MHz,'1000000000000000');
 assert.equal(units.values.amplitude,undefined);assert.equal(units.values.secret_hz,undefined);
});

test('current and recorded parameter conversions stay associated with their own source layer',async()=>{
 const tools=runtimeFixture({drive_hz:4200000001.25});
 const result=await tools.execute('inspect_workflow',{workflowId:'wf-one',nodeId:'n1'},{scope:{kind:'workspace'}});
 assert.equal(result.data.effectiveParameters.source.field,'parameters');
 assert.equal(result.data.recordedNodeParameters.source.field,'node.parameters');
 assert.equal(result.data.latestAttemptParameters.attemptId,'attempt1');
 for(const key of ['effectiveParameters','recordedNodeParameters','latestAttemptParameters'])
  assert.equal(result.data[key].frequencyUnits.values.drive_hz.MHz,'4200.00000125');
});

test('bounded conversion previews never expose a clipped decimal as a precise value',async()=>{
 const parameters=Object.fromEntries(Array.from({length:75},(_,index)=>['frequency_'+index+'_hz',1234567890.123456]));
 const result=await runtimeFixture(parameters).execute('inspect_workflow',{workflowId:'wf-one'},{scope:{kind:'workspace'}});
 assert.equal(result.truncated,true);
 assert.equal(result.data.currentEffectiveParameters.frequencyUnits,undefined);
 assert.equal(result.data.currentEffectiveParameters.frequencyConversionStatus,'omitted_or_partial');
});

test('Brain exposes scoped search evidence and precision guidance without writable capabilities',async t=>{
 const {put,tools}=await sourceFixture(t);await put('backend/gates.py','tolerance_hz = 1000');
 const store=new Store(':memory:');t.after(()=>store.close());let turns=0,system;
 const brain=new Brain({configured:true,protocol:'openai_chat_completions',model:'stub',timeoutMs:2000},store,async(_,messages)=>{
  system=messages[0].content;
  return turns++===0?JSON.stringify({type:'tool_call',tool:'search_source',arguments:{query:'frequency tolerance threshold'}}):JSON.stringify({type:'final',content:JSON.stringify({message:'本次字面检索未找到匹配，不能据此判定阈值不存在。',proposals:[]})});
 },{source:tools});
 const result=await brain.run('workspace',{message:'只读，精确MHz和阈值证据',context:{scope:{kind:'workspace'},intent:'discuss'}});
 assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[]);
 const observation=store.events(result.sessionId).find(e=>e.event_type==='runtime_tool_result').payload.result;
 assert.equal(observation.query.mode,'literal_substring');assert.equal(observation.matches.length,0);
 assert.match(system,/未找到不等于不存在/);assert.match(system,/frequencyUnits/);
 assert.match(system,/精确.*不得.*四舍五入/);assert.match(system,/不是.*测量精度/);
});
