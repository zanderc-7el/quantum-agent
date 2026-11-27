import test from 'node:test';
import assert from 'node:assert/strict';
import {pulseFromCurrentParameters} from '../src/lib/scienceCurrentPulse.ts';

test('pulse uses current server parameters rather than historical node snapshots',()=>{
 const detail:any={node:{id:'Q11:s01',parameters:{readout_hz:7009510000}},workflowRevision:590,
  parameters:{readout_hz:7009610000,drive_hz:5e9,drive_amp:.3,drive_length_s:20e-9},parameterVersions:{readout_hz:4},
  resolvedExecutionPlan:{status:'ready',parameters:{readout_hz:7009510000,readout_width_s:2e-6}}};
 const value=pulseFromCurrentParameters(detail,'wf');
 assert.equal(value.readoutFrequencyHz,7009610000);assert.equal(value.durationNs,20);
 assert.equal(value.phaseDeg,null);assert.equal(value.readoutPhaseDeg,null);assert.equal(value.readoutAmplitude,null);
 assert.deepEqual(value.parameterSource.parameterVersions,{readout_hz:4});assert.equal(value.parameterSource.workflowRevision,590);
 assert.equal(value.parameterSource.source,'controller_current_parameters');
});
