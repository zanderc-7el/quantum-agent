import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';
import {createRemoteGateway} from '../src/lib/remoteGateway.ts';
const requirement={resourceId:'Q11',nodeId:'flux',lineId:'LF-confirmed',routeSha256:'a'.repeat(64),startSettingV:.1,endSettingV:-.1,unit:'V'};
const intent={workflowId:'wf',nodeId:'flux',operationId:'click-unique',expectedRevision:8,requirement};
function elements(t:any):any[]{return t?.props?[t,...[t.props.children].flat(Infinity).flatMap(elements)]:[];}
function text(t:any):string{return typeof t==='string'||typeof t==='number'?String(t):t?.props?[t.props.children].flat(Infinity).map(text).join(''):'';}

test('actual manual confirmation dialog starts unchecked, names line/settings and freezes one click',()=>{
 const code=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/BiasStartConfirmation.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react/jsx-runtime']}).outputFiles[0].text;
 let checked=false;const hooks={...React,useState:()=>[checked,(v:boolean)=>checked=v]};const mod={exports:{} as any};
 new Function('require','module','exports',code)((id:string)=>id==='react'?hooks:createRequire(import.meta.url)(id),mod,mod.exports);
 const sent:any[]=[];const props={intent,currentWorkflowId:'wf',currentRevision:8,busy:false,onCancel:()=>{},onConfirm:(v:any)=>sent.push(v)};
 const render=()=>mod.exports.default(props);let tree=render();assert.match(text(tree),/LF-confirmed/);assert.match(text(tree),/0\.1 V/);assert.match(text(tree),/-0\.1 V/);assert.match(text(tree),/未独立测压/);
 let submit=elements(tree).find(e=>e.type==='button'&&text(e)==='确认并运行本步');assert.equal(submit.props.disabled,true);submit.props.onClick();assert.equal(sent.length,0);
 elements(tree).find(e=>e.type==='input').props.onChange({target:{checked:true}});tree=render();submit=elements(tree).find(e=>e.type==='button'&&text(e)==='确认并运行本步');assert.equal(submit.props.disabled,false);submit.props.onClick();
 assert.equal(sent[0].operationId,'click-unique');assert.equal(sent[0].expectedRevision,8);assert.deepEqual(sent[0].biasStartConfirmation,{confirmed:true,routeSha256:requirement.routeSha256,startSettingV:.1,endSettingV:-.1});
 props.currentRevision=9;tree=render();submit=elements(tree).find(e=>e.type==='button'&&text(e)==='确认并运行本步');assert.equal(submit.props.disabled,true);submit.props.onClick();assert.equal(sent.length,1);
});

test('confirmation revision and operation remain fixed through session refresh, stale snapshot sends nothing',async()=>{
 let revision=8;const writes:string[]=[];let calls=0;
 const wf=()=>({id:'wf',revision,nodes:[{id:'flux',latestAttemptId:'old'}]});
 const gateway=createRemoteGateway({fetch:(async(url:any,init:any)=>{
  if(String(url).endsWith('/session'))return Response.json({token:'token'});
  if(String(url).endsWith('/actions')){writes.push(init.body);return ++calls===1?Response.json({error:{code:'INVALID_SESSION',message:'expired'}},{status:401}):Response.json(wf());}
  return Response.json(wf());
 }) as typeof fetch});
 const options={operationId:'click-unique',expectedRevision:8,biasStartConfirmation:{confirmed:true as const,routeSha256:requirement.routeSha256,startSettingV:.1,endSettingV:-.1}};
 await gateway.command('wf','run_step','flux',options);assert.equal(writes.length,2);assert.equal(writes[0],writes[1]);const body=JSON.parse(writes[0]);assert.equal(body.operationId,'click-unique');assert.equal(body.expectedRevision,8);assert.deepEqual(body.biasStartConfirmation,options.biasStartConfirmation);
 revision=9;await gateway.get('wf');await assert.rejects(gateway.command('wf','run_step','flux',options),/任务已更新/);assert.equal(writes.length,2);
});

test('real RemoteApp manual entry opens confirmation before dispatch and passes only checked click',async()=>{
 const code=buildSync({entryPoints:[fileURLToPath(new URL('../src/RemoteApp.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react/jsx-runtime','./lib/useWorkflowStore','./lib/remoteGateway'],loader:{'.css':'empty'}}).outputFiles[0].text;
 const slots:any[]=[];let cursor=0;const hooks={...React,useState(initial:any){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],(v:any)=>slots[i]=typeof v==='function'?v(slots[i]):v];},useMemo(fn:any){const i=cursor++;return slots[i]??=fn();},useRef(v:any){const i=cursor++;return slots[i]??={current:v};},useEffect(){}};
 const wf:any={id:'wf',revision:8,name:'fixture manual',status:'draft',mode:'real',policy:'strict',nodes:[{id:'flux',name:'flux',label:'偏置扫描',status:'pending',attempt:0,parameters:{},resourceId:'Q11',procedureId:'ro_flux_scan'}],events:[],biasStartRequirements:{flux:requirement},machine:'fixture',currentNodeId:'flux'};
 const writes:any[]=[];const gateway={command:async(...args:any[])=>writes.push(args)};
 const original=(globalThis as any).location;(globalThis as any).location={hash:'#manual',origin:'http://fixture'};
 try {
  const mod={exports:{} as any};const require=createRequire(import.meta.url);
  new Function('require','module','exports',code)((id:string)=>id==='react'?hooks:id==='./lib/useWorkflowStore'?{useWorkflowStore:()=>({state:{workflows:[wf]}})}:id==='./lib/remoteGateway'?{createRemoteGateway:()=>gateway,reportUrl:()=>'',ApiError:Error}:require(id),mod,mod.exports);
  const render=()=>{cursor=0;return mod.exports.default();};let tree=render();let workspace=elements(tree).find(e=>e.type?.name==='ManualExperimentWorkspace');assert.ok(workspace);
  workspace.props.onSelectWorkflow('wf');tree=render();workspace=elements(tree).find(e=>e.type?.name==='ManualExperimentWorkspace');await workspace.props.onAction('run_step','flux');assert.equal(writes.length,0);
  tree=render();const modal=elements(tree).find(e=>e.type?.name==='BiasStartConfirmation');assert.ok(modal);assert.equal(modal.props.intent.requirement.lineId,'LF-confirmed');
  await modal.props.onConfirm({operationId:modal.props.intent.operationId,expectedRevision:8,biasStartConfirmation:{confirmed:true,routeSha256:requirement.routeSha256,startSettingV:.1,endSettingV:-.1}});
  assert.equal(writes.length,1);assert.equal(writes[0][0],'wf');assert.equal(writes[0][1],'run_step');assert.equal(writes[0][2],'flux');assert.equal(writes[0][3].expectedRevision,8);
 } finally {(globalThis as any).location=original;}
});
