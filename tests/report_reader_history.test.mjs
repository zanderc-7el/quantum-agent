import test from 'node:test';
import assert from 'node:assert/strict';
import {acquisitionPages,diagnosticPages,presentationSummary} from '../scripts/report_reader_layout.mjs';
test('all stages retain their identities across bounded pages',()=>{
 const rows=Array.from({length:21},(_,i)=>({attemptId:'attempt-'+i,operationId:'operation-'+i,workpoint:'archive '+i}));
 const pages=acquisitionPages({acquisitionHistory:rows});
 assert.equal(pages.length,6);assert.ok(pages.every(p=>p.length<=4));assert.deepEqual(pages.flat(),rows);
 assert.deepEqual(acquisitionPages({}),[]);
});
test('long diagnostic is not silently clipped or repeated on chart page',()=>{
 const summary='原因甲。'.repeat(300)+'最终限制。';const n={label:'测量',status:'未通过',summary};
 assert.ok(presentationSummary(n).length<130);
 const pages=diagnosticPages([n]);assert.equal(pages.map(p=>p.text).join(''),summary);
 assert.ok(pages.every(p=>Array.from(p.text).length<=430));
 assert.deepEqual(diagnosticPages([{...n,summary:'短摘要'}]),[]);
});
