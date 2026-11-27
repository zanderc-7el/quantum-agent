import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const components=new Map<string,Parameters<typeof createElement>[0]>();
function render(name:string,props:Record<string,unknown>){
 if(!components.has(name)){const compiled=buildSync({entryPoints:[fileURLToPath(new URL(`../src/components/${name}.tsx`,import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
 const module={exports:{} as {default:Parameters<typeof createElement>[0]}};new Function('require','module','exports',compiled)(createRequire(import.meta.url),module,module.exports);components.set(name,module.exports.default);}
 return renderToStaticMarkup(createElement(components.get(name)!,props));
}
test('actual create parameter form marks zero step invalid beside that field',()=>{
 const markup=render('ManualCreationParameters',{procedureId:'sq_rabi_amplitude',bounds:{amp_start:{min:0,max:1},amp_stop:{min:0,max:1},points:{min:3,max:161},drive_length_s:{min:1e-9,max:1e-6},shots:{min:10,max:2047}},values:{amp_start:'0',amp_stop:'.6',scan_step:'0'},onChange(){},disabled:false});
 assert.match(markup,/aria-label="扫描步进"[^>]*aria-invalid="true"/);assert.match(markup,/id="manual-step-error"[^>]*>扫描步进必须大于零/);
});
test('actual topology renders only declared design geometry and explicitly states missing evidence',()=>{
 const markup=render('ChipTopology',{scope:'chip1',topology:{nodes:[{resourceId:'Q1',x:1,y:3},{resourceId:'Q2',x:2,y:5}],edges:[]}});
 assert.match(markup,/选择比特 Q1/);assert.match(markup,/选择比特 Q2/);assert.doesNotMatch(markup,/<line/);
 assert.match(render('ChipTopology',{scope:'chip1'}),/尚未提供有来源/);
});
test('actual Rabi principle preserves observed values, raw file/hash and time units',()=>{
 const markup=render('RabiPrinciple',{workflowId:'wf-test',node:{procedureId:'sq_rabi_time',resourceId:'Q1'},axis:'q.pi_len',operationId:'op-test',point:{operationId:'op-test',axes:{'q.pi_len':4e-8},meanI:.3,meanQ:-.4,meanMagnitude:.5,shots:1200,pointFile:'origin.json',pointSha256:'hash-test'}});
 for(const text of ['实验原理示意','脉冲时长','μs','0.04','I=0.3','Q=-0.4','未换算为激发态概率','origin.json','hash-test'])assert.ok(markup.includes(text),text);
});
test('actual missing signal path remains unverified rather than drawing invented components',()=>{
 const markup=render('SignalPaths',{scope:'station1'});assert.match(markup,/尚未录入有来源的线路图/);assert.doesNotMatch(markup,/<li/);
});
