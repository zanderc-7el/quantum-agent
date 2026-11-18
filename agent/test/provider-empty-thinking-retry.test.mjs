import test from 'node:test';
import assert from 'node:assert/strict';
import {complete} from '../src/provider.mjs';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

// Structural reproduction of safe event 170: max_tokens, [thinking,text], no
// usable text or error envelope. No captured reasoning or account data is used.
const event170={stop_reason:'max_tokens',content:[
  {type:'thinking',thinking:'SYNTHETIC_PRIVATE_REASONING'}, {type:'text',text:''},
]};
const config={configured:true,protocol:'anthropic_messages',providerId:'ziyor',
  baseUrl:'http://127.0.0.1:1/v1',model:'offline-fixture',apiKey:'OFFLINE_ONLY',timeoutMs:2000};
const messages=[{role:'system',content:'Offline rules.'},{role:'user',content:'Offline supplied observations.'}];
const answer=text=>({stop_reason:'end_turn',content:[{type:'text',text}]});
function mockResponses(t,responses){
  const calls=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    calls.push({body:JSON.parse(options.body),signal:options.signal});
    assert.ok(calls.length<=responses.length,'unexpected additional provider request');
    const entry=responses[calls.length-1];
    return new Response(JSON.stringify(entry.body??entry),{status:entry.status??200});
  });
  return calls;
}

test('event 170 structural fixture gets one budget retry, unchanged context and original abort signal',async t=>{
  const calls=mockResponses(t,[event170,answer('final answer')]),events=[];
  assert.equal(await complete({...config,onProviderRetry:e=>events.push(e)},messages),'final answer');
  assert.deepEqual(calls.map(c=>c.body.max_tokens),[4096,8192]);
  assert.deepEqual({...calls[1].body,max_tokens:4096},calls[0].body);
  assert.equal(calls[1].signal,calls[0].signal,'retry must share the original timeout/account signal');
  assert.equal(events.length,1);assert.equal(events[0].retryCount,1);
  assert.deepEqual(events[0].providerDiagnostic,{protocol:'anthropic_messages',stopReason:'max_tokens',
    blockTypes:['thinking','text'],hasErrorEnvelope:false,hasText:false});
  assert.ok(!JSON.stringify(events).includes('SYNTHETIC_PRIVATE_REASONING'));
});

test('all whitespace text and redacted thinking qualify only with actual thinking content',async t=>{
  for(const [name,content] of [
    ['whitespace text',[{type:'text',text:' \t\r\n\u00a0'},{type:'thinking',thinking:'SYNTHETIC'}]],
    ['redacted thinking',[{type:'redacted_thinking',data:'SYNTHETIC'},{type:'text',text:''}]],
    ['thinking beyond diagnostic cap',[...Array.from({length:21},()=>({type:'text',text:''})),{type:'thinking',thinking:'SYNTHETIC'}]],
  ])await t.test(name,async st=>{
    const calls=mockResponses(st,[{stop_reason:'max_tokens',content},answer('ok')]);
    assert.equal(await complete(config,messages),'ok');assert.equal(calls.length,2);
  });
});

test('text, tools, refusals, malformed blocks and error envelopes never enter the new retry path',async t=>{
  const cases=[
    ['nonempty text',{...event170,content:[event170.content[0],{type:'text',text:'fragment'}]},null,'fragment'],
    ['zero-width non-whitespace text',{...event170,content:[event170.content[0],{type:'text',text:'\u200b'}]},null,'\u200b'],
    ['empty text without thinking',{stop_reason:'max_tokens',content:[{type:'text',text:''}]},'provider_output_limit_without_text'],
    ['tool_use',{...event170,content:[...event170.content,{type:'tool_use',id:'synthetic'}]},'provider_output_limit_without_text'],
    ['tool beyond diagnostic cap',{...event170,content:[...Array(21).fill(event170.content[0]),{type:'tool_use',id:'synthetic'}]},'provider_output_limit_without_text'],
    ['refusal',{...event170,content:[...event170.content,{type:'refusal',refusal:'synthetic'}]},'provider_output_limit_without_text'],
    ['unknown block',{...event170,content:[...event170.content,{type:'future_block'}]},'provider_output_limit_without_text'],
    ['null block',{...event170,content:[...event170.content,null]},'provider_output_limit_without_text'],
    ['missing text property',{...event170,content:[event170.content[0],{type:'text'}]},'provider_output_limit_without_text'],
    ['null text property',{...event170,content:[event170.content[0],{type:'text',text:null}]},'provider_output_limit_without_text'],
    ['nonstring text property',{...event170,content:[event170.content[0],{type:'text',text:0}]},'provider_output_limit_without_text'],
    ['error envelope',{...event170,error:{message:'SYNTHETIC_PRIVATE_ERROR'}},'provider_error_envelope'],
    ['null error field',{...event170,error:null},'provider_output_limit_without_text'],
    ['false error field',{...event170,error:false},'provider_output_limit_without_text'],
    ['end turn',{...event170,stop_reason:'end_turn'},'provider_no_final_text'],
  ];
  for(const [name,body,error,text] of cases)await t.test(name,async st=>{
    const calls=mockResponses(st,[body]);
    if(error)await assert.rejects(complete(config,messages),e=>{
      assert.equal(e.message,error);assert.ok(!JSON.stringify(e).includes('SYNTHETIC_PRIVATE'));return true;
    });else assert.equal(await complete(config,messages),text);
    assert.equal(calls.length,1);
  });
});

test('second exhausted response or HTTP error stops after the existing single retry',async t=>{
  for(const [name,second,code] of [
    ['second exhaustion',event170,'provider_output_limit_without_text'],
    ['second HTTP failure',{body:{},status:503},'provider_http_503'],
  ])await t.test(name,async st=>{
    const calls=mockResponses(st,[event170,second]);let retries=0;
    await assert.rejects(complete({...config,onProviderRetry:()=>retries++},messages),new RegExp(code));
    assert.equal(calls.length,2);assert.equal(retries,1);
  });
});

test('disabled or nonincreasing retry budget does not create a second request',async t=>{
  for(const options of [{outputBudgetRetry:false},{maxOutputTokens:8192,retryMaxOutputTokens:8192},
    {maxOutputTokens:16384,retryMaxOutputTokens:8192}])await t.test(JSON.stringify(options),async st=>{
    const calls=mockResponses(st,[event170]);
    await assert.rejects(complete({...config,...options},messages),/provider_output_limit_without_text/);
    assert.equal(calls.length,1);
  });
});

test('account cancellation at retry boundary prevents the second request',async t=>{
  const account=new AbortController(),calls=mockResponses(t,[event170]);
  await assert.rejects(complete({...config,accountSignal:account.signal,onProviderRetry:()=>account.abort()},messages),
    /model_configuration_changed/);
  assert.equal(calls.length,1);
});

test('Brain shares the one-retry allowance across rounds and never logs hidden reasoning',async t=>{
  const tool=JSON.stringify({type:'tool_call',tool:'list_rules',arguments:{}});
  const calls=mockResponses(t,[event170,answer(tool),event170]),store=new Store(':memory:');
  try{
    const brain=new Brain(config,store),result=await brain.run('chat',{message:'Offline fixture.'});
    assert.equal(result.status,'waiting_model');assert.equal(result.error.code,'provider_output_limit_without_text');
    assert.deepEqual(calls.map(c=>c.body.max_tokens),[4096,8192,4096]);
    const events=store.events(result.sessionId);
    assert.equal(events.filter(e=>e.event_type==='runtime_provider_retry').length,1);
    assert.equal(events.filter(e=>e.event_type==='runtime_tool_use').length,1);
    assert.ok(!JSON.stringify(events).includes('SYNTHETIC_PRIVATE_REASONING'));
    assert.equal(store.history(result.sessionId).filter(m=>m.role==='assistant').length,0);
  }finally{store.close();}
});
