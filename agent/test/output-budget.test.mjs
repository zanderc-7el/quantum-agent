import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {loadConfig,publicConfig,resolveOutputBudget} from '../src/config.mjs';
import {complete} from '../src/provider.mjs';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

const cfg={configured:true,protocol:'anthropic_messages',providerId:'ziyor',model:'offline-test-model',apiKey:'dummy-offline-key',timeoutMs:2000};
const thinking={stop_reason:'max_tokens',content:[{type:'thinking',thinking:'private-reasoning'}]};
const answer=text=>({stop_reason:'end_turn',content:[{type:'text',text}]});
const messages=[{role:'system',content:'test system'},{role:'user',content:'test context'}];
async function withServer(handler,fn){
  const seen=[];
  const server=http.createServer(async(req,res)=>{
    let raw='';for await(const chunk of req)raw+=chunk;
    seen.push(JSON.parse(raw));
    await handler(seen.length,res,seen.at(-1));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{return await fn({...cfg,baseUrl:`http://127.0.0.1:${server.address().port}/v1`},seen);}
  finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
}
function json(res,body,status=200){res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(body));}

test('output budget uses original client defaults, validates integers and publishes safe limits',()=>{
  const configured=loadConfig({QCAL_MODEL_PROTOCOL:'anthropic_messages',QCAL_MODEL:'test',QCAL_MODEL_API_KEY:'private-key'},'missing.env');
  assert.equal(configured.maxOutputTokens,4096);assert.equal(configured.retryMaxOutputTokens,8192);
  assert.deepEqual(publicConfig(configured).outputBudget,{initialMaxTokens:4096,retryMaxTokens:8192,clientMaxTokens:32768,retryLimit:1,maxHttpRequests:2,scope:'one_provider_completion',limitSource:'ziyor_original_client_policy'});
  assert.ok(!JSON.stringify(publicConfig(configured)).includes('private-key'));
  for(const invalid of ['0','-1','4096.5','NaN','Infinity','1e4','32769','true','']){
    const c=loadConfig({QCAL_MODEL_MAX_OUTPUT_TOKENS:invalid,QCAL_MODEL_RETRY_MAX_OUTPUT_TOKENS:invalid},'missing.env');
    assert.equal(c.maxOutputTokens,4096,invalid);assert.equal(c.retryMaxOutputTokens,8192,invalid);
  }
  const bounded=loadConfig({QCAL_MODEL_MAX_OUTPUT_TOKENS:'16384',QCAL_MODEL_RETRY_MAX_OUTPUT_TOKENS:'32768'},'missing.env');
  assert.equal(bounded.maxOutputTokens,16384);assert.equal(bounded.retryMaxOutputTokens,32768);
  assert.equal(resolveOutputBudget({...cfg,maxOutputTokens:32768}).retryLimit,0);
  assert.equal(resolveOutputBudget({...cfg,outputBudgetRetry:false}).maxHttpRequests,1);
});

test('thinking-only budget exhaustion gets one bounded larger request with identical context',async()=>{
  const events=[];
  await withServer((n,res)=>json(res,n===1?thinking:answer('final answer')),async(config,seen)=>{
    assert.equal(await complete({...config,onProviderRetry:event=>events.push(event)},messages),'final answer');
    assert.deepEqual(seen.map(body=>body.max_tokens),[4096,8192]);
    assert.deepEqual({...seen[1],max_tokens:4096},seen[0]);
    assert.equal(events.length,1);assert.equal(events[0].retryCount,1);
    assert.equal(events[0].providerDiagnostic.stopReason,'max_tokens');
    assert.ok(!JSON.stringify(events).includes('private-reasoning'));
  });
});

test('ordinary empty, text at max_tokens, mixed blocks and error envelopes do not retry',async t=>{
  for(const [name,response,expected] of [
    ['empty',{stop_reason:'end_turn',content:[]},'provider_empty_text'],
    ['text at limit',{stop_reason:'max_tokens',content:[{type:'text',text:'final fragment'}]},null],
    ['empty text without thinking',{stop_reason:'max_tokens',content:[{type:'text',text:''}]},'provider_output_limit_without_text'],
    ['mixed beyond diagnostic cap',{stop_reason:'max_tokens',content:[...Array(21).fill(thinking.content[0]),{type:'tool_use',id:'test'}]},'provider_output_limit_without_text'],
    ['error envelope',{...thinking,error:{message:'private-error'}},'provider_error_envelope'],
  ])await t.test(name,async()=>withServer((n,res)=>json(res,response),async(config,seen)=>{
    if(expected)await assert.rejects(complete(config,messages),new RegExp(expected));
    else assert.equal(await complete(config,messages),'final fragment');
    assert.equal(seen.length,1);
  }));
});

test('second exhaustion or HTTP failure ends at two requests',async t=>{
  for(const [name,second,status,code] of [['exhausted',thinking,200,'provider_output_limit_without_text'],['HTTP error',{},503,'provider_http_503']])
    await t.test(name,async()=>withServer((n,res)=>json(res,n===1?thinking:second,n===1?200:status),async(config,seen)=>{
      let retries=0;
      await assert.rejects(complete({...config,onProviderRetry:()=>retries++},messages),new RegExp(code));
      assert.equal(seen.length,2);assert.equal(retries,1);
    }));
});

test('account abort cancels a retry using the original signal',async()=>{
  const account=new AbortController();
  await withServer((n,res)=>{if(n===1)json(res,thinking);else account.abort();},async(config,seen)=>{
    await assert.rejects(complete({...config,accountSignal:account.signal},messages),/model_configuration_changed/);
    assert.equal(seen.length,2);
  });
  let calls=0;
  await withServer((n,res)=>{calls++;json(res,answer('never'));},async config=>{
    await assert.rejects(complete({...config,accountSignal:account.signal},messages),/model_configuration_changed/);
    assert.equal(calls,0);
  });
});

test('retry shares the original absolute timeout instead of receiving a fresh timeout',async()=>{
  await withServer(async(n,res)=>{
    await new Promise(resolve=>setTimeout(resolve,n===1?100:250));
    if(!res.destroyed)json(res,n===1?thinking:answer('too late'));
  },async(config,seen)=>{
    const start=Date.now();
    await assert.rejects(complete({...config,timeoutMs:280},messages),/model_timeout/);
    assert.equal(seen.length,2);assert.ok(Date.now()-start<420,'must not reset the deadline for the retry');
  });
});

test('Brain records only whitelisted retry metadata and allows only one budget retry per run',async()=>{
  const store=new Store(':memory:');let count=0;
  try{
    const brain=new Brain(cfg,store,async(config)=>{
      count++;
      if(count===1){
        config.onProviderRetry({providerDiagnostic:{protocol:'anthropic_messages',stopReason:'max_tokens',blockTypes:['thinking'],hasText:false,privateText:'private-reasoning'},raw:'private-key'});
        return JSON.stringify({type:'tool_call',tool:'list_rules',arguments:{}});
      }
      assert.equal(config.outputBudgetRetry,false);
      return JSON.stringify({type:'final',content:'ok'});
    });
    const result=await brain.run('chat',{message:'offline test'});
    assert.equal(result.status,'ok');assert.equal(count,2);
    const retry=store.events(result.sessionId).filter(e=>e.event_type==='runtime_provider_retry');
    assert.equal(retry.length,1);assert.equal(retry[0].payload.fromMaxTokens,4096);assert.equal(retry[0].payload.toMaxTokens,8192);
    assert.ok(!JSON.stringify(retry).includes('private-'));
  }finally{store.close();}
});

test('Brain tool rounds share one absolute deadline and decreasing remaining timeout',async()=>{
  const store=new Store(':memory:'),seen=[];
  try{
    const brain=new Brain(cfg,store,async config=>{
      seen.push({deadline:config.requestDeadlineAt,timeout:config.timeoutMs});
      if(seen.length===1){await new Promise(resolve=>setTimeout(resolve,25));return JSON.stringify({type:'tool_call',tool:'list_rules',arguments:{}});}
      return JSON.stringify({type:'final',content:'ok'});
    });
    assert.equal((await brain.run('chat',{message:'offline test'})).status,'ok');
    assert.equal(seen.length,2);assert.equal(seen[0].deadline,seen[1].deadline);
    assert.ok(seen[1].timeout<seen[0].timeout);
  }finally{store.close();}
});
