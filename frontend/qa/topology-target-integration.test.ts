import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';

function harness(component:string,exportName='default'){
 const compiled=buildSync({entryPoints:[fileURLToPath(new URL(`../src/components/${component}.tsx`,import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
 const slots:any[]=[];let cursor=0;const effects:(()=>void)[]=[];
 const hooks={...React,useState(initial:any){const n=cursor++;slots[n]??={value:typeof initial==='function'?initial():initial};return [slots[n].value,(value:any)=>{slots[n].value=typeof value==='function'?value(slots[n].value):value;}];},useRef(value:any){return slots[cursor++]??={current:value};},useCallback(fn:any){return fn;},useMemo(fn:any,deps:any[]){const n=cursor++;if(!slots[n]||deps.some((d,i)=>d!==slots[n].deps[i]))slots[n]={value:fn(),deps};return slots[n].value;},useEffect(fn:any,deps:any[]){const n=cursor++;if(!slots[n]||deps.some((d,i)=>d!==slots[n].deps[i])){slots[n]?.cleanup?.();slots[n]={deps};effects.push(()=>{slots[n].cleanup=fn();});}}};
 const module={exports:{} as any};const require=createRequire(import.meta.url);new Function('require','module','exports',compiled)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
 return {render(props:any){cursor=0;const tree=module.exports[exportName](props);while(effects.length)effects.shift()!();return tree;},close(){for(const slot of slots)slot?.cleanup?.();}};
}
function all(tree:any):any[]{return tree&&typeof tree==='object'&&tree.props?[tree,...[tree.props.children].flat(Infinity).flatMap(all)]:[];}
function text(tree:any):string{return typeof tree==='string'||typeof tree==='number'?String(tree):tree?.props?[tree.props.children].flat(Infinity).map(text).join(''):'';}
function field(tree:any,label:string){const value=all(tree).find(x=>['input','select'].includes(x.type)&&x.props['aria-label']===label);assert.ok(value,'missing field '+label);return value.props;}
function diagram(tree:any){const value=all(tree).find(x=>x.props.topology&&typeof x.props.onSelect==='function');assert.ok(value,'missing selectable topology');return value.props;}
const q=(id:string,identityStatus='confirmed')=>({id,label:id,physicalId:'physical-'+id,identityStatus,parameters:{}});
const topology={nodes:[{resourceId:'Q1',designId:'alias',x:0,y:0},{resourceId:'Q2',x:1,y:0},{resourceId:'draft',x:2,y:0},{resourceId:'ghost',x:3,y:0}],edges:[]};
const chips=[{id:'chip',name:'机台一芯片',versions:[{id:'c1',topology,qubits:[q('Q1'),q('Q2'),q('no-coordinate'),q('draft','unresolved')]}]},{id:'other',name:'机台二芯片',versions:[{id:'c2',topology:{nodes:[{resourceId:'Q1',x:8,y:8}],edges:[]},qubits:[q('Q1')]}]}];
const deployments=[{id:'wiring',name:'草稿名称不可替代版本',versions:[{id:'d1',name:'冻结接线',machine:'测试机台二'}]}];
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const api={get:async(path:string)=>path==='/chips'?chips:path==='/deployments'?deployments:[],write:async()=>{throw new Error('no write authorized by this test');}};

test('manual creation synchronizes topology and select, rejects draft aliases, and clears same-named resources on version change',async()=>{
 const requests:any[]=[];const view=harness('ManualExperimentWorkspace');const props={gateway:{},workspaceApi:api,catalog:{manualMethods:[{procedureId:'ro_resonator_survey',label:'找腔',capability:'available'}]},workflows:[],busy:false,onBusy(){},async onCreate(input:any){requests.push(input);return {id:'draft-only',nodes:[]};},onSelectWorkflow(){},onSelectNode(){},onBlueprint(){},onParameterDirty(){}};
 let tree=view.render(props);await tick();tree=view.render(props);
 field(tree,'新建实验类型').onChange({target:{value:'ro_resonator_survey'}});tree=view.render(props);field(tree,'手动实验芯片版本').onChange({target:{value:'c1'}});tree=view.render(props);
 field(tree,'实验对象').onChange({target:{value:'Q1'}});tree=view.render(props);assert.equal(diagram(tree).selectedResourceId,'Q1');
 diagram(tree).onSelect('Q2');tree=view.render(props);assert.equal(field(tree,'实验对象').value,'Q2');assert.equal(diagram(tree).selectedResourceId,'Q2');
 for(const id of ['draft','ghost','alias']){diagram(tree).onSelect(id);tree=view.render(props);assert.equal(field(tree,'实验对象').value,'Q2');assert.match(text(tree),/仅供浏览/);}
 const draftOption=all(tree).find(x=>x.type==='option'&&x.props.value==='draft');assert.equal(draftOption.props.disabled,true);
 field(tree,'实验对象').onChange({target:{value:'draft'}});tree=view.render(props);assert.equal(field(tree,'实验对象').value,'Q2');
 field(tree,'实验对象').onChange({target:{value:'no-coordinate'}});tree=view.render(props);assert.equal(field(tree,'实验对象').value,'no-coordinate');
 field(tree,'手动实验芯片版本').onChange({target:{value:'c2'}});tree=view.render(props);assert.equal(field(tree,'实验对象').value,'');assert.equal(diagram(tree).selectedResourceId,'');assert.equal(diagram(tree).scope,'c2');
 field(tree,'手动实验接线版本').onChange({target:{value:'d1'}});tree=view.render(props);diagram(tree).onSelect('Q1');tree=view.render(props);field(tree,'实验名称').onChange({target:{value:'只创建草稿'}});tree=view.render(props);const next=all(tree).find(x=>x.type==='button'&&text(x).startsWith('下一步'));assert.equal(next.props.disabled,false);next.props.onClick();await tick();assert.equal(requests.length,1);assert.deepEqual(requests[0].resourceIds,['Q1']);assert.equal(requests[0].chipVersionId,'c2');assert.equal(requests[0].deploymentVersionId,'d1');assert.equal(requests[0].machine,'测试机台二');
 view.close();
});

test('published science setup binds graph selection to this version and never fetches a draft target catalog',async()=>{
 const calls:string[]=[];const view=harness('ScienceExecutionSetup');const props={chips,deployments,name:'科学测试',disabled:false,onNameChange(){},onDraftChange(){},api:{get:async(path:string)=>{calls.push(path);if(path.startsWith('/science'))return {records:[]};const url=new URL(path,'https://fixture.invalid');return {manualMethods:[],configurationBinding:{chipVersionId:url.searchParams.get('chipVersionId'),deploymentVersionId:'d1',resourceId:url.searchParams.get('resourceId')}};}}};
 let tree=view.render(props);field(tree,'科学实验芯片版本').onChange({target:{value:'c1'}});tree=view.render(props);field(tree,'科学实验接线版本').onChange({target:{value:'d1'}});tree=view.render(props);
 diagram(tree).onSelect('draft');tree=view.render(props);await tick();assert.deepEqual(calls,[]);assert.equal(field(tree,'科学实验目标比特').value,'');
 diagram(tree).onSelect('Q2');view.render(props);await tick();tree=view.render(props);assert.equal(field(tree,'科学实验目标比特').value,'Q2');assert.equal(diagram(tree).selectedResourceId,'Q2');assert.match(calls[0],/resourceId=Q2/);
 field(tree,'科学实验目标比特').onChange({target:{value:'Q1'}});view.render(props);await tick();tree=view.render(props);assert.equal(diagram(tree).selectedResourceId,'Q1');
 field(tree,'科学实验芯片版本').onChange({target:{value:'c2'}});tree=view.render(props);assert.equal(diagram(tree).selectedResourceId,'');assert.equal(field(tree,'科学实验目标比特').value,'');
 view.close();
});

test('campaign topology adds only confirmed targets while preserving independent multi-selection',async(t)=>{
 const view=harness('WorkspaceControl','Campaigns');t.after(()=>view.close());const props={api,onOpenWorkflow(){}};
 let tree=view.render(props);await tick();tree=view.render(props);field(tree,'芯片版本').onChange({target:{value:'c1'}});tree=view.render(props);
 const checks=(t:any)=>all(t).filter(x=>x.type==='label'&&x.props.className==='wc-check');
 diagram(tree).onSelect('Q1');tree=view.render(props);diagram(tree).onSelect('Q2');tree=view.render(props);
 assert.equal(diagram(tree).selectedResourceId,undefined,'multiple targets must not masquerade as one selected resource');
 for(const id of ['Q1','Q2'])assert.equal(all(checks(tree).find(x=>text(x)===id)).find(x=>x.type==='input').props.checked,true);
 diagram(tree).onSelect('draft');tree=view.render(props);const draft=all(checks(tree).find(x=>text(x).startsWith('draft'))).find(x=>x.type==='input');assert.equal(draft.props.checked,false);assert.equal(draft.props.disabled,true);
 field(tree,'芯片版本').onChange({target:{value:'c2'}});tree=view.render(props);assert.equal(all(checks(tree)[0]).find(x=>x.type==='input').props.checked,false);
 view.close();
});
