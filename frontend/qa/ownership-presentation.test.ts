import assert from 'node:assert/strict';
import test from 'node:test';
import {ownershipRows} from '../src/lib/ownershipPresentation.ts';
import type {OwnershipCheck} from '../src/lib/ownershipPresentation.ts';

test('ownership technical details only include approved fields and labels',()=>{
 const data={schema:'qcal-hardware-ownership-check/1',id:'SECRET-ID',reason:'RAW-SECRET',lockPath:'C:/SECRET',
   stage:'dispatch',checkedAt:'2026-11-26T13:45:12Z',verified:false,outcome:'unverified',checkStatus:'timeout',
   sources:['windows_tcp_table','provider-private-token','chassis_lock_files'],ownerCount:4,socketCount:1,lockCount:2,unknownCount:1,
   visiblePids:[111,0,222,333,444,555,666,777,888,999],pidsTruncated:true};
 const rendered=JSON.stringify(ownershipRows(data as OwnershipCheck));
 assert.match(rendered,/TCP 连接表、机箱锁记录/);
 assert.match(rendered,/核查状态","未核实/);
 assert.match(rendered,/TCP 记录 1，锁记录 2，未知类型 1/);
 assert.match(rendered,/888（部分）/);
 assert.doesNotMatch(rendered,/999|SECRET|private-token/);
 assert.deepEqual(ownershipRows(undefined),[]);
});
