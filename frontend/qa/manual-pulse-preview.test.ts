import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';
import {createManualParameterDraft} from '../src/lib/manualParameters.ts';
import {manualPreviewChanges} from '../src/lib/manualPulsePreview.ts';
import {createRemoteGateway} from '../src/lib/remoteGateway.ts';

function harness(component:string){
 const code=buildSync({entryPoints:[fileURLToPath(new URL(`../src/components/${component}.tsx`,import.meta.url))],bundle:true,write:false,format:'cjs',platform:'node',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
 let cursor=0;const slots:any[]=[],effects:(()=>void)[]=[];
 const hooks={...React,useId:()=> 'preview-form',useState(initial:any){const n=cursor++;slots[n]??={value:typeof initial==='function'?initial():initial};return[slots[n].value,(v:any)=>{slots[n].value=typeof v==='function'?v(slots[n].value):v;}];},useRef(initial:any){return slots[cursor++]??={current:initial};},useEffect(fn:any,deps:any[]){const n=cursor++;if(!slots[n]||deps.some((d,i)=>d!==slots[n].deps[i])){slots[n]?.cleanup?.();slots[n]={deps};effects.push(()=>{slots[n].cleanup=fn();});}}};
 const module={exports:{} as any},require=createRequire(import.meta.url);new Function('require','module','exports',code)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
 return {render(props:any){cursor=0;const tree=module.exports.default(props);while(effects.length)effects.shift()!();return tree;},close(){for(const slot of slots)slot?.cleanup?.();}};
}
const all=(tree:any):any[]=>Array.isArray(tree)?tree.flatMap(all):tree?.props?[tree,...all(tree.props.children)]:[];
const text=(tree:any):string=>Array.isArray(tree)?tree.map(text).join(''):tree?.props?text(tree.props.children):typeof tree==='number'||typeof tree==='string'?String(tree):'';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const delay=()=>new Promise(resolve=>setTimeout(resolve,210));
const response=(width=20e-9)=>({schema:'manual-pulse-preview/1',workflowId:'wf',nodeId:'node',workflowRevision:4,resourceId:'Q11',status:'ready',readoutStartS:80e-6,controlReadoutGapS:300e-9,xyTriggerDelayS:100e-9,requestedChanges:{},normalizedChanges:{},segments:[{id:'drive',channel:'XY',label:'X',startS:80e-6-300e-9-width,durationS:width,envelopeDurationS:width,amplitude:.4,frequencyHz:4.8e9,phaseRad:0},{id:'readout',channel:'Read in',label:'读出',startS:80e-6,durationS:2e-6,amplitude:.2,frequencyHz:7e9,phaseRad:null}],axes:[{name:'drive_amplitude',binding:'cfg.Q1.gate.U_gates.pi_amp',values:[.1,.2],index:0}],limitations:[]});
const props=(gateway:any)=>({gateway,workflowId:'wf',nodeId:'node',resourceId:'Q11',procedureId:'sq_rabi_amplitude',revision:4,changes:{},fields:[],dirty:false,onFocusField(){}});

test('draft uses the exact save parser, preserves units and rejects invalid or stale values',()=>{
 const node:any={id:'node',procedureId:'sq_rabi_amplitude'};const detail:any={node,parameters:{drive_length_s:20e-9,readout_hz:7e9},parameterBounds:{drive_length_s:{min:1e-9,max:1e-6},readout_hz:{min:6e9,max:8e9}}};
 const draft=createManualParameterDraft(detail,node,4);draft.values.drive_length_s='70';draft.values.readout_hz='7.01';
 assert.deepEqual(manualPreviewChanges(draft,4),{drive_length_s:70e-9,readout_hz:7.01e9});
 assert.throws(()=>manualPreviewChanges(draft,5),/版本/);draft.values.drive_length_s='NaN';assert.throws(()=>manualPreviewChanges(draft,4),/有限/);
 assert.throws(()=>manualPreviewChanges(draft,4,'扫描步进必须大于零'),/扫描步进/);
});
test('preview ignores old asynchronous receipts, hides stale picture immediately and never submits an action',async()=>{
 const pending:((v:any)=>void)[]=[];const calls:any[]=[];const view=harness('ManualPulsePreview');
 const base=props({pulsePreview:(...args:any[])=>{calls.push(args);return new Promise(resolve=>pending.push(resolve));},command(){throw Error('no hardware');},parameters(){throw Error('preview must not save');}});
 view.render(base);await delay();const changed={...base,changes:{drive_length_s:70e-9},dirty:true};let tree=view.render(changed);assert.ok(!all(tree).some(n=>n.type==='svg'));await delay();
 pending[1](response(70e-9));await tick();tree=view.render(changed);assert.ok(text(tree).includes('70 ns'));
 pending[0](response(20e-9));await tick();tree=view.render(changed);assert.ok(text(tree).includes('70 ns'));assert.ok(!text(tree).includes('20 ns'));
 assert.equal(calls.length,2);assert.deepEqual(calls[1].slice(0,4),['wf','node',4,{drive_length_s:70e-9}]);
 tree=view.render({...changed,problem:'扫描步进必须大于零'});assert.ok(!all(tree).some(n=>n.type==='svg'));assert.ok(text(tree).includes('扫描步进必须大于零'));view.close();
});
test('wrong-source receipt and unsupported methods are explicit and never acquire a model fallback',async()=>{
 const view=harness('ManualPulsePreview');const base=props({pulsePreview:async()=>({...response(),nodeId:'other'})});view.render(base);await delay();await tick();assert.ok(text(view.render(base)).includes('来源或版本'));
 const unsupported={...base,revision:5,gateway:{pulsePreview:async()=>({...response(),workflowRevision:5,status:'unavailable',segments:[],reason:'完整原生时序尚未覆盖'})}};view.render(unsupported);await delay();assert.ok(text(view.render(unsupported)).includes('未覆盖'));assert.ok(!all(view.render(unsupported)).some(n=>n.type==='svg'));view.close();
});
test('real parameter form drives preview and save from the same revision-bound draft',async()=>{
 const view=harness('ManualParameterForm');const node:any={id:'node',procedureId:'sq_rabi_amplitude',resourceId:'Q11'};
 const detail:any={node,parameters:{drive_length_s:20e-9,readout_hz:7e9},parameterBounds:{drive_length_s:{min:1e-9,max:1e-6},readout_hz:{min:6e9,max:8e9}}};const calls:any[]=[];
 const base:any={gateway:{parameters:async(...args:any[])=>{calls.push(args);return{id:'wf',revision:5};}},workflow:{id:'wf',target:'Q11',revision:4},node,detail,detailRevision:4,busy:false,canEdit:true,onBusy(){}};
 view.render(base);let tree=view.render(base);all(tree).find(n=>n.props['aria-label']==='参数 drive_length_s').props.onChange({target:{value:'70'}});tree=view.render(base);
 const preview=all(tree).find(n=>n.type?.name==='ManualPulsePreview');assert.deepEqual(preview.props.changes,{drive_length_s:70e-9});assert.equal(preview.props.revision,4);
 all(tree).find(n=>n.type==='button'&&text(n)==='保存配置').props.onClick();await tick();assert.deepEqual(calls,[['wf','node',4,{drive_length_s:70e-9}]]);
 tree=view.render({...base,workflow:{...base.workflow,revision:5}});assert.ok(all(tree).find(n=>n.type?.name==='ManualPulsePreview').props.problem);view.close();
});
test('gateway preview carries only a read-only revision draft and does not create a command',async()=>{
 const calls:any[]=[];const api=createRemoteGateway({fetch:async(url:any,init:any)=>{calls.push({url:String(url),body:init?.body&&JSON.parse(init.body)});return new Response(JSON.stringify(String(url).endsWith('/session')?{token:'fixture',actor:{}}:response()),{status:200,headers:{'Content-Type':'application/json'}});}});
 await api.pulsePreview('wf','node',4,{drive_length_s:20e-9},[1]);
 assert.equal(calls[1].url,'/api/workflows/wf/nodes/node/pulse-preview');assert.deepEqual(calls[1].body,{expectedRevision:4,parameters:{drive_length_s:20e-9},scanIndices:[1]});assert.ok(!('operationId' in calls[1].body));
});
