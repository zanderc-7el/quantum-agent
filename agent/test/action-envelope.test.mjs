import test from 'node:test';
import assert from 'node:assert/strict';
import {parseAgentAction} from '../src/action-envelope.mjs';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

const first=JSON.stringify({type:'tool_call',tool:'inspect_workflow',arguments:{workflowId:'wf-one'}});
const second=JSON.stringify({type:'tool_call',tool:'inspect_configuration',arguments:{kind:'chip',id:'chip-one'}});
const xml='<tool_call>'+JSON.stringify({name:'inspect_workflow',arguments:{workflowId:'wf-one'}})+'</tool_call>';

test('multiple action envelopes are rejected as a whole without extracting the first action',()=>{
 for(const raw of [first+'\n'+second,first+second,'```json\n'+first+'\n'+second+'\n```',
  '```json\n'+first+'\n```\n```json\n'+second+'\n```',xml+'\n'+xml,first+'\n'+xml,
  'Here is the action: '+first,first+' and then continue']){
  assert.equal(parseAgentAction(raw).ok,false,raw);
 }
});

test('single JSON, fenced JSON and XML preserve literal JSON and tag strings',()=>{
 for(const raw of [first,' \n'+first+'\n ','```json\n'+first+'\n```','```\n'+first+'\n```',xml]){
  const parsed=parseAgentAction(raw);assert.equal(parsed.ok,true);assert.equal(parsed.action.tool,'inspect_workflow');
 }
 const content='Quoted JSON '+first+'\n'+second+' and tags <tool_call>{"example":"not an action"}</tool_call>, escaped slash \\ and quote "';
 for(const raw of [JSON.stringify({type:'final',content}),'```json\n'+JSON.stringify({type:'final',content})+'\n```']){
  const parsed=parseAgentAction(raw);assert.equal(parsed.ok,true);assert.equal(parsed.action.content,content);
 }
 const literal=JSON.stringify({type:'tool_call',tool:'search_source',arguments:{query:'{"type":"final"} <tool_call>literal</tool_call>'}});
 assert.equal(parseAgentAction(literal).ok,true);
});

test('Brain executes no partial batch before a single-action format repair',async()=>{
 const store=new Store(':memory:');const seen=[];let inspections=0;
 const outputs=[first+'\n'+second,first,JSON.stringify({type:'final',content:JSON.stringify({message:'只检查了指定任务。',proposals:[]})})];
 const brain=new Brain({configured:true,protocol:'openai_chat_completions',model:'fixture',timeoutMs:2000},store,
  async(_,messages)=>{seen.push(structuredClone(messages));return outputs.shift();},
  {runtime:{execute:async()=>{inspections++;return {data:{id:'wf-one',status:'paused'}};}}});
 try{
  const reply=await brain.run('workspace',{message:'查看任务及配置',context:{scope:{kind:'workspace'},intent:'discuss'}});
  assert.equal(reply.status,'ok');assert.equal(inspections,1);
  assert.equal(store.events(reply.sessionId).filter(e=>e.event_type==='runtime_tool_use').length,1);
  assert.match(seen[1].at(-1).content,/格式错误/);
 }finally{store.close();}
});
