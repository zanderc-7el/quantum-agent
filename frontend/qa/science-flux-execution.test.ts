import test from 'node:test';
import assert from 'node:assert/strict';
import {scienceCatalogPath,scienceParameters,scienceParameterValues,scienceExecutionInput} from '../src/lib/scienceExecutionDraft.ts';
import {readWorkingPoint} from '../src/lib/scienceModels.ts';

test('flux arrays remain explicit finite typed values with route-provided bounds',()=>{
 const method:any={procedureId:'ro_flux_scan',parameterBounds:{
  bias_values_v:{type:'numberArray',label:'偏置寄存器设置',unit:'V',minItems:1,maxItems:21,min:-.1,max:.1,unique:true,required:true},
  readout_frequencies_hz:{type:'numberArray',minItems:5,maxItems:101,min:6.5e9,max:7.6e9,strictlyIncreasing:true,required:true}
 }};
 assert.match(scienceCatalogPath({sourceWorkflowId:'source',resourceId:'Q11'},'science-flux')!,/executionProfile=science-flux/);
 assert.deepEqual(scienceParameterValues(method),{bias_values_v:'',readout_frequencies_hz:''});
 assert.throws(()=>scienceParameters(method,{}),/偏置/);
 const values={bias_values_v:'-.1, 0, 1e-1',readout_frequencies_hz:'7.0, 7.001, 7.002, 7.003, 7.004'};
 assert.deepEqual(scienceParameters(method,values),{bias_values_v:[-.1,0,.1],readout_frequencies_hz:[7e9,7.001e9,7.002e9,7.003e9,7.004e9]});
 for(const invalid of ['0, 0','0, Infinity','true','[0]','.1001','0x01','0,,.1'])assert.throws(()=>scienceParameters(method,{...values,bias_values_v:invalid}),invalid);
 assert.throws(()=>scienceParameters(method,{...values,readout_frequencies_hz:'7,7.002,7.001,7.003,7.004'}));
 const provided={...method,parameterDefaults:{readout_frequencies_hz:[7e9,7.001e9]}};
 assert.equal(scienceParameterValues(provided).readout_frequencies_hz,'7, 7.001');
});

test('native working point opens only the dedicated bias profile using server-resolved values',()=>{
 const record:any={id:'point',kind:'working_point',scope:{workflowId:'scan-wf',resourceId:'Q11'},input:{sourceRecordId:'scan',bias:999},
  result:{fluxPointEvidence:{schema:'qcal-flux-point-evidence/1'},point:{sourceRecordId:'scan',purpose:'work',bias:.02,biasUnit:'V',fluxChannel:'confirmed-line',readoutFrequencyHz:7e9}}};
 const value=readWorkingPoint(record,'Q11');assert.equal(value.executionProfile,'science-flux');
 assert.equal(value.bias,.02);assert.equal(value.recordId,'point');assert.equal(value.workflowId,'scan-wf');
});

test('point execution binds only a native saved point to its selected scan source',()=>{
 const scope={chipVersionId:'chip',deploymentVersionId:'dep',resourceId:'Q11'};
 const source=(id:string,procedureId:string)=>({id,kind:'measurement',scope,dataKind:'real',reference:{},evidenceCurrent:true,evidenceIntact:true,provenance:{independentlyVerified:true},result:{procedureId,quality:{status:'passed'}}});
 const point={id:'point',kind:'working_point',scope,sourceRecordId:'scan',result:{fluxPointEvidence:{schema:'qcal-flux-point-evidence/1'}}};
 const method={procedureId:'sq_flux_point_recheck',capability:'available',scienceSourceRoles:['cavity_seed','flux_scan'],scienceSourceMethods:{cavity_seed:['ro_resonator_survey'],flux_scan:['ro_flux_scan']},parameterBounds:{}};
 const draft:any={name:'point',scope,executionProfile:'science-flux',catalog:{executionProfile:'science-flux',configurationBinding:scope,preflight:{staticReady:true},manualMethods:[method]},procedureId:method.procedureId,values:{},references:{cavity_seed:'seed',flux_scan:'scan'},records:[source('seed','ro_resonator_survey'),source('scan','ro_flux_scan'),point]};
 assert.throws(()=>scienceExecutionInput(draft),/点位/);
 assert.equal(scienceExecutionInput({...draft,workingPointId:'point'}).manualWorkingPointId,'point');
 assert.throws(()=>scienceExecutionInput({...draft,workingPointId:'point',records:[...draft.records.slice(0,2),{...point,sourceRecordId:'other'}]}),/点位/);
 assert.throws(()=>scienceExecutionInput({...draft,workingPointId:'point',records:[...draft.records.slice(0,2),{...point,result:{}}]}),/点位/);
});
