import test from 'node:test';
import assert from 'node:assert/strict';
import { comparisonPoints, pulseEdges, projectBloch, readWorkingPoint, modelInputFromRecord,fluxRecheckOverlay } from '../src/lib/scienceModels.ts';
import { measurementScope } from '../src/lib/scienceModels.ts';
import { measuredModelSequence, comparisonModelParameters } from '../src/lib/scienceModels.ts';

test('point recheck overlays original and new cavity raw samples without resampling or normalizing',()=>{
 const old={frequenciesHz:[7e9,7.001e9,7.002e9],responseMagnitude:[.4,.1,.5],readoutFrequencyHz:7.001e9};
 const local={phase:'local_cavity',frequenciesHz:[7.0005e9,7.001e9,7.0015e9],responseMagnitude:[.3,.12,.35],readoutFrequencyHz:7.0011e9};
 const result:any={procedureId:'sq_flux_point_recheck',source:'acquisition',scienceFlux:{sourcePoint:{originalCut:old},points:[local,{...local,phase:'f01',responseMagnitude:[99,98,99]}]}};
 const traces=fluxRecheckOverlay(result);
 assert.equal(traces.length,2);assert.deepEqual(traces[0].x,old.frequenciesHz);assert.deepEqual(traces[1].x,local.frequenciesHz);
 assert.deepEqual(traces[0].y,old.responseMagnitude);assert.deepEqual(traces[1].y,local.responseMagnitude);
 assert.ok(traces.every(t=>t.xUnit==='Hz'&&t.yUnit==='ADC'&&t.quantity==='iq_amplitude'));
 assert.equal(traces[0].markerX,7.001e9);assert.equal(traces[1].markerX,7.0011e9);
 assert.ok(traces[0].name.includes('7.001 GHz'));assert.ok(traces[1].name.includes('7.0011 GHz'));
 assert.deepEqual(fluxRecheckOverlay({provenance:{scienceFlux:result.scienceFlux}}),traces);
 assert.deepEqual(fluxRecheckOverlay({...result,scienceFlux:{points:[local]}}),[]);
});
test('record-backed simulation leaves method and preparation to the frozen server evidence',()=>{
 const record:any={dataKind:'real',result:{procedureId:'sq_t1'}};
 assert.equal(measuredModelSequence(record),'t1');
 assert.deepEqual(comparisonModelParameters(record,{sequence:'x',initialState:'0',t1Ns:30}),{t1Ns:30});
 assert.deepEqual(comparisonModelParameters({dataKind:'model'} as any,{sequence:'x'}),{sequence:'x'});
});
test('measurement evidence follows its actual node resource in a multi-qubit workflow',()=>{
 const workflow:any={id:'w',target:'Q11',resourceIds:['Q11','Q18'],nodes:[{id:'Q11:n',resourceId:'Q11',latestAttemptId:'a11'},{id:'Q18:n',resourceId:'Q18',latestAttemptId:'a18'}]};
 assert.deepEqual(measurementScope(workflow,'a18'),{workflowId:'w',resourceId:'Q18'});
 assert.throws(()=>measurementScope(workflow,'old'),/采集记录/);
});
test('pulse delay edits move only capture and preserve read in width',()=>{
 const config={xyWidthNs:20,gapNs:2,readoutStartNs:100,readoutWidthNs:2000,captureDelayNs:50};
 const before=pulseEdges(config), after=pulseEdges({...config,captureDelayNs:70});
 assert.deepEqual(before.xy,[[0,20],[22,42]]); assert.deepEqual(after.readIn,[100,2100]); assert.deepEqual(after.readOut,[170,2170]); assert.equal(after.readOut[0]-before.readOut[0],20);
 assert.throws(()=>pulseEdges({...config,xyWidthNs:-1}));
});
test('comparison preserves missing points and refuses probability minus ADC',()=>{
 const p={name:'model',x:[0,1,2],y:[0,.5,1],xUnit:'ns',yUnit:'probability'};
 const m={name:'measured',x:[0,2],y:[.1,.8],xUnit:'ns',yUnit:'probability'};
 assert.deepEqual(comparisonPoints(p,m),[{x:0,predicted:0,measured:.1,difference:.1},{x:1,predicted:.5,measured:null,difference:null},{x:2,predicted:1,measured:.8,difference:-.19999999999999996}]);
 assert.throws(()=>comparisonPoints(p,{...m,yUnit:'ADC'}),/单位/); assert.throws(()=>comparisonPoints(p,{...m,xUnit:'us'}),/单位/);
});
test('Bloch sphere poles project above and below origin',()=>{
 assert.deepEqual(projectBloch([0,0,1],0),[0,-1]); assert.deepEqual(projectBloch([0,0,-1],2),[0,1]); assert.deepEqual(projectBloch([1,0,0],0),[1,0]);
});
test('working point handoff requires persisted matching scope and units',()=>{
 const r:any={id:'p',kind:'working_point',scope:{workflowId:'w',resourceId:'q1'},input:{purpose:'work',fluxChannel:'Z1',bias:.12,biasUnit:'V',readoutFrequencyHz:6e9,sourceRecordId:'scan'}};
 assert.equal(readWorkingPoint(r,'q1').bias,.12); assert.throws(()=>readWorkingPoint(r,'q2'),/比特/);
 assert.throws(()=>readWorkingPoint({...r,input:{...r.input,biasUnit:''}},'q1'),/单位/); assert.throws(()=>readWorkingPoint({...r,id:''},'q1'),/保存/);
});
test('same configuration simulation copies frozen scan without mutating record',()=>{
 const r:any={id:'a',input:{experiment:'Rabi',scanPoints:[0,20,40],driveAmp:.2},scope:{resourceId:'q1'}};
 const input=modelInputFromRecord(r,{t1Ns:20000}); assert.deepEqual(input.scanPoints,[0,20,40]); assert.equal(input.sourceRecordId,'a');
 (input.scanPoints as number[])[0]=123; assert.equal(r.input.scanPoints[0],0);
});
test('persisted multi-trace result retains each trace native axis and physical units', async()=>{
 const {resultPlots}=await import('../src/lib/scienceModels.ts');
 const result:any={source:'acquisition',axes:{x:{name:'time',unit:'ns',values:[0,10]}},series:[{id:'a',label:'A',quantity:'iq',unit:'V',values:[1,2]}],sourceSeries:[{id:'a',label:'A',quantity:'iq',unit:'V',x:[0,10],xUnit:'ns',values:[1,2]},{id:'b',label:'B',quantity:'frequency_response',unit:'dB',x:[6e9,7e9],xUnit:'Hz',values:[-20,-10]}]};
 const plots=resultPlots(result);assert.equal(plots.length,2);assert.deepEqual(plots[1].x,[6e9,7e9]);assert.equal(plots[1].xUnit,'Hz');assert.equal(plots[1].yUnit,'dB');
});
test('invalidated working point cannot be handed to manual acquisition',()=>{
 const r:any={id:'p',kind:'working_point',validity:'stale',evidenceIntact:false,scope:{resourceId:'q1'},input:{purpose:'work',fluxChannel:'Z1',bias:.1,biasUnit:'V',readoutFrequencyHz:6e9,sourceRecordId:'scan'}};
 assert.throws(()=>readWorkingPoint(r,'q1'),/失效/);
});
