import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import * as React from 'react';
import {generatedDiagramSource,readableDiagramSource} from './fixtures/generated-diagram-source.ts';
const bundle=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ConfigurationDiagramEditor.tsx',import.meta.url))],bundle:true,platform:'node',format:'cjs',write:false,external:['react','react/jsx-runtime'],loader:{'.css':'empty'}}).outputFiles[0].text;
function harness(){const states=new Map<string,any[]>();let state:any[]=[],cursor=0;const hooks={...React,useState(initial:any){const i=cursor++;if(!(i in state))state[i]=typeof initial==='function'?initial():initial;const own=state;return [own[i],(v:any)=>{own[i]=typeof v==='function'?v(own[i]):v;}];},useRef(initial:any){const i=cursor++;return state[i]??={current:initial};},useEffect(){},useMemo(fn:any){return fn();}};const mod={exports:{} as any};const require=createRequire(import.meta.url);new Function('require','module','exports',bundle)((id:string)=>id==='react'?hooks:require(id),mod,mod.exports);
function expand(node:any,path:string):any{if(Array.isArray(node))return node.map((child,i)=>expand(child,`${path}.${child?.key??i}`));if(!node?.props)return node;if(typeof node.type==='function'){const key=`${path}:${node.type.name}:${node.key??''}`;state=states.get(key)??[];states.set(key,state);cursor=0;return expand(node.type(node.props),key);}return {...node,props:{...node.props,children:expand(node.props.children,path+'.children')}};}
return (props:any)=>expand(React.createElement(mod.exports.default,props),'root');}
const elements=(node:any):any[]=>Array.isArray(node)?node.flatMap(elements):node?.props?[node,...elements(node.props.children)]:[];
const text=(node:any):string=>Array.isArray(node)?node.map(text).join(''):node?.props?text(node.props.children):typeof node==='string'||typeof node==='number'?String(node):'';
function control(tree:any,label:string){const el=elements(tree).find(n=>n.props['aria-label']===label);assert.ok(el,`missing ${label}`);return el.props;}
function button(tree:any,label:string){const el=elements(tree).find(n=>n.type==='button'&&text(n)===label);assert.ok(el,`missing button ${label}`);return el.props;}

test('topology form maps only an explicit selected chip resource, and removes incident edges with a node',()=>{
 const render=harness();let value:any={nodes:[],edges:[]};const props=()=>({kind:'topology',scope:'a',resources:[{id:'Q1',label:'A'},{id:'Q2',label:'B'}],value,onChange:(v:any)=>{value=v;}});
 let tree=render(props());control(tree,'映射资源').onChange({target:{value:'Q1'}});tree=render(props());control(tree,'设计编号').onChange({target:{value:'D7'}});tree=render(props());button(tree,'添加节点').onClick();
 assert.deepEqual(value.nodes,[{resourceId:'Q1',designId:'D7',x:0,y:0}]);assert.deepEqual(value.edges,[]);
 tree=render(props());control(tree,'映射资源').onChange({target:{value:'Q2'}});tree=render(props());button(tree,'添加节点').onClick();tree=render(props());button(tree,'添加连线').onClick();assert.equal(value.edges[0].confirmation,'pending');
 tree=render(props());button(tree,'删除节点 Q1').onClick();assert.deepEqual(value.nodes.map((n:any)=>n.resourceId),['Q2']);assert.deepEqual(value.edges,[]);
});
test('source-image click converts displayed position into original image pixels without making inferred connections',()=>{
 const render=harness();let value:any={nodes:[],edges:[],image:{name:'design.png',dataUrl:'data:image/png;base64,AA==',width:1000,height:500}};const props=()=>({kind:'topology',scope:'a',resources:[{id:'Q1',label:'A'}],value,onChange:(v:any)=>{value=v;}});
 let tree=render(props());control(tree,'映射资源').onChange({target:{value:'Q1'}});tree=render(props());control(tree,'拓扑原图定位').onClick({clientX:260,clientY:145,currentTarget:{getBoundingClientRect:()=>({left:10,top:20,width:500,height:250})}});tree=render(props());button(tree,'添加节点').onClick();assert.equal(value.nodes[0].x,500);assert.equal(value.nodes[0].y,250);assert.deepEqual(value.edges,[]);
});
test('path forms retain ports, units and source while components can be reordered and removed',()=>{
 const render=harness();let value:any=[];const props=()=>({kind:'signalPaths',scope:'station-a',value,onChange:(v:any)=>{value=v;}});let tree=render(props());button(tree,'添加线路').onClick();tree=render(props());control(tree,'线路名称').onChange({target:{value:'Read out'}});tree=render(props());control(tree,'线路来源').onChange({target:{value:'OneNote 八号机 p2'}});tree=render(props());button(tree,'添加部件').onClick();tree=render(props());control(tree,'部件 1 入口端口').onChange({target:{value:'IN'}});tree=render(props());control(tree,'部件 1 出口端口').onChange({target:{value:'OUT'}});tree=render(props());control(tree,'部件 1 温级 K').onChange({target:{value:'4'}});tree=render(props());control(tree,'部件 1 衰减 dB').onChange({target:{value:'20'}});tree=render(props());button(tree,'添加部件').onClick();tree=render(props());button(tree,'上移部件 2').onClick();
 assert.equal(value[0].components[1].fromPort,'IN');assert.equal(value[0].components[1].toPort,'OUT');assert.equal(value[0].components[1].temperatureK,4);assert.equal(value[0].components[1].attenuationDb,20);assert.equal(value[0].confirmation,'pending');assert.equal(value[0].source,'OneNote 八号机 p2');
 tree=render(props());button(tree,'删除部件 1').onClick();assert.equal(value[0].components.length,1);
});
test('switching chip resets source import text and pending point mapping',()=>{
 const render=harness();const props={kind:'topology',scope:'a',resources:[{id:'Q1'}],value:{nodes:[],edges:[]},onChange(){}};let tree=render(props);control(tree,'设计编号').onChange({target:{value:'OLD'}});tree=render({...props,scope:'b'});assert.equal(control(tree,'设计编号').value,'');
});
test('changing component data invalidates a previously confirmed entire path',()=>{
 const render=harness();let value:any=[{id:'p',label:'Read out',source:'p2',direction:'input',confirmation:'confirmed',components:[{id:'c',type:'filter',label:'LPF',confirmation:'confirmed',fromPort:'A'}]}];const props=()=>({kind:'signalPaths',scope:'a',value,onChange:(v:any)=>{value=v;}});const tree=render(props());control(tree,'部件 1 入口端口').onChange({target:{value:'B'}});assert.equal(value[0].components[0].confirmation,'pending');assert.equal(value[0].confirmation,'pending');
});
test('technical import rejects invalid optional fields without corrupting the form',()=>{
 const render=harness();let writes=0;const props={kind:'signalPaths',scope:'a',value:[],onChange(){writes++;}};let tree=render(props);control(tree,'信号路径 JSON').onChange({target:{value:JSON.stringify([{id:'p',label:'r',source:'s',direction:'input',confirmation:'pending',components:[{id:'c',label:'f',type:'filter',temperatureK:-1,fromPort:{bad:'object'}}]}])}});tree=render(props);button(tree,'应用 JSON 到表单').onClick();assert.equal(writes,0);assert.ok(elements(render(props)).some(n=>n.props.role==='alert'));
});

test('design sources accept month precision without supplying a fictitious day', () => {
 const render = harness();
 let value = {nodes: [], edges: [], date: '2026年10月'};
 const props = () => ({kind: 'topology', scope: 'source-month', resources: [], value, onChange(next: typeof value) { value = next; }});
 const field = control(render(props()), '版图资料日期');
 assert.equal(field.type, 'text');
 assert.equal(field.value, '2026年10月');
 field.onChange({target: {value: '2026-11-20'}});
 assert.equal(control(render(props()), '版图资料日期').value, '2026-11-20');
});

test('an archived layout with just a source opens without inventing resource mappings', () => {
 const render = harness();
 const original = {source: '图1 芯片设计版图'};
 let next: any;
 const tree = render({kind: 'topology', scope: 'archive', value: original, resources: [{id: 'Q11'}],
   onChange(value: any) { next = value; }});
 const input = control(tree, '版图资料日期');
 input.onChange({target: {value: '2026年10月'}});
 assert.deepEqual(next, {...original, date: '2026年10月', nodes: [], edges: []});
 assert.deepEqual(original, {source: '图1 芯片设计版图'});
});

test('generated DOCX source is a read-only summary and editing other fields preserves exact evidence',()=>{
 const render=harness();
 const image={name:'image2.png',dataUrl:'data:image/png;base64,AA==',width:1000,height:500,source:generatedDiagramSource};
 let value:any={nodes:[],edges:[],source:generatedDiagramSource,image};
 const props=()=>({kind:'topology',scope:'imported',resources:[{id:'Q11'}],value,onChange:(v:any)=>{value=v;}});
 let tree=render(props());
 const source=control(tree,'版图来源 / 版本');
 assert.equal(source.value,readableDiagramSource);
 assert.equal(source.readOnly,true);
 assert.equal(source.onChange,undefined,'a summary keystroke must never replace stored evidence');
 const evidence=elements(tree).find(n=>n.type==='details'&&text(n).startsWith('完整来源证据'));
 assert.ok(evidence);assert.equal(evidence.props.open,undefined);assert.ok(text(evidence).includes(generatedDiagramSource));
 control(tree,'版图资料日期').onChange({target:{value:'2026年10月'}});
 tree=render(props());control(tree,'映射资源').onChange({target:{value:'Q11'}});tree=render(props());button(tree,'添加节点').onClick();
 assert.equal(value.source,generatedDiagramSource);assert.equal(value.image.source,generatedDiagramSource);assert.equal(value.image,image);
 tree=render(props());assert.equal(control(tree,'版图来源 / 版本').value,readableDiagramSource);
});

test('custom source remains verbatim and editable even when it mentions a hash or XML',()=>{
 const render=harness();const original='现场笔记；word/document.xml body[73]；SHA-256 待核；自定义修订';
 let value:any={nodes:[],edges:[],source:original};
 const props=()=>({kind:'topology',scope:'manual',resources:[],value,onChange:(v:any)=>{value=v;}});
 const source=control(render(props()),'版图来源 / 版本');assert.equal(source.value,original);assert.notEqual(source.readOnly,true);
 source.onChange({target:{value:'人工核对修订 B'}});assert.equal(value.source,'人工核对修订 B');
});

test('source-image evidence remains accessible when the display source is manually overridden',()=>{
 const render=harness();const image={name:'image2.png',dataUrl:'data:image/png;base64,AA==',width:1000,height:500,source:generatedDiagramSource};
 let value:any={nodes:[],edges:[],source:'现场核对 2026-11-26',image};
 const props=()=>({kind:'topology',scope:'manual-over-import',resources:[],value,onChange:(v:any)=>{value=v;}});
 const tree=render(props());
 assert.ok(elements(tree).some(n=>n.type==='details'&&text(n).includes(generatedDiagramSource)));
 control(tree,'版图来源 / 版本').onChange({target:{value:'现场核对修订'}});
 assert.equal(value.image.source,generatedDiagramSource);
});

test('inherited imported evidence stays intact when editing edges and signal components',()=>{
 const render=harness();let topology:any={source:generatedDiagramSource,nodes:[{resourceId:'Q11',x:0,y:0},{resourceId:'Q18',x:1,y:1}],edges:[]};
 const tp=()=>({kind:'topology',scope:'edge-evidence',resources:[{id:'Q11'},{id:'Q18'}],value:topology,onChange:(v:any)=>{topology=v;}});
 button(render(tp()),'添加连线').onClick();const edgeSource=control(render(tp()),'连线 1 来源');
 assert.equal(edgeSource.value,readableDiagramSource);assert.equal(edgeSource.readOnly,true);
 assert.equal(topology.edges[0].source,generatedDiagramSource);assert.equal(topology.edges[0].confirmation,'pending');
 const pathRender=harness();let paths:any=[{id:'p',label:'test',direction:'output',confirmation:'pending',source:generatedDiagramSource,components:[{id:'c',label:'test',type:'other',source:generatedDiagramSource}]}];
 const pp=()=>({kind:'signalPaths',scope:'path-evidence',value:paths,onChange:(v:any)=>{paths=v;}});
 const tree=pathRender(pp());assert.equal(control(tree,'线路来源').value,readableDiagramSource);assert.equal(control(tree,'部件 1 来源').value,readableDiagramSource);
 control(tree,'部件 1 名称').onChange({target:{value:'人工补充部件名称'}});
 assert.equal(paths[0].source,generatedDiagramSource);assert.equal(paths[0].components[0].source,generatedDiagramSource);
});
