import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import * as React from 'react';
const bundle=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ScienceWorkbench.tsx',import.meta.url))],bundle:true,platform:'node',format:'cjs',write:false,external:['react','react/jsx-runtime'],loader:{'.css':'empty'}}).outputFiles[0].text;
const realRequire=createRequire(import.meta.url);
function harness(){const states=new Map<string,any[]>();let state:any[]=[],cursor=0;const hooks={...React,useState(initial:any){const i=cursor++;if(!(i in state))state[i]=typeof initial==='function'?initial():initial;const own=state;return [own[i],(v:any)=>{own[i]=typeof v==='function'?v(own[i]):v;}];},useRef(initial:any){const i=cursor++;return state[i]??={current:initial};},useEffect(){},useMemo(fn:any){return fn();},useCallback(fn:any){return fn;}};const mod={exports:{} as any};new Function('require','module','exports',bundle)((id:string)=>id==='react'?hooks:realRequire(id),mod,mod.exports);
 function expand(node:any,path:string):any{if(Array.isArray(node))return node.map((child,i)=>expand(child,`${path}.${child?.key??i}`));if(!node?.props)return node;if(typeof node.type==='function'){const key=`${path}:${node.type.name}:${node.key??''}`;state=states.get(key)??[];states.set(key,state);cursor=0;return expand(node.type(node.props),key);}return {...node,props:{...node.props,children:expand(node.props.children,path+'.children')}};}
 return (props:any)=>expand(React.createElement(mod.exports.default,props),'root');}
const elements=(node:any):any[]=>Array.isArray(node)?node.flatMap(elements):node?.props?[node,...elements(node.props.children)]:[];
const text=(node:any):string=>Array.isArray(node)?node.map(text).join(''):node?.props?text(node.props.children):typeof node==='string'||typeof node==='number'?String(node):'';
const button=(tree:any,label:string)=>elements(tree).find(n=>n.type==='button'&&text(n)===label)?.props;
const field=(tree:any,label:string)=>elements(elements(tree).find(n=>n.type==='label'&&text(n).includes(label))).find(n=>n.type==='input')?.props;
const sample={source:'model',dataKind:'simulation',dispatched:false,modelVersion:'test-v1',axes:{x:{name:'t',unit:'ns',values:[0,20]}},series:[{id:'i',label:'I',quantity:'iq_envelope',unit:'normalized',values:[0,1]}]};
const base={gateway:{get:async()=>({records:[]}),write:async()=>sample}};
test('pulse selection changes capture edge without moving excitation or sending hardware commands',()=>{const render=harness();let tree=render(base);button(tree,'Read out42–2042 ns');field(tree,'Read out 相对 Read in 延时').onChange({target:{value:'70'}});tree=render(base);const titles=elements(tree).filter(n=>n.type==='title').map(text);assert(titles.includes('Read in：42–2042 ns'));assert(titles.includes('Read out：112–2112 ns'));assert(titles.includes('XY1：0–20 ns'));});
test('preview request stays on science route and editing invalidates the displayed calculation',async()=>{const sent:any[]=[];const props={gateway:{get:async()=>({records:[]}),write:async(path:string,body:any)=>{sent.push([path,body]);return sample;}}};const render=harness();let tree=render(props);field(tree,'初相').onChange({target:{value:'0'}});tree=render(props);await button(tree,'更新预览').onClick();await Promise.resolve();tree=render(props);assert.equal(sent.length,1);assert.equal(sent[0][0],'/science/preview');assert.equal(sent[0][1].kind,'pulse');assert(text(tree).includes('test-v1'));field(tree,'幅度').onChange({target:{value:'.7'}});tree=render(props);assert(!text(tree).includes('test-v1'),'旧计算必须在编辑后清除');});
test('empty scientific workspace never presents manufactured measured heatmap or temperature',()=>{const render=harness();let tree=render(base);button(tree,'磁通与工作点').onClick();tree=render(base);assert(text(tree).includes('先选择已有扫描记录'));assert(!elements(tree).some(n=>n.props?.['aria-label']==='S21磁通二维谱'));button(tree,'制冷机温度').onClick();tree=render(base);assert(text(tree).includes('待接线'));});
test('a response that arrives after a parameter edit cannot revive obsolete preview',async()=>{
 const render=harness();let finish:(value:any)=>void=()=>{};const props={gateway:{get:async()=>({records:[]}),write:()=>new Promise(resolve=>{finish=resolve;})}};
 let tree=render(props);button(tree,'更新预览').onClick();tree=render(props);field(tree,'幅度').onChange({target:{value:'.8'}});finish(sample);await Promise.resolve();await Promise.resolve();tree=render(props);assert(!text(tree).includes('test-v1'));assert.equal(button(tree,'更新预览').disabled,false);
});
test('native compilation binds selected Q18 node and never calls workflow start',async()=>{
 const sent:any[]=[];const props:any={workflow:{id:'w',currentNodeId:'Q18:rabi',resourceIds:['Q11','Q18'],nodes:[{id:'Q11:rabi',resourceId:'Q11',label:'Rabi'},{id:'Q18:rabi',resourceId:'Q18',label:'Rabi'}]},gateway:{get:async()=>({records:[]}),write:async(path:string,body:any)=>{sent.push([path,body]);return {...sample,source:'native_compiler',modelVersion:'native-test',input:{}};}}};
 const render=harness();let tree=render(props);await button(tree,'编译原生样点').onClick();await Promise.resolve();tree=render(props);
 assert.equal(sent.length,1);assert.equal(sent[0][0],'/native-preview');assert.equal(sent[0][1].nodeId,'Q18:rabi');assert.equal(sent[0][1].scope.resourceId,'Q18');
 assert(text(tree).includes('native-test'));field(tree,'XY 至读出间隔').onChange({target:{value:'400'}});tree=render(props);assert(!text(tree).includes('native-test'));
});

test('legacy native compiler cannot silently compile an empirical gate as normalized lambda',async()=>{
 const sent:any[]=[];
 const props:any={workflow:{id:'empirical',currentNodeId:'half',nodes:[{id:'half',resourceId:'Q11',label:'经验半门',parameters:{drag_mode:'empirical_beta',drag_beta_s:1e-9}}]},gateway:{get:async()=>({records:[]}),write:async(path:string,body:any)=>{sent.push([path,body]);return sample;}}};
 const render=harness();let tree=render(props);button(tree,'DRAG 与门验证').onClick();tree=render(props);
 assert.equal(button(tree,'编译原生样点').disabled,true);
 await button(tree,'编译原生样点').onClick();assert.deepEqual(sent,[]);
 assert.match(text(tree),/此编译入口.*尚不支持经验 β/);
 assert.equal(field(tree,'DRAG 系数 λ'),undefined);
});
