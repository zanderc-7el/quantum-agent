import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';

function harness(component='PhysicalRoutesEditor',exportName='default'){
 const entry=component==='WorkspaceControl'&&process.env.QCAL_WIRING_BASELINE_COMPONENT?process.env.QCAL_WIRING_BASELINE_COMPONENT:fileURLToPath(new URL(`../src/components/${component}.tsx`,import.meta.url));
 const bundle=buildSync({entryPoints:[entry],bundle:true,platform:'node',format:'cjs',write:false,packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
 const states=new Map<string,any[]>();let state:any[]=[],cursor=0;
 const hooks={...React,useState(initial:any){const index=cursor++;if(!(index in state))state[index]=typeof initial==='function'?initial():initial;const own=state;return [own[index],(v:any)=>{own[index]=typeof v==='function'?v(own[index]):v;}];},useRef(initial:any){const index=cursor++;return state[index]??={current:initial};},useEffect(){},useMemo(fn:any){return fn();},useCallback(fn:any){return fn;}};
 const module={exports:{} as any};const require=createRequire(import.meta.url);new Function('require','module','exports',bundle)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
 function expand(node:any,path:string):any{if(Array.isArray(node))return node.map((child,index)=>expand(child,`${path}.${child?.key??index}`));if(!node?.props)return node;if(typeof node.type==='function'){const key=`${path}:${node.type.name}:${node.key??''}`;state=states.get(key)??[];states.set(key,state);cursor=0;return expand(node.type(node.props),key);}return {...node,props:{...node.props,children:expand(node.props.children,path+'.children')}};}
 return (props:any)=>expand(React.createElement(module.exports[exportName],props),'root');
}
const elements=(node:any):any[]=>Array.isArray(node)?node.flatMap(elements):node?.props?[node,...elements(node.props.children)]:[];
const text=(node:any):string=>Array.isArray(node)?node.map(text).join(''):node?.props?text(node.props.children):typeof node==='string'||typeof node==='number'?String(node):'';
function field(tree:any,label:string){const el=elements(tree).find(n=>n.props['aria-label']===label);assert.ok(el,`missing ${label}`);return el.props;}
function button(tree:any,label:string){const el=elements(tree).find(n=>n.type==='button'&&text(n)===label);assert.ok(el,`missing button ${label}`);return el.props;}
function labeledInput(tree:any,label:string){const row=elements(tree).find(n=>n.type==='label'&&text(n)===label);assert.ok(row,`missing label ${label}`);const input=elements(row).find(n=>n.type==='input');assert.ok(input,`missing input ${label}`);return input.props;}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const source='现场扫频记录 2026-11-26，附件 A，第2页';
const route=()=>({xyPhysical:[0,0],readoutPhysical:[8,0],adcPhysical:[0,0],xyPassbandHz:[3.2e9,5.8e9],xyPassbandSource:source,xyPassbandConfirmed:true});

test('actual ChipWiring controls save partial GHz input as Hz and reload the unchanged source',async()=>{
 const render=harness('WorkspaceControl','ChipWiring');const writes:any[]=[];
 const api={get:async()=>[],write:async(path:string,body:any)=>{writes.push({path,body:structuredClone(body)});return {...body.draft,id:'saved-deployment',revision:1};}};
 const props={api,active:false};let tree=render(props);button(tree,'仪器接线').onClick();tree=render(props);
 field(tree,'接线逻辑比特 ID').onChange({target:{value:'Q11'}});tree=render(props);button(tree,'添加路由').onClick();tree=render(props);
 field(tree,'Q11 XY 通带上限 (GHz)').onChange({target:{value:'5.825'}});tree=render(props);
 field(tree,'Q11 XY 通带来源').onChange({target:{value:source}});tree=render(props);button(tree,'保存草稿').onClick();await tick();tree=render(props);
 assert.equal(writes[0].path,'/deployments');assert.deepEqual(writes[0].body.draft.routes.Q11.xyPassbandHz,[null,5.825e9]);
 assert.equal(writes[0].body.draft.routes.Q11.xyPassbandSource,source);assert.equal(writes[0].body.draft.routes.Q11.xyPassbandConfirmed,false);
 assert.equal(field(tree,'Q11 XY 通带上限 (GHz)').value,'5.825');assert.equal(field(tree,'Q11 XY 通带下限 (GHz)').value,'');
 assert.equal(field(tree,'Q11 XY 通带来源').value,source);assert.equal(field(tree,'Q11 确认 XY 通带').checked,false);
 field(tree,'Q11 XY 通带下限 (GHz)').onChange({target:{value:'3.725'}});tree=render(props);
 assert.equal(field(tree,'Q11 确认 XY 通带').checked,false);assert.equal(field(tree,'Q11 确认 XY 通带').disabled,false);
 field(tree,'Q11 确认 XY 通带').onChange({target:{checked:true}});tree=render(props);button(tree,'保存草稿').onClick();await tick();tree=render(props);
 assert.deepEqual(writes[1].body.draft.routes.Q11.xyPassbandHz,[3.725e9,5.825e9]);assert.equal(writes[1].body.draft.routes.Q11.xyPassbandConfirmed,true);
 assert.equal(field(tree,'Q11 确认 XY 通带').checked,true);
});

test('empty and partial passbands roundtrip without defaults, null coercion to zero, or NaN',()=>{
 const render=harness();let routes:any={Q11:{xyPhysical:[0,0]}};const props=()=>({routes,onChange:(next:any)=>{routes=JSON.parse(JSON.stringify(next));}});
 let tree=render(props());assert.equal(field(tree,'Q11 XY 通带下限 (GHz)').value,'');assert.equal(field(tree,'Q11 XY 通带上限 (GHz)').value,'');
 assert.equal(field(tree,'Q11 确认 XY 通带').disabled,true);assert.equal(field(tree,'Q11 确认 XY 通带').checked,false);
 const folded=elements(tree).find(e=>e.type==='details');assert.equal(folded.props.open,undefined);assert.equal(text(folded).startsWith('XY 驱动通带'),true);
 field(tree,'Q11 XY 通带下限 (GHz)').onChange({target:{value:'4.125'}});tree=render(props());assert.deepEqual(routes.Q11.xyPassbandHz,[4.125e9,null]);
 field(tree,'Q11 XY 通带来源').onChange({target:{value:'  附件 A 第2页\n现场核对  '}});tree=render(props());
 field(tree,'Q11 XY 通带上限 (GHz)').onChange({target:{value:'5.5'}});tree=render(props());assert.deepEqual(routes.Q11.xyPassbandHz,[4.125e9,5.5e9]);assert.equal(routes.Q11.xyPassbandConfirmed,false);
 field(tree,'Q11 XY 通带下限 (GHz)').onChange({target:{value:''}});tree=render(props());assert.deepEqual(routes.Q11.xyPassbandHz,[null,5.5e9]);
 field(tree,'Q11 XY 通带上限 (GHz)').onChange({target:{value:''}});tree=render(props());assert.equal(Object.hasOwn(routes.Q11,'xyPassbandHz'),false);
 assert.equal(routes.Q11.xyPassbandSource,'  附件 A 第2页\n现场核对  ');assert.equal(routes.Q11.xyPassbandConfirmed,false);
 for(const value of ['NaN','Infinity','1e308']){field(tree,'Q11 XY 通带下限 (GHz)').onChange({target:{value}});tree=render(props());assert.equal(Object.hasOwn(routes.Q11,'xyPassbandHz'),false);}
});

test('range/source and XY slot or port changes revoke only the edited route confirmation',()=>{
 for(const [label,value] of [['XY 通带下限 (GHz)','3.3'],['XY 通带上限 (GHz)','5.9'],['XY 通带来源','独立核查附件 B'],['XY 驱动槽位','1'],['XY 驱动端口','2']]){
  const render=harness();const original={Q11:route(),Q18:route()};const snapshot=structuredClone(original);let routes:any=original;
  const props=()=>({routes,onChange:(next:any)=>{routes=next;}});field(render(props()),`Q11 ${label}`).onChange({target:{value}});
  assert.equal(routes.Q11.xyPassbandConfirmed,false,label);assert.equal(routes.Q18,original.Q18);assert.equal(routes.Q18.xyPassbandConfirmed,true);
  assert.deepEqual(original,snapshot,'editing must not mutate old published snapshots');
  if(label!=='XY 通带来源')assert.equal(routes.Q11.xyPassbandSource,source);
 }
});

test('readout and ADC edits preserve a valid XY passband and its source',()=>{
 for(const label of ['读出输出槽位','读出输出端口','ADC 输入槽位','ADC 输入端口','ADC 采样率 (Hz)']){
  const render=harness();let routes:any={Q11:route()};field(render({routes,onChange:(next:any)=>{routes=next;}}),`Q11 ${label}`).onChange({target:{value:'2'}});
  assert.equal(routes.Q11.xyPassbandConfirmed,true,label);assert.deepEqual(routes.Q11.xyPassbandHz,[3.2e9,5.8e9]);assert.equal(routes.Q11.xyPassbandSource,source);
 }
});

test('duplicate resource IDs cannot replace a route even through a directly invoked disabled handler',()=>{
 const render=harness();const original={Q11:route()};let routes:any=original;let writes=0;
 const props=()=>({routes,onChange:(next:any)=>{writes++;routes=next;}});
 let tree=render(props());field(tree,'接线逻辑比特 ID').onChange({target:{value:' Q11 '}});tree=render(props());
 assert.equal(button(tree,'添加路由').disabled,true);button(tree,'添加路由').onClick();assert.equal(writes,0);assert.equal(routes,original);
 field(tree,'接线逻辑比特 ID').onChange({target:{value:'Q18'}});tree=render(props());assert.equal(button(tree,'添加路由').disabled,false);button(tree,'添加路由').onClick();
 assert.equal(routes.Q11,original.Q11);assert.equal(routes.Q18.xyPassbandConfirmed,false);assert.equal(Object.hasOwn(routes.Q18,'xyPassbandHz'),false);
});

test('resources and deployments with the same route ID display only the current controlled values',()=>{
 const render=harness();let routes:any={Q11:route(),Q18:{xyPhysical:[1,1],xyPassbandHz:[6e9,null],xyPassbandSource:'另一条通路'}};
 const props=()=>({routes,onChange:(next:any)=>{routes=next;}});let tree=render(props());
 field(tree,'Q11 XY 通带下限 (GHz)').onChange({target:{value:'3.1'}});tree=render(props());assert.equal(field(tree,'Q18 XY 通带下限 (GHz)').value,'6');
 routes={Q11:{xyPhysical:[3,1]}};tree=render(props());assert.equal(field(tree,'Q11 XY 通带下限 (GHz)').value,'');assert.equal(field(tree,'Q11 XY 通带来源').value,'');
 button(tree,'移除路由').onClick();tree=render(props());field(tree,'接线逻辑比特 ID').onChange({target:{value:'Q11'}});tree=render(props());button(tree,'添加路由').onClick();tree=render(props());
 assert.equal(field(tree,'Q11 XY 通带下限 (GHz)').value,'');assert.equal(field(tree,'Q11 确认 XY 通带').checked,false);
});

test('invalid or incomplete ranges and placeholder sources cannot be confirmed, including direct handler calls',()=>{
 const invalidRanges:any[]=[undefined,[null,null],[3e9,null],[null,5e9],[0,5e9],[-1,5e9],[5e9,5e9],[6e9,5e9],[NaN,5e9],[3e9,Infinity],['3e9',5e9],[true,5e9]];
 for(const range of invalidRanges){
  const render=harness();let routes:any={Q11:{...route(),xyPassbandHz:range,xyPassbandConfirmed:false}};const props=()=>({routes,onChange:(next:any)=>{routes=next;}});
  const control=field(render(props()),'Q11 确认 XY 通带');assert.equal(control.disabled,true);control.onChange({target:{checked:true}});assert.equal(routes.Q11.xyPassbandConfirmed,false);
 }
 for(const invalidSource of ['',null,'   ','待核实','ＴＢＤ',' u n k n o w n ','确认','???','a'.repeat(2001)]){
  const render=harness();let routes:any={Q11:{...route(),xyPassbandSource:invalidSource,xyPassbandConfirmed:false}};
  const control=field(render({routes,onChange:(next:any)=>{routes=next;}}),'Q11 确认 XY 通带');assert.equal(control.disabled,true,String(invalidSource));control.onChange({target:{checked:true}});assert.equal(routes.Q11.xyPassbandConfirmed,false);
 }
 const render=harness();let routes:any={Q11:{...route(),xyPassbandSource:'未知信号响应分析报告，第 2 页',xyPassbandConfirmed:false}};
 const control=field(render({routes,onChange:(next:any)=>{routes=next;}}),'Q11 确认 XY 通带');assert.equal(control.disabled,false);control.onChange({target:{checked:true}});assert.equal(routes.Q11.xyPassbandConfirmed,true);
});

test('dirty chip and wiring drafts cannot publish old saved revisions, even from stale button callbacks',async()=>{
 for(const kind of ['chip','wiring']){
  const render=harness('WorkspaceControl','ChipWiring');let saved:any;let revision=0;let failSave=false;const publications:any[]=[];
  const endpoint=kind==='chip'?'/chips':'/deployments';
  const api={get:async(path:string)=>path===`${endpoint}/saved`?saved:[],write:async(path:string,body:any)=>{
   if(path.endsWith('/publish')){publications.push(structuredClone(body));saved={...saved,versions:[{id:'published-version'}]};return {};}
   if(failSave)throw new Error('草稿版本冲突');saved={...body.draft,id:'saved',revision:++revision};return saved;
  }};
  const props={api,active:false};let tree=render(props);if(kind==='wiring'){button(tree,'仪器接线').onClick();tree=render(props);}
  const label=kind==='chip'?'芯片名称':'接线名称';labeledInput(tree,label).onChange({target:{value:'已存草稿'}});tree=render(props);
  button(tree,'保存草稿').onClick();await tick();tree=render(props);assert.equal(button(tree,'发布当前配置').disabled,false);
  const stalePublish=button(tree,'发布当前配置').onClick;
  labeledInput(tree,label).onChange({target:{value:'尚未保存的修改'}});
  stalePublish();await tick();tree=render(props);assert.equal(publications.length,0,kind);assert.equal(button(tree,'发布当前配置').disabled,true);
  assert.equal(button(tree,'发布当前配置').title,'请先保存草稿');
  failSave=true;button(tree,'保存草稿').onClick();await tick();tree=render(props);assert.equal(button(tree,'发布当前配置').disabled,true);
  button(tree,'发布当前配置').onClick();await tick();assert.equal(publications.length,0,'failed CAS save must not publish');
  failSave=false;tree=render(props);button(tree,'保存草稿').onClick();await tick();tree=render(props);
  assert.equal(button(tree,'发布当前配置').disabled,false);button(tree,'发布当前配置').onClick();await tick();tree=render(props);
  assert.equal(publications.length,1);assert.equal(publications[0].expectedRevision,2);assert.equal(labeledInput(tree,label).value,'尚未保存的修改');
 }
});

test('rapid tab switch cannot publish the previous tab via its stale callback',async()=>{
 const render=harness('WorkspaceControl','ChipWiring');const publications:any[]=[];
 const api={get:async()=>[],write:async(path:string,body:any)=>{if(path.endsWith('/publish'))publications.push(path);return {...body.draft,id:'saved-chip',revision:1};}};
 const props={api,active:false};let tree=render(props);button(tree,'保存草稿').onClick();await tick();tree=render(props);
 const stalePublish=button(tree,'发布当前配置').onClick;assert.equal(button(tree,'发布当前配置').disabled,false);
 button(tree,'仪器接线').onClick();stalePublish();await tick();tree=render(props);assert.deepEqual(publications,[]);assert.equal(button(tree,'发布当前配置').disabled,true);
});

test('a late save response never replaces a different draft opened while the request was pending',async()=>{
 const render=harness('WorkspaceControl','ChipWiring');let finish:(value:any)=>void=()=>{};
 const api={get:async()=>[],write:()=>new Promise(resolve=>{finish=resolve;})};const props={api,active:false};let tree=render(props);
 labeledInput(tree,'芯片名称').onChange({target:{value:'正在保存的 A'}});tree=render(props);const newChip=button(tree,'新建芯片').onClick;
 button(tree,'保存草稿').onClick();newChip();finish({id:'saved-A',revision:1,name:'正在保存的 A',qubits:[]});await tick();tree=render(props);
 assert.equal(labeledInput(tree,'芯片名称').value,'新芯片');assert.equal(button(tree,'发布当前配置').disabled,true);
});
