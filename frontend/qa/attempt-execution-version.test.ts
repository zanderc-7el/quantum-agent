import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';
import {createRemoteGateway} from '../src/lib/remoteGateway.ts';

function harness(){
 const code=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ManualParameterForm.tsx',import.meta.url))],bundle:true,write:false,format:'cjs',platform:'node',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
 let cursor=0;const slots:any[]=[],effects:(()=>void)[]=[];
 const hooks={...React,useId:()=> 'attempt-form',useState(initial:any){const n=cursor++;slots[n]??={value:typeof initial==='function'?initial():initial};return[slots[n].value,(v:any)=>{slots[n].value=typeof v==='function'?v(slots[n].value):v;}];},useRef(initial:any){return slots[cursor++]??={current:initial};},useEffect(fn:any,deps:any[]){const n=cursor++;if(!slots[n]||deps.some((d,i)=>d!==slots[n].deps[i])){slots[n]?.cleanup?.();slots[n]={deps};effects.push(()=>{slots[n].cleanup=fn();});}}};
 const module={exports:{} as any},require=createRequire(import.meta.url);new Function('require','module','exports',code)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
 return {render(props:any){cursor=0;const tree=module.exports.default(props);while(effects.length)effects.shift()!();return tree;},close(){for(const slot of slots)slot?.cleanup?.();}};
}
const all=(tree:any):any[]=>Array.isArray(tree)?tree.flatMap(all):tree?.props?[tree,...all(tree.props.children)]:[];
const text=(tree:any):string=>Array.isArray(tree)?tree.map(text).join(''):tree?.props?text(tree.props.children):typeof tree==='number'||typeof tree==='string'?String(tree):'';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const button=(tree:any,label:string)=>all(tree).find(n=>n.type==='button'&&text(n)===label);
function props(gateway:any){
 const node:any={id:'node',procedureId:'sq_rabi_amplitude',resourceId:'Q11'};
 const detail:any={node,parameters:{drive_length_s:375e-9,shots:2000,acquisitions_per_point:8},parameterBounds:{drive_length_s:{min:1e-9,max:1e-6},acquisitions_per_point:{min:1,max:8,integer:true}},executionVersionOptions:[{id:'rabi16-v1',label:'16 blocks'}]};
 return {gateway,workflow:{id:'wf',target:'Q11',revision:4},node,detail,detailRevision:4,busy:false,canEdit:true,onBusy(){}} as any;
}
test('normal form selects only explicit next-attempt version, without starting or changing a budget',async()=>{
 const calls:any[]=[];const view=harness();const base=props({executionVersion:async(...args:any[])=>{calls.push(args);return{id:'wf',revision:5};},command(){throw Error('must not start');}});
 view.render(base);let tree=view.render(base);assert.equal(calls.length,0);
 await button(tree,'选择下一次 16 块采集').props.onClick();await tick();tree=view.render(base);
 assert.deepEqual(calls[0].slice(0,4),['wf','node',4,'rabi16-v1']);assert.match(calls[0][4],/首测与复测/);
 assert.match(text(tree),/尚未开始采集/);assert.equal(button(tree,'选择下一次 16 块采集').props.disabled,true);view.close();
});
test('dirty, stale and readonly forms cannot select; selected parameters freeze and cancel is explicit',async()=>{
 const calls:any[]=[];const view=harness();let base=props({executionVersion:async(...a:any[])=>{calls.push(a);return{id:'wf',revision:5};}});
 view.render(base);let tree=view.render(base);all(tree).find(n=>n.props['aria-label']==='参数 drive_length_s').props.onChange({target:{value:'400'}});
 tree=view.render(base);assert.equal(button(tree,'选择下一次 16 块采集').props.disabled,true);await button(tree,'选择下一次 16 块采集').props.onClick();assert.equal(calls.length,0);
 view.close();const locked=harness();base=props(base.gateway);base.detail.attemptExecutionVersion={state:'selected',nextAttemptNumber:4,label:'每点16块',scope:'仅该次首复',adapterId:'extension',adapterProfileHash:'a'.repeat(64),authorizationId:'authorization'};
 locked.render(base);tree=locked.render(base);assert.equal(button(tree,'选择下一次 16 块采集').props.disabled,true);
 assert.ok(all(tree).filter(n=>n.type==='fieldset').every(n=>n.props.disabled));assert.match(text(tree),/第 4 次尝试/);
 tree=locked.render({...base,canEdit:false});assert.equal(button(tree,'恢复原执行版本').props.disabled,true);
 tree=locked.render({...base,workflow:{...base.workflow,revision:5}});assert.equal(button(tree,'恢复原执行版本').props.disabled,true);
 tree=locked.render(base);await button(tree,'恢复原执行版本').props.onClick();await tick();assert.equal(calls[0][3],'default');locked.close();
});
test('switching workflow drops late selection receipt instead of applying it to the new form',async()=>{
 let resolve:any;const saved:any[]=[];const view=harness();const base=props({executionVersion:()=>new Promise(r=>resolve=r)});base.onSaved=(w:any)=>saved.push(w);
 view.render(base);const tree=view.render(base);button(tree,'选择下一次 16 块采集').props.onClick();
 const next={...base,workflow:{...base.workflow,id:'other'}};view.render(next);view.render(next);
 resolve({id:'wf',revision:5});await tick();assert.equal(saved.length,0);assert.doesNotMatch(text(view.render(next)),/已选择下一次/);view.close();
});
test('gateway version command binds revision and unique operation identity, keeps explicit reason',async()=>{
 const calls:any[]=[];const api=createRemoteGateway({fetch:async(url:any,init:any)=>{calls.push({url:String(url),body:init?.body&&JSON.parse(init.body)});return new Response(JSON.stringify(String(url).endsWith('/session')?{token:'fixture',actor:{}}:{id:'wf',revision:5}),{status:200,headers:{'Content-Type':'application/json'}});}});
 await api.executionVersion!('wf','Q11:amp',4,'rabi16-v1','Explicit TEST ONLY');
 assert.equal(calls[1].url,'/api/workflows/wf/nodes/Q11%3Aamp/execution-version');const {operationId,...body}=calls[1].body;
 assert.match(operationId,/^[0-9a-f-]{36}$/);assert.deepEqual(body,{expectedRevision:4,executionVersion:'rabi16-v1',reason:'Explicit TEST ONLY'});
});
