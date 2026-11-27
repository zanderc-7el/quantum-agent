import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {buildSync} from 'esbuild';
import * as React from 'react';

const helper=new URL('../src/lib/stabilityReport.ts',import.meta.url);
test('report filters preserve calendar dates and encode scope without execution fields',async()=>{
 assert.ok(existsSync(helper),'stability range report query helper missing');
 const {stabilityReportPath}=await import(helper.href);
 assert.equal(stabilityReportPath({startDate:'2026-11-26',endDate:'2026-11-26',chipVersionId:'chip & b',resourceId:'Q11'}),'/stability/report?startDate=2026-11-26&endDate=2026-11-26&chipVersionId=chip+%26+b&resourceId=Q11');
 assert.match(stabilityReportPath({sourceWorkflowId:'wf/a'},'download'),/sourceWorkflowId=wf%2Fa&format=html&download=1$/);
 assert.throws(()=>stabilityReportPath({startDate:'2026-11-27',endDate:'2026-11-26'}));
});

function harness(){
 const file=new URL('../src/components/StabilityReportPanel.tsx',import.meta.url);
 assert.ok(existsSync(file),'stability report UI missing');
 const compiled=buildSync({entryPoints:[fileURLToPath(file)],bundle:true,write:false,platform:'node',format:'cjs',packages:'external'}).outputFiles[0].text;
 const slots:any[]=[];let cursor=0;const effects:(()=>void)[]=[];
 const hooks={...React,useState(initial:any){const n=cursor++;slots[n]??={value:typeof initial==='function'?initial():initial};return [slots[n].value,(v:any)=>slots[n].value=typeof v==='function'?v(slots[n].value):v];},useRef(value:any){return slots[cursor++]??={current:value};},useEffect(fn:any,deps:any[]){const n=cursor++;if(!slots[n]||deps.some((v,i)=>v!==slots[n].deps[i])){slots[n]?.cleanup?.();slots[n]={deps};effects.push(()=>slots[n].cleanup=fn());}}};
 const module={exports:{} as any};const require=createRequire(import.meta.url);
 new Function('require','module','exports',compiled)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
 return {render(props:any){cursor=0;const tree=module.exports.default(props);while(effects.length)effects.shift()!();return tree;}};
}
const all=(node:any):any[]=>node?.props?[node,...[node.props.children].flat(Infinity).flatMap(all)]:[];
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('report apply and export use same scope; dirty form does not silently change loaded report',async()=>{
 const view=harness(),calls:string[]=[];
 const empty={summary:{rounds:0,validFrequencyRounds:0,validT1Rounds:0},groups:[],choices:{chipVersionIds:['chip-a'],resourceIds:['Q11'],sourceWorkflowIds:['wf-a']},capturedAt:'2026-11-25T21:00:00Z',snapshotSha256:'abc',filters:{resourceId:'Q11'}};
 const props={resourceId:'Q11',gateway:{get:async(path:string)=>{calls.push(path);return empty;}}};
 let tree=view.render(props);await tick();tree=view.render(props);
 assert.ok(JSON.stringify(tree).includes('暂无周期记录'));
 const field=(name:string)=>all(tree).find(n=>n.props['aria-label']===name);
 field('报告开始日期').props.onChange({target:{value:'2026-11-26'}});
 tree=view.render(props);
 let download=all(tree).find(n=>n.type==='a'&&n.props.download);
 assert.ok(!download.props.href.includes('startDate'));
 all(tree).find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await tick();tree=view.render(props);
 assert.match(calls.at(-1)!,/startDate=2026-11-26/);
 download=all(tree).find(n=>n.type==='a'&&n.props.download);
 assert.match(download.props.href,/startDate=2026-11-26/);
 assert.ok(!calls.some(path=>path.includes('/actions')));
});

test('report renders Hz differences without dropping significant trailing zeroes',async()=>{
 const view=harness();const report={summary:{rounds:1,validFrequencyRounds:1,validT1Rounds:1},choices:{chipVersionIds:[],resourceIds:['Q11'],sourceWorkflowIds:[]},capturedAt:'2026-11-25T21:00:00Z',snapshotSha256:'sha',filters:{},groups:[{id:'g',resourceId:'Q11',rows:[{scheduleId:'s',scheduleName:'plan',number:1,state:'completed',startedAt:'2026-11-25T21:00:00Z',frequencyHz:4.802e9,adjacentDeltaHz:10000000,referenceDeltaHz:1e10,t1Seconds:40e-6,evidenceIssues:[]}]}]};
 const props={gateway:{get:async()=>report}};view.render(props);await tick();const tree=view.render(props);
 const cells=all(tree).filter(n=>n.type==='td').map(n=>n.props.children);
 assert.ok(cells.includes('10000000'));assert.ok(cells.includes('10000000000'));
});

test('changing resource clears stale draft and export scope before loading the new resource',async()=>{
 const view=harness(),calls:string[]=[];
 const report={summary:{rounds:0,validFrequencyRounds:0,validT1Rounds:0},groups:[],choices:{chipVersionIds:[],resourceIds:['Q11','Q12'],sourceWorkflowIds:[]},capturedAt:'2026-11-26T00:00:00Z',snapshotSha256:'TEST_ONLY',filters:{}};
 const gateway={get:async(path:string)=>{calls.push(path);return report;}};
 let props={resourceId:'Q11',gateway};view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.props['aria-label']==='报告开始日期').props.onChange({target:{value:'2026-11-26'}});
 props={resourceId:'Q12',gateway};view.render(props);tree=view.render(props);
 assert.equal(all(tree).find(n=>n.props['aria-label']==='报告比特').props.value,'Q12');
 assert.equal(all(tree).find(n=>n.props['aria-label']==='报告开始日期').props.value,'');
 assert.equal(all(tree).filter(n=>n.type==='a').length,0);
 await tick();tree=view.render(props);
 all(tree).find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await tick();
 assert.equal(calls.at(-1),'/stability/report?resourceId=Q12');
});
