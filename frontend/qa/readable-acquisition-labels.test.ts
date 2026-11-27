import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

function load(name:string, buffer?:unknown) {
  let state=0;
  const hooks=buffer===undefined?React:{...React,useState(initial:any){const index=state++;return [index===0?buffer:typeof initial==='function'?initial():initial,()=>{}];},useMemo(fn:any){return fn();},useEffect(){}};
  const code=buildSync({entryPoints:[fileURLToPath(new URL(`../src/components/${name}.tsx`,import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react/jsx-runtime'],loader:{'.css':'empty'}}).outputFiles[0].text;
  const module={exports:{} as any},require=createRequire(import.meta.url);
  new Function('require','module','exports',code)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
  return module.exports;
}
const main=(html:string)=>html.replace(/<details\b[^>]*>[\s\S]*?<\/details>/g,'');

test('measurement progress keeps operation messages and unknown fields only in existing details',()=>{
  const {MeasurementProgress}=load('RemotePanels');
  const node={attempt:3,telemetry:{jobIndex:1,jobCount:2,completedRows:4,totalRows:20,
    message:'op_df5c123456:4/20',currentAxes:{capture:1,axes:{'exp.cir.default':'off','internal.unrecognized':'opaque'}}}};
  const before=JSON.stringify(node);
  const html=renderToStaticMarkup(React.createElement(MeasurementProgress,{node}));
  assert.doesNotMatch(main(html),/op_df5c|cir\.default|opaque/);
  assert.match(main(html),/采集轮次/);assert.match(main(html),/扫描点/);
  assert.match(html,/op_df5c123456/);assert.equal(JSON.stringify(node),before);
});

test('live groups have numbered readable labels and exact on/off names without exposing native fields',()=>{
  const points=['off','on','unknown-sequence'].map((condition,index)=>({operationId:'op_df5c123456',jobId:'native-job',jobName:'op_df5c123456',
    capture:0,pointIndex:index,baseIndex:index,axes:{'cfg.Q1.para.drivFreq':4.8e9,'exp.cir.default':condition,'internal.unrecognized':'opaque'},
    meanI:.1,meanQ:.2,meanMagnitude:.3,meanAbs:.3,shots:20,pointSha256:'fixture',pointFile:'fixture.json',cursor:index+1}));
  const buffer={operationId:'op_df5c123456',cursor:3,points};const before=JSON.stringify(buffer);
  const Component=load('LiveMeasurementSeries',buffer).default;
  const html=renderToStaticMarkup(React.createElement(Component,{gateway:{},workflowId:'fixture',node:{id:'node',procedureId:'sq_spectroscopy'}}));
  const visible=main(html);assert.doesNotMatch(visible,/op_df5c|cir\.default|unknown-sequence|opaque/);
  assert.match(visible,/采集曲线 1/);assert.match(visible,/驱动关闭/);assert.match(visible,/驱动开启/);
  assert.match(html,/op_df5c123456/);assert.match(html,/原始数据与来源/);assert.equal(JSON.stringify(buffer),before);
});

test('generic Raw trace labels become readable without changing the result',()=>{
  const {RealResult}=load('RemotePanels');
  const result={dataKind:'real',metrics:{},quality:{status:'unverified'},artifacts:[],series:[{name:'Raw trace 1',x:[0,1],y:[.1,.2],xUnit:'Hz',yUnit:'ADC'}]};
  const before=JSON.stringify(result);const html=renderToStaticMarkup(React.createElement(RealResult,{result}));
  assert.match(main(html),/采集曲线 1/);assert.doesNotMatch(main(html),/Raw trace/);assert.equal(JSON.stringify(result),before);
});
