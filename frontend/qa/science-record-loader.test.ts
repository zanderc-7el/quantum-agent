import test from 'node:test';
import assert from 'node:assert/strict';
import {createScienceRecordLoader} from '../src/lib/scienceRecordLoader.ts';

const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('selected record requests are deduplicated and preserve complete plots and source input',async()=>{
 const calls:string[]=[],published:any[]=[];let finish!:(value:any)=>void;
 const loader=createScienceRecordLoader(id=>{calls.push(id);return new Promise(resolve=>finish=resolve);},record=>published.push(record),()=>assert.fail('unexpected error'));
 loader.load('a');loader.load('a');await tick();assert.deepEqual(calls,['a']);
 const full={id:'a',kind:'measurement',scope:{},input:{frequencyHz:5e9},result:{series:[{values:[1,2]}]},dependencyProof:{closureSha256:'immutable'}};
 finish(full);await tick();loader.load('a');await tick();assert.deepEqual(published,[full]);assert.deepEqual(calls,['a']);
});
test('refresh discards late old details and a failed selection waits for explicit retry',async()=>{
 let finish!:(value:any)=>void;const published:any[]=[],errors:string[]=[];let calls=0;
 const loader=createScienceRecordLoader(()=>{calls++;return calls===1?new Promise(resolve=>finish=resolve):Promise.reject(new Error('source changed'));},record=>published.push(record),error=>errors.push(String(error)));
 loader.load('a');await tick();loader.reset();finish({id:'a',kind:'measurement',scope:{},input:{}});await tick();assert.deepEqual(published,[]);
 loader.load('a');await tick();loader.load('a');await tick();assert.equal(calls,2);assert.equal(errors.length,1);
 loader.reset();loader.load('a');await tick();assert.equal(calls,3);
});
test('a summary or another record identity cannot be published as full detail',async()=>{
 for(const response of [{id:'a',summary:true},{id:'other'}]){
  const errors:any[]=[];const loader=createScienceRecordLoader(async()=>response as any,()=>assert.fail('untrusted detail'),error=>errors.push(error));
  loader.load('a');await tick();assert.equal(errors.length,1);
 }
});
