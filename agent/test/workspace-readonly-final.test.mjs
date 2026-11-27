import test from 'node:test';
import assert from 'node:assert/strict';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

const call = JSON.stringify({type:'tool_call',tool:'inspect_workflow',arguments:{workflowId:'wf-q11'}});
const message = '读出腔频首测和独立复测均为 7.009610 GHz。';
const final = JSON.stringify({message,proposals:[]});

async function run(outputs, intent='discuss') {
  const store = new Store(':memory:');
  let calls=0, reads=0;
  const request = async () => outputs[Math.min(calls++,outputs.length-1)];
  const runtime = {execute:async () => {reads++; return {ok:true,data:{status:'running'}};}};
  const brain = new Brain({configured:true,model:'fixture',timeoutMs:3000},store,request,{runtime});
  try {
    const reply=await brain.run('workspace',{message:'查询本轮腔频',context:{intent}});
    return {reply,reads,calls,events:store.events(reply.sessionId)};
  } finally {store.close();}
}

test('a complete readonly workspace response can follow a real inspection without a second envelope', async () => {
  const {reply,reads,calls,events}=await run([call,final]);
  assert.equal(reply.message,message);
  assert.equal(reply.status,'ok');
  assert.deepEqual(reply.proposals,[]);
  assert.equal(reads,1);
  assert.equal(calls,2);
  assert.equal(reply.inspections.length,1);
  assert.ok(events.some(e=>e.event_type==='runtime_action_normalized'&&e.payload.formatVersion==='workspace-readonly-final/1'));
});

test('a bare reply cannot carry a proposal, hide duplicate fields, or append another action',async () => {
  const invalid=[
    JSON.stringify({message,proposals:[{tool:'workflow_action',arguments:{workflowId:'wf-q11',action:'pause'}}]}),
    JSON.stringify({message,proposals:[],tool:'inspect_workflow'}),
    '{"message":"wrong","message":"read","proposals":[]}',
    final+'\n'+call
  ];
  for(const output of invalid) {
    const {reply,reads}=await run([call,output]);
    assert.equal(reply.status,'waiting_model');
    assert.equal(reads,1);
  }
});

test('a bare reply does not change write-intent or uninspected request semantics',async () => {
  for(const [outputs,intent] of [[[call,final],'execute'],[[final],'discuss']]) {
    const {reply}=await run(outputs,intent);
    assert.equal(reply.status,'waiting_model');
  }
});
