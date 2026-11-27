import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { buildSync } from 'esbuild';
import { fileURLToPath } from 'node:url';
import * as React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import type {Workflow} from '../src/types.ts';

function component(name: string) {
  const {outputFiles} = buildSync({entryPoints:[fileURLToPath(new URL('../src/components/'+name+'.tsx', import.meta.url))],
    bundle:true,write:false,platform:'node',format:'cjs',external:['react','react/jsx-runtime'],loader:{'.css':'empty'}});
  const module={exports:{} as any};
  new Function('require','module','exports',outputFiles[0].text)(createRequire(import.meta.url),module,module.exports);
  return module.exports;
}
type Event = Workflow['events'][number];
const op1='op_99fad891aa12b4dd',op2='op_a41ffaa88b8b1cfd';
const event=(sec:number,msg:string,tech?:Record<string,unknown>):Event=>(
  {time:'2026-11-26T18:51:'+String(sec).padStart(2,'0')+'Z',message:msg,kind:'info',...(tech?{technicalDetails:tech}:{})});
function shown(events: Event[]) {
  const html=renderToStaticMarkup(React.createElement(component('WorkflowActivity').default,{events}));
  return {html, summary:html.split('<details')[0]};
}
test('consecutive operation rows collapse in the human view; all original timestamps remain available',()=>{
  const msg=op1+': 86/122 点；任务 1/1', rows=[event(10,msg),event(11,msg)];
  const before=JSON.stringify(rows), {html,summary}=shown(rows);
  assert.equal((summary.match(/86\/122/g)??[]).length,1);
  assert.doesNotMatch(summary,/op_99fa/);assert.match(summary,/已采集/);
  assert.match(html,/18:51:10Z/);assert.match(html,/18:51:11Z/);
  assert.match(html,/op_99fad891aa12b4dd/);assert.equal(JSON.stringify(rows),before);
});
test('different operations, job phases, nonadjacent repeats and generic messages remain separate',()=>{
  const msg=op1+': 86/122 点；任务 1/2';
  const records=[event(1,msg),event(2,msg.replace(op1,op2)),event(3,msg.replace('1/2','2/2')),
    event(4,'需要核查'),event(5,'需要核查'),event(6,msg),
    event(7,msg,{telemetry:{event:'verify'}}),event(8,msg,{telemetry:{event:'measure'}})];
  const {summary}=shown(records);
  assert.equal((summary.match(/86\/122/g)??[]).length,6);assert.equal((summary.match(/需要核查/g)??[]).length,2);
  const warnings=[event(1,msg),event(2,msg)].map(row=>({...row,kind:'warning' as const}));
  assert.equal((shown(warnings).summary.match(/op_99fa/g)??[]).length,2);
});
test('eight visible entries are selected after collapsing repeats and the tail stays newest first',()=>{
  const msg=op2+': 82/82 点；任务 1/1';
  const {summary}=shown([...Array.from({length:8},(_,i)=>event(i,'条目'+i)),event(8,msg),event(9,msg),event(10,msg)]);
  assert.equal((summary.match(/82\/82/g)??[]).length,1);
  assert.match(summary,/条目1/);assert.doesNotMatch(summary,/条目0/);
  assert.ok(summary.indexOf('82/82')<summary.indexOf('条目7'));
});
test('real result metric names are human readable with exact original keys available in technical details',()=>{
  const values={contrast_snr:9.54,r_squared:0.8599,cycles:3.1865,relative_stderr:.0141,amplitude_oscillation_frequency:3.319};
  const result={dataKind:'real',metrics:values,quality:{status:'failed',reasons:[]}};
  const html=renderToStaticMarkup(React.createElement(component('RemotePanels').RealResult,{result}));
  const summary=html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g,'');
  assert.doesNotMatch(summary,/contrast_snr|r_squared|relative_stderr|amplitude_oscillation_frequency/);
  assert.match(summary,/对比度信噪比/);assert.match(summary,/R²/);assert.match(summary,/3\.1865/);
  assert.match(html,/contrast_snr/);assert.match(html,/r_squared/);
});

