import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const condition=(counts:number[][],perCaptureFidelity:Record<string,number>)=>({counts,n0:counts[0].reduce((a,b)=>a+b),n1:counts[1].reduce((a,b)=>a+b),fidelity:1-.5*(counts[0][1]/counts[0].reduce((a,b)=>a+b)+counts[1][0]/counts[1].reduce((a,b)=>a+b)),standardErrorBinomial:.01,perCaptureFidelity});
const acquisition=()=>({comparison:{off:condition([[180,20],[10,190]],{'2':.92,'3':.93}),on:condition([[196,4],[8,192]],{'2':.96,'3':.98})},classifierProtocol:{type:'separate condition LDA; equal priors; fixed training/validation capture split',trainingCaptures:[0,1],validationCaptures:[2,3],logicalLabelsRetained:true},pulseIdentities:{probe12:{identity:{frequencyHz:4.6e9,amplitude:.2,durationS:20e-9,shape:'Gaussian'}}},metrics:{assignment_gain_pp:4.5,paired_capture_gain_stderr_pp:.5}});
function render(extra:any={}){
 const compiled=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ScienceShelveResult.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external'}).outputFiles[0].text;
 const module={exports:{} as any};new Function('require','module','exports',compiled)(createRequire(import.meta.url),module,module.exports);
 const result={procedureId:'sq_shelve_readout_compare',source:'acquisition',dataKind:'real',shelveStage:'verification',quality:{status:'passed'},...acquisition(),...extra};
 return renderToStaticMarkup(React.createElement(module.exports.default,{result}));
}
test('real SHELVE renders heldout confusion counts, errors, fidelity, paired gain and separate acquisition stages',()=>{
 const html=render({shelveSelection:{...acquisition(),acquiredAt:'2026-11-26T00:00:00Z'}});
 for(const text of ['首测','独立复测','关闭 SHELVE','启用 SHELVE','制备 |0⟩','判为 1','180','196','10.00%','5.00%','92.50%','97.00%','4.50 个百分点','0.50 个百分点','训练采集：1、2','验证采集：3、4','4.600000 GHz','20.000 ns'])assert.ok(html.includes(text),text);
 assert.ok(html.includes('不是 |2⟩ 布居、转移保真度或泄漏测量'));
 assert.ok(!html.includes('输入计数计算'));
});
test('legacy metrics alone never fabricate missing counts, split or first acquisition',()=>{
 const html=render({comparison:undefined,classifierProtocol:undefined,pulseIdentities:undefined});
 assert.ok(html.includes('缺少完整分类计数'));
 assert.ok(!html.includes('<table'));
 assert.ok(!html.includes('训练采集：1'));
});
test('negative improvement and failed quality are retained, never relabeled as a pass',()=>{
 const a=acquisition();const html=render({comparison:{off:a.comparison.on,on:a.comparison.off},metrics:{assignment_gain_pp:-4.5},quality:{status:'failed'}});
 assert.ok(html.includes('-4.50 个百分点'));assert.ok(html.includes('未通过'));
 assert.ok(!html.includes('独立复测通过'));
});
test('unverified or partial results keep explicit limited status and malformed counts degrade safely',()=>{
 const html=render({partial:true,quality:{status:'unverified'}});
 assert.ok(html.includes('未完成'));assert.ok(!html.includes('独立复测通过'));
 const missing=render({comparison:{off:{counts:[[1,NaN],[2,3]]},on:{counts:[[2,3],[3,4]]}}});
 assert.ok(missing.includes('缺少完整分类计数'));assert.ok(!missing.includes('NaN'));
});
test('manual calculation or a different procedure cannot acquire a real SHELVE display',()=>{
 assert.equal(render({procedureId:undefined,source:'provided_counts',dataKind:'provided_data'}),'');
 assert.equal(render({procedureId:'sq_rabi_time'}),'');
});
