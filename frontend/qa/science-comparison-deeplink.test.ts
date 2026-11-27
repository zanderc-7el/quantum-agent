import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';

function harness(){
 const code=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ScienceWorkbench.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
 const scopes=new Map<string,any[]>();let slots:any[]=[],cursor=0,last='';const effects:(()=>void)[]=[];
 const hooks={...React,useState(initial:any){const n=cursor++,s=slots;s[n]??={value:typeof initial==='function'?initial():initial};return [s[n].value,(v:any)=>s[n].value=typeof v==='function'?v(s[n].value):v];},useRef(v:any){return slots[cursor++]??={current:v};},useMemo(fn:any,deps:any[]){const n=cursor++;if(!slots[n]||deps.some((d,i)=>d!==slots[n].deps[i]))slots[n]={value:fn(),deps};return slots[n].value;},useCallback(fn:any,deps:any[]){return hooks.useMemo(()=>fn,deps);},useEffect(fn:any,deps:any[]){const n=cursor++,s=slots;if(!s[n]||deps.some((d,i)=>d!==s[n].deps[i])){s[n]?.cleanup?.();s[n]={deps};effects.push(()=>s[n].cleanup=fn());}}};
 const module={exports:{} as any},require=createRequire(import.meta.url);new Function('require','module','exports',code)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
 function renderNode(node:any,key:string){slots=scopes.get(key)??[];scopes.set(key,slots);cursor=0;const tree=node.type(node.props);while(effects.length)effects.shift()!();return tree;}
 return {render(props:any){const node=module.exports.default(props),key='workspace:'+node.key;if(last&&last!==key)for(const s of scopes.get(last)??[])s?.cleanup?.();last=key;return renderNode(node,key);},renderNode};
}
function all(tree:any):any[]{return tree&&typeof tree==='object'&&tree.props?[tree,...[tree.props.children].flat(Infinity).flatMap(all)]:[];}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const raw={id:'raw',kind:'raw',sha256:'a'.repeat(64)};
function fixture(attemptId='old'){
 const source={workflowId:'wf',nodeId:'Q18:time',attemptId};
 const result={dataKind:'real',acquiredAt:'2026-11-18T12:00:00Z',parameters:{drive_amp:.3},partial:false,quality:{status:'passed',reasons:[]},artifacts:[raw]};
 const detail={workflowRevision:7,node:{id:source.nodeId,resourceId:'Q18',procedureId:'sq_rabi_time'},attempts:[{id:attemptId,nodeId:source.nodeId,mode:'real',state:'verified',procedureId:'sq_rabi_time',result}]};
 const record={id:'record-'+attemptId,kind:'measurement',dataKind:'real',summary:false,scope:{workflowId:'wf',resourceId:'Q18'},reference:{workflowId:'wf',attemptId},input:{},evidence:[raw],evidenceIntact:true,provenance:{independentlyVerified:true},result:{...result,procedureId:'sq_rabi_time',axes:{x:{name:'Time',unit:'s',values:[0,1]}},sourceSeries:[{id:'raw-trace',label:'实际曲线',quantity:'iq_amplitude',unit:'IQ a.u.',values:[.1,.4]}]}};
 return {source,detail,record};
}
const workflow={id:'wf',name:'多比特任务',target:'Q11',resourceIds:['Q11','Q18'],nodes:[{id:'Q18:time',resourceId:'Q18',latestAttemptId:'newer'}]};

test('node comparison deep link opens compare and selects its full historical record with real curve',async()=>{
 const {source,detail,record}=fixture();const calls:string[]=[];
 const gateway={get:async(path:string)=>{calls.push(path);if(path.includes('/nodes/'))return detail;if(path.includes('/records/record-old'))return record;if(path.includes('/overview'))return {};return {records:[{...record,summary:true}],hasMore:false};},write:async()=>assert.fail('stored record does not need binding')};
 const props={gateway,workflow,comparisonSource:source};const view=harness();let tree=view.render(props);await tick();tree=view.render(props);
 assert.equal(all(tree).find(n=>n.type==='button'&&n.props.children==='仿真实测对照').props['aria-pressed'],true);
 const panel=all(tree).find(n=>n.type?.name==='ComparePanel');assert.ok(panel,'a resolved deep link exposes the actual compare panel');
 const content=view.renderNode(panel,'compare');const selected=all(content).find(n=>n.props.label==='原始采集记录');assert.equal(selected.props.value,record.id);
 assert.ok(all(content).some(n=>n.type==='option'&&n.props.value==='raw-trace'));
 assert.equal(all(content).find(n=>n.props.title==='实测记录及原始来源').props.value.reference.attemptId,'old');
 assert.ok(calls.some(p=>p.includes('/nodes/Q18%3Atime')));
});

test('global entry remains on pulse without creating or auto-selecting a comparison source',async()=>{
 const calls:string[]=[];const props={gateway:{get:async(path:string)=>{calls.push(path);return path.includes('/overview')?{}:{records:[],hasMore:false};},write:async()=>assert.fail('no bind')}};
 const view=harness();view.render(props);await tick();const tree=view.render(props);
 assert.equal(all(tree).find(n=>n.type==='button'&&n.props.children==='脉冲时序').props['aria-pressed'],true);
 assert.ok(!calls.some(p=>p.includes('/nodes/')));assert.ok(!all(tree).some(n=>n.type?.name==='ComparePanel'));
});

test('unknown or wrong-workflow deep link shows a retryable failure instead of a different record',async()=>{
 const {source,detail,record}=fixture();let writes=0;
 const gateway={get:async(path:string)=>path.includes('/nodes/')?{...detail,attempts:[]}:path.includes('/overview')?{}:{records:[record],hasMore:false},write:async()=>{writes++;}};
 const view=harness(),props={gateway,workflow,comparisonSource:source};view.render(props);await tick();const tree=view.render(props);
 assert.ok(JSON.stringify(tree).includes('未找到'));assert.ok(all(tree).some(n=>n.type==='button'&&n.props.children==='重试载入该次采集'));
 assert.ok(!all(tree).some(n=>n.type?.name==='ComparePanel'));assert.equal(writes,0);
 const wrong=view.render({...props,workflow:{...workflow,id:'different'}});await tick();const failed=view.render({...props,workflow:{...workflow,id:'different'}});
 assert.ok(JSON.stringify(failed).includes('当前任务'));assert.ok(!all(wrong).some(n=>n.type?.name==='ComparePanel'));
});

test('late record response from a previous deep link cannot replace the newly selected attempt',async()=>{
 const first=fixture('first'),second=fixture('second');let finish!:(v:any)=>void;
 const gateway={get:async(path:string)=>{if(path.includes('/nodes/'))return {...first.detail,attempts:[...first.detail.attempts,...second.detail.attempts]};if(path.endsWith('/record-first'))return new Promise(resolve=>finish=resolve);if(path.endsWith('/record-second'))return second.record;if(path.includes('/overview'))return {};return {records:[first.record,second.record].map(r=>({...r,summary:true})),hasMore:false};},write:async()=>assert.fail('no binding')};
 const view=harness();view.render({gateway,workflow,comparisonSource:first.source});await tick();assert.ok(finish);
 const props={gateway,workflow,comparisonSource:second.source};view.render(props);await tick();finish(first.record);await tick();const tree=view.render(props);
 const content=view.renderNode(all(tree).find(n=>n.type?.name==='ComparePanel'),'compare-second');
 assert.equal(all(content).find(n=>n.props.label==='原始采集记录').props.value,'record-second');
});

test('signal deep link renders evidence for the exact archive and omits editable and comparison panels',async()=>{
 const {source,detail,record:original}=fixture(); const record={...original,source:'acquisition'};
 const gateway={get:async(path:string)=>path.includes('/nodes/')?detail:path.endsWith('/'+record.id)?record:path.includes('/overview')?{}:{records:[{...record,summary:true}],hasMore:false},
  write:async()=>assert.fail('opening signal evidence is read-only')};
 const view=harness(),props={gateway,workflow,comparisonSource:{...source,panel:'signals'}};
 view.render(props);await tick();const tree=view.render(props);
 assert.equal(all(tree).find(n=>n.type==='button'&&n.props.children==='信号与预补偿').props['aria-pressed'],true);
 const selected=all(tree).find(n=>n.type?.name==='SignalRecordEvidence');assert.equal(selected.props.record.id,record.id);
 assert.ok(!all(tree).some(n=>['SignalPanel','ComparePanel'].includes(n.type?.name)));
 const content=view.renderNode(selected,'signal-archive');assert.ok(JSON.stringify(content).includes('归档主结果'));
});
