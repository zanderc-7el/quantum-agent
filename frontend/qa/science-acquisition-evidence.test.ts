import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';

function harness(){
 const entry=new URL('../src/components/ScienceAcquisitionEvidence.tsx',import.meta.url);
 assert.ok(existsSync(entry),'actual acquisition evidence component is missing');
 const compiled=buildSync({entryPoints:[fileURLToPath(entry)],bundle:true,write:false,platform:'node',format:'cjs',packages:'external'}).outputFiles[0].text;
 const slots:any[]=[];let cursor=0;const effects:(()=>void)[]=[];
 const hooks={...React,useState(initial:any){const n=cursor++;slots[n]??={value:typeof initial==='function'?initial():initial};return [slots[n].value,(v:any)=>{slots[n].value=typeof v==='function'?v(slots[n].value):v;}];},useRef(v:any){return slots[cursor++]??={current:v};},useEffect(fn:any,deps:any[]){const n=cursor++;if(!slots[n]||deps.some((d,i)=>d!==slots[n].deps[i])){slots[n]?.cleanup?.();slots[n]={deps};effects.push(()=>{slots[n].cleanup=fn();});}}};
 const mod={exports:{} as any},require=createRequire(import.meta.url);new Function('require','module','exports',compiled)((id:string)=>id==='react'?hooks:require(id),mod,mod.exports);
 return {render(props:any){cursor=0;const tree=mod.exports.default(props);while(effects.length)effects.shift()!();return tree;}};
}
const all=(tree:any):any[]=>tree?.props?[tree,...[tree.props.children].flat(Infinity).flatMap(all)]:[];
const text=(tree:any):string=>typeof tree==='string'||typeof tree==='number'?String(tree):tree?.props?[tree.props.children].flat(Infinity).map(text).join(''):'';
const button=(tree:any,label:string)=>{const found=all(tree).find(n=>n.type==='button'&&text(n)===label);assert.ok(found,`missing ${label}`);return found.props;};
const record={id:'irb',kind:'measurement',scope:{resourceId:'Q11'},input:{},result:{procedureId:'sq_drag_irb',gateCharacterization:{sourceReferences:[{role:'drag',recordId:'scan / 1'}]}}};

test('saved IRB has a direct read-only link to its independent coefficient scan',async()=>{
 const view=harness(),calls:string[]=[];
 const source={id:'scan / 1',kind:'measurement',scope:{resourceId:'Q11'},input:{},result:{procedureId:'sq_drag_half_scan',series:[{values:[.1,.2]}]}};
 const props={record,gateway:{get:async(path:string)=>{calls.push(path);return source;},write:()=>assert.fail('viewing evidence must not write')},renderResult:(result:any)=>React.createElement('output',{'data-result':result}),Plot:()=>null};
 let tree=view.render(props);assert.deepEqual(calls,[]);
 await button(tree,'打开系数扫描与复测').onClick();tree=view.render(props);
 assert.deepEqual(calls,['/science/records/scan%20%2F%201']);
 assert.ok(all(tree).some(n=>n.props['data-result']===source.result));
 assert.ok(button(tree,'返回门表征记录'));
 button(tree,'返回门表征记录').onClick();tree=view.render(props);
 assert.ok(button(tree,'打开系数扫描与复测'));
});

const archived={schema:'qcal-archived-pulse-samples/1',recordId:'irb',stage:'verification',stages:[{id:'selection',label:'系数选择'},{id:'verification',label:'独立复测'}],currentEvidence:true,
 rows:[{rowId:0,condition:'diagnostic',axisValue:0},{rowId:3,condition:'verification_candidate',axisValue:1e-9}],segments:[{id:'xy0',channel:'xy',label:'X/2'},{id:'xy1',channel:'xy',label:'Y/2'}],selected:{rowId:0,segmentId:'xy0'},segment:{basebandI:{sha256:'i-sha'},basebandQ:{sha256:'q-sha'}},
 samples:{timeNs:[0,.25,.5],i:[0,.2,0],q:[.1,0,-.1],rfTimeNs:[0,.25,.5],rf:[.12,-.2,.08],basebandSampleCount:3,rfSampleCount:3,decimation:'exact-index-subsampling',maxDisplaySamples:2048},upload:{status:'matched',receipts:[{rowId:0,sha256:'rf-sha'}],deviceWaveformReadback:false},artifacts:[{id:'raw / 1',name:'preflight_0000.npz',sha256:'npz-sha'},{id:'plan',name:'compiled_plan.json',sha256:'plan-sha'},{id:'receipt',name:'uploads.jsonl',sha256:'receipt-sha'}]};
test('normal signal viewer permits a genuine legacy measurement lookup but never claims missing samples or upload',async()=>{
 const view=harness(),calls:string[]=[];
 const legacy={...record,id:'old-time',source:'acquisition',reference:{workflowId:'wf',attemptId:'a'},result:{procedureId:'sq_rabi_time',dataKind:'real',source:'acquisition'}};
 const props={record:legacy,allowAnyMeasurement:true,gateway:{get:async(path:string)=>{calls.push(path);return {...archived,recordId:legacy.id,workflowId:'wf',attemptId:'a',status:'unavailable',reason:'旧版清单只保存波形摘要和时序',samples:null,rows:[],segments:[],selected:null,upload:{status:'not_recorded',receipts:[],deviceWaveformReadback:false}};}},renderResult:()=>null,Plot:()=>null};
 let tree=view.render(props);assert.equal(calls.length,0);
 await button(tree,'查看脉冲样点').onClick();tree=view.render(props);
 assert.deepEqual(calls,['/science/records/old-time/pulse-samples']);assert.match(text(tree),/旧版清单/);
 assert.match(text(tree),/未找到.*上传命令/);assert.ok(!text(tree).includes('单路 RF 编译样点'));
});

test('normal signal lookup rejects another attempt and can keep source-navigation outside the same-record view',async()=>{
 const view=harness();const source={...record,source:'acquisition',reference:{workflowId:'wf',attemptId:'own'},result:{...record.result,dataKind:'real'}};
 const props={record:source,allowAnyMeasurement:true,showRelatedSources:false,gateway:{get:async()=>({...archived,workflowId:'wf',attemptId:'different'})},renderResult:()=>null,Plot:()=>null};
 let tree=view.render(props);assert.ok(!all(tree).some(n=>n.type==='button'&&text(n).startsWith('打开')));
 await button(tree,'查看脉冲样点').onClick();tree=view.render(props);
 assert.match(text(tree),/任务或尝试不一致/);assert.equal(all(tree).filter(n=>n.type==='a').length,0);
});
test('archived plots use exact returned I/Q and RF samples and keep stage row and segment in requests',async()=>{
 const view=harness(),calls:string[]=[];const Plot=()=>null;
 const props={record,gateway:{get:async(path:string)=>{calls.push(path);return archived;}},renderResult:()=>null,Plot};
 let tree=view.render(props);await button(tree,'查看脉冲样点').onClick();tree=view.render(props);
 assert.deepEqual(calls,['/science/records/irb/pulse-samples']);
 const plots=all(tree).filter(n=>n.type===Plot);assert.equal(plots.length,2);
 assert.deepEqual(plots[0].props.series.map((s:any)=>s.y),[archived.samples.i,archived.samples.q]);assert.deepEqual(plots[1].props.series[0].y,archived.samples.rf);
 assert.match(text(tree),/上传命令记录.*匹配/);assert.match(text(tree),/未回读设备内部波形/);
 const links=all(tree).filter(n=>n.type==='a');assert.equal(links.length,3);assert.equal(links[0].props.href,'/api/artifacts/raw%20%2F%201');
 const select=(label:string)=>all(tree).find(n=>n.props['aria-label']===label)!.props;
 await select('脉冲序列行').onChange({target:{value:'3'}});tree=view.render(props);assert.match(calls.at(-1)!,/stage=verification&rowId=3$/);
 await select('脉冲分段').onChange({target:{value:'xy1'}});assert.match(calls.at(-1)!,/stage=verification&rowId=0&segmentId=xy1$/);
});

test('legacy missing baseband exposes archive links without synthesizing plots',async()=>{
 const view=harness(),Plot=()=>null;
 const props={record,gateway:{get:async()=>({...archived,status:'unavailable',reason:'该归档未保存载频前 I/Q 样点。',samples:null})},renderResult:()=>null,Plot};
 let tree=view.render(props);await button(tree,'查看脉冲样点').onClick();tree=view.render(props);
 assert.equal(all(tree).filter(n=>n.type===Plot).length,0);assert.match(text(tree),/未保存载频前/);assert.equal(all(tree).filter(n=>n.type==='a').length,3);
});

test('an old record with no compiled row can show its missing-evidence reason without crashing',async()=>{
 const view=harness();const props={record,gateway:{get:async()=>({...archived,status:'unavailable',selected:null,segments:[],rows:[],samples:null,artifacts:[],reason:'该归档未保存载频前 I/Q 样点。'})},renderResult:()=>null,Plot:()=>null};
 let tree=view.render(props);await button(tree,'查看脉冲样点').onClick();tree=view.render(props);
 assert.match(text(tree),/未保存载频前/);assert.equal(all(tree).some(n=>n.props['aria-label']==='脉冲分段'),false);
});

test('the actual backend fixture response renders without remapping archive samples',{skip:!existsSync(new URL('../qa-artifacts/r19-p102-contract-fixture.json',import.meta.url))&&'Generated QA artifact excluded from source release'},async()=>{
 const response=JSON.parse(readFileSync(new URL('../qa-artifacts/r19-p102-contract-fixture.json',import.meta.url),'utf8'));
 assert.equal(response.testFixtureOnly,true);
 const view=harness(),Plot=()=>null,props={record:{...record,id:response.recordId},gateway:{get:async()=>response},renderResult:()=>null,Plot};
 let tree=view.render(props);await button(tree,'查看脉冲样点').onClick();tree=view.render(props);
 const plots=all(tree).filter(n=>n.type===Plot);assert.equal(plots.length,2);
 assert.deepEqual(plots[0].props.series[0].y,[0,.1,.2,0]);assert.deepEqual(plots[0].props.series[1].y,[0,-.1,0,0]);
 assert.deepEqual(plots[1].props.series[0].y,[.1,.2,.1,0]);
 assert.equal(all(tree).filter(n=>n.type==='a').length,response.artifacts.length);
 assert.ok(text(tree).includes(response.segment.basebandI.sha256));
});

test('a response arriving after returning to the original record cannot replace its evidence',async()=>{
 const view=harness();let finish!:(value:any)=>void;let count=0;
 const source={...record,id:'source',result:{procedureId:'sq_drag_half_scan'}};
 const props={record,gateway:{get:()=>++count===1?Promise.resolve(source):new Promise(resolve=>{finish=resolve;})},renderResult:()=>null,Plot:()=>null};
 let tree=view.render(props);await button(tree,'打开系数扫描与复测').onClick();tree=view.render(props);
 const pending=button(tree,'查看脉冲样点').onClick();tree=view.render(props);button(tree,'返回门表征记录').onClick();
 finish({...archived,recordId:'source'});await pending;tree=view.render(props);
 assert.equal(all(tree).filter(n=>n.type==='a').length,0);assert.ok(button(tree,'打开系数扫描与复测'));
});
