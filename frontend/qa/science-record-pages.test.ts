import test from 'node:test';
import assert from 'node:assert/strict';
import {createScienceGateway} from '../src/lib/scienceGateway.ts';

test('summary page keeps explicit continuation and detail uses the complete record endpoint',async()=>{
 const paths:string[]=[];const page={records:[{id:'one',summary:true}],hasMore:true,nextCursor:'41',responseBytes:200};
 const api=createScienceGateway({get:async(path:string)=>{paths.push(path);return path.includes('?')?page:{id:'one',input:{durationNs:50},result:{series:[{values:[0,1]}]}};} } as any);
 const result=await api.recordsPage('wf / source','52');
 assert.equal(result.nextCursor,'41');assert.equal(result.hasMore,true);
 const query=new URL('https://fixture'+paths[0]).searchParams;
 assert.equal(query.get('summary'),'true');assert.equal(query.get('workflowId'),'wf / source');assert.equal(query.get('cursor'),'52');
 const full=await api.record('one');assert.deepEqual(full.result?.series,[{values:[0,1]}]);assert.equal(paths[1],'/science/records/one');
});
