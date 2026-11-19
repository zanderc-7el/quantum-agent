import test from 'node:test';
import assert from 'node:assert/strict';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

const cfg={configured:true,protocol:'openai_chat_completions',model:'offline-recorder',apiKey:'unused',timeoutMs:2000};
const context={workflow:{id:'offline',revision:8653},acquisitionState:{schema:'chat-acquisition-state/1',
  snapshotAt:'2026-11-18T02:59:10Z',workflowRevision:8653,readOnly:true,rawFileRead:false,nodes:[{
    nodeId:'Q11:power',attemptId:'same-attempt',controllerQuality:'unverified',verificationPending:true,
    operations:[{stage:'selection',operationId:'same-attempt/measure',receiptState:'completed',geometry:{
      status:'registered_plan',totalScanPoints:915,jobs:[{frequencyPoints:61,amplitudePoints:5,captureCount:3,
        blocksPerPoint:6,shotsPerBlock:2000,shotsPerPoint:12000}]}},
    {stage:'verification',operationId:'same-attempt/verify',receiptState:'pending',acquisitionState:'acquiring',
      geometry:{status:'not_registered'},progress:{completedScanPoints:245,totalScanPoints:915,asOfRevision:8653}}]}]}};

test('chat recorder receives stage and point semantics; measure and proposal tools cannot execute',async()=>{
  const store=new Store(':memory:');const captured=[];
  const outputs=[{type:'tool_call',tool:'measure',arguments:{}},
    {type:'tool_call',tool:'validate_proposal',arguments:{decision:{action:'retry'}}},
    {type:'tool_call',tool:'audit_reader_text',arguments:{readerDecision:'Decide if verification has completed',
      claims:[{claim:'verification in progress',evidenceIds:['same-attempt'],scope:'snapshot 8653',limitation:'No completion receipt'}],
      sentences:[{text:'OFFLINE fixture: snapshot 8653, verification 245/915. No measurement started.',function:'evidence',consumer:'operator',decision:'wait for completion',ifRemoved:'snapshot scope is lost',action:'KEEP',revision:'OFFLINE fixture: snapshot 8653, verification 245/915. No measurement started.'}],
      retainedLimitations:['Snapshot only'],unresolvedQuestions:[]}},
    {type:'final',content:'OFFLINE fixture: snapshot 8653, verification 245/915. No measurement started.'}];
  try{
    const brain=new Brain(cfg,store,async(_config,messages)=>{
      captured.push(structuredClone(messages));return JSON.stringify(outputs.shift());
    });
    const result=await brain.run('chat',{message:'只读说明首测和复测',context});
    assert.equal(result.status,'ok');
    const system=captured[0][0].content;
    for(const term of ['聊天是只读解释','snapshotAt/workflowRevision','frequencyPoints','totalScanPoints',
      '一个 attempt 可以合法包含不同 /measure 与 /verify operation','telemetry.analysisAccepted'])assert.ok(system.includes(term),term);
    assert.deepEqual(JSON.parse(captured[0].at(-1).content).context,context);
    const errors=store.events(result.sessionId).filter(e=>e.event_type==='runtime_tool_result'&&!e.payload.result.ok).map(e=>e.payload.result.error);
    assert.deepEqual(errors,['tool_not_allowed','proposal_only_in_decide']);
    assert.ok(!store.events(result.sessionId).some(e=>e.event_type==='decision_proposed'));
    assert.deepEqual(JSON.parse(store.history(result.sessionId)[0].content).context,context);
    assert.equal(store.history(result.sessionId).at(-1).content,result.message);
  }finally{store.close();}
});

test('chat-specific explanations do not alter decision prompt or allowed decision contract',async()=>{
  const store=new Store(':memory:');let system;
  try{
    const brain=new Brain(cfg,store,async(_config,messages)=>{
      system=messages[0].content;
      return JSON.stringify({type:'final',content:JSON.stringify({action:'wait',reason:'offline',evidenceIds:[]})});
    });
    const result=await brain.run('decide',{runId:'offline',nodeId:'n',revision:1,goal:'offline',mode:'real',
      observations:[],history:[],allowedActions:['wait'],parameterBounds:{}});
    assert.equal(result.status,'ok');assert.equal(result.decision.action,'wait');
    assert.ok(!system.includes('聊天是只读解释'));assert.ok(!system.includes('snapshotAt/workflowRevision'));
  }finally{store.close();}
});
