import test from 'node:test';
import assert from 'node:assert/strict';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';
import {auditReaderText,readerWritingPolicy} from '../src/reader-writing.mjs';
import {parseAgentAction} from '../src/action-envelope.mjs';
const context={workflow:{id:'wf-fixture',revision:7,nodes:[{id:'rb',engineState:'quality_attention'}]}};
const body='RB参考漂移26.9%，超过10%的验收门槛。单门保真度尚未验收。';
const audit=()=>({readerDecision:'判断RB能否验收',claims:[{claim:body,evidenceIds:['rb'],scope:'本轮Q11',limitation:'参考漂移未通过'}],
  sentences:[{text:body,function:'claim',consumer:'实验负责人',decision:'保留未通过状态',ifRemoved:'失去质量依据',action:'KEEP',revision:body}],
  retainedLimitations:['单门保真度尚未验收'],unresolvedQuestions:[]});
const cfg={configured:true,protocol:'openai_chat_completions',model:'offline-recorder',apiKey:'unused',timeoutMs:2000};

test('specified skill has a source hash and review never implies human acceptance or factual verification',()=>{
  const policy=readerWritingPolicy();assert.match(policy.skillSha256,/^[a-f0-9]{64}$/);assert.ok(policy.guidance.includes('Sentence')||policy.guidance.includes('sentence'));
  const result=auditReaderText(audit(),context);assert.equal(result.approvedText,body);assert.equal(result.humanAccepted,false);assert.equal(result.factsIndependentlyVerified,false);
});
test('material edits, unknown evidence and numeric removal are not approved by a style audit',()=>{
  for(const change of [a=>{a.sentences[0].action='DROP';a.sentences[0].revision='';},
    a=>{a.sentences[0].revision='单门保真度已经验收。';},
    a=>{a.claims[0].evidenceIds=['invented'];},
    a=>{a.sentences[0].function='generic_disclaimer';a.sentences[0].action='DROP';a.sentences[0].revision='';}]){
    const data=audit();change(data);assert.throws(()=>auditReaderText(data,context));
  }
});
test('removes a redundant announcement without changing the adjacent experimental claim',()=>{
  const data=audit();data.sentences.unshift({text:'值得注意的是，下面是本次报告的说明。',function:'signposting',consumer:'实验负责人',decision:'不改变实验判断',ifRemoved:'没有事实损失',action:'DROP',revision:''});
  assert.equal(auditReaderText(data,context).approvedText,body);
});
test('chat requires audit then exact approved prose, recording the audit separately from chat content',async()=>{
  const store=new Store(':memory:');let n=0;
  const actions=[{type:'final',content:body},{type:'tool_call',tool:'audit_reader_text',arguments:audit()},
    {type:'final',content:body+' 未批准的新结论。'},{type:'final',content:body}];
  try{
    const brain=new Brain(cfg,store,async()=>JSON.stringify(actions[n++]));
    const result=await brain.run('chat',{message:'现在测得怎么样？',context});
    assert.equal(result.status,'ok');assert.equal(result.message,body);assert.equal(n,4);
    const events=store.events(result.sessionId);assert.equal(events.filter(e=>e.event_type==='reader_text_audited').length,1);
    assert.equal(events.find(e=>e.event_type==='reader_text_audited').payload.humanAccepted,false);
    assert.equal(store.history(result.sessionId).at(-1).content,body);
    assert.ok(!events.some(e=>e.event_type==='decision_proposed'));
  }finally{store.close();}
});
test('writing audit is unavailable in the measurement decision lane',async()=>{
  const store=new Store(':memory:');let n=0;
  try{
    const brain=new Brain(cfg,store,async()=>JSON.stringify(n++?{type:'final',content:JSON.stringify({action:'wait',reason:'fixture',evidenceIds:[]})}:{type:'tool_call',tool:'audit_reader_text',arguments:audit()}));
    const result=await brain.run('decide',{runId:'fixture',nodeId:'rb',revision:7,goal:'fixture',mode:'real',observations:[],allowedActions:['wait'],parameterBounds:{}});
    assert.equal(result.status,'ok');assert.equal(store.events(result.sessionId).find(e=>e.event_type==='runtime_tool_result').payload.result.error,'tool_not_allowed');
  }finally{store.close();}
});
test('complete public tool envelope can normalize only the whitelisted audit tool',()=>{
  assert.equal(parseAgentAction(`<tool_call>${JSON.stringify({name:'audit_reader_text',arguments:audit()})}</tool_call>`).ok,true);
  assert.equal(parseAgentAction(`<tool_call>${JSON.stringify({name:'publish_report',arguments:{}})}</tool_call>`).ok,false);
});
