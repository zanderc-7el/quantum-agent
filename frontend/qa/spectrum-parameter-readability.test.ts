import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSync } from 'esbuild';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readableParameter } from '../src/lib/manualParameters.ts';
import { engineLabel } from '../src/lib/runtimeLabels.ts';

test('native f01 geometry formats the sampled quantities once with the method-specific point unit', () => {
  const source = { acquisitions_per_point: 8, capture_repeats: 2, points: 21, shots: 2000, span_hz: 5000000,
    spectroscopy_width_s: 8e-6, readout_hz: 7009676666.666667, pulse_compatibility: 'f8_validated_short_half_dc_buff2ns_v1' };
  const rows = Object.entries(source).map(([k,v]) => readableParameter(k,v,undefined,'sq_spectroscopy',true));
  assert.deepEqual(rows.map(row => row!.value),['8 块/点','2 次','21 个频点','2000 次/块','5 MHz','8 µs',
    '7.009676666666667 GHz','π/2 与 π 同幅，时长减半']);
  assert.equal(readableParameter('points',21,undefined,'sq_rabi_time')?.value,'21 点');
  assert.deepEqual(readableParameter('shots',2000),{label:'采样次数',value:'2000 次'});
  assert.equal(readableParameter('span_hz',5,'MHz')?.value,'5 MHz');
  assert.equal(readableParameter('spectroscopy_width_s',8000,'ns')?.value,'8000 ns');
  assert.equal(readableParameter('drive_hz',4.85905,'GHz')?.value,'4.85905 GHz');
  assert.equal(engineLabel('plan_rejected'),'采集方案预检未通过');
});

test('unknown controls or policies only appear in the closed technical section with original bytes and units', () => {
  const opts=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ReadableParameterSummary.tsx',import.meta.url))],
    bundle:true,write:false,packages:'external',platform:'node',format:'cjs',jsx:'automatic'});
  const mod={exports:{} as {default:(props:{value:unknown,procedure:string})=>ReturnType<typeof createElement>}};
  new Function('require','module','exports',opts.outputFiles[0].text)(createRequire(import.meta.url),mod,mod.exports);
  const data={Q11:{span_hz:{value:5,unit:'MHz'}},private_driver_hz:123456,spectroscopy_width_s:8e-6,pulse_compatibility:'future_policy',
    mystery:{drive_hz:5e9},rb_lengths:[1,2,4]};
  const html=renderToStaticMarkup(createElement(mod.exports.default,{value:data,procedure:'sq_spectroscopy'}));
  const visible=html.split('<details>')[0];
  assert.match(visible,/Q11 · 频率扫描跨度/);assert.match(visible,/5 MHz/);assert.match(visible,/1、2、4/);
  assert.doesNotMatch(visible,/private_driver|future_policy|5 GHz|span_hz|<pre/);
  for (const key of ['future_policy','private_driver_hz','mystery','span_hz']) assert.ok(html.includes(key));
  assert.doesNotMatch(html,/<details open/);
});

