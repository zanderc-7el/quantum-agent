import test from 'node:test';
import assert from 'node:assert/strict';
import {createRuntimeTools} from '../src/runtime-tools.mjs';
const root='D:/allowed/platform';
function fixture(overrides={}){
 const requests=[];
 const values={'/api/health':{projectId:'qcal-local-f8-platform',projectRoot:root,status:'ok',hardware:{ownership:{verified:true,owners:[]}}},
 '/api/workflows':[{id:'wf-one',revision:3,status:'paused',resourceIds:['Q11']},{id:'wf-two',status:'running',resourceIds:['Q18']}],
 '/api/workflows/wf-one':{id:'wf-one',revision:3,status:'paused',resourceIds:['Q11'],nodes:[{id:'s01',label:'读出',engineState:'stale'}],configurationBinding:{profile:{apiKey:'secret-never-output',instrument:{host:'10.0.0.1'}}}},
 '/api/campaigns/group':{id:'group',chipVersionId:'cv-one',deploymentVersionId:'dv-one',children:[{workflowId:'wf-one'}]},
 '/api/chips/cv-one':{id:'cv-one',qubits:[{id:'Q11',parameters:{drive_hz:4.4e9}}]},...overrides};
 const fetch=async(url,options)=>{requests.push({url:String(url),...options});return new Response(JSON.stringify(values[new URL(url).pathname]??{}));};
 return {requests,tools:createRuntimeTools({root,baseUrl:'http://127.0.0.1:8765',request:fetch})};
}
test('report inspection preserves completion, acceptance, and figure provenance in bounded data',async()=>{
 const node={id:'s1',engineState:'verified',result:{dataKind:'real',provenance:{freshAcquisition:true},quality:{status:'passed'},metrics:{fidelity:0.99}}};
 const {tools,requests}=fixture({'/api/workflows/wf-one/report':{schema:'qcal-report/1',generatedAt:'snapshot',source:'recorded_data_only',workflowComplete:false,workflow:{id:'wf-one',revision:7,mode:'real',status:'paused'},nodes:[{node,attempts:[{id:'a1',result:{selectionResult:{artifacts:[{id:'image-selection',kind:'image',sha256:'abc'}]},artifacts:[{id:'image-verification',kind:'image',sha256:'def'}]}}]},{node:{...node,id:'s2',result:{...node.result,stale:true}},attempts:[]}]}});
 const value=await tools.execute('inspect_report',{workflowId:'wf-one'},{scope:{kind:'workspace'}});
 assert.equal(value.data.workflowComplete,false);assert.equal(value.data.summary.total,2);assert.equal(value.data.summary.accepted,1);
 assert.equal(value.data.nodes[0].accepted,true);assert.equal(value.data.nodes[1].accepted,false);
 assert.equal(value.data.nodes[0].figures[0].stage,'selection');assert.equal(value.data.nodes[0].figures[1].artifactId,'image-verification');
 assert.ok(requests.every(r=>r.method==='GET'));assert.ok(JSON.stringify(value).length<12000);
});

test('report export inspection checks binding and never exposes an unfinished download',async()=>{
 for(const status of ['queued','completed']){
  const {tools}=fixture({'/api/report-exports/report-one':{id:'report-one',workflowId:'wf-one',status,revision:3,downloadUrl:'/api/report-exports/report-one/file'}});
  const value=await tools.execute('inspect_report_export',{exportId:'report-one'},{scope:{kind:'campaign',id:'group'}});
  assert.equal(value.data.status,status);assert.equal(value.data.downloadUrl,status==='completed'?'/api/report-exports/report-one/file':undefined);
 }
 const {tools}=fixture({'/api/report-exports/report-one':{id:'report-one',workflowId:'wf-two',status:'completed'}});
 await assert.rejects(tools.execute('inspect_report_export',{exportId:'report-one'},{scope:{kind:'campaign',id:'group'}}),/scope/);
 await assert.rejects(tools.execute('inspect_report',{workflowId:'wf-two'},{scope:{kind:'campaign',id:'group'}}),/scope/);
});

test('malformed controller JSON never leaks response content through a tool error',async()=>{
 const tools=createRuntimeTools({root,request:async()=>new Response('private-controller-sentinel is not JSON')});
 await assert.rejects(tools.execute('inspect_runtime',{kind:'health'},{scope:{kind:'workspace'}}),error=>{
  assert.equal(error.message,'runtime_response_invalid');assert.doesNotMatch(error.message,/private-controller-sentinel/);return true;
 });
});
test('runtime inspection reads fresh snapshots, strips secrets and never POSTs',async()=>{
 const {tools,requests}=fixture();
 const result=await tools.execute('inspect_workflow',{workflowId:'wf-one'},{scope:{kind:'workspace'}});
 assert.equal(result.data.revision,3);assert.equal(result.data.nodes[0].engineState,'stale');
 assert.ok(result.checkedAt);assert.doesNotMatch(JSON.stringify(result),/secret-never-output/);
 assert.ok(requests.every(r=>r.method==='GET'));assert.ok(requests.every(r=>r.redirect==='error'));
});
test('runtime validates platform identity before reading task and never accepts arbitrary endpoints',async()=>{
 const {tools,requests}=fixture({'/api/health':{projectId:'qcal-local-f8-platform',projectRoot:'D:/another'}});
 await assert.rejects(tools.execute('inspect_workflow',{workflowId:'wf-one'},{scope:{kind:'workspace'}}),/identity/);
 assert.equal(requests.length,1);
 const clean=fixture();
 await assert.rejects(clean.tools.execute('inspect_workflow',{workflowId:'../account/status'},{scope:{kind:'workspace'}}));
 await assert.rejects(clean.tools.execute('inspect_runtime',{kind:'account'},{scope:{kind:'workspace'}}));
 assert.equal(clean.requests.length,0);
 assert.throws(()=>createRuntimeTools({root,baseUrl:'https://external.example'}));
});
test('campaign scope filters list and rejects foreign workflow and configuration',async()=>{
 const {tools,requests}=fixture(),context={scope:{kind:'campaign',id:'group'}};
 const result=await tools.execute('inspect_runtime',{kind:'workflows'},context);
 assert.deepEqual(result.data.map(w=>w.id),['wf-one']);
 await assert.rejects(tools.execute('inspect_workflow',{workflowId:'wf-two'},context),/scope/);
 assert.ok(!requests.some(r=>r.url.endsWith('/workflows/wf-two')));
 await assert.rejects(tools.execute('inspect_configuration',{kind:'chip',id:'foreign'},context),/scope/);
 const config=await tools.execute('inspect_configuration',{kind:'chip',id:'cv-one'},context);
 assert.equal(config.data.qubits[0].parameters.drive_hz,4.4e9);
});
test('large results remain valid JSON with explicit truncation',async()=>{
 const {tools}=fixture({'/api/workflows/wf-one':{id:'wf-one',status:'paused',nodes:Array.from({length:500},(_,i)=>({id:'s'+i,detail:'a'.repeat(5000)}))}});
 const value=await tools.execute('inspect_workflow',{workflowId:'wf-one'},{scope:{kind:'workspace'}});
 assert.ok(value.truncated);assert.ok(JSON.stringify(value).length<=12000);assert.equal(value.data.id,'wf-one');
 assert.equal(value.data.nodeSummary.total,500);assert.equal(value.data.nodeSummary.complete,false);
 assert.equal(value.data.nodeSummary.returned,value.data.nodes.length);
});

test('large early results cannot hide later verified prerequisites or the current failure',async()=>{
 const nodes=Array.from({length:13},(_,i)=>({id:'Q11:node-'+i,procedureId:'procedure-'+i,status:'pending',engineState:'stale',dependsOn:[],attempt:0,parameters:{sweep:Array(10000).fill(1)},result:{series:Array(10000).fill(2)},telemetry:{samples:Array(10000).fill(3)}}));
 nodes[0]={...nodes[0],status:'success',engineState:'verified',attempt:1};
 nodes[8]={...nodes[8],procedureId:'ro_power_shift',status:'success',engineState:'verified',attempt:1,dependsOn:[nodes[0].id]};
 nodes[1]={...nodes[1],procedureId:'sq_spectroscopy',status:'attention',engineState:'quality_attention',attempt:1,latestAttemptId:'attempt-current',dependsOn:[nodes[0].id,nodes[8].id],detail:'Candidate does not repeat away from scan boundaries',result:{...nodes[1].result,quality:{status:'failed',reasons:['Candidate does not repeat away from scan boundaries','需要独立采集复测']}}};
 const {tools}=fixture({'/api/workflows/wf-one':{id:'wf-one',revision:9,status:'paused',currentNodeId:nodes[1].id,nodes}});
 const value=await tools.execute('inspect_workflow',{workflowId:'wf-one'},{scope:{kind:'workspace'}});
 assert.equal(value.data.nodes.length,13);assert.equal(value.data.nodes[8].engineState,'verified');
 assert.deepEqual(value.data.nodes[1].dependsOn,['Q11:node-0','Q11:node-8']);
 assert.equal(value.data.nodeSummary.complete,true);assert.equal(value.data.nodeSummary.total,13);
 assert.equal(value.data.currentNodeDiagnostic.qualityStatus,'failed');
 assert.equal(value.data.currentNodeDiagnostic.qualityReasons[1],'需要独立采集复测');
 assert.deepEqual(value.data.currentNodeDiagnostic.prerequisites.satisfied.map(n=>n.id),['Q11:node-0','Q11:node-8']);
 assert.deepEqual(value.data.currentNodeDiagnostic.prerequisites.unsatisfied,[]);
 assert.equal(value.data.currentNodeDiagnostic.allPrerequisitesVerified,true);
 assert.ok(value.data.nodes.every(n=>!Object.hasOwn(n,'result')&&!Object.hasOwn(n,'parameters')&&!Object.hasOwn(n,'telemetry')));
 assert.equal(value.detailsOmitted,true);assert.ok(JSON.stringify(value).length<=12000);
});

test('prerequisite diagnosis distinguishes verified, stale, and unknown dependencies',async()=>{
 const {tools}=fixture({'/api/workflows/wf-one':{id:'wf-one',status:'paused',currentNodeId:'current',nodes:[
  {id:'current',status:'attention',engineState:'prerequisites_missing',dependsOn:['passed','stale','absent'],attempt:1},
  {id:'passed',status:'success',engineState:'verified',dependsOn:[],attempt:1},
  {id:'stale',status:'success',engineState:'stale',dependsOn:[],attempt:2}]}});
 const value=await tools.execute('inspect_workflow',{workflowId:'wf-one'},{scope:{kind:'workspace'}});
 const diagnostic=value.data.currentNodeDiagnostic;
 assert.deepEqual(diagnostic.prerequisites.satisfied.map(n=>n.id),['passed']);
 assert.deepEqual(diagnostic.prerequisites.unsatisfied.map(n=>n.id),['stale']);
 assert.deepEqual(diagnostic.prerequisites.unknown,['absent']);assert.equal(diagnostic.allPrerequisitesVerified,false);
});

test('credential-like diagnostic strings are redacted and error envelopes cannot become successful inspections',async()=>{
 const {tools}=fixture({'/api/workflows/wf-one':{id:'wf-one',status:'paused',nodes:[{id:'s1',detail:'Authorization: Bearer FAKE_AUDIT_SENTINEL_1234567890'}],events:[{message:'token=DO_NOT_EXPOSE_THIS'},{message:'{"access_token":"FAKE_AUDIT_SENTINEL_1234567890"}'},{message:'-----BEGIN PRIVATE KEY-----\nDO_NOT_EXPOSE_THIS\n-----END PRIVATE KEY-----'}]}});
 const value=await tools.execute('inspect_workflow',{workflowId:'wf-one'},{scope:{kind:'workspace'}});
 assert.doesNotMatch(JSON.stringify(value),/FAKE_AUDIT_SENTINEL|DO_NOT_EXPOSE_THIS/);
 for(const bad of [{},{error:{code:'failed'}},{id:'wf-foreign',status:'paused',nodes:[]}]){
  const next=fixture({'/api/workflows/wf-one':bad});
  await assert.rejects(next.tools.execute('inspect_workflow',{workflowId:'wf-one'},{scope:{kind:'workspace'}}),/response_invalid/);
 }
});
