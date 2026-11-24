import test from 'node:test';
import assert from 'node:assert/strict';
import { attentionCandidate, calibrationParameterLabel, campaignAction, campaignChildAllows, eligibleCampaignResources, createWorkspaceGateway, namespace, parameterUnit, uploadBody } from '../src/lib/workspaceGateway.ts';
import type { Attention, Campaign } from '../src/lib/workspaceGateway.ts';

test('server conversation storage namespaces separate project, workspace, campaign and workflow', () => {
 const keys=[namespace('a',{kind:'workspace'}),namespace('b',{kind:'workspace'}),namespace('a',{kind:'workflow',id:'same'}),namespace('a',{kind:'campaign',id:'same'}),namespace('a:same',{kind:'workspace'}),namespace('a',{kind:'workflow',id:'x:y'})];
 assert.equal(new Set(keys).size,keys.length);
 assert.notEqual(namespace('a:b',{kind:'workflow',id:'c'}),namespace('a',{kind:'workflow',id:'b:c'}));
});
const campaign={id:'batch',revision:7,resourceIds:['Q1','Q2']} as Campaign;
test('batch actions omit resourceIds for all and explicitly scope individual resources', () => {
 const all=campaignAction(campaign,'pause');assert.equal(Object.hasOwn(all,'resourceIds'),false);assert.equal(all.expectedRevision,7);
 const one=campaignAction(campaign,'skip',['Q2']);assert.deepEqual(one.resourceIds,['Q2']);assert.notEqual(one.operationId,all.operationId);
 assert.throws(()=>campaignAction(campaign,'start',[]));assert.throws(()=>campaignAction(campaign,'start',['other']));
});
test('attention queue opens unseen revisions, keeps deferred items unresolved, and revisits new revision',()=>{
 const first={id:'a',revision:1,state:'open'} as Attention;const second={id:'b',revision:2,state:'open'} as Attention;
 assert.equal(attentionCandidate([first,second],{})?.id,'a');
 assert.equal(attentionCandidate([first,second],{a:1})?.id,'b');
 assert.equal(attentionCandidate([{...first,state:'deferred'}],{}),undefined);
 assert.equal(attentionCandidate([{...first,revision:2}],{a:1})?.id,'a');
 assert.equal(attentionCandidate([{...first,state:'resolved'}],{}),undefined);
});
test('upload submits local base64 bytes, filename and operation ID; rejects unsupported or empty payload',()=>{
 const body=uploadBody('芯片.PDF','data:application/pdf;base64,JVBERi0=');assert.equal(body.filename,'芯片.PDF');assert.equal(body.contentBase64,'JVBERi0=');assert.ok(body.operationId);assert.equal(Object.hasOwn(body,'path'),false);
 assert.throws(()=>uploadBody('x.exe','data:application/octet-stream;base64,WA=='));
 assert.throws(()=>uploadBody('x.docx','data:application/octet-stream;base64,'));
 assert.throws(()=>uploadBody('x.png','https://x/image.png'));
});
test('conversation write carries explicit intent, scope server ID and token; network failure never replays execution',async()=>{
 const writes:RequestInit[]=[];let authCalls=0;
 const api=createWorkspaceGateway(async()=>{authCalls++;return {token:'local-token'};},(async(url,init)=>{assert.equal(url,'/api/conversations/server-id/messages');writes.push(init!);throw new TypeError('connection lost');}) as typeof fetch);
 await assert.rejects(api.write('/conversations/server-id/messages',{operationId:'same-op',expectedRevision:5,message:'启动 Q1',intent:'execute',attachmentIds:['doc-1']}),/connection lost/);
 assert.equal(authCalls,1);assert.equal(writes.length,1);assert.equal((writes[0].headers as Record<string,string>)['X-Qcal-Token'],'local-token');assert.equal(JSON.parse(String(writes[0].body)).intent,'execute');
});
test('read-only receipt reconciliation never resubmits a message',async()=>{
 const requests:{url:string;init:RequestInit}[]=[];
 const api=createWorkspaceGateway(async()=>({token:'token'}),(async(url,init)=>{requests.push({url:String(url),init:init!});return new Response(JSON.stringify({id:'c',revision:2,pending:{operationId:'pending'},messages:[]}));}) as typeof fetch);
 const value=await api.get<{pending:unknown}>('/conversations/c');assert.ok(value.pending);assert.equal(requests.length,1);assert.equal(requests[0].init.method,undefined);assert.equal(requests[0].init.body,undefined);
});

test('parameter units follow explicit name suffix without inventing amplitude units',()=>{ assert.equal(parameterUnit('drive_hz'),'Hz'); assert.equal(parameterUnit('xy_filter_mhz'),'MHz'); assert.equal(parameterUnit('drive_length_s'),'s'); assert.equal(parameterUnit('xy_attenuation_db'),'dB'); assert.equal(parameterUnit('drive_amp'),''); });

test('batch buttons reject impossible child actions and retain eligible siblings',()=>{
 const child=(status:string,engineState?:string)=>({resourceId:'Q1',workflowId:'wf1',status,engineState,verifiedCount:0,totalCount:15});
 for(const action of ['start','pause','resume'])assert.equal(campaignChildAllows(child('attention','configuration_required'),action),false);
 for(const status of ['completed','aborted','skipped'])for(const action of ['start','pause','resume','abort','skip'])assert.equal(campaignChildAllows(child(status),action),false);
 assert.equal(campaignChildAllows(child('draft'),'start'),true);assert.equal(campaignChildAllows(child('draft'),'pause'),false);assert.equal(campaignChildAllows(child('paused'),'resume'),true);assert.equal(campaignChildAllows(child('running'),'pause'),true);
 for(const state of ['budget_exhausted','unsupported_method','adapter_changed','diagnostic_closed'])assert.equal(campaignChildAllows(child('attention',state),'resume'),false);
 for(const action of ['start','pause','resume','abort','skip'])assert.equal(campaignChildAllows(child('running','stopping_at_checkpoint'),action),false);
 const batch={...campaign,children:[child('attention','configuration_required'),{...child('draft'),resourceId:'Q2'}]};assert.deepEqual(eligibleCampaignResources(batch,'start'),['Q2']);assert.deepEqual(eligibleCampaignResources(batch,'pause'),[]);assert.deepEqual(eligibleCampaignResources(batch,'start',['Q1']),[]);
});
test('known calibration labels are readable without changing parameter keys or units',()=>{assert.equal(calibrationParameterLabel('drive_hz'),'驱动频率');assert.equal(calibrationParameterLabel('readout_amp'),'读出幅度');assert.equal(calibrationParameterLabel('drive_length_s'),'π脉冲时长');assert.equal(calibrationParameterLabel('unknown_key'),'unknown_key');assert.equal(parameterUnit('drive_hz'),'Hz');});

test('unknown acquisition receipt still permits pause and abort but never resume or skip',()=>{
 const child={resourceId:'Q1',workflowId:'wf1',status:'attention',engineState:'needs_reconciliation',verifiedCount:0,totalCount:15};
 for(const action of ['pause','abort'])assert.equal(campaignChildAllows(child,action),true);
 for(const action of ['start','resume','skip'])assert.equal(campaignChildAllows(child,action),false);
});
