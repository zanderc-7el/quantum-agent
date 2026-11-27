import test from 'node:test';
import assert from 'node:assert/strict';
import {recordStoredTime,acquiredTime} from '../src/lib/scienceRecordTime.ts';

test('archiving an older acquisition never relabels it as measured at record creation',()=>{
 const record={kind:'measurement',createdAt:'2026-11-26T03:18:47+08:00',result:{acquiredAt:'2026-11-18T12:30:00+08:00'}};
 assert.match(recordStoredTime(record),/^归档于/);assert.match(recordStoredTime(record),/11\/26/);
 assert.match(acquiredTime(record.result),/^原始采集时间/);assert.match(acquiredTime(record.result),/11\/18/);
 assert.match(recordStoredTime({...record,kind:'pulse'}),/^保存于/);
 assert.equal(acquiredTime({}),undefined);
 assert.equal(record.result.acquiredAt,'2026-11-18T12:30:00+08:00');
});
