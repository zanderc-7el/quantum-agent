import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';

const url=new URL('../src/lib/scienceExecutionDraft.ts',import.meta.url);
async function helpers(){assert.ok(existsSync(url),'science execution draft helpers are missing');return import(url.href);}
const scope={chipVersionId:'chip/1',deploymentVersionId:'wiring 1',resourceId:'Q18'};
const method={procedureId:'sq_shape_rabi_amplitude',label:'形状专属幅度 Rabi',capability:'available',executionProfile:'science-v3',scienceSourceRoles:['f01','readout'],scienceSourceMethods:{f01:['sq_spectroscopy'],readout:['ro_iq01']},parameterBounds:{pulse_shape:{enum:['Gaussian','CosPulse']},pulse_length_s:{min:4e-9,max:2e-6},points:{min:9,max:241,integer:true}},parameterDefaults:{pulse_shape:'CosPulse',pulse_length_s:20e-9,points:61}};
const catalog={executionProfile:'science-v3',configurationBinding:{...scope,adapterProfileHash:'profile'},manualMethods:[method],preflight:{staticReady:true}};
function source(id:string,procedure:string){return {id,label:procedure==='sq_spectroscopy'?'比特定频':'读出分类',kind:'measurement',dataKind:'real',scope:{...scope,workflowId:'source-task'},input:{},evidenceCurrent:true,evidenceIntact:true,provenance:{independentlyVerified:true},reference:{workflowId:'source-task',attemptId:id+'-attempt'},result:{procedureId:procedure,quality:{status:'passed'},parameters:{drive_hz:4.8e9,readout_hz:7e9}}};}
const records=[source('frequency','sq_spectroscopy'),source('readout','ro_iq01')];

test('catalog query requires complete published identity and safely encodes it',async()=>{
 const m=await helpers();assert.equal(m.scienceCatalogPath({...scope,resourceId:''}),undefined);
 const path=m.scienceCatalogPath(scope);const query=new URL('https://fixture'+path).searchParams;
 assert.equal(query.get('executionProfile'),'science-v3');for(const [k,v] of Object.entries(scope))assert.equal(query.get(k),v);
});
test('science defaults come from the server and convert pulse seconds to ns once',async()=>{
 const m=await helpers();const defaults=m.scienceParameterValues(method);
 assert.deepEqual(defaults,{pulse_shape:'CosPulse',pulse_length_s:'20',points:'61'});
 assert.deepEqual(m.scienceParameters(method,{...defaults,pulse_length_s:'40'}),{pulse_shape:'CosPulse',pulse_length_s:4e-8,points:61});
 assert.deepEqual(m.scienceParameterValues({...method,parameterDefaults:{},parameterBounds:{points:method.parameterBounds.points}}),{points:''});
});
test('science enum numeric range and nonfinite values fail before creation',async()=>{
 const m=await helpers();const good=m.scienceParameterValues(method);
 for(const bad of [{pulse_shape:'DC'},{pulse_length_s:'Infinity'},{pulse_length_s:'0'},{points:'9.5'},{points:'1e400'},{points:'[9,10]'}])assert.throws(()=>m.scienceParameters(method,{...good,...bad}));
 assert.throws(()=>m.scienceParameters({...method,parameterBounds:{scan_axis_values:{type:'array'}}},{scan_axis_values:'[1,2,3]'}),/数组|不支持/);
});
test('source menu excludes wrong method configuration and failed or model evidence',async()=>{
 const m=await helpers();const variants=[...records,{...records[0],id:'wrong-wiring',scope:{...scope,deploymentVersionId:'other'}},{...records[0],id:'stale',evidenceCurrent:false},{...records[0],id:'model',kind:'drag',dataKind:'simulation'},{...records[0],id:'failed',result:{...records[0].result,quality:{status:'failed'}}}];
 assert.deepEqual(m.scienceSourceChoices(variants,method,'f01',scope).map((r:any)=>r.id),['frequency']);
 assert.match(m.scienceSourceLabel(records[0]),/比特定频/);assert.doesNotMatch(m.scienceSourceLabel(records[0]),/source-task|attempt/);
});
test('creation uses the catalog bound to this selection and sends only reference IDs',async()=>{
 const m=await helpers();const payload=m.scienceExecutionInput({name:'形状标定',scope,catalog,procedureId:method.procedureId,values:m.scienceParameterValues(method),references:{f01:'frequency',readout:'readout'},records});
 assert.equal(payload.executionProfile,'science-v3');assert.equal(payload.mode,'real');assert.equal(payload.policy,'strict');assert.deepEqual(payload.resourceIds,['Q18']);
 assert.deepEqual(payload.manualScienceReferences,[{role:'f01',recordId:'frequency'},{role:'readout',recordId:'readout'}]);assert.equal(payload.manualParameters.pulse_length_s,20e-9);
 assert.equal('acceptedCalibration' in payload,false);assert.equal('scienceDependencies' in payload,false);
 const base={name:'形状标定',scope,catalog,procedureId:method.procedureId,values:m.scienceParameterValues(method),references:{f01:'frequency',readout:'readout'},records};
 for(const changes of [{references:{f01:'frequency'}},{scope:{...scope,resourceId:'Q11'}},{catalog:{...catalog,manualMethods:[{...method,capability:'unavailable'}]}},{records:records.map(r=>({...r,evidenceIntact:false}))}])assert.throws(()=>m.scienceExecutionInput({...base,...changes}));
});
test('observed resource fork retains origin identity and never invents a chip version',async()=>{
 const m=await helpers();const observed={sourceWorkflowId:'origin',resourceId:'Q11'};
 const binding={sourceWorkflowId:'origin',resourceId:'Q11',sha256:'bound-sha',identityClaim:'measured_resource_not_design_identity',physicalDesignId:null};
 const response={...catalog,configurationBinding:{...observed,chipVersionId:null,deploymentVersionId:null,observedResourceBinding:binding}};
 const origin=records.map(r=>({...r,scope:{workflowId:'origin',resourceId:'Q11'}}));
 const inherited={...origin[0],id:'child',scope:{workflowId:'child-task',resourceId:'Q11',observedResourceBindingSha256:'bound-sha'}};
 const unrelated={...origin[0],id:'unrelated',scope:{workflowId:'unrelated-task',resourceId:'Q11'}};
 const choices=m.scienceSourceChoices([...origin,inherited,unrelated],method,'f01',observed,binding);
 assert.deepEqual(choices.map((r:any)=>r.id),['frequency','child']);
 const payload=m.scienceExecutionInput({name:'实测Q11',scope:observed,catalog:response,procedureId:method.procedureId,values:m.scienceParameterValues(method),references:{f01:'frequency',readout:'readout'},records:origin});
 assert.equal(payload.sourceWorkflowId,'origin');assert.equal('chipVersionId' in payload,false);assert.equal('deploymentVersionId' in payload,false);
 const path=m.scienceCatalogPath(observed);assert.match(path,/sourceWorkflowId=origin/);assert.doesNotMatch(path,/chipVersionId|deploymentVersionId/);
 assert.throws(()=>m.scienceExecutionInput({name:'错源',scope:{...observed,sourceWorkflowId:'other'},catalog:response,procedureId:method.procedureId,values:{},references:{},records:origin}));
});

test('repeat-five calibration selects its own catalog and preserves the requested target and method',async()=>{
 const lib=await helpers();
 const profile='science-shape-repeat5';
 const procedureId='sq_shape_rabi_amplitude_repeat5';
 const path=lib.scienceCatalogPath(scope,profile);
 assert.equal(new URL('https://fixture'+path).searchParams.get('executionProfile'),profile);
 const method={...catalog.manualMethods[0],procedureId,executionProfile:profile};
 const repeatCatalog={...catalog,executionProfile:profile,manualMethods:[method]};
 const input=lib.scienceExecutionInput({name:'五脉冲',scope,catalog:repeatCatalog,procedureId,executionProfile:profile,
   values:lib.scienceParameterValues(method),references:{f01:'frequency',readout:'readout'},records});
 assert.equal(input.executionProfile,profile);
 assert.equal(input.manualProcedureId,procedureId);
 assert.equal(input.target,scope.resourceId);
 assert.equal(Object.hasOwn(input.manualParameters,'gate_repeats'),false);
});
