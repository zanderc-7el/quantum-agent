import test from 'node:test';
import assert from 'node:assert/strict';
import { reportReceipts, reportReceiptJob } from '../src/lib/conversationReports.ts';

test('assistant action receipts and legacy tool results recover report IDs without accepting arbitrary URLs',()=>{
 const receipt={status:'report_receipt',exportId:'report-1',workflowId:'wf-1',state:'processing',downloadUrl:'https://untrusted.test/file'};
 assert.deepEqual(reportReceipts({actionTrace:[{tool:'export_report',result:receipt}],toolResults:[receipt]}),[{...receipt,downloadUrl:undefined}]);
 assert.deepEqual(reportReceipts({toolResults:[{...receipt,exportId:'../../elsewhere'}]}),[]);
 assert.equal(reportReceiptJob(receipt,{id:'other',workflowId:'wf-1',status:'completed'}),undefined);
 assert.equal(reportReceiptJob(receipt,{id:'report-1',workflowId:'other',status:'completed'}),undefined);
 assert.equal(reportReceiptJob(receipt,{id:'report-1',workflowId:'wf-1',status:'completed'})?.status,'completed');
});
