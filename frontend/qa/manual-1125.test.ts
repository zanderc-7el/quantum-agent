import test from 'node:test';
import assert from 'node:assert/strict';
import * as manual from '../src/lib/manualParameters.ts';
import type { LivePoint } from '../src/lib/liveSeries.ts';
import {manualCreationMatches} from '../src/lib/manualWorkbench.ts';

// The explicit-step admission must finish before the caller can create a task.
test('scan step rejects zero, nonfinite and excessive counts before mutation', () => {
  assert.equal(typeof manual.validateScanStep, 'function');
  for (const step of ['0', '-1', 'NaN', 'Infinity', '1e-999', '1e-99', '']) {
    assert.throws(() => manual.validateScanStep('0', '1', step, 1000));
  }
  assert.throws(() => manual.validateScanStep('1', '0', '.1', 1000));
});
test('scan step preserves exact requested endpoints instead of silently changing the grid', () => {
  assert.equal(typeof manual.validateScanStep, 'function');
  assert.deepEqual(manual.validateScanStep('0.1', '0.7', '0.1', 1000), { start: .1, stop: .7, step: .1, points: 7 });
  assert.throws(() => manual.validateScanStep('0', '1', '.3', 1000));
});

test('manual Rabi draft converts ns once and validates all inputs before creation', async () => {
  const { manualCreationParameters } = await import('../src/lib/manualExperimentDraft.ts');
  const bounds={amp_start:{min:0,max:1},amp_stop:{min:0,max:1},points:{min:3,max:161},drive_length_s:{min:1e-9,max:1e-6},shots:{min:10,max:2047}};
  const raw={amp_start:'.1',amp_stop:'.7',scan_step:'.1',drive_length_s:'40',shots:'1000'};
  assert.deepEqual(manualCreationParameters('sq_rabi_amplitude',raw,bounds),{amp_start:.1,amp_stop:.7,points:7,drive_length_s:4e-8,shots:1000});
  assert.throws(()=>manualCreationParameters('sq_rabi_amplitude',{...raw,scan_step:'0'},bounds));
  assert.throws(()=>manualCreationParameters('sq_rabi_amplitude',{...raw,drive_length_s:''},bounds));
  assert.throws(()=>manualCreationParameters('sq_rabi_amplitude',{...raw,shots:'1e400'},bounds));
});

test('editing a saved scan step changes only points and rejects zero without altering the draft',()=>{
 assert.equal(typeof manual.applyManualScanStep,'function');
 const node={id:'n1'} as any;
 const draft=manual.createManualParameterDraft({parameters:{amp_start:.1,amp_stop:.7,points:7},parameterBounds:{amp_start:{min:0,max:1},amp_stop:{min:0,max:1},points:{min:3,max:161}}} as any,node,3);
 assert.throws(()=>manual.applyManualScanStep(draft,'amp_start','amp_stop','0'));
 assert.equal(draft.values.points,'7');
 const next=manual.applyManualScanStep(draft,'amp_start','amp_stop','.05');
 assert.deepEqual(manual.validateManualParameterDraft(next,3),{points:13});
});

test('experiment phases depend on acquired evidence and never call mere execution success verified', async () => {
  const { experimentPhases } = await import('../src/lib/experimentEvidence.ts');
  assert.deepEqual(experimentPhases({ engineState: 'running', attempt: 1, telemetry: { completedRows: 2, totalRows: 8 } } as any).map(x => x.state), ['done', 'active', 'pending', 'pending']);
  assert.equal(experimentPhases({ engineState: 'failed', attempt: 1, result: { partial: true } } as any)[1].state, 'failed');
  assert.equal(experimentPhases({ engineState: 'awaiting_acceptance', attempt: 1, result: { quality: { status: 'passed' } } } as any)[3].state, 'pending');
});

test('Rabi explanation follows the actual selected axis and retains raw IQ provenance', async () => {
  const { rabiPointEvidence } = await import('../src/lib/experimentEvidence.ts');
  const point = { operationId: 'op1', axes: { 'q.pi_amp': .2, 'q.pi_len': 4e-8 }, shots: 1200, meanI: .3, meanQ: -.4, meanMagnitude: .5, pointFile: 'point.json', pointSha256: 'abc' } as unknown as LivePoint;
  const time = rabiPointEvidence('q.pi_len', point);
  assert.equal(time.variable, '脉冲时长'); assert.equal(time.unit, 'μs'); assert.equal(time.value, .04);
  assert.equal(rabiPointEvidence('q.pi_amp', point).value, .2);
  assert.equal(time.observable, '原始读出 I/Q'); assert.equal(time.point, point);
  assert.equal(rabiPointEvidence('missing', point).value, undefined);
});

test('independent verification remains the active acquisition despite retained first-result receipt',async()=>{
 const {experimentPhases}=await import('../src/lib/experimentEvidence.ts');
 for(const result of [undefined,{partial:true,verificationPending:true,selectionResult:{quality:{status:'unverified'}}},{quality:{status:'passed'}}]){
  const phases=experimentPhases({engineState:'verifying',attempt:3,result,telemetry:{completedRows:317,totalRows:324,operationId:'attempt/verify'}} as any);
  assert.equal(phases[1].state,'active');assert.equal(phases[1].label,'数据采集（独立复测）');
  assert.equal(phases[2].state,'pending');assert.equal(phases[3].state,'pending');
 }
});

test('confirmed neighbors come only from explicit coupling edges; chip switch clears selection', async () => {
  const { topologySelection } = await import('../src/lib/configurationDiagrams.ts');
  const topology = { nodes: [{resourceId:'Q1',x:0,y:0},{resourceId:'Q2',x:1,y:0},{resourceId:'Q3',x:0,y:1}], edges: [
    {id:'e1',from:'Q1',to:'Q2',kind:'coupling',confirmation:'confirmed',source:'design'},
    {id:'e2',from:'Q1',to:'Q3',kind:'coupling',confirmation:'pending',source:'check'},
  ] } as const;
  assert.deepEqual(topologySelection(topology, 'chip-a', {scope:'chip-a',resourceId:'Q1'}).neighbors, ['Q2']);
  assert.equal(topologySelection(topology, 'chip-b', {scope:'chip-a',resourceId:'Q1'}).resourceId, undefined);
  assert.deepEqual(topologySelection({nodes:topology.nodes,edges:[]}, 'chip-a', {scope:'chip-a',resourceId:'Q1'}).neighbors, []);
});

test('uncertain creation reconciliation cannot select another experiment with the same name and qubit',()=>{
 const base={id:'new',name:'test',mode:'real',policy:'strict',experiment:'single',coverage:'full',resourceIds:['Q1']};
 const attempt={name:'test',resourceId:'Q1',knownIds:[],procedureId:'sq_rabi_time'};
 const wrong={...base,manualExperiment:{procedureId:'sq_rabi_amplitude'}};
 assert.equal(manualCreationMatches([wrong as any],attempt).length,0);
 const right={...base,manualExperiment:{procedureId:'sq_rabi_time'}};
 assert.equal(manualCreationMatches([right as any],attempt).length,1);
});

test('published chip selection uses that chip resource map, without leaking current-adapter IDs',async()=>{
 const {manualResources}=await import('../src/lib/manualExperimentDraft.ts');
 assert.equal(typeof manualResources,'function');
 const catalog={resources:[{id:'OLD',label:'old'}],capabilities:{resources:['OLD']}} as any;
 assert.deepEqual(manualResources(catalog,{qubits:[{id:'NEW',label:'new'}]} as any),[{id:'NEW',label:'new'}]);
 assert.deepEqual(manualResources(catalog,{qubits:[]} as any),[]);
});

test('saved flux point cannot dispatch through ordinary S21 or Rabi methods',async()=>{
 const {workingPointBlockReason}=await import('../src/lib/manualExperimentDraft.ts');
 assert.equal(typeof workingPointBlockReason,'function');
 assert.match(workingPointBlockReason({capabilities:{procedures:{ro_resonator_survey:{available:true}}}} as any,{recordId:'wp1'} as any)!,/偏置通道尚未接入/);
 assert.equal(workingPointBlockReason(undefined,undefined),undefined);
});
