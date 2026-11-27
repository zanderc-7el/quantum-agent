import test from 'node:test';
import assert from 'node:assert/strict';
import {scienceCatalogPath,scienceParameters,scienceParameterValues} from '../src/lib/scienceExecutionDraft.ts';

const method:any={procedureId:'sq_drag_irb',parameterBounds:{rb_lengths:{type:'integerArray',minItems:5,maxItems:12,min:1,max:512,strictlyIncreasing:true,default:[1,4,16,64,128]}}};
test('IRB requests an explicit distinct profile and bounded integer array',()=>{
 assert.match(scienceCatalogPath({sourceWorkflowId:'source',resourceId:'Q11'},'science-v3-irb')!,/executionProfile=science-v3-irb/);
 const values=scienceParameterValues(method);assert.equal(values.rb_lengths,'1, 4, 16, 64, 128');
 assert.deepEqual(scienceParameters(method,values),{rb_lengths:[1,4,16,64,128]});
 for(const value of ['1,4,16,64','1,4,16,16,128','1,4,16,64,513','true,4,16,64,128','[1,4,16,64,128]','1,4,16,64,128.1'])assert.throws(()=>scienceParameters(method,{rb_lengths:value}));
});
