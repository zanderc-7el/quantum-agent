import test from 'node:test';
import assert from 'node:assert/strict';
import {Store} from '../src/store.mjs';
import {Brain} from '../src/brain.mjs';
const config={configured:true,protocol:'openai_chat_completions',model:'stub-model',timeoutMs:2000};
const final=value=>JSON.stringify({type:'final',content:JSON.stringify(value)});
const proposal={tool:'create_campaign',arguments:{name:'multi qubit',chipVersionId:'chip-v1',deploymentVersionId:'dep-v1',resourceIds:['Q12','Q19'],coverage:'full'}};

test('workspace proposes using bounded readonly tools and never executes',async()=>{
 const store=new Store(':memory:');let index=0;const messages=[];
 const outputs=[JSON.stringify({type:'tool_call',tool:'list_workspace',arguments:{}}),JSON.stringify({type:'tool_call',tool:'read_document',arguments:{documentId:'doc1'}}),JSON.stringify({type:'tool_call',...proposal}),final({message:'已提出 Q12、Q19 全流程方案，等待控制器校验。',proposals:[proposal]})];
 const brain=new Brain(config,store,async(_,m)=>{messages.push(structuredClone(m));return outputs[index++];});
 try{const result=await brain.run('workspace',{sessionId:'workspace-one',message:'为 Q12、Q19 创建任务',context:{scope:'global',intent:'execute',chips:[],campaigns:[],documents:[{id:'doc1',blocks:[{text:'Q12 explicit mapping'}]}]}});
 assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[proposal]);assert.ok(messages[2].at(-1).content.includes('Q12 explicit mapping'));
 const events=store.events(result.sessionId);assert.equal(events.find(e=>e.event_type==='workspace_proposal').payload.executed,false);
 assert.ok(!events.some(e=>e.event_type==='campaign_created'));assert.ok(messages[0][0].content.includes('控制器独立核对用户授权'));
 }finally{store.close();}
});
test('workspace rejects arbitrary tools and model-generated operation identities',async()=>{
 const store=new Store(':memory:');let index=0;const seen=[];
 const outputs=[JSON.stringify({type:'tool_call',tool:'shell',arguments:{command:'unsafe'}}),final({message:'bad',proposals:[{...proposal,arguments:{...proposal.arguments,operationId:'invented'}}]}),final({message:'仅讨论方案。',proposals:[]})];
 const brain=new Brain(config,store,async(_,m)=>{seen.push(structuredClone(m));return outputs[index++];});
 try{const result=await brain.run('workspace',{message:'讨论方案',context:{intent:'discuss'}});assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[]);assert.ok(seen[1].at(-1).content.includes('tool_not_allowed'));assert.ok(seen[2].at(-1).content.includes('unknown_proposal_argument'));}finally{store.close();}
});
test('structure returns draft issues with no writable tools',async()=>{
 const store=new Store(':memory:');let request;
 const value={draft:{name:'chip',qubits:[{id:'Q19',physicalId:null,identityStatus:'unresolved',parameters:{drive_hz:5.1e9}}]},issues:['Q19 physical identity unresolved']};
 const brain=new Brain(config,store,async(_,m)=>{request=m;return final(value);});
 try{const result=await brain.run('structure',{documentId:'doc1',blocks:[{id:'b1',text:'Q19 drive 5.1GHz'}],currentDraft:{name:'chip',qubits:[]}});assert.equal(result.status,'ok');assert.deepEqual(result.draft,value.draft);assert.deepEqual(result.issues,value.issues);assert.ok(request[0].content.includes('不可信数据'));}finally{store.close();}
});
test('workspace and structure return waiting_model with no fallback provider',async()=>{
 const store=new Store(':memory:');let calls=0;const brain=new Brain({...config,configured:false},store,async()=>{calls++;});
 try{for(const [kind,input] of [['workspace',{message:'create',context:{}}],['structure',{blocks:[]}]])assert.equal((await brain.run(kind,input)).status,'waiting_model');assert.equal(calls,0);}finally{store.close();}
});

test('workspace analyze_import envelope proposes attached-document analysis without executing',async()=>{
 const store=new Store(':memory:');const seen=[];let index=0;
 const analysis={tool:'analyze_import',arguments:{documentId:'doc-attached'}};
 const outputs=[`<tool_call>${JSON.stringify({name:'analyze_import',arguments:analysis.arguments})}</tool_call>`,final({message:'建议先分析附件，整理成芯片配置草稿，再核对比特和接线版本。',proposals:[analysis]})];
 const brain=new Brain(config,store,async(_,messages)=>{seen.push(structuredClone(messages));return outputs[index++];});
 try{
  const result=await brain.run('workspace',{message:'请分析这份附件并整理芯片配置',context:{intent:'execute',documents:[{id:'doc-attached',blocks:[{id:'b1',text:'Q19 5.1GHz'}]}]}});
  assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[analysis]);
  const toolResult=store.events(result.sessionId).find(e=>e.event_type==='runtime_tool_result').payload.result;
  assert.equal(toolResult.status,'proposed');assert.equal(toolResult.executed,false);
  assert.equal(store.events(result.sessionId).find(e=>e.event_type==='runtime_action_normalized').payload.tool,'analyze_import');
  assert.equal(store.events(result.sessionId).find(e=>e.event_type==='workspace_proposal').payload.executed,false);
  assert.equal(seen.length,2);assert.ok(seen[0][0].content.includes('只有控制器能核对附件范围、执行分析和保存草稿'));
  assert.ok(seen[0][0].content.includes('写作规范'));assert.ok(seen[0][0].content.includes('比特、芯片版本、接线版本、全流程测量'));
  assert.ok(!store.events(result.sessionId).some(e=>['document_analyzed','chip_saved','configuration_published'].includes(e.event_type)));
 }finally{store.close();}
});

test('workspace analyze_import rejects unknown attachment and extra writable arguments',async()=>{
 const store=new Store(':memory:');let index=0;
 const outputs=[JSON.stringify({type:'tool_call',tool:'analyze_import',arguments:{documentId:'not-attached'}}),JSON.stringify({type:'tool_call',tool:'analyze_import',arguments:{documentId:'doc-attached',publish:true}}),final({message:'请先添加需要分析的附件。',proposals:[]})];
 const brain=new Brain(config,store,async()=>outputs[index++]);
 try{
  const result=await brain.run('workspace',{message:'整理配置',context:{intent:'execute',documents:[{id:'doc-attached'}]}});
  assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[]);
  const errors=store.events(result.sessionId).filter(e=>e.event_type==='runtime_tool_result').map(e=>e.payload.result.error);
  assert.deepEqual(errors,['unknown_document_id','unknown_tool_argument']);
  assert.equal(store.events(result.sessionId).filter(e=>e.event_type==='workspace_proposal').length,0);
 }finally{store.close();}
});

test('workspace repairs internal field names in human message while preserving tool JSON',async()=>{
 const store=new Store(':memory:');let index=0;const seen=[];
 const outputs=[final({message:'请确认 resourceIds 和 chipVersionId，coverage=full。',proposals:[proposal]}),final({message:'请核对比特、芯片版本和接线版本，再安排全流程测量。',proposals:[proposal]})];
 const brain=new Brain(config,store,async(_,messages)=>{seen.push(structuredClone(messages));return outputs[index++];});
 try{
  const result=await brain.run('workspace',{message:'请安排Q12与Q19',context:{intent:'execute'}});
  assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[proposal]);
  assert.doesNotMatch(result.message,/\b(?:resourceIds|chipVersionId|deploymentVersionId|full)\b/);
  assert.ok(seen[1].at(-1).content.includes('invalid_workspace_reader_terminology'));
  assert.ok(seen[1].at(-1).content.includes('内部字段仅放在工具参数中'));
  assert.equal(result.proposals[0].arguments.coverage,'full');
 }finally{store.close();}
});


test('workspace import-analysis XML envelope remains unavailable in readonly chat',async()=>{
 const store=new Store(':memory:');let index=0;
 const outputs=['<tool_call>{"name":"analyze_import","arguments":{"documentId":"doc-attached"}}</tool_call>',JSON.stringify({type:'final',content:'请在工作区中整理附件配置。'})];
 const brain=new Brain(config,store,async()=>outputs[index++]);
 try{
  const result=await brain.run('chat',{message:'解释附件',context:{documents:[{id:'doc-attached'}]}});
  assert.equal(result.status,'ok');
  assert.equal(store.events(result.sessionId).find(e=>e.event_type==='runtime_tool_result').payload.result.error,'tool_not_allowed');
  assert.equal(store.events(result.sessionId).filter(e=>e.event_type==='workspace_proposal').length,0);
 }finally{store.close();}
});

test('workspace human message guidance stays concise while authorization constraints remain',async()=>{
 const store=new Store(':memory:');let prompt;
 const brain=new Brain(config,store,async(_,messages)=>{prompt=messages[0].content;return final({message:'请先核对物理比特身份。',proposals:[]});});
 try{assert.equal((await brain.run('workspace',{message:'讨论资料',context:{intent:'discuss'}})).status,'ok');assert.ok(prompt.includes('message 简短直接'));assert.ok(prompt.includes('不要使用“先说结论”'));assert.ok(prompt.includes('控制器独立核对用户授权'));assert.ok(prompt.includes('intent=discuss 只讨论'));assert.ok(prompt.includes('不得把历史结果复制为新比特校准值'));}finally{store.close();}
});

test('global operator tools propose native start and workflow stop without device execution',async()=>{
 const store=new Store(':memory:');let index=0;
 const actions=[{tool:'start_measurement',arguments:{resourceIds:['Q11']}},{tool:'workflow_action',arguments:{workflowId:'wf-selected',action:'pause'}}];
 const outputs=[...actions.map(p=>JSON.stringify({type:'tool_call',...p})),final({message:'操作交由控制器校验。',proposals:actions})];
 const brain=new Brain(config,store,async()=>outputs[index++]);
 try{
  const result=await brain.run('workspace',{message:'测试操作工具',context:{intent:'execute',execution:{existingNativeResources:['Q11']}}});
  assert.equal(result.status,'ok');assert.deepEqual(result.proposals,actions);
  const proposed=store.events(result.sessionId).filter(e=>e.event_type==='workspace_proposal');
  assert.equal(proposed.length,2);assert.ok(proposed.every(e=>e.payload.executed===false));
 }finally{store.close();}
});

test('global workflow control rejects unsupported actions and extra routing parameters',async()=>{
 const store=new Store(':memory:');let index=0;
 const outputs=[JSON.stringify({type:'tool_call',tool:'workflow_action',arguments:{workflowId:'wf-selected',action:'force_stop'}}),JSON.stringify({type:'tool_call',tool:'start_measurement',arguments:{resourceIds:['Q11'],adapterId:'invented'}}),final({message:'请使用已配置的任务。',proposals:[]})];
 const brain=new Brain(config,store,async()=>outputs[index++]);
 try{
  const result=await brain.run('workspace',{message:'讨论工具',context:{intent:'execute'}});
  assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[]);
  const errors=store.events(result.sessionId).filter(e=>e.event_type==='runtime_tool_result').map(e=>e.payload.result.error);
  assert.equal(errors.length,2);assert.ok(errors.every(Boolean));
  assert.equal(store.events(result.sessionId).filter(e=>e.event_type==='workspace_proposal').length,0);
 }finally{store.close();}
});

test('workspace model inspects source then live state before producing an evidence-backed reply',async()=>{
 const store=new Store(':memory:');let index=0;const calls=[],seen=[];
 const outputs=[
  JSON.stringify({type:'tool_call',tool:'search_source',arguments:{query:'waiting_hardware',pathPrefix:'backend'}}),
  JSON.stringify({type:'tool_call',tool:'read_source',arguments:{path:'backend/controller.py',startLine:100,endLine:120}}),
  JSON.stringify({type:'tool_call',tool:'inspect_workflow',arguments:{workflowId:'wf-one'}}),
  final({message:'当前任务停在接线确认，尚未采集。',proposals:[]})];
 const source={execute:async(name,args)=>{calls.push([name,args]);return {path:'backend/controller.py',startLine:100,sha256:'a'.repeat(64),lines:[{line:100,text:'waiting_hardware'}],truncated:false};}};
 const runtime={execute:async(name,args,context)=>{calls.push([name,args]);assert.equal(context.scope.kind,'workspace');return {checkedAt:'2026-11-19T00:00:00Z',source:{path:'/api/workflows/wf-one'},data:{engineState:'waiting_hardware'}};}};
 const brain=new Brain(config,store,async(_,messages)=>{seen.push(structuredClone(messages));return outputs[index++];},{source,runtime});
 try{
  const result=await brain.run('workspace',{message:'查源码，为什么任务没开始',context:{scope:{kind:'workspace'},intent:'discuss'}});
  assert.equal(result.status,'ok');assert.equal(result.inspections.length,3);assert.ok(result.inspections.every(r=>r.ok));
  assert.deepEqual(calls.map(c=>c[0]),['search_source','read_source','inspect_workflow']);
  assert.ok(seen.at(-1).at(-1).content.includes('waiting_hardware'));assert.deepEqual(result.proposals,[]);
  assert.equal(store.events(result.sessionId).filter(e=>e.event_type==='workspace_proposal').length,0);
 }finally{store.close();}
});

test('readonly source tools remain unavailable to measurement decision and workflow chat loops',async()=>{
 const store=new Store(':memory:');let index=0,calls=0;
 const brain=new Brain(config,store,async()=>[
  JSON.stringify({type:'tool_call',tool:'read_source',arguments:{path:'backend/controller.py'}}),
  JSON.stringify({type:'final',content:'当前任务尚未完成。'})][index++],{source:{execute:async()=>{calls++;}}});
 try{
  const result=await brain.run('chat',{message:'解释任务',context:{}});
  assert.equal(result.status,'ok');assert.equal(calls,0);
  assert.equal(store.events(result.sessionId).find(e=>e.event_type==='runtime_tool_result').payload.result.error,'tool_not_allowed');
 }finally{store.close();}
});
