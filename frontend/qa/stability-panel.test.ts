import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';
const compiled=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ScienceWorkbench.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
function harness(){
 const scopes=new Map<string,any[]>();let slots:any[]=[],cursor=0;const effects:(()=>void)[]=[];
 const hooks={...React,useState(initial:any){const n=cursor++,own=slots;own[n]??={value:typeof initial==='function'?initial():initial};return [own[n].value,(value:any)=>{own[n].value=typeof value==='function'?value(own[n].value):value;}];},useRef(v:any){return slots[cursor++]??={current:v};},useMemo(fn:any){cursor++;return fn();},useCallback(fn:any){cursor++;return fn;},useEffect(fn:any,deps:any[]){const n=cursor++,own=slots;if(!own[n]||deps.some((v,i)=>v!==own[n].deps[i])){own[n]?.cleanup?.();own[n]={deps};effects.push(()=>own[n].cleanup=fn());}}};
 const mod={exports:{} as any},require=createRequire(import.meta.url);new Function('require','module','exports','setInterval','clearInterval',compiled)((id:string)=>id==='react'?hooks:require(id),mod,mod.exports,()=>0,()=>{});
 const renderNode=(node:any,key:string)=>{slots=scopes.get(key)??[];scopes.set(key,slots);cursor=0;const tree=node.type(node.props);while(effects.length)effects.shift()!();return tree;};
 return {render:(props:any)=>renderNode(mod.exports.default(props),'workbench'),renderNode};
}
const all=(tree:any):any[]=>tree?.props?[tree,...[tree.props.children].flat(Infinity).flatMap(all)]:[];
const text=(tree:any):string=>typeof tree==='string'||typeof tree==='number'?String(tree):tree?.props?[tree.props.children].flat(Infinity).map(text).join(''):'';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('actual stability form rejects short intervals and invalid integer ranges before any write',async()=>{
 const view=harness(),writes:any[]=[];const props={workflow:{id:'TEST_ONLY-source',target:'Q11',name:'TEST_ONLY',resourceIds:['Q11'],nodes:[]},gateway:{get:async(path:string)=>path==='/stability'?{schedules:[]}:path.includes('/overview')?{}:{records:[]},write:async(...args:any[])=>{writes.push(args);return {id:'new',state:'draft',rounds:[]};}}};
 let tree=view.render(props);all(tree).find(n=>n.type==='button'&&n.props.children==='长期稳定性').props.onClick();tree=view.render(props);const panel=all(tree).find(n=>n.type?.name==='StabilityPanel');let content=view.renderNode(panel,'stability');await tick();
 const render=()=>content=view.renderNode(panel,'stability');
 const field=(label:string)=>all(content).find(n=>n.props.label===label)!.props;
 const save=()=>all(content).find(n=>n.type==='button'&&n.props.children==='保存周期计划')!.props;
 for(const [label,value] of [['轮次间隔',.01],['最大轮次',2.5],['精扫频宽',999],['频率点数',162]] as const){
  render();const old=field(label).value;field(label).onChange(value);render();assert.equal(save().disabled,true,label);await save().onClick();assert.equal(writes.length,0,label);field(label).onChange(old);
 }
 render();assert.equal(field('频率点数').max,161);assert.equal(field('频率点数').step,1);
 field('轮次间隔').onChange(1/60);field('频率点数').onChange(4);render();await save().onClick();
 assert.equal(writes.length,1);assert.equal(writes[0][1].intervalSeconds,60);assert.equal(writes[0][1].points,4);
});

test('nonempty stability plot preserves missing round and labels the scaled frequency unit',async()=>{
 const view=harness();const schedule={id:'TEST_ONLY-schedule',resourceId:'Q11',state:'completed',rounds:[0,1,2].map(i=>({number:i+1,startedAt:`2026-11-26T0${i}:00:00Z`,driftHz:i===1?null:i*1e6,t1Seconds:i===1?null:40e-6}))};
 const props={workflow:{id:'TEST_ONLY-source',target:'Q11',resourceIds:['Q11'],nodes:[]},gateway:{get:async(path:string)=>path==='/stability'?{schedules:[schedule]}:path.includes('/overview')?{}:{records:[]}}};
 let tree=view.render(props);all(tree).find(n=>n.type==='button'&&n.props.children==='长期稳定性').props.onClick();tree=view.render(props);
 const panel=all(tree).find(n=>n.type?.name==='StabilityPanel');view.renderNode(panel,'stability');await tick();let content=view.renderNode(panel,'stability');
 all(content).find(n=>n.props['aria-label']==='已有周期计划').props.onChange({target:{value:'TEST_ONLY-schedule'}});content=view.renderNode(panel,'stability');
 const plot=all(content).find(n=>n.props.label==='频率漂移');const rendered=view.renderNode(plot,'plot');
 assert.equal(all(rendered).find(n=>n.type==='text'&&n.props.x===58).props.children,'MHz');
 const d=all(rendered).find(n=>n.type==='path').props.d;assert.equal((d.match(/M/g)||[]).length,2);assert.equal((d.match(/L/g)||[]).length,0);
 assert.deepEqual(plot.props.series[0].y,[0,null,2e6]);
});

test('new policy shows acquisition completion while an unacquired queued round has no invented time',async()=>{
 const view=harness(),writes:any[]=[];const schedule={id:'TEST_ONLY-clock',resourceId:'Q11',state:'running',clockPolicy:'first_frequency_acquisition_completed_v1',nextDueAt:null,rounds:[
  {number:1,startedAt:'2040-01-01T00:00:00Z',intervalAnchor:{acquiredAt:'2040-01-01T08:00:00Z'},driftHz:0,t1Seconds:40e-6},
  {number:2,startedAt:'2040-01-01T11:00:00Z',intervalAnchor:null,driftHz:null,t1Seconds:null}]};
 const props={workflow:{id:'TEST_ONLY-source',target:'Q11',resourceIds:['Q11'],nodes:[]},gateway:{get:async(path:string)=>path==='/stability'?{schedules:[schedule]}:path.includes('/overview')?{}:{records:[]},write:async(...args:any[])=>writes.push(args)}};
 let tree=view.render(props);all(tree).find(n=>n.type==='button'&&n.props.children==='长期稳定性').props.onClick();tree=view.render(props);
 const panel=all(tree).find(n=>n.type?.name==='StabilityPanel');view.renderNode(panel,'clock');await tick();let content=view.renderNode(panel,'clock');
 all(content).find(n=>n.props['aria-label']==='已有周期计划').props.onChange({target:{value:schedule.id}});content=view.renderNode(panel,'clock');
 assert.match(text(content),/间隔从每轮定频首测采集完成时起算/);assert.match(text(content),/尚无采集时间/);assert.match(text(content),/待首测采集完成/);
 const plot=all(content).find(n=>n.props.label==='频率漂移');assert.equal(plot.props.series[0].x[0],0);assert.ok(Number.isNaN(plot.props.series[0].x[1]));assert.equal(writes.length,0);
 for(const label of ['频率漂移','本轮 T₁ 趋势']){
  const item=all(content).find(n=>n.props.label===label);assert.ok(item,label);
  // A recorded value without an authenticated time still cannot acquire a
  // plotted position or a hover target.
  const checked={...item,props:{...item.props,cursor:1,series:item.props.series.map((s:any)=>({...s,y:[s.y[0],1]}))}};
  const chart=view.renderNode(checked,`clock-plot-${label}`);
  assert.equal(all(chart).filter(n=>n.type==='rect'&&n.props.onMouseEnter).length,1);
  assert.equal(all(chart).filter(n=>n.type==='circle').length,1);
  for(const element of all(chart))for(const key of ['x','y','cx','cy','x1','x2','y1','y2'])if(typeof element.props[key]==='number')assert.ok(Number.isFinite(element.props[key]),`${label} ${element.type}.${key}`);
  assert.doesNotMatch(text(chart),/第 2 点/);
 }
});
