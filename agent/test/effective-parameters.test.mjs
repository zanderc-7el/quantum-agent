import test from 'node:test';
import assert from 'node:assert/strict';
import {createRuntimeTools} from '../src/runtime-tools.mjs';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

const root='D:/allowed/platform',scope={scope:{kind:'workspace'}};
const initial={readout_hz:7009510000,drive_hz:4862690000};
const effective={...initial,readout_hz:7009676666.666666};
const node={id:'Q11:s02',resourceId:'Q11',procedureId:'sq_spectroscopy',status:'attention',engineState:'failed_after_shutdown',parameters:initial,parameterVersions:{readout_hz:0},latestAttemptId:'attempt-latest',dependsOn:[]};
const workflow={id:'wf-one',revision:3639,status:'paused',currentNodeId:node.id,nodes:[node]};
const nodeRoute='/api/workflows/wf-one/nodes/Q11%3As02';
function fixture(overrides={}){
 const calls=[],values={'/api/health':{projectId:'qcal-local-f8-platform',projectRoot:root},'/api/workflows/wf-one':workflow,
  '/api/campaigns/group':{id:'group',children:[{workflowId:'wf-one'}]},
  [nodeRoute]:{attempts:[{id:'attempt-latest',parameters:initial,state:'failed_after_shutdown'}],node,parameters:effective},...overrides};
 return {calls,tools:createRuntimeTools({root,request:async(url,options)=>{calls.push({path:new URL(url).pathname,...options});return new Response(JSON.stringify(values[new URL(url).pathname]??{}));}})};
}

test('workflow inspection reads effective detail parameters instead of configuration or node snapshots',async()=>{
 const {tools,calls}=fixture();
 const result=await tools.execute('inspect_workflow',{workflowId:'wf-one'},scope);
 assert.equal(result.data.currentEffectiveParameters.parameters.readout_hz,effective.readout_hz);
 assert.equal(result.data.currentEffectiveParameters.meaning,'effective_configuration_not_instrument_readback');
 assert.equal(result.data.currentEffectiveParameters.source.path,nodeRoute);
 assert.equal(result.data.currentEffectiveParameters.source.field,'parameters');
 assert.equal(result.data.currentEffectiveParameters.parametersComplete,true);
 assert.equal(result.data.currentEffectiveParameters.workflowRevisionBeforeRead,3639);
 assert.equal(result.data.currentEffectiveParameters.snapshotConsistency,'separate_read');
 assert.equal(result.data.nodeSummary.complete,true);
 assert.deepEqual(calls.map(c=>c.path),['/api/health','/api/workflows/wf-one',nodeRoute]);
 assert.ok(calls.every(c=>c.method==='GET'));
});

test('large old curves cannot hide effective, recorded-node, and latest-attempt parameter layers',async()=>{
 const {tools}=fixture({[nodeRoute]:{attempts:[{id:'attempt-old',result:{series:Array(1000).fill('curve'.repeat(200))}},
  {id:'attempt-latest',parameters:{...initial,readout_hz:7009600000},parameterVersions:{readout_hz:2},state:'failed_after_shutdown'}],
  node:{...node,result:{series:Array(1000).fill('x'.repeat(800))}},parameters:effective}});
 const result=await tools.execute('inspect_workflow',{workflowId:'wf-one',nodeId:node.id},scope);
 assert.equal(result.data.effectiveParameters.parameters.readout_hz,effective.readout_hz);
 assert.equal(result.data.recordedNodeParameters.parameters.readout_hz,initial.readout_hz);
 assert.equal(result.data.recordedNodeParameters.meaning,'recorded_node_snapshot');
 assert.equal(result.data.latestAttemptParameters.parameters.readout_hz,7009600000);
 assert.equal(result.data.latestAttemptParameters.meaning,'attempt_dispatch_snapshot');
 assert.equal(result.data.latestAttemptParameters.attemptId,'attempt-latest');
 assert.equal(result.data.effectiveParameters.parameterVersions,undefined);
 assert.ok(result.truncated);assert.ok(JSON.stringify(result).length<12000);
});

test('unavailable or mismatched effective detail never falls back to seed or recorded values',async()=>{
 for(const detail of [{},{node,attempts:[]},{node:{...node,id:'foreign'},attempts:[],parameters:effective}]){
  const {tools}=fixture({[nodeRoute]:detail});
  const value=await tools.execute('inspect_workflow',{workflowId:'wf-one'},scope);
  assert.equal(value.data.currentEffectiveParameters.status,'unavailable');
  assert.equal(value.data.currentEffectiveParameters.parameters,undefined);
 }
 const unsafe=fixture({'/api/workflows/wf-one':{...workflow,currentNodeId:'../account',nodes:[{...node,id:'../account'}]}});
 const value=await unsafe.tools.execute('inspect_workflow',{workflowId:'wf-one'},scope);
 assert.equal(value.data.currentEffectiveParameters.status,'unavailable');assert.equal(unsafe.calls.length,2);
});

test('effective parameter reads remain within the authorized workflow and campaign',async()=>{
 const {tools,calls}=fixture();
 await assert.rejects(tools.execute('inspect_workflow',{workflowId:'wf-foreign'},{scope:{kind:'campaign',id:'group'}}),/scope/);
 assert.ok(!calls.some(c=>c.path.includes('/workflows/')));
 const allowed=await tools.execute('inspect_workflow',{workflowId:'wf-one'},{scope:{kind:'campaign',id:'group'}});
 assert.equal(allowed.data.currentEffectiveParameters.workflowId,'wf-one');
 await assert.rejects(tools.execute('inspect_workflow',{workflowId:'wf-one',nodeId:'../account'},scope),/arguments/);
});

test('bounded effective parameters explicitly identify incomplete values',async()=>{
 const {tools}=fixture({[nodeRoute]:{node,attempts:[],parameters:{readout_hz:effective.readout_hz,sweep:Array(1000).fill(1)}}});
 const value=await tools.execute('inspect_workflow',{workflowId:'wf-one',nodeId:node.id},scope);
 assert.equal(value.data.effectiveParameters.parametersComplete,false);
 assert.equal(value.data.effectiveParameters.status,'partial');
});

test('workspace observation and prompt distinguish current effective configuration from seed and hardware readback',async()=>{
 const {tools}=fixture(),store=new Store(':memory:');let calls=0,seen;
 const brain=new Brain({configured:true,protocol:'openai_chat_completions',model:'stub',timeoutMs:2000},store,async(_,messages)=>{
  seen=messages;
  return calls++===0?JSON.stringify({type:'tool_call',tool:'inspect_workflow',arguments:{workflowId:'wf-one'}}):JSON.stringify({type:'final',content:JSON.stringify({message:'只读检查完成',proposals:[]})});
 },{runtime:tools});
 try{
  const result=await brain.run('workspace',{message:'对比任务当前Q11参数',context:{...scope,intent:'discuss',chips:[{qubits:[{id:'Q11',parameters:initial}]}]}});
  assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[]);
  assert.match(seen[0].content,/currentEffectiveParameters/);
  assert.match(seen[0].content,/不是仪器频率回读/);
  const observation=store.events(result.sessionId).find(e=>e.event_type==='runtime_tool_result').payload.result;
  assert.equal(observation.data.currentEffectiveParameters.parameters.readout_hz,effective.readout_hz);
 }finally{store.close();}
});

test('spectroscopy separates the ON pulse, stored gate length, shots per block and independently repeated captures',async()=>{
 const params={...effective,spectroscopy_width_s:8e-6,drive_length_s:320e-9,shots:2000,acquisitions_per_point:8,capture_repeats:2};
 const {tools}=fixture({[nodeRoute]:{node,attempts:[],parameters:params}});
 const record=await tools.execute('inspect_workflow',{workflowId:'wf-one'},scope);
 const timing=record.data.currentEffectiveParameters.spectroscopySettings;
 assert.equal(timing.onPulseDurationUs,8);
 assert.equal(timing.shotsPerBlock,2000);
 assert.equal(timing.blocksPerOnOrOffPoint,8);
 assert.equal(timing.shotsPerOnOrOffPoint,16000);
 assert.equal(timing.captures,2);
 const without=await fixture().tools.execute('inspect_workflow',{workflowId:'wf-one'},scope);
 assert.equal(without.data.currentEffectiveParameters.spectroscopySettings,undefined);
});
