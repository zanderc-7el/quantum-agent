import test from 'node:test';
import assert from 'node:assert/strict';
import {blochProtocol,activeProtocolStep} from '../src/lib/blochProtocol.ts';

function result(sequence:string,duration=10000,count=sequence==='echo'?1:sequence==='cpmg'?2:0,initialState=sequence==='x'?'0':sequence==='t1'?'1':'+x'){
 return {kind:'bloch',sequence,initialState,replayScan:true,selectedDelayNs:duration,refocusAxis:sequence==='echo'?'y':'x',
  trajectory:[{t:0,x:0,y:0,z:0,p1:.5},...(duration?[{t:duration,x:0,y:0,z:0,p1:.5}]:[])],metrics:{piPulses:count},
  segments:Array.from({length:count},(_,i)=>({id:`pi-${i}`,label:'π',startNs:duration*(i+.5)/count,endNs:duration*(i+.5)/count}))};
}
test('T1 and Ramsey include preparation, wait and ordered readout without invented widths',()=>{
 const t1=blochProtocol(result('t1'))!,ramsey=blochProtocol(result('ramsey'))!;
 assert.deepEqual(t1.steps.map(s=>s.kind),['preparation','wait','readout']);
 assert.match(t1.steps[0].label,/制备.*π/);
 assert.deepEqual(ramsey.steps.map(s=>s.kind),['preparation','wait','analysis','readout']);
 for(const protocol of [t1,ramsey]){
  assert.equal(protocol.steps[0].startNs,undefined);assert.equal(protocol.steps.at(-1)!.endNs,undefined);
  assert.match(protocol.notes.join(' '),/未给出绝对触发时间、实际脉宽或 ADC 窗口/);
 }
});
test('Echo and CPMG use selected result centers and count rather than longest scan or draft defaults',()=>{
 const cpmg=result('cpmg'),before=JSON.stringify(cpmg),p=blochProtocol(cpmg)!;
 assert.deepEqual(p.steps.filter(s=>s.kind==='refocus').map(s=>s.startNs),[2500,7500]);
 assert.deepEqual(p.steps.filter(s=>s.kind==='wait').map(s=>[s.startNs,s.endNs]),[[0,2500],[2500,7500],[7500,10000]]);
 assert.equal(p.refocusCount,2);assert.equal(JSON.stringify(cpmg),before);
 assert.match(blochProtocol(result('echo'))!.steps.find(s=>s.kind==='refocus')!.label,/模型 Y 轴/);
 const shorter=blochProtocol(result('cpmg',1000,3))!;assert.equal(shorter.refocusCount,3);assert.equal(shorter.steps.filter(s=>s.kind==='refocus')[0].startNs,1000/6);
});
test('zero delay preserves preparation, zero wait, every pi, analysis and readout in order',()=>{
 const p=blochProtocol(result('cpmg',0,2))!;
 assert.deepEqual(p.steps.map(s=>s.kind),['preparation','wait','refocus','refocus','analysis','readout']);
 assert.match(p.notes.join(' '),/不表示这些动作同时触发/);assert.equal(activeProtocolStep(p,0),undefined);
 assert.deepEqual(blochProtocol(result('t1',0))!.steps.map(s=>s.kind),['preparation','wait','readout']);
});
test('nonstandard manual initial state never invents a preparation pulse',()=>{
 for(const sequence of ['t1','ramsey','echo','cpmg']){
  const p=blochProtocol(result(sequence,1000,sequence==='echo'?1:sequence==='cpmg'?2:0,'0'))!;
  assert.equal(p.steps[0].label,'设定模型初态 |0⟩');assert.match(p.notes.join(' '),/未推定.*实际制备/);
 }
});
test('X drive model has a drive interval and Z endpoint, no imaginary analysis pi/2',()=>{
 const p=blochProtocol({...result('x',50),replayScan:false})!;
 assert.deepEqual(p.steps.map(s=>s.kind),['preparation','drive','readout']);
 assert.match(p.steps[1].label,/驱动演化/);assert.equal(activeProtocolStep(p,25),'drive');assert.equal(activeProtocolStep(p,50),'readout');
});
test('cursor follows wait intervals and exact model refocus; endpoint cannot highlight preparation',()=>{
 const p=blochProtocol(result('cpmg'))!;
 assert.equal(activeProtocolStep(p,0),'wait-1');assert.equal(activeProtocolStep(p,2500),'refocus-1');
 assert.equal(activeProtocolStep(p,3000),'wait-2');assert.equal(activeProtocolStep(p,10000),'readout');
 assert.equal(activeProtocolStep(p,-1),undefined);assert.equal(activeProtocolStep(p,10001),undefined);
});
test('missing or inconsistent result metadata fails visibly rather than rebuilding a protocol',()=>{
 assert.equal(blochProtocol(undefined),null);assert.equal(blochProtocol({...result('cpmg'),segments:[]}),null);
 assert.equal(blochProtocol({...result('cpmg'),selectedDelayNs:40000}),null);
 assert.equal(blochProtocol({...result('t1'),initialState:'unknown'}),null);
 const bad=result('echo');bad.segments[0].endNs++;assert.equal(blochProtocol(bad),null);
});


test('maximum supported N64 keeps every pulse and zero-delay ordering',()=>{
 for(const duration of [0,6400]){
  const r=result('cpmg',duration,64),before=JSON.stringify(r),p=blochProtocol(r)!;
  assert.equal(p.steps.filter(s=>s.kind==='refocus').length,64);
  assert.equal(p.steps.length,duration===0?68:132);assert.equal(JSON.stringify(r),before);
 }
 for(const sequence of ['t1','x'])assert.ok(!blochProtocol(result(sequence))!.notes.join(' ').includes('末端分析'));
});
