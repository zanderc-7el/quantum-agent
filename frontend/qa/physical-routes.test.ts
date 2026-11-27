import test from 'node:test';
import assert from 'node:assert/strict';
import {canConfirmXYPassband,validXYPassbandSource,updateXYPassband,addPhysicalRoute} from '../src/lib/physicalRoutes.ts';

test('XY source placeholders match the backend exact-value policy without rejecting report titles',()=>{
 for(const source of ['unknown','unconfirmed','pending','tbd','todo','n/a','na','none','null','test','example','placeholder','未提供','暂无','未知','待确认','待核实','待填写','待补充','待定','不详','无','无资料','无来源','已确认','确认','占位','示例','测试']){
  assert.equal(validXYPassbandSource(source),false,source);assert.equal(validXYPassbandSource(` ${source.toUpperCase()} `),false,source);
 }
 assert.equal(validXYPassbandSource('实测未知频段报告，2026-11-26，第 2 页'),true);
 assert.equal(validXYPassbandSource('Ｔ Ｂ Ｄ'),false);assert.equal(validXYPassbandSource('!!!'),false);
 assert.equal(validXYPassbandSource('𠀀'.repeat(2000)),true);assert.equal(validXYPassbandSource('𠀀'.repeat(2001)),false);
 assert.equal(canConfirmXYPassband({xyPassbandHz:[3e9,6e9],xyPassbandSource:'扫描文件 sweep_001.dat'}),true);
});

test('no GHz conversion can place NaN or infinity into a JSON route payload',()=>{
 const route={xyPassbandHz:[3e9,6e9],xyPassbandSource:'原始文件',xyPassbandConfirmed:true};
 for(const text of ['NaN','Infinity','-Infinity','1e309','1e308','abc','']){
  const next=updateXYPassband(route,0,text);assert.deepEqual(next.xyPassbandHz,[null,6e9]);assert.equal(next.xyPassbandConfirmed,false);
  assert.deepEqual(JSON.parse(JSON.stringify(next)),next);assert.deepEqual(route.xyPassbandHz,[3e9,6e9]);
 }
 const routes={Q11:route};assert.equal(addPhysicalRoute(routes,' Q11 '),routes);assert.equal(addPhysicalRoute(routes,''),routes);
});
