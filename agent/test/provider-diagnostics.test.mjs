import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {complete} from '../src/provider.mjs';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

const cfg={configured:true,protocol:'anthropic_messages',model:'offline-test-model',apiKey:'dummy-offline-key',timeoutMs:2000,outputBudgetRetry:false};
async function withResponse(body,fn){
  let calls=0;
  const server=http.createServer((req,res)=>{calls++;req.resume();res.setHeader('Content-Type','application/json');res.end(JSON.stringify(body));});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{return await fn({...cfg,baseUrl:`http://127.0.0.1:${server.address().port}/v1`},()=>calls);}
  finally{await new Promise(resolve=>server.close(resolve));}
}
const messages=[{role:'system',content:'Only answer JSON.'},{role:'user',content:'Inspect supplied test evidence.'}];

test('empty provider responses preserve only bounded structural diagnostics',async t=>{
  for(const [name,body,code,stopReason,blockTypes] of [
    ['thinking exhausted budget',{stop_reason:'max_tokens',content:[{type:'thinking',thinking:'private-reasoning-must-not-be-logged'}]},'provider_output_limit_without_text','max_tokens',['thinking']],
    ['thinking without final answer',{stop_reason:'end_turn',content:[{type:'thinking',thinking:'private-reasoning-must-not-be-logged'}]},'provider_no_final_text','end_turn',['thinking']],
    ['empty end turn',{stop_reason:'end_turn',content:[]},'provider_empty_text','end_turn',[]],
    ['malformed content',{stop_reason:'end_turn',content:{text:'private-content'}},'provider_empty_text','end_turn',[]],
    ['null block',{stop_reason:'end_turn',content:[null]},'provider_empty_text','end_turn',['other']],
    ['HTTP 200 error envelope',{error:{type:'upstream_failure',message:'private-provider-error'}},'provider_error_envelope',null,[]],
  ])await t.test(name,async()=>withResponse(body,async(config,count)=>{
    await assert.rejects(complete(config,messages),error=>{
      assert.equal(error.message,code);
      assert.deepEqual(error.providerDiagnostic,{protocol:'anthropic_messages',stopReason,blockTypes,hasErrorEnvelope:Boolean(body.error),hasText:false});
      assert.ok(!JSON.stringify(error).includes('private-'));
      return true;
    });
    assert.equal(count(),1,'empty response must not blindly retry a paid model request');
  }));
});

test('provider only returns final text and never thinking content',async()=>withResponse({stop_reason:'end_turn',content:[null,{type:'thinking',thinking:'private-reasoning'},{type:'text',text:'{"type":"final","content":"ok"}'}]},async config=>{
  assert.equal(await complete(config,messages),'{"type":"final","content":"ok"}');
}));

test('Brain records whitelisted diagnostics and stays waiting without a fabricated answer',async()=>{
  const store=new Store(':memory:');
  try{
    const brain=new Brain(cfg,store,async()=>{throw Object.assign(new Error('provider_output_limit_without_text'),{providerDiagnostic:{
      protocol:'anthropic_messages',stopReason:'max_tokens',blockTypes:['thinking','private-type',...Array(25).fill('text')],
      hasErrorEnvelope:false,hasText:false,apiKey:'private-credential',rawText:'private-reasoning',headers:{Authorization:'private-credential'},
    }});});
    const result=await brain.run('chat',{message:'Use this offline test evidence.'});
    assert.equal(result.status,'waiting_model');
    assert.equal(result.error.code,'provider_output_limit_without_text');
    assert.match(result.message,/输出上限/);
    const events=store.events(result.sessionId);
    const error=events.find(e=>e.event_type==='runtime_error');
    assert.deepEqual(Object.keys(error.payload).sort(),['code','providerDiagnostic']);
    assert.deepEqual(Object.keys(error.payload.providerDiagnostic).sort(),['blockTypes','hasErrorEnvelope','hasText','protocol','stopReason']);
    assert.equal(error.payload.providerDiagnostic.blockTypes.length,20);
    assert.equal(error.payload.providerDiagnostic.blockTypes[1],'other');
    assert.ok(!JSON.stringify(events).includes('private-'));
    assert.equal(events.some(e=>e.event_type==='runtime_model_output'||e.event_type==='runtime_done'),false);
    assert.equal(store.history(result.sessionId).filter(m=>m.role==='assistant').length,0);
  }finally{store.close();}
});

test('Brain cannot log unknown diagnostic enum strings',async()=>{
  const store=new Store(':memory:');
  try{
    const brain=new Brain(cfg,store,async()=>{throw Object.assign(new Error('provider_empty_text'),{providerDiagnostic:{protocol:'private-protocol',stopReason:'private-stop-reason',blockTypes:['private-block']}});});
    const result=await brain.run('chat',{message:'Offline test.'});
    assert.equal(result.status,'waiting_model');
    assert.match(result.message,/空回答/);
    const diagnostic=store.events(result.sessionId).find(e=>e.event_type==='runtime_error').payload.providerDiagnostic;
    assert.deepEqual(diagnostic,{protocol:'unknown',stopReason:'other',blockTypes:['other'],hasErrorEnvelope:false,hasText:false});
    assert.ok(!JSON.stringify(store.events(result.sessionId)).includes('private-'));
  }finally{store.close();}
});
