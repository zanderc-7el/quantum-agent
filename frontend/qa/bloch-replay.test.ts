import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {deriveBlochReplay} from '../src/lib/blochReplay.ts';
const fixtureUrl=new URL('./fixtures/bloch-historical-records.json',import.meta.url);
const fixtureAvailable=existsSync(fixtureUrl);
const records=fixtureAvailable?JSON.parse(readFileSync(fixtureUrl,'utf8')).records:[];
const privateFixtureOptions={skip:!fixtureAvailable&&'Private regression fixture excluded from source release'};
const get=(procedure:string)=>structuredClone(records.find((r:any)=>r.result.procedureId===procedure));
test('historical failed CPMG binds exact N2 and 40us but never adopts failed fitted T2',privateFixtureOptions,()=>{
 const r=get('sq_cpmg'),before=JSON.stringify(r),d=deriveBlochReplay(r);
 assert.equal(d.input.sequence,'cpmg');assert.equal(d.input.piPulses,2);assert.equal(d.input.durationNs,40000);
 assert.equal(d.input.timesNs.length,41);assert.equal(d.input.readoutSign,-1);assert.equal(d.input.refocusAxis,'x');
 assert.equal(d.input.analysisPhaseHz,0);assert.equal(d.input.t2Ns,undefined);assert.equal(d.source.quality,'failed');
 assert.match(d.warnings.join(' '),/未通过/);assert.equal(JSON.stringify(r),before);
});
test('accepted historical fits have explicit seconds to ns conversion and native analysis convention',privateFixtureOptions,()=>{
 const t1=get('sq_t1'),ramsey=get('sq_ramsey'),echo=get('sq_echo');
 assert.equal(deriveBlochReplay(t1).input.t1Ns,t1.result.metrics.t1_s*1e9);
 assert.equal(deriveBlochReplay(t1).input.t2Ns,2*t1.result.metrics.t1_s*1e9);
 assert.equal(deriveBlochReplay(t1).sourceFields.t2Ns,undefined);assert.match(deriveBlochReplay(t1).warnings.join(' '),/不是实测 T₂/);
 assert.equal(deriveBlochReplay(ramsey).input.t2Ns,ramsey.result.metrics.t2_s*1e9);
 const e=deriveBlochReplay(echo);assert.equal(e.input.refocusAxis,'y');assert.equal(e.input.analysisPhaseHz,-1200000);assert.equal(e.input.readoutSign,-1);
 assert.equal(e.input.detuningHz,undefined,'analysis rotation is not detuning');
});
test('missing N, unknown phase, wrong scope and invalid axes do not invent recorded parameters',privateFixtureOptions,()=>{
 const r=get('sq_cpmg');delete r.result.parameters.pi_pulses;delete r.result.parameters.pulse_compatibility;
 const d=deriveBlochReplay(r);assert.equal(d.input.piPulses,undefined);assert.equal(d.input.readoutSign,undefined);
 assert.match(d.missingFields.join(' '),/脉冲数/);assert.match(d.warnings.join(' '),/相位/);
 r.reference.workflowId='wrong';assert.deepEqual(deriveBlochReplay(r).input,{});
 const e=get('sq_echo');e.result.axes.x.unit='a.u.';for(const t of e.result.sourceSeries??[])t.xUnit='a.u.';
 assert.equal(deriveBlochReplay(e).input.durationNs,undefined);
});
test('unsupported and damaged records only retain honest independent model fallback',privateFixtureOptions,()=>{
 const r=get('sq_t1');r.evidenceIntact=false;assert.deepEqual(deriveBlochReplay(r).input,{});
 r.evidenceIntact=true;r.result.procedureId='sq_rabi_amplitude';assert.deepEqual(deriveBlochReplay(r).input,{});
});
test('partial and out-of-model-range fit values remain manual instead of silently clipping',privateFixtureOptions,()=>{
 const r=get('sq_t1');r.result.partial=true;assert.equal(deriveBlochReplay(r).input.t1Ns,undefined);
 r.result.partial=false;r.result.metrics.t1_s=1e300;assert.equal(deriveBlochReplay(r).input.t1Ns,undefined);
});
