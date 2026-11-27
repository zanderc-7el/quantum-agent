import test from 'node:test';import assert from 'node:assert/strict';
import {createDecisionHistory,historyCapability} from '../src/decision-history.mjs';
import {Brain} from '../src/brain.mjs';import {Store} from '../src/store.mjs';import {validateDecision,collectEvidence} from '../src/validation.mjs';
const input={runId:'run',nodeId:'resource:rabi',revision:7,mode:'real',goal:'TEST ONLY',observations:[{id:'current'}],allowedActions:['retry','wait'],parameterBounds:{amp_stop:{min:.1,max:.99}},rules:[],history:[],
 historicalEvidenceSearch:{schema:'qcal-historical-evidence-search/1',runId:'run',nodeId:'resource:rabi',revision:7,procedureId:'sq_rabi_amplitude',resourceId:'resource'}};
const result={schema:'qcal-historical-measurements/1',binding:input.historicalEvidenceSearch,projectId:'qcal-local-f8-platform',projectRoot:'C:/test',readOnly:true,currentCalibration:false,executionAuthorized:false,
 cases:[{caseRef:'historical-only',sourceAttempt:'old-attempt',currentCalibration:false,operations:[{stage:'selection',measuredCandidates:{drive_amp:.86}},{stage:'verification',measuredCandidates:{drive_amp:.84}}]}]};
const cfg={configured:true,protocol:'openai_chat_completions',model:'test-only',apiKey:'TEST',timeoutMs:2000};
test('bound GET uses loopback identity, revision and no model-supplied target',async()=>{
 const calls=[];const tool=createDecisionHistory({root:'C:/test',request:async(url,options)=>{calls.push([url.pathname+url.search,options]);return Response.json(result);}});
 assert.deepEqual((await tool.execute({limit:2},input)).cases,result.cases);assert.equal(calls.length,1);assert.ok(calls.every(c=>c[1].method==='GET'));
 assert.match(calls[0][0],/resource%3Arabi\/historical-evidence\?revision=7&limit=2$/);
 for(const args of [{workflowId:'other'},{limit:4},{limit:1.5},{limit:true}])await assert.rejects(tool.execute(args,input),/arguments_invalid/);
 assert.equal(calls.length,1);
});
test('scope, foreign service, oversized and wrong-version responses fail closed',async()=>{
 assert.equal(historyCapability({...input,mode:'replay'}),null);assert.equal(historyCapability({...input,revision:8}),null);
 assert.throws(()=>createDecisionHistory({root:'C:/test',baseUrl:'http://10.0.0.1:7110'}),/address_not_allowed/);
 for(const damage of ['foreign','oversize','revision','authorization']){
  const tool=createDecisionHistory({root:'C:/test',request:async url=>{
   const data=structuredClone(result);if(damage==='revision')data.binding.revision++;if(damage==='authorization')data.executionAuthorized=true;
   if(damage==='foreign')data.projectRoot='C:/other';
   return damage==='oversize'?new Response('x'.repeat(15001)):Response.json(data);
  }});await assert.rejects(tool.execute({},input));
 }
});
test('actual decision tool loop receives historical pair and cannot cite it as current acceptance',async()=>{
 const store=new Store(':memory:');let calls=0;const seen=[];
 const decision={action:'retry',reason:'Compare the historical conditions; current gates remain unchanged.',evidenceIds:['current'],parameterChanges:{amp_stop:.99}};
 const outputs=[{type:'tool_call',tool:'search_historical_measurements',arguments:{limit:2}},
  {type:'tool_call',tool:'validate_proposal',arguments:{decision:{...decision,evidenceIds:['old-attempt']}}},
  {type:'final',content:JSON.stringify(decision)}];
 const brain=new Brain(cfg,store,async(_,messages)=>{seen.push(structuredClone(messages));return JSON.stringify(outputs.shift());},
  {decisionHistory:{execute:async(args,bound)=>{calls++;assert.equal(bound,input);return {ok:true,...result};}}});
 try{const out=await brain.run('decide',input);assert.equal(out.status,'ok');assert.equal(calls,1);
  assert.match(seen[0][0].content,/search_historical_measurements/);assert.match(seen[1].at(-1).content,/historical-only/);
  assert.match(seen[2].at(-1).content,/unknown_evidence/);assert.deepEqual([...collectEvidence(input)],['current']);
  assert.throws(()=>validateDecision({...decision,evidenceIds:['historical-only']},input));
 }finally{store.close();}
});
test('ordinary decisions without capability do not expose or execute search',async()=>{
 const store=new Store(':memory:');let called=0;const plain={...input};delete plain.historicalEvidenceSearch;
 const outputs=[{type:'tool_call',tool:'search_historical_measurements',arguments:{}},{type:'final',content:JSON.stringify({action:'wait',reason:'No current permission',evidenceIds:['current']})}];
 const brain=new Brain(cfg,store,async()=>JSON.stringify(outputs.shift()),{decisionHistory:{execute:async()=>{called++;}}});
 try{assert.equal((await brain.run('decide',plain)).status,'ok');assert.equal(called,0);}finally{store.close();}
});
