import assert from 'node:assert/strict';
import test from 'node:test';
import { validateDecision } from '../src/validation.mjs';

const bounds = {type:'integerArray', minItems:5, maxItems:12, min:1, max:512, strictlyIncreasing:true};
const input = {allowedActions:['retry'], observations:[{id:'current'}], parameterBounds:{rb_lengths:bounds}};
const decision = lengths => ({action:'retry', reason:'缩短最长序列以适配当前门时长', evidenceIds:['current'],
  parameterChanges:{rb_lengths:lengths}});

test('same-gate IRB retry accepts only the controller-advertised ordered length grid', () => {
  const valid = decision([1,2,4,8,16]);
  assert.deepEqual(validateDecision(valid,input),valid);
  for (const invalid of [[1,2,3,4], [1,2,3,4,513], [1,2,3.5,4,5], [1,2,3,3,4], [1,3,2,4,5], '1,2,4,8,16']) {
    assert.throws(() => validateDecision(decision(invalid),input),/parameter_bounds:rb_lengths/);
  }
  assert.throws(() => validateDecision(valid,{...input,parameterBounds:{}}),/parameter_not_allowed/);
  assert.throws(() => validateDecision({...valid,evidenceIds:['old']},input),/unknown_evidence_id/);
});
