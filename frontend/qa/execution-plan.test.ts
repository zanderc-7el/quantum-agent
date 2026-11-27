import test from 'node:test';
import assert from 'node:assert/strict';
import { executionDefaultLabel } from '../src/lib/executionPlan.ts';
import { buildManualParameterFields } from '../src/lib/manualParameters.ts';
import type { NodeDetail } from '../src/types.ts';
const field=buildManualParameterFields({span_hz:{min:1e5,max:1e8}})[0];
const detail={parameters:{},executionDefaults:{span_hz:4e6},executionDefaultsSource:'adapter.build_plan',resolvedExecutionPlan:{status:'ready'}} as NodeDetail;
test('actual resolved default is converted for display without modifying saved parameters',()=>{
 const before=JSON.stringify(detail);assert.equal(executionDefaultLabel(detail,field),'执行默认：4 MHz');assert.equal(JSON.stringify(detail),before);
});
test('unavailable plans and explicit parameters never masquerade as defaults',()=>{
 assert.equal(executionDefaultLabel({...detail,parameters:{span_hz:6e6}},field),'');
 assert.equal(executionDefaultLabel({...detail,resolvedExecutionPlan:{status:'unavailable',source:'adapter.build_plan'}},field),'');
 assert.equal(executionDefaultLabel({...detail,executionDefaultsSource:'example'},field),'');
 assert.equal(executionDefaultLabel({...detail,executionDefaults:{span_hz:NaN}},field),'');
});
