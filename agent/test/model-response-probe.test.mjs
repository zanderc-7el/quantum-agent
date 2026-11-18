import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Store} from '../src/store.mjs';
import {parseProbeArgs,readLatestDecisionInput,probeOnce,safeProbeCode} from '../../scripts/model-response-probe.mjs';

const input={runId:'offline-r1',nodeId:'s2',revision:2,mode:'real',goal:'test',observations:[{id:'e1',value:1}],allowedActions:['wait'],parameterBounds:{},rules:[],history:[]};
const config={configured:true,providerId:'ziyor',protocol:'anthropic_messages',model:'offline-model',apiKey:'dummy-offline-key',timeoutMs:2000};

test('probe defaults to preview and rejects unknown arguments',()=>{
  assert.deepEqual(parseProbeArgs(['--session','run_test']),{sessionId:'run_test',execute:false});
  assert.deepEqual(parseProbeArgs(['--execute','--session','run_test']),{sessionId:'run_test',execute:true});
  for(const args of [[],['--session','../outside'],['--session','x','--restart'],['--session','x','--execute','--execute']])assert.throws(()=>parseProbeArgs(args));
});

test('probe reads only the specified latest decide input and leaves source database unchanged',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'qcal-probe-offline-')),dbPath=path.join(dir,'test.sqlite');
  try{
    const store=new Store(dbPath),id=store.session('chosen');
    store.message(id,'user',JSON.stringify({...input,revision:1}));
    store.event(id,'runtime_started',{kind:'decide'});
    store.message(id,'user',JSON.stringify(input));
    const seq=store.event(id,'runtime_started',{kind:'decide'});
    store.message('another','user',JSON.stringify({...input,revision:99}));
    store.event('another','runtime_started',{kind:'decide'});
    store.close();
    const before=fs.readFileSync(dbPath),result=readLatestDecisionInput(dbPath,'chosen');
    assert.deepEqual(result.input,input);assert.equal(result.source.eventSeq,seq);
    assert.equal(result.source.kind,'decide');assert.ok(result.source.inputChars>0);
    assert.deepEqual(fs.readFileSync(dbPath),before);
    assert.throws(()=>readLatestDecisionInput(dbPath,'missing'),/probe_decide_input_missing/);
  }finally{assert.equal(path.dirname(path.resolve(dir)),path.resolve(os.tmpdir()));fs.rmSync(dir,{recursive:true,force:true});}
});

test('probe captures genuine HTTP empty-response structure without body text or credentials',async()=>{
  let requests=0;
  const server=http.createServer((req,res)=>{requests++;req.resume();res.setHeader('Content-Type','application/json');res.end(JSON.stringify({stop_reason:'max_tokens',content:[{type:'thinking',thinking:'private-reasoning'}]}));});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    const result=await probeOnce({...config,baseUrl:`http://127.0.0.1:${server.address().port}/v1`},input);
    assert.equal(requests,1);assert.equal(result.requests,1);assert.equal(result.maxHttpRequests,1);assert.equal(result.maxOutputTokens,4096);assert.equal(result.hasText,false);assert.equal(result.textChars,0);
    assert.equal(result.code,'provider_output_limit_without_text');assert.equal(result.decisionValidated,false);
    assert.equal(result.providerDiagnostic.stopReason,'max_tokens');assert.deepEqual(result.providerDiagnostic.blockTypes,['thinking']);
    assert.ok(!JSON.stringify(result).includes('private-'));assert.ok(!JSON.stringify(result).includes(config.apiKey));
  }finally{await new Promise(resolve=>server.close(resolve));}
});

test('probe validates a final response but prints only presence and length',async()=>{
  const response=JSON.stringify({type:'final',content:JSON.stringify({action:'wait',reason:'private-answer-body',evidenceIds:['e1']})});
  const result=await probeOnce(config,input,async()=>response);
  assert.equal(result.requests,1);assert.equal(result.hasText,true);assert.equal(result.textChars,response.length);assert.equal(result.decisionValidated,true);
  assert.ok(!JSON.stringify(result).includes('private-answer-body'));
});

test('probe never sends a second request for tools or JSON repair',async t=>{
  for(const response of ['bad-json',JSON.stringify({type:'tool_call',tool:'list_rules',arguments:{}})])await t.test(response.startsWith('{')?'tool':'repair',async()=>{
    let calls=0;const result=await probeOnce(config,input,async()=>{calls++;return response;});
    assert.equal(calls,1);assert.equal(result.requests,1);assert.equal(result.code,'model_probe_single_request_limit');
    assert.equal(result.hasText,true);assert.equal(result.decisionValidated,false);
  });
});

test('probe does not fall back from unavailable membership and sanitizes unknown errors',async()=>{
  let calls=0;
  await assert.rejects(probeOnce({...config,providerId:'other'},input,async()=>{calls++;}),/probe_membership_unavailable/);
  assert.equal(calls,0);assert.equal(safeProbeCode('private-key-in-error'),'probe_failed');
  assert.equal(safeProbeCode('provider_http_429'),'provider_http_429');assert.equal(safeProbeCode('provider_http_429-private'),'probe_failed');
});
