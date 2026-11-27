import assert from 'node:assert/strict';
import test from 'node:test';
import {biasConfirmation, biasSheetConfirmed, biasSheetComplete, updateBiasSheet} from '../src/lib/biasRoutes.ts';
import type {BiasDeployment, BiasSheet} from '../src/lib/biasRoutes.ts';

test('editing an LF sheet removes its previous runnable route without altering other resources',()=>{
  const base:BiasDeployment={biasRoutes:{Q11:{resourceId:'Q11'},Q18:{resourceId:'Q18'}}};
  const fields={endSettingV:'0'};
  const next=updateBiasSheet(base,'Q11',{fields});
  assert.equal(biasSheetComplete(fields),false);
  assert.deepEqual(next.biasRoutes,{Q18:{resourceId:'Q18'}});
  assert.equal(next.biasRouteDrafts?.Q11.fields.endSettingV,'0');
  assert.deepEqual(updateBiasSheet(next,'Q11').biasRouteDrafts,{});
  assert.deepEqual(base.biasRoutes?.Q11,{resourceId:'Q11'});
});

test('confirmation is tied to exact fields, target and the current physical wiring',()=>{
  const deployment:BiasDeployment={machine:'测试',instrument:{host:'fixture',port:7110},routes:{Q11:{xyPhysical:[3,2]}}};
  const fields={board:'0',endSettingV:'0'};const sheet:BiasSheet={fields,confirmation:biasConfirmation(deployment,'Q11',fields)};
  assert.equal(biasSheetConfirmed(deployment,'Q11',sheet),true);
  assert.equal(biasSheetConfirmed(deployment,'Q18',sheet),false);
  assert.equal(biasSheetConfirmed({...deployment,routes:{Q11:{xyPhysical:[2,2]}}},'Q11',sheet),false);
  assert.equal(biasSheetConfirmed(deployment,'Q11',{...sheet,fields:{...fields,endSettingV:'1'}}),false);
});
