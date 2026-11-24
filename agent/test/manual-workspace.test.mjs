import test from 'node:test';
import assert from 'node:assert/strict';
import {Store} from '../src/store.mjs';
import {Brain} from '../src/brain.mjs';
import {createRuntimeTools} from '../src/runtime-tools.mjs';
const config={configured:true,protocol:'openai_chat_completions',model:'stub-model',timeoutMs:2000};
const final=value=>JSON.stringify({type:'final',content:JSON.stringify(value)});

test('manual creation proposals preserve strict policy at the Agent schema boundary',async()=>{
 for(const proposal of [
  {tool:'start_measurement',arguments:{resourceIds:['Q11'],policy:'strict'}},
  {tool:'create_campaign',arguments:{name:'manual',chipVersionId:'chip-v1',deploymentVersionId:'dep-v1',resourceIds:['Q11'],coverage:'full',policy:'strict'}}
 ]){
  const store=new Store(':memory:');
  const brain=new Brain(config,store,async()=>final({message:'建议建立人工逐步任务。',proposals:[proposal]}));
  try{
   const result=await brain.run('workspace',{message:'开始Q11人工逐步执行',context:{intent:'execute'}});
   assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[proposal]);
  }finally{store.close();}
 }
});

test('global Agent cannot propose hidden run_step or policy-changing workflow writes',async()=>{
 for(const action of ['run_step','set_policy','approve']){
  const store=new Store(':memory:');let calls=0;
  const brain=new Brain(config,store,async()=>final(calls++===0?
   {message:'start',proposals:[{tool:'workflow_action',arguments:{workflowId:'manual',action}}]}:
   {message:'请在蓝图中操作步骤。',proposals:[]}));
  try{
   const result=await brain.run('workspace',{message:'开始Q11人工逐步执行',context:{intent:'execute'}});
   assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[]);assert.equal(calls,2);
  }finally{store.close();}
 }
});

test('runtime snapshots retain manual policy and evidence needed to distinguish pending acceptance',async()=>{
 const root='D:/allowed/platform';
 const workflow={id:'manual',revision:9,mode:'real',policy:'strict',status:'attention',engineState:'awaiting_acceptance',currentNodeId:'Q11:readout',nodes:[
  {id:'Q11:readout',engineState:'awaiting_acceptance',latestAttemptId:'attempt-current',result:{dataKind:'real',provenance:{freshAcquisition:true},quality:{status:'passed'},verificationPending:false,stale:false}}
 ]};
 const tools=createRuntimeTools({root,request:async url=>new Response(JSON.stringify(new URL(url).pathname==='/api/health'?
  {projectId:'qcal-local-f8-platform',projectRoot:root,status:'ok'}:workflow))});
 const result=await tools.execute('inspect_workflow',{workflowId:'manual'},{scope:{kind:'workspace'}});
 assert.equal(result.data.policy,'strict');assert.equal(result.data.revision,9);assert.ok(result.checkedAt);
 assert.equal(result.data.currentNodeDiagnostic.engineState,'awaiting_acceptance');
 assert.equal(result.data.currentNodeDiagnostic.dataKind,'real');
 assert.equal(result.data.currentNodeDiagnostic.freshAcquisition,true);
 assert.equal(result.data.currentNodeDiagnostic.latestAttemptId,'attempt-current');
});
