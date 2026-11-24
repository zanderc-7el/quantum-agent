import test from 'node:test';
import assert from 'node:assert/strict';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';
import {parseAgentAction} from '../src/action-envelope.mjs';
const config={configured:true,protocol:'openai_chat_completions',model:'fixture',timeoutMs:2000};
const final=value=>JSON.stringify({type:'final',content:JSON.stringify(value)});
const tool=value=>JSON.stringify({type:'tool_call',...value});

test('operator can propose bounded configuration and report actions without executing',async()=>{
 const store=new Store(':memory:');
 const actions=[
  {tool:'save_chip_draft',arguments:{draft:{name:'样片',qubits:[{id:'Q19',parameters:{drive_hz:5e9}}]}}},
  {tool:'save_deployment_draft',arguments:{draft:{name:'接线',instrument:{},routes:{Q19:{xyPhysical:'xy1'}}}}},
  {tool:'publish_configuration',arguments:{kind:'chip',configurationId:'chip-one'}},
  {tool:'export_report',arguments:{workflowId:'wf-one',plotsPerAppendix:64}}];
 const outputs=[...actions.map(tool),final({message:'提交方案供控制器校验。',proposals:actions})];
 const brain=new Brain(config,store,async()=>outputs.shift());
 try{
  const result=await brain.run('workspace',{message:'保存配置并导出报告',context:{scope:{kind:'workspace'},intent:'execute'}});
  assert.equal(result.status,'ok');assert.deepEqual(result.proposals,actions);
  assert.equal(store.events(result.sessionId).filter(e=>e.event_type==='workspace_proposal').length,4);
  assert.ok(store.events(result.sessionId).filter(e=>e.event_type==='runtime_tool_result').every(e=>e.payload.result.executed===false));
  for(const action of actions)assert.equal(parseAgentAction(`<tool_call>${JSON.stringify({name:action.tool,arguments:action.arguments})}</tool_call>`).ok,true);
 }finally{store.close();}
});

test('draft schema rejects nested executable fields and invalid report options before proposing',async()=>{
 const store=new Store(':memory:');
 const invalid=[
  {tool:'save_chip_draft',arguments:{draft:{name:'样片',qubits:[{id:'Q19',parameters:{},operationId:'invented'}]}}},
  {tool:'save_deployment_draft',arguments:{draft:{name:'接线',instrument:{},routes:{},revision:9}}},
  {tool:'publish_configuration',arguments:{kind:'account',configurationId:'x'}},
  {tool:'export_report',arguments:{workflowId:'wf-one',plotsPerAppendix:6}}];
 const outputs=[...invalid.map(tool),final({message:'请核对输入。',proposals:[]})];
 const brain=new Brain(config,store,async()=>outputs.shift());
 try{
  const result=await brain.run('workspace',{message:'保存配置',context:{}});
  assert.equal(result.status,'ok');assert.equal(store.events(result.sessionId).filter(e=>e.event_type==='workspace_proposal').length,0);
  assert.ok(store.events(result.sessionId).filter(e=>e.event_type==='runtime_tool_result').every(e=>e.payload.result.ok===false));
 }finally{store.close();}
});

test('large document tool result remains parseable and announces truncation',async()=>{
 const store=new Store(':memory:');let index=0;const seen=[];
 const brain=new Brain(config,store,async(_,messages)=>{seen.push(structuredClone(messages));return index++===0?tool({tool:'read_document',arguments:{documentId:'doc1'}}):final({message:'资料预览待核对。',proposals:[]});});
 try{
  await brain.run('workspace',{message:'阅读资料',context:{documents:[{id:'doc1',blocks:[{id:'b1',text:'字'.repeat(30000)}]}]}});
  const text=seen[1].at(-1).content;const value=JSON.parse(text.slice(text.indexOf('{')));
  assert.equal(value.truncated,true);assert.equal(value.document.id,'doc1');assert.ok(text.length<17000);
 }finally{store.close();}
});

test('controller continuation receipts survive the following user turn as data',async()=>{
 const store=new Store(':memory:');const seen=[];
 const brain=new Brain(config,store,async(_,messages)=>{seen.push(structuredClone(messages));return final({message:'已按回执核对。',proposals:[]});});
 const base={sessionId:'same-conversation',message:'保存配置',context:{scope:{kind:'workspace'},intent:'execute'}};
 try{
  await brain.run('workspace',base);
  await brain.run('workspace',{...base,context:{...base.context,continuation:true,continuationRound:1,toolResults:[{tool:'save_chip_draft',arguments:{},status:'draft_ready',result:{chipId:'chip-saved'},executed:true}]}});
  await brain.run('workspace',{...base,message:'刚才保存到了哪里',context:{scope:{kind:'workspace'},intent:'discuss'}});
  const followup=seen[2].filter(m=>m.role==='user').map(m=>JSON.parse(m.content));
  assert.equal(followup.filter(m=>m.message==='保存配置').length,1);
  assert.ok(followup.some(m=>m.context?.toolResults?.[0].result?.chipId==='chip-saved'));
 }finally{store.close();}
});

test('a proposed save omitted by final is repaired before claiming successful handoff',async()=>{
 const store=new Store(':memory:');const seen=[];
 const proposal={tool:'save_chip_draft',arguments:{draft:{name:'测试芯片Q18',qubits:[{id:'Q18',physicalId:null,busId:null,identityStatus:'unresolved',parameters:{readout_hz:7.2e9,drive_hz:4.6e9}}]}}};
 const outputs=[tool(proposal),final({message:'已提交提议，等待控制器。',proposals:[]}),final({message:'保存草稿。',proposals:[proposal]})];
 const brain=new Brain(config,store,async(_,messages)=>{seen.push(structuredClone(messages));return outputs.shift();});
 try{
  const result=await brain.run('workspace',{message:'保存 Q18 草稿，不要发布，不要启动。',context:{intent:'execute'}});
  assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[proposal]);
  assert.equal(seen.length,3);assert.match(seen[2].at(-1).content,/pending_proposals_omitted/);
  assert.equal(store.events(result.sessionId).filter(e=>e.event_type==='runtime_done').length,1);
 }finally{store.close();}
});

test('equivalent repeated tool calls and final proposals are handed off once in discussion',async()=>{
 const store=new Store(':memory:');
 const proposal={tool:'export_report',arguments:{workflowId:'wf-one',plotsPerAppendix:16}};
 const reordered={tool:'export_report',arguments:{plotsPerAppendix:16,workflowId:'wf-one'}};
 const outputs=[tool(proposal),tool(reordered),final({message:'导出建议。',proposals:[reordered,proposal]})];
 const brain=new Brain(config,store,async()=>outputs.shift());
 try{
  const result=await brain.run('workspace',{message:'讨论报告方案',context:{intent:'discuss'}});
  assert.equal(result.status,'ok');assert.equal(result.proposals.length,1);
  const events=store.events(result.sessionId);
  assert.equal(events.filter(e=>e.event_type==='workspace_proposal').length,1);
  assert.ok(events.filter(e=>e.event_type==='runtime_tool_result').every(e=>e.payload.result.executed===false));
  assert.equal(events.find(e=>e.event_type==='runtime_done').payload.executed,false);
 }finally{store.close();}
});

test('repeated omission fails closed without returning successful empty proposals',async()=>{
 const store=new Store(':memory:');let calls=0;
 const proposal={tool:'save_chip_draft',arguments:{draft:{name:'草稿',qubits:[{id:'Q18',parameters:{}}]}}};
 const brain=new Brain(config,store,async()=>calls++===0?tool(proposal):final({message:'已提交提议。',proposals:[]}));
 try{
  const result=await brain.run('workspace',{message:'保存 Q18 草稿',context:{intent:'execute'}});
  assert.equal(result.status,'waiting_model');assert.equal(result.proposals,undefined);
  assert.ok(calls<=14);assert.ok(!store.events(result.sessionId).some(e=>e.event_type==='runtime_done'));
 }finally{store.close();}
});

test('proposal repair retains its budget after a long inspection sequence',async()=>{
 const store=new Store(':memory:');
 const proposal={tool:'save_chip_draft',arguments:{draft:{name:'草稿',qubits:[{id:'Q18',parameters:{}}]}}};
 const outputs=[...Array.from({length:6},()=>tool({tool:'list_workspace',arguments:{}})),tool(proposal),
  final({message:'提交草稿。',proposals:[]}),final({message:'保存草稿。',proposals:[proposal]})];
 const brain=new Brain(config,store,async()=>outputs.shift());
 try{
  const result=await brain.run('workspace',{message:'查清楚后保存 Q18 草稿',context:{intent:'execute'}});
  assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[proposal]);
  assert.equal(store.events(result.sessionId).filter(e=>e.event_type==='workspace_proposal').length,1);
 }finally{store.close();}
});
