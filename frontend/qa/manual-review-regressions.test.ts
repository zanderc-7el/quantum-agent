import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';

function harness(name:string,initialBuffer?:unknown){
 const slots:any[]=[];let cursor=0;const hooks={...React,useState(initial:any){const i=cursor++;if(!(i in slots))slots[i]=i===0&&initialBuffer!==undefined?initialBuffer:typeof initial==='function'?initial():initial;return[slots[i],(v:any)=>{slots[i]=typeof v==='function'?v(slots[i]):v;}];},useRef(initial:any){const i=cursor++;return slots[i]??={current:initial};},useMemo(fn:any){return fn();},useCallback(fn:any){return fn;},useEffect(){}};
 const bundle=buildSync({entryPoints:[fileURLToPath(new URL(`../src/components/${name}.tsx`,import.meta.url))],bundle:true,platform:'node',format:'cjs',write:false,external:['react','react/jsx-runtime'],loader:{'.css':'empty'}}).outputFiles[0].text;
 const require=createRequire(import.meta.url),module={exports:{} as any};new Function('require','module','exports',bundle)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
 return(props:any)=>{cursor=0;return module.exports.default(props);};
}
const elements=(n:any):any[]=>Array.isArray(n)?n.flatMap(elements):n?.props?[n,...elements(n.props.children)]:[];
const text=(n:any):string=>Array.isArray(n)?n.map(text).join(''):n?.props?text(n.props.children):typeof n==='string'||typeof n==='number'?String(n):'';
const control=(tree:any,label:string)=>elements(tree).find(n=>['input','select'].includes(n.type)&&n.props['aria-label']===label)?.props;
const button=(tree:any,label:string)=>elements(tree).find(n=>n.type==='button'&&text(n)===label)?.props;
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('uncertain create recovery repeats only the frozen draft request and operation, never chooses a same-name foreign chip task',async()=>{
 const render=harness('ManualExperimentWorkspace');const calls:any[]=[];const selected:string[]=[];let listCalls=0;
 const wrong={id:'foreign-chip',name:'same-name',mode:'real',policy:'strict',experiment:'single',coverage:'full',resourceIds:['Q11'],target:'Q11',chipVersionId:'chip-B',manualExperiment:{procedureId:'sq_rabi_amplitude'},nodes:[{id:'wrong-node',procedureId:'sq_rabi_amplitude'}]};
 const correct={...wrong,id:'original-receipt',chipVersionId:undefined,nodes:[{id:'right-node',procedureId:'sq_rabi_amplitude'}]};
 const bounds={amp_start:{min:0,max:1},amp_stop:{min:0,max:1},drive_length_s:{min:1e-9,max:1e-6},shots:{min:1,max:2000},points:{min:2,max:161}};
 const props={gateway:{list:async()=>{listCalls++;return[wrong];},get:async()=>wrong},catalog:{resources:[{id:'Q11',label:'Q11'}],capabilities:{resources:['Q11']},manualMethods:[{procedureId:'sq_rabi_amplitude',label:'Rabi',capability:'available',parameterBounds:bounds}]},workflows:[],busy:false,onBusy(){},onSelectWorkflow(id:string){selected.push(id);},onSelectNode(){},onAction(){throw Error('Hardware action must not run');},onBlueprint(){},onParameterDirty(){},async onCreate(input:any){calls.push(structuredClone(input));if(calls.length===1)throw Error('response lost');return correct;}};
 let tree=render(props);control(tree,'新建实验类型').onChange({target:{value:'sq_rabi_amplitude'}});tree=render(props);control(tree,'实验名称').onChange({target:{value:'same-name'}});tree=render(props);control(tree,'实验对象').onChange({target:{value:'Q11'}});tree=render(props);
 elements(tree).find(n=>typeof n.type==='function'&&n.type.name==='ManualCreationParameters').props.onChange({amp_start:'.1',amp_stop:'.9',scan_step:'.2',drive_length_s:'80',shots:'100'});tree=render(props);button(tree,'下一步').onClick();await settle();
 assert.equal(calls.length,1);tree=render(props);control(tree,'实验名称').onChange({target:{value:'edited-after-timeout'}});tree=render(props);button(tree,'核查创建结果').onClick();await settle();
 assert.equal(listCalls,0,'task list resemblance cannot establish the original creation identity');assert.equal(calls.length,2);assert.deepEqual(calls[1],calls[0]);assert.ok(calls[0].operationId);assert.equal(calls[0].manualParameters.drive_length_s,80e-9);assert.deepEqual(selected,['original-receipt']);
});

test('Rabi default point follows acquisition cursor despite shuffled amplitude order and keeps an explicit user selection',()=>{
 const point=(x:number,cursor:number)=>({operationId:'op',jobId:'job',jobName:'Rabi',capture:0,pointIndex:cursor,baseIndex:cursor,axes:{'cfg.Q1.gate.U_gates.pi_amp':x},meanI:x,meanQ:0,meanMagnitude:x,meanAbs:x,shots:100,pointSha256:'s'+cursor,pointFile:cursor+'.json',cursor});
 const points=[point(.1,1),point(.9,2),point(.3,3)];const render=harness('LiveMeasurementSeries',{operationId:'op',cursor:3,points});const props={gateway:{},workflowId:'wf',node:{id:'rabi',procedureId:'sq_rabi_amplitude',resourceId:'Q11'}};
 let tree=render(props);const principle=()=>elements(tree).find(n=>typeof n.type==='function'&&n.type.name==='RabiPrinciple');assert.equal(principle().props.point.cursor,3);
 const scatter=elements(tree).find(n=>typeof n.type==='function'&&n.type.name==='LiveScatter');scatter.props.onSelectPoint(points[0]);tree=render(props);assert.equal(principle().props.point.cursor,1,'an explicit selection remains pinned');
});
