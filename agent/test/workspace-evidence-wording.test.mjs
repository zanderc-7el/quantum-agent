import test from 'node:test';
import assert from 'node:assert/strict';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

test('workspace instructions retain node scope and distinguish unreported evidence from absence',async()=>{
 const store=new Store(':memory:');let round=0,system;
 const observations=[
  {currentNodeId:'current-spectrum',currentEffectiveParameters:{nodeId:'current-spectrum',parameters:{drive_hz:4800000000}}},
  {effectiveParameters:{nodeId:'earlier-readout',parameters:{drive_hz:4900000000}},evidenceSummary:{candidateComparison:{executionRecorded:false},metricSemantics:{perGroupSnr:'not_exposed_in_result_not_evidence_of_absence'}}},
 ];
 const calls=[];
 const runtime={execute:async(name,args)=>{calls.push({name,args});return {ok:true,data:observations[calls.length-1]};}};
 const brain=new Brain({configured:true,protocol:'openai_chat_completions',model:'stub',timeoutMs:2000},store,async(_,messages)=>{
  system=messages[0].content;
  return round++<2?JSON.stringify({type:'tool_call',tool:'inspect_workflow',arguments:{workflowId:'wf-fixture',...(round===2?{nodeId:'earlier-readout'}:{})}}):
   JSON.stringify({type:'final',content:JSON.stringify({message:'本次未提供逐组趋势，所选读出节点的参数与当前定频节点分开。',proposals:[]})});
 },{runtime});
 try{
  const out=await brain.run('workspace',{message:'只读解释先前步骤',context:{scope:{kind:'workspace'},intent:'discuss'}});
  assert.equal(out.status,'ok');assert.deepEqual(out.proposals,[]);assert.equal(calls.length,2);
  assert.match(system,/本次回执未提供.*本回答尚未分析/);
  assert.match(system,/所选节点.*当前运行节点/);
  assert.match(system,/nodeId.*currentNodeId/);
  const results=store.events(out.sessionId).filter(e=>e.event_type==='runtime_tool_result');
  assert.deepEqual(results.map(e=>e.payload.result.data),observations);
 }finally{store.close();}
});
