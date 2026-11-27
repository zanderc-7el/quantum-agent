import test from 'node:test';
import assert from 'node:assert/strict';
import * as helpers from '../src/lib/stabilitySchedule.ts';
test('schedule bounds match 60 seconds through 30 days and permit every integer point count',()=>{
 assert.equal(typeof helpers.stabilityScheduleParameters,'function');
 assert.deepEqual(helpers.stabilityScheduleParameters({hours:1/60,rounds:1,span:1000,points:4}),{intervalSeconds:60,maxRounds:1,spanHz:1000,points:4});
 assert.deepEqual(helpers.stabilityScheduleParameters({hours:720,rounds:1000,span:1e8,points:161}),{intervalSeconds:2592000,maxRounds:1000,spanHz:1e8,points:161});
});
test('out-of-range nonfinite or fractional integer parameters are rejected before dispatch',()=>{
 assert.equal(typeof helpers.stabilityScheduleParameters,'function');
 const good={hours:3,rounds:3,span:2e6,points:101};
 for(const bad of [{hours:.01},{hours:720.1},{hours:NaN},{hours:Infinity},{rounds:1.5},{rounds:0},{rounds:1001},{span:999},{span:1e8+1},{span:Infinity},{points:2},{points:162},{points:3.5}])assert.throws(()=>helpers.stabilityScheduleParameters({...good,...bad}));
});
test('new schedule clock never labels an enqueue timestamp as actual acquisition',()=>{
 const queued='2040-01-01T00:00:00Z',acquired='2040-01-01T08:00:00Z';
 assert.deepEqual(helpers.stabilityRoundClock({clockPolicy:helpers.stabilityClockPolicy},{startedAt:queued}),{at:null,label:'首测采集完成'});
 assert.deepEqual(helpers.stabilityRoundClock({clockPolicy:helpers.stabilityClockPolicy},{startedAt:queued,intervalAnchor:{acquiredAt:acquired}}),{at:acquired,label:'首测采集完成'});
 assert.deepEqual(helpers.stabilityRoundClock({},{startedAt:queued}),{at:queued,label:'入队时间（旧计划）'});
 assert.match(helpers.stabilityClockNote(),/首测采集完成/);assert.match(helpers.stabilityClockNote({}),/旧计划/);
 assert.equal(helpers.stabilityRoundClock({clockPolicy:helpers.stabilityClockPolicy},{intervalAnchor:{acquiredAt:'invalid'}}).at,null);
});
