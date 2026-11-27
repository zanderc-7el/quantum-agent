import test from 'node:test';
import assert from 'node:assert/strict';
import {validateDecision} from '../src/validation.mjs';
const input={allowedActions:['retry'],observations:{parameters:{drive_length_s:375e-9,acquisitions_per_point:16,points:81,shots:2000,capture_repeats:2,amp_stop:.99}},
 parameterBounds:{drive_length_s:{min:376e-9,max:2e-6},acquisitions_per_point:{min:1,max:16,integer:true},points:{min:9,max:161,integer:true},shots:{min:16,max:2000,integer:true},capture_repeats:{min:2,max:6,integer:true},amp_stop:{min:0,max:.99}},
 smartRabiWindowScope:{schema:'qcal-smart-rabi-window/1',proposalPolicy:'fixed_dc_window_actual_changes_v2',currentParameters:{drive_length_s:375e-9,acquisitions_per_point:16,points:81,shots:2000,capture_repeats:2,amp_stop:.99},sampleQuantumS:.5e-9}};
const decision=changes=>({action:'retry',reason:'基于当前失败记录延长门长，采样降为8块',evidenceIds:[],parameterChanges:changes});
const valid={drive_length_s:2e-6,acquisitions_per_point:8,points:81,shots:2000,capture_repeats:2};
test('real rejected R36 proposal with unchanged echoes is accepted as a proposal',()=>assert.deepEqual(validateDecision(decision(valid),input),decision(valid)));
test('tool rejects actual multivariable changes and stale sixteen-block window',()=>{
 for(const delta of [{points:41},{shots:1000},{capture_repeats:3},{amp_stop:.98},{acquisitions_per_point:16}])
  assert.throws(()=>validateDecision(decision({...valid,...delta}),input),/rabi_window_/);
 assert.throws(()=>validateDecision(decision({...valid,drive_length_s:750.25e-9}),input),/rabi_window_/);
 assert.throws(()=>validateDecision(decision({drive_length_s:2e-6}),input),/rabi_window_/);
 assert.throws(()=>validateDecision(decision({...valid,shots:true}),input),/parameter_bounds/);
});
test('ordinary no-width retry stays on existing bounds path',()=>{
 const ordinary=decision({points:101});assert.deepEqual(validateDecision(ordinary,input),ordinary);
 assert.deepEqual(validateDecision(decision(valid),{...input,smartRabiWindowScope:undefined}),decision(valid));
});
test('unchanged width echo does not turn ordinary retry into a new-window request',()=>{
 const ordinary=decision({drive_length_s:375e-9,shots:1000,acquisitions_per_point:8});
 const currentBounds={...input,parameterBounds:{...input.parameterBounds,drive_length_s:{min:375e-9,max:2e-6}}};
 assert.deepEqual(validateDecision(ordinary,currentBounds),ordinary);
});
