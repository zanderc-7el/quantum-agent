import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';

// Exercise the actual component and click/keyboard handlers without an app,
// database, browser, or instrument connection.
const compiled=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ChipTopology.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react/jsx-runtime'],loader:{'.css':'empty'}}).outputFiles[0].text;
const realRequire=createRequire(import.meta.url);
function harness(){
 const slots:any[]=[];let cursor=0;
 const hooks={...React,useState(initial:any){const index=cursor++;if(!(index in slots))slots[index]=initial;return [slots[index],(value:any)=>{slots[index]=typeof value==='function'?value(slots[index]):value;}];}};
 const module={exports:{} as {default:(props:any)=>any}};
 new Function('require','module','exports',compiled)((id:string)=>id==='react'?hooks:realRequire(id),module,module.exports);
 return (props:any)=>{cursor=0;return module.exports.default(props);};
}
function elements(tree:any):any[]{return tree&&typeof tree==='object'&&tree.props?[tree,...[tree.props.children].flat(Infinity).flatMap(elements)]:[];}
function text(tree:any):string{return typeof tree==='string'||typeof tree==='number'?String(tree):tree?.props?[tree.props.children].flat(Infinity).map(text).join(''):'';}
const node=(tree:any,id:string)=>elements(tree).find(e=>e.props['aria-label']===`选择比特 ${id}`);
const detail=(tree:any)=>elements(tree).find(e=>e.props.className==='cd-details');
const title=(tree:any,id:string)=>text(elements(node(tree,id)).find(e=>e.type==='title'));
const topology={nodes:[{resourceId:'Q1',designId:'D1',x:1,y:1},{resourceId:'Q2',designId:'D2',x:2,y:1}],edges:[]};
const qubit=(id:string,status?:string,physicalId?:string)=>({id,label:id,identityStatus:status,physicalId,parameters:{}});

test('imported unresolved design stays a draft identity in hover and selected details',()=>{
 const render=harness(),props={scope:'design-import',topology,qubits:[qubit('Q1','unresolved','unverified-P1')]};
 let tree=render(props);
 assert.match(title(tree,'Q1'),/设计 D1/);assert.match(title(tree,'Q1'),/草稿编号 Q1.*实测身份待核实/);assert.doesNotMatch(title(tree,'Q1'),/实测资源/);
 node(tree,'Q1').props.onClick();tree=render(props);
 assert.match(text(detail(tree)),/草稿编号 Q1.*实测身份待核实/);
 assert.match(text(detail(tree)),/物理编号（待核实）：unverified-P1/);
 assert.match(text(detail(tree)),/邻接资料待核实/);assert.doesNotMatch(text(detail(tree)),/无耦合/);
});

test('explicit confirmed physical identities retain measured resource and coupled-neighbor display',()=>{
 const render=harness(),props={scope:'confirmed',topology:{...topology,edges:[{id:'c12',from:'Q1',to:'Q2',kind:'coupling',confirmation:'confirmed',source:'verified layout'}]},qubits:[qubit('Q1','confirmed','P1'),qubit('Q2','confirmed','P2')]};
 let tree=render(props);assert.equal(title(tree,'Q1'),'设计 D1 → 实测资源 Q1');
 node(tree,'Q1').props.onClick();tree=render(props);
 assert.match(text(detail(tree)),/目标 Q1/);assert.match(text(detail(tree)),/物理编号：P1/);
 assert.match(text(detail(tree)),/已确认直接耦合比特：Q2/);assert.doesNotMatch(text(detail(tree)),/实测身份待核实/);
});

test('missing or incomplete identities do not become confirmed from a design ID or physical ID alone',()=>{
 for(const q of [undefined,qubit('Q1',undefined,'P1'),qubit('Q1','observed_resource','P1'),qubit('Q1','confirmed','   ')]){
  const render=harness(),props={scope:'draft',topology,qubits:q?[q]:[]};
  let tree=render(props);assert.match(title(tree,'Q1'),/草稿编号 Q1.*实测身份待核实/);assert.doesNotMatch(title(tree,'Q1'),/实测资源/);
  node(tree,'Q1').props.onClick();tree=render(props);assert.match(text(detail(tree)),/实测身份待核实/);
 }
});

test('design coupling remains design adjacency until both endpoint identities are confirmed',()=>{
 const render=harness(),props={scope:'layout',topology:{...topology,edges:[{id:'c12',from:'Q1',to:'Q2',kind:'coupling',confirmation:'confirmed',source:'delivery design figure 1'}]},qubits:[qubit('Q1','confirmed','P1'),qubit('Q2','unresolved')]};
 let tree=render(props);node(tree,'Q1').props.onClick();tree=render(props);
 assert.match(text(detail(tree)),/设计图已记录直接耦合编号：D2/);
 assert.doesNotMatch(text(detail(tree)),/已确认直接耦合比特：Q2/);
});

test('pending or absent coupling evidence remains unknown instead of claiming no coupling',()=>{
 for(const edges of [[],[{id:'c12',from:'Q1',to:'Q2',kind:'coupling',confirmation:'pending',source:'delivery design'}],[{id:'c12',from:'Q1',to:'Q2',kind:'coupling',confirmation:'confirmed',source:''}]]){
  const render=harness(),props={scope:'unknown-coupling',topology:{...topology,edges},qubits:[qubit('Q1','confirmed','P1'),qubit('Q2','confirmed','P2')]};
  let tree=render(props);node(tree,'Q1').props.onClick();tree=render(props);
  assert.match(text(detail(tree)),/邻接资料待核实/);assert.doesNotMatch(text(detail(tree)),/无耦合|已确认直接耦合比特：Q2/);
 }
});

test('identity labels do not change selection callbacks keyboard behavior disabled state or scope reset',()=>{
 const render=harness(),calls:string[]=[],props={scope:'draft',topology,qubits:[qubit('Q1','unresolved')],onSelect:(id:string)=>calls.push(id)};
 let tree=render(props);assert.equal(node(tree,'Q1').props['aria-disabled'],false);assert.equal(node(tree,'Q1').props.tabIndex,0);
 let prevented=false;node(tree,'Q1').props.onKeyDown({key:'Enter',preventDefault(){prevented=true;}});
 tree=render(props);assert.equal(prevented,true);assert.deepEqual(calls,['Q1']);assert.equal(node(tree,'Q1').props['aria-pressed'],true);
 tree=render({...props,disabled:true});assert.equal(node(tree,'Q2').props['aria-disabled'],true);assert.equal(node(tree,'Q2').props.tabIndex,-1);
 node(tree,'Q2').props.onClick();node(tree,'Q2').props.onKeyDown({key:' ',preventDefault(){}});assert.deepEqual(calls,['Q1']);
 tree=render({...props,scope:'another-chip'});assert.equal(detail(tree),undefined);
});

test('controlled topology follows external selection and clear without changing until the parent accepts a click',()=>{
 const render=harness(),calls:string[]=[],props={scope:'v1',topology:{...topology,edges:[{id:'c12',from:'Q1',to:'Q2',kind:'coupling',confirmation:'confirmed',source:'layout'}]},qubits:[qubit('Q1','confirmed','P1'),qubit('Q2','confirmed','P2')],selectedResourceId:'Q2',onSelect:(id:string)=>calls.push(id)};
 let tree=render(props);assert.equal(node(tree,'Q2').props['aria-pressed'],true);assert.match(node(tree,'Q1').props.className,/neighbor/);
 node(tree,'Q1').props.onClick();tree=render(props);assert.deepEqual(calls,['Q1']);assert.equal(node(tree,'Q2').props['aria-pressed'],true);
 tree=render({...props,selectedResourceId:'Q1'});assert.equal(node(tree,'Q1').props['aria-pressed'],true);assert.match(node(tree,'Q2').props.className,/neighbor/);
 tree=render({...props,selectedResourceId:''});assert.equal(detail(tree),undefined);assert.equal(node(tree,'Q1').props['aria-pressed'],false);assert.equal(node(tree,'Q2').props['aria-pressed'],false);
});

test('controlled selection never highlights an absent or unpositioned node from another version',()=>{
 const render=harness(),props={scope:'v1',topology:{...topology,nodes:[...topology.nodes,{resourceId:'Q3',x:NaN,y:2}]},qubits:[qubit('Q1','confirmed','P1')],selectedResourceId:'Q3'};
 let tree=render(props);assert.equal(detail(tree),undefined);assert.equal(node(tree,'Q3'),undefined);
 tree=render({...props,selectedResourceId:'from-old-version'});assert.equal(detail(tree),undefined);
 tree=render({...props,scope:'v2',topology:{nodes:[{resourceId:'Q8',x:1,y:1}],edges:[]},selectedResourceId:''});assert.equal(detail(tree),undefined);assert.equal(node(tree,'Q8').props['aria-pressed'],false);
});
