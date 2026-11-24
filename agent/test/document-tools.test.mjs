import test from 'node:test';
import assert from 'node:assert/strict';
import {createRuntimeTools} from '../src/runtime-tools.mjs';
import {Brain} from '../src/brain.mjs';
import {Store} from '../src/store.mjs';

const root='D:/allowed/platform';
const scope={scope:{kind:'workspace'},documents:[]};
const filename='TGQ-CD1QV2.03-SATA-P260000-01-33-47#.docx';
const document={id:'import-one',revision:2,filename,status:'analyzed',sha256:'a'.repeat(64),
 blocks:[...Array.from({length:136},(_,i)=>({id:'b'+i,location:'table row '+i,text:'Q2 unrelated source row'})),
  {id:'b136',location:'table 5 row 13',kind:'table_row',text:'比特名称 Q11 | 比特频率 (GHz) 2.14 | 谐振腔频率 (GHz) 6.89'}],
 draft:{name:'delivery',qubits:[{id:'Q11',identityStatus:'unresolved',physicalId:null,
  parameters:{drive_hz:2.14e9,readout_hz:6.89e9},provenance:{drive_hz:{role:'design_initialization_only',acceptedCalibration:false,blockId:'b136'}}}],
  designResources:[{id:'Q11',sourceRole:'design',acceptedCalibration:false,design_drive_hz:2.14e9,design_resonator_hz:6.89e9}]}};
function fixture(overrides={}){
 const requests=[],values={'/api/health':{projectId:'qcal-local-f8-platform',projectRoot:root,status:'ok'},
  '/api/chip-imports':[document,{...document,id:'import-two',revision:3}],'/api/chip-imports/import-one':document,...overrides};
 const request=async(url,options)=>{requests.push({url:String(url),...options});const value=values[new URL(url).pathname];
  return new Response(JSON.stringify(value??{}),{status:value===undefined?404:200});};
 return {requests,tools:createRuntimeTools({root,request})};
}
const tool=(name,args={})=>JSON.stringify({type:'tool_call',tool:name,arguments:args});
const final=()=>JSON.stringify({type:'final',content:JSON.stringify({message:'文档设计值仅供核对，身份仍未确认。',proposals:[]})});
const config={configured:true,protocol:'openai_chat_completions',model:'stub',timeoutMs:2000};

test('global imported-document directory includes unattached uploads and preserves duplicate identities',async()=>{
 const {tools,requests}=fixture();const result=await tools.execute('list_documents',{},scope);
 assert.deepEqual(result.data.items.map(d=>d.id),['import-one','import-two']);
 assert.equal(result.data.items[0].filename,filename);assert.equal(result.data.items[0].sha256,'a'.repeat(64));
 assert.equal(result.data.page.complete,true);assert.equal(result.data.page.total,2);
 assert.doesNotMatch(JSON.stringify(result),/unrelated source row/);
 assert.ok(requests.every(r=>r.method==='GET'&&r.redirect==='error'));
});

test('reading a source by ID and keyword reaches Q11 beyond initial preview without accepting its identity',async()=>{
 const {tools}=fixture();const result=await tools.execute('read_document',{documentId:'import-one',query:'Q11'},scope);
 assert.equal(result.data.id,'import-one');assert.equal(result.data.revision,2);
 assert.equal(result.data.page.total,137);assert.equal(result.data.page.matching,1);
 assert.equal(result.data.blocks[0].id,'b136');assert.match(result.data.blocks[0].text,/2\.14.*6\.89/);
 assert.equal(result.data.draft.qubits[0].physicalId,null);assert.equal(result.data.draft.qubits[0].identityStatus,'unresolved');
 assert.equal(result.data.draft.designResources[0].acceptedCalibration,false);
 assert.equal(result.source.path,'/api/chip-imports/import-one');assert.ok(result.checkedAt);
});

test('document reads paginate and search text around matches rather than silently dropping late text',async()=>{
 const long={...document,blocks:[{id:'large',location:'page 2',text:'前文'.repeat(10000)+' Q11 2.14 GHz 原始表格后文'}]};
 const {tools}=fixture({'/api/chip-imports/import-one':long});
 const result=await tools.execute('read_document',{documentId:'import-one',query:'Q11'},scope);
 assert.match(result.data.blocks[0].text,/Q11 2\.14/);assert.ok(result.data.blocks[0].textRange.start>0);
 assert.equal(result.data.blocks[0].textTruncated,true);
 const page=await fixture().tools.execute('read_document',{documentId:'import-one',offset:135,limit:1},scope);
 assert.equal(page.data.blocks[0].id,'b135');assert.equal(page.data.page.nextOffset,136);assert.equal(page.data.page.complete,false);
});

test('document tools enforce project identity and reject foreign scope or path arguments before reading',async()=>{
 for(const context of [{scope:{kind:'campaign',id:'group'}},{scope:{kind:'workflow',id:'wf-one'}},{}]){
  const {tools,requests}=fixture();
  await assert.rejects(tools.execute('list_documents',{},context),/scope/);
  await assert.rejects(tools.execute('read_document',{documentId:'import-one'},context),/scope/);
  assert.equal(requests.length,0);
 }
 for(const args of [{documentId:'../account'},{documentId:'D:/qoskit-workspace/file'},
   {documentId:'import-one',path:'secret'},{documentId:'import-one',offset:-1},
   {documentId:'import-one',limit:200},{documentId:'import-one',query:'x'.repeat(201)}]){
  const {tools,requests}=fixture();await assert.rejects(tools.execute('read_document',args,scope));assert.equal(requests.length,0);
 }
 const mismatch=fixture({'/api/health':{projectId:'qcal-local-f8-platform',projectRoot:'D:/foreign'}});
 await assert.rejects(mismatch.tools.execute('list_documents',{},scope),/identity/);assert.equal(mismatch.requests.length,1);
 const wrong=fixture({'/api/chip-imports/import-one':{...document,id:'import-foreign'}});
 await assert.rejects(wrong.tools.execute('read_document',{documentId:'import-one'},scope),/response_invalid/);
});

test('workspace list and document read use the registered upload directory even with no conversation attachment',async()=>{
 const {tools,requests}=fixture(),store=new Store(':memory:');
 const outputs=[tool('list_workspace'),tool('read_document',{documentId:'import-one',query:'Q11'}),final()];
 const brain=new Brain(config,store,async()=>outputs.shift(),{runtime:tools});
 try{
  const result=await brain.run('workspace',{message:'只查询刚导入的交付资料Q11，不要改配置',context:{...scope,intent:'discuss'}});
  assert.equal(result.status,'ok');assert.deepEqual(result.proposals,[]);
  const events=store.events(result.sessionId).filter(e=>e.event_type==='runtime_tool_result');
  assert.equal(events[0].payload.result.workspace.documents[0].id,'import-one');
  assert.match(events[1].payload.result.document.blocks[0].text,/2\.14/);
  assert.ok(result.inspections.some(i=>i.tool==='read_document'&&i.source.path==='/api/chip-imports/import-one'));
  assert.ok(requests.every(r=>r.method==='GET'));
 }finally{store.close();}
});

test('reading an unattached upload does not authorize analyze-import mutation proposals',async()=>{
 const {tools}=fixture(),store=new Store(':memory:');
 const outputs=[tool('list_documents'),tool('read_document',{documentId:'import-one'}),
  tool('analyze_import',{documentId:'import-one'}),final()];
 const brain=new Brain(config,store,async()=>outputs.shift(),{runtime:tools});
 try{
  const result=await brain.run('workspace',{message:'只读取资料',context:{...scope,intent:'execute'}});
  assert.deepEqual(result.proposals,[]);assert.equal(store.events(result.sessionId).filter(e=>e.event_type==='workspace_proposal').length,0);
  const denied=store.events(result.sessionId).find(e=>e.event_type==='runtime_tool_result'&&e.payload.name==='analyze_import');
  assert.equal(denied.payload.result.ok,false);assert.equal(denied.payload.result.error,'unknown_document_id');
 }finally{store.close();}
});

test('campaign document access stays attached-only and never lists the global registry',async()=>{
 const {tools,requests}=fixture(),store=new Store(':memory:');
 const outputs=[tool('list_workspace'),tool('list_documents'),tool('read_document',{documentId:'private'}),
  tool('read_document',{documentId:'attached'}),final()];
 const brain=new Brain(config,store,async()=>outputs.shift(),{runtime:tools});
 try{
  const result=await brain.run('workspace',{message:'查看本批次附件',context:{scope:{kind:'campaign',id:'group'},documents:[{id:'attached',filename:'attached.docx',blocks:[]}],intent:'discuss'}});
  const events=store.events(result.sessionId).filter(e=>e.event_type==='runtime_tool_result');
  assert.deepEqual(events[0].payload.result.workspace.documents.map(d=>d.id),['attached']);
  assert.equal(events[1].payload.result.ok,true);assert.deepEqual(events[1].payload.result.data.items.map(d=>d.id),['attached']);
  assert.equal(events[2].payload.result.ok,false);assert.equal(events[3].payload.result.document.id,'attached');
  assert.equal(requests.length,0);
 }finally{store.close();}
});

test('an unavailable upload registry is not reported as an empty workspace document list',async()=>{
 const {tools}=fixture({'/api/chip-imports':undefined}),store=new Store(':memory:');
 const outputs=[tool('list_workspace'),final()];const brain=new Brain(config,store,async()=>outputs.shift(),{runtime:tools});
 try{
  const result=await brain.run('workspace',{message:'查看交付资料',context:scope});
  const observation=store.events(result.sessionId).find(e=>e.event_type==='runtime_tool_result').payload.result;
  assert.equal(observation.workspace.documents,null);assert.equal(observation.workspace.documentCatalog.ok,false);
 }finally{store.close();}
});
