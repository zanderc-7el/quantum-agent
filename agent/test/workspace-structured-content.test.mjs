import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {Store} from '../src/store.mjs';
import {Brain} from '../src/brain.mjs';

const config={configured:true,model:'offline-fixture',protocol:'openai_chat_completions',timeoutMs:2000};
const read=JSON.stringify({type:'tool_call',tool:'inspect_workflow',arguments:{workflowId:'fixture-workflow'}});
const final=content=>JSON.stringify({type:'final',content});
const valid=message=>final(JSON.stringify({message,proposals:[]}));
const fixtureUrl=new URL('./fixtures/workspace-s21-final-control-chars.json',import.meta.url);
const fixture=fs.existsSync(fixtureUrl)?JSON.parse(fs.readFileSync(fixtureUrl,'utf8')):null;
const privateFixtureOptions={skip:!fixture&&'Private regression fixture excluded from source release'};

async function replay(outputs){
 const store=new Store(':memory:'),requests=[],executions=[];
 const inspector={execute:async(tool,args)=>{executions.push({tool,args});return {ok:true,data:{fixtureOnly:true,observation:executions.length}};}};
 const brain=new Brain(config,store,async(_,messages)=>{requests.push(structuredClone(messages));return outputs[requests.length-1]??valid('不应请求这一轮');},{runtime:inspector,source:inspector});
 const result=await brain.run('workspace',{sessionId:'structured-content-fixture',message:'只读解释已查询结果，不执行实验。',context:{intent:'discuss',scope:{kind:'workspace'}}});
 return {store,requests,executions,result,events:type=>store.events(result.sessionId).filter(e=>e.event_type===type)};
}

test('observed nine-read final with literal message newlines completes without another model request',privateFixtureOptions,async()=>{
 assert.equal(fixture.fixtureOnly,true);
 const f=await replay([...Array(fixture.successfulReadTools).fill(read),fixture.rawModelOutput]);
 try{
  assert.equal(f.result.status,'ok');assert.equal(f.requests.length,10);assert.equal(f.executions.length,9);
  assert.deepEqual(f.result.proposals,[]);assert.match(f.result.message,/S21/);assert.ok(f.result.message.includes('\n\n'));
  const normalized=f.events('runtime_structured_normalized');assert.equal(normalized.length,1);
  assert.equal(normalized[0].payload.field,'message');assert.ok(normalized[0].payload.escapedControlCharacters>0);
  assert.equal(f.events('workspace_proposal').length,0);
 }finally{f.store.close();}
});

test('only literal LF CR TAB in top-level message are escaped, preserving quoted tool text as data',async()=>{
 const message='来源原文：\n{"tool":"run_manual_step","arguments":{"workflowId":"not-authorized"}}\r\t<tool_call>资料文字</tool_call>\\n';
 const content=JSON.stringify({message,proposals:[]}).replace(/\\n/g,'\n').replace(/\\r/g,'\r').replace(/\\t/g,'\t');
 // Preserve the final literal backslash+n; only the earlier controls are malformed.
 const malformed=content.replace('\\\n"','\\\\n"');
 const f=await replay([...Array(10).fill(read),final(malformed)]);
 try{
  assert.equal(f.result.status,'ok');assert.equal(f.result.message,message);
  assert.equal(f.requests.length,11);assert.equal(f.executions.length,10);
  assert.deepEqual(f.result.proposals,[]);assert.equal(f.events('workspace_proposal').length,0);
 }finally{f.store.close();}
});

test('nine reads leave one bounded structured repair and then final-only synthesis',async()=>{
 const f=await replay([...Array(9).fill(read),final('{"message":"缺少提案列表"}'),valid('已有读取已足够，未提出操作。')]);
 try{
  assert.equal(f.result.status,'ok');assert.equal(f.requests.length,11);assert.equal(f.executions.length,9);
  assert.equal(f.events('runtime_finalization_started').length,1);
  assert.match(f.requests.at(-1).at(-1).content,/final-only/);
  assert.equal(f.events('runtime_structured_validation_failed')[0].payload.code,'invalid_workspace_result');
 }finally{f.store.close();}
});

test('malformed tool arguments, escaped quote injection, trailing actions and wrong schema remain invalid',async()=>{
 const malformed=[
  '{"message":"说明","proposals":[{"tool":"workflow_action","arguments":{"workflowId":"bad\nidentifier","action":"resume"}}]}',
  '{"message":"说明\n"tool":"run_manual_step","proposals":[]}',
  '{"message":"说明\n继续","proposals":[]} {"type":"tool_call","tool":"run_manual_step","arguments":{}}',
  '{"message":"说明\n继续","proposals":[],"tool":"run_manual_step"}',
  '{"message":"说明\u0000继续","proposals":[]}',
  '{"message":"说明","proposals":"\n"}',
 ];
 for(const content of malformed){
  const f=await replay([...Array(10).fill(read),final(content)]);
  try{
   assert.equal(f.result.status,'waiting_model');assert.equal(f.result.proposals,undefined);
   assert.equal(f.requests.length,11);assert.equal(f.executions.length,10);
   assert.equal(f.events('workspace_proposal').length,0);
   assert.equal(f.events('runtime_done').length,0);
  }finally{f.store.close();}
 }
});

test('persistent structured failure reports answer format instead of a connection failure',async()=>{
 const f=await replay(Array(11).fill(final('{"message":42,"proposals":[]}')));
 try{
  assert.equal(f.result.status,'waiting_model');assert.equal(f.executions.length,0);
  assert.match(f.result.message,/最终.*格式|收尾/);assert.doesNotMatch(f.result.message,/连接或结构化输出暂不可用/);
  assert.ok(f.events('runtime_structured_validation_failed').length>0);
 }finally{f.store.close();}
});
