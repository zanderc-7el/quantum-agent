// Actual Brain/tool loop with an explicit offline provider. No model account,
// instrument, server or production database is contacted by these contracts.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

const fixtureUrl=new URL('../../tests/fixtures/chat-cavity-roles.json',import.meta.url);
const fixture=fs.existsSync(fixtureUrl)?JSON.parse(fs.readFileSync(fixtureUrl,'utf8')):null;
const privateFixtureOptions={skip:!fixture&&'Private regression fixture excluded from source release'};
const result=fixture?.nodes[0].attempts[0].result;
const op=(stage,source)=>({id:stage,workflowId:'test-only',attemptId:'test-attempt',
  operationId:'test-attempt/'+(stage==='selection'?'measure':'verify'),metrics:source.metrics,
  parameterEvidence:{source:'this_operation_result',measuredCandidates:{readout_hz:{value:source.candidateParameters.readout_hz,
    role:'measured_result_candidate',frequencyUnits:{GHz:'7.009676666666667'}}},
  configuredInputs:{readout_hz:{value:source.parameters.readout_hz,role:'acquisition_configured_input'}}}});
const observations=result?[op('selection',result.selectionResult),op('verification',result)]:[];
const context={workflow:{id:'test-only',revision:1},observations,
  acquisitionState:{snapshotAt:'2026-11-26T19:02:03+00:00',snapshotAtBeijing:'2026-11-27T03:02:03+08:00',
    displayTimeZone:'Asia/Shanghai (UTC+08:00)',nodes:[{operations:observations}]}};
const config={configured:true,protocol:'openai_chat_completions',model:'OFFLINE-provider-fixture',timeoutMs:2000};

for(const question of ['请只查看最新记录，首测和独立复测各得到多少GHz、信噪比是多少？',
  '找腔第一轮与重复验证的数据相同吗？请讨论差异的可能原因。']){
  test('real Brain keeps candidate roles through inspect_observation: '+question,privateFixtureOptions,async()=>{
    const store=new Store(':memory:');let calls=0,answer;
    try{
      const brain=new Brain(config,store,async(_config,messages)=>{
        calls++;
        const prompt=messages[0].content;
        for(const term of ['measuredCandidates','configuredInputs','committedParameters','snapshotAtBeijing','UTC+08:00'])assert.ok(prompt.includes(term),term);
        if(calls===1){
          assert.equal(JSON.parse(messages.at(-1).content).message,question);
          return JSON.stringify({type:'tool_call',tool:'inspect_observation',arguments:{evidenceId:'selection'}});
        }
        if(calls===2){
          const tool=store.events(store.db.prepare('SELECT id FROM sessions LIMIT 1').get().id)
            .find(e=>e.event_type==='runtime_tool_result'&&e.payload.result?.observation)?.payload.result.observation;
          assert.equal(tool.parameterEvidence.configuredInputs.readout_hz.value,7009610000);
          assert.equal(tool.parameterEvidence.measuredCandidates.readout_hz.value,7009676666.666667);
          assert.equal(tool.metrics.contrast_snr,40.6003991710448);
          answer=`首测候选频率 ${tool.parameterEvidence.measuredCandidates.readout_hz.frequencyUnits.GHz} GHz；信噪比 ${tool.metrics.contrast_snr}。截至北京时间 2026-11-27 03:02:03（UTC+08:00）。`;
          return JSON.stringify({type:'tool_call',tool:'audit_reader_text',arguments:{readerDecision:'Understand measured source',
            claims:[{claim:'recorded selection candidate',evidenceIds:['selection'],scope:'test-only snapshot',limitation:'No new acquisition'}],
            sentences:[{text:answer,function:'evidence',consumer:'operator',decision:'inspect evidence',ifRemoved:'source is lost',action:'KEEP',revision:answer}],
            retainedLimitations:['Snapshot only'],unresolvedQuestions:[]}});
        }
        return JSON.stringify({type:'final',content:answer});
      });
      const reply=await brain.run('chat',{message:question,context});
      assert.equal(reply.status,'ok');assert.equal(calls,3);assert.equal(reply.message,answer);
      assert.ok(!store.events(reply.sessionId).some(e=>e.event_type==='decision_proposed'));
    }finally{store.close();}
  });
}
