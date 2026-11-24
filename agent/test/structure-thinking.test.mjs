import test from 'node:test';
import assert from 'node:assert/strict';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';
import {complete} from '../src/provider.mjs';
const config={configured:true,protocol:'anthropic_messages',providerId:'ziyor',model:'DeepSeek-V4.1-Flash',baseUrl:'http://127.0.0.1:1/v1',apiKey:'OFFLINE_FIXTURE',timeoutMs:2000,maxOutputTokens:4096,retryMaxOutputTokens:8192};
const final=value=>JSON.stringify({type:'final',content:typeof value==='string'?value:JSON.stringify(value)});
const cases=[
 ['structure',{documentId:'doc-fixture',blocks:[{id:'b1',text:'Q1 drive 5.1GHz'}],currentDraft:{name:'fixture',qubits:[]}},{draft:{name:'fixture',qubits:[]},issues:[]}],
 ['workspace',{message:'讨论资料',context:{scope:{kind:'workspace'},intent:'discuss'}},{message:'请先核对资料。',proposals:[]}],
 ['chat',{message:'解释结果'},'仅解释现有结果。'],
 ['decide',{runId:'fixture-run',nodeId:'n1',revision:1,mode:'replay',goal:'fixture',observations:[],allowedActions:['wait'],parameterBounds:{},rules:[],history:[]},{action:'wait',reason:'等待证据',evidenceIds:[]}],
];
test('only DeepSeek Anthropic structure requests get the internal non-thinking flag, without modifying shared config',async()=>{
 const shared=Object.freeze({...config}),before=JSON.stringify(shared),store=new Store(':memory:'),seen=[];
 const brain=new Brain(shared,store,async(request,messages)=>{seen.push(request);const kind=JSON.parse(messages.at(-1).content);return final(kind.blocks?cases[0][2]:kind.runId?cases[3][2]:kind.context?.scope?cases[1][2]:cases[2][2]);});
 try{for(const [kind,input] of cases)assert.equal((await brain.run(kind,input)).status,'ok');assert.equal(seen[0].structureNonThinking,true);for(const value of seen.slice(1))assert.equal(value.structureNonThinking,undefined);assert.equal(JSON.stringify(shared),before);assert.equal(Object.hasOwn(shared,'structureNonThinking'),false);}finally{store.close();}
});
test('other Anthropic models and other protocols do not receive the structure override',async()=>{
 for(const options of [{model:'claude-sonnet-fixture'},{model:'notdeepseek-fixture'},{protocol:'openai_chat_completions'},{protocol:'openai_responses'},{protocol:'codex_cli'}]){
  const store=new Store(':memory:');let seen;const brain=new Brain({...config,...options},store,async value=>{seen=value;return final(cases[0][2]);});
  try{assert.equal((await brain.run('structure',cases[0][1])).status,'ok');assert.equal(seen.structureNonThinking,undefined);}finally{store.close();}
 }
});
test('real provider wire sets supported thinking disabled only for structure; experiment/workspace bodies retain defaults',async t=>{
 const bodies=[];let reply;
 t.mock.method(globalThis,'fetch',async(url,options)=>{assert.equal(String(url),'http://127.0.0.1:1/v1/messages');bodies.push(JSON.parse(options.body));return new Response(JSON.stringify({stop_reason:'end_turn',content:[{type:'text',text:final(reply)}]}));});
 const shared=Object.freeze({...config}),store=new Store(':memory:'),brain=new Brain(shared,store);
 try{for(const [kind,input,value] of cases){reply=value;assert.equal((await brain.run(kind,input)).status,'ok');}assert.deepEqual(bodies[0].thinking,{type:'disabled'});assert.equal(bodies[0].max_tokens,4096);for(const body of bodies.slice(1)){assert.equal(Object.hasOwn(body,'thinking'),false);assert.equal(body.max_tokens,4096);}assert.equal(bodies.length,4);assert.equal(Object.hasOwn(shared,'thinking'),false);}finally{store.close();}
});
test('provider ignores the internal flag for non-DeepSeek models and non-Anthropic protocols',async t=>{
 const bodies=[];t.mock.method(globalThis,'fetch',async(url,options)=>{const body=JSON.parse(options.body);bodies.push(body);return new Response(JSON.stringify(String(url).endsWith('/messages')?{content:[{type:'text',text:'ok'}]}:String(url).endsWith('/responses')?{output_text:'ok'}:{choices:[{message:{content:'ok'}}]}));});
 for(const value of [{...config,model:'claude-fixture',structureNonThinking:true},{...config,protocol:'openai_responses',structureNonThinking:true},{...config,protocol:'openai_chat_completions',structureNonThinking:true}])assert.equal(await complete(value,[{role:'user',content:'fixture'}]),'ok');
 assert.ok(bodies.every(body=>!Object.hasOwn(body,'thinking')));
});
test('bounded provider retry retains the per-request disabled thinking control',async t=>{
 const bodies=[];t.mock.method(globalThis,'fetch',async(url,options)=>{bodies.push(JSON.parse(options.body));return new Response(JSON.stringify(bodies.length===1?{stop_reason:'max_tokens',content:[{type:'thinking',thinking:'SYNTHETIC_UNEXPECTED_UPSTREAM_THINKING'}]}:{stop_reason:'end_turn',content:[{type:'text',text:'ok'}]}));});
 assert.equal(await complete({...config,structureNonThinking:true},[{role:'user',content:'fixture'}]),'ok');assert.equal(bodies.length,2);assert.deepEqual(bodies.map(b=>b.thinking),[{type:'disabled'},{type:'disabled'}]);assert.deepEqual(bodies.map(b=>b.max_tokens),[4096,8192]);
});
