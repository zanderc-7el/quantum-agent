import test from 'node:test';
import assert from 'node:assert/strict';
import {attentionQueueItems, attentionWorkflowLabel, attentionRowLabel} from '../src/lib/attentionQueuePresentation.ts';
import {attentionCandidate, type Attention} from '../src/lib/workspaceGateway.ts';

const item=(id:string,state:Attention['state']='deferred',name='历史任务'):Attention=>({id,revision:1,workflowId:'wf-'+id,
 workflowName:name,workflowCreatedAt:`2026-11-26T10:00:${id.padStart(2,'0')}Z`,title:'Q11 · 设备准备',message:'待核查',severity:'warning',scope:'device',state,actions:[]});
test('new unresolved item precedes twelve deferred items without reordering either group',()=>{
 const original=[...Array.from({length:12},(_,n)=>item(String(n))),item('12','open','当前任务'),item('13','resolved')];
 const snapshot=JSON.stringify(original);const rows=attentionQueueItems(original);
 assert.deepEqual(rows.map(x=>x.id),['12',...Array.from({length:12},(_,n)=>String(n))]);
 assert.equal(JSON.stringify(original),snapshot);
 assert.equal(attentionCandidate(original,{})?.id,'12');
 assert.equal(attentionCandidate(original,{'12':1}),undefined);
});
test('same-name tasks get their actual creation times while a task with several items retains its label',()=>{
 const a=item('01','open','验收任务'),b=item('02','deferred','验收任务'),c={...a,id:'another-item'};
 assert.notEqual(attentionWorkflowLabel(a,[a,b]),attentionWorkflowLabel(b,[a,b]));
 assert.match(attentionWorkflowLabel(a,[a,b]),/验收任务.*11\/26.*18:00:01/);
 assert.equal(attentionWorkflowLabel(a,[a,c]),'验收任务');
 assert.equal(attentionRowLabel(a,[a,b]),`${attentionWorkflowLabel(a,[a,b])} · Q11 · 设备准备 · 待处理`);
});
test('unique workflow names remain readable and legacy missing data does not expose identifiers',()=>{
 const a=item('01','open','本轮 Q11 完整验收');assert.equal(attentionWorkflowLabel(a,[a]),'本轮 Q11 完整验收');
 const old={...a,workflowName:undefined,workflowCreatedAt:undefined};
 assert.equal(attentionWorkflowLabel(old,[old]),'任务名称未记录');
 assert.ok(!attentionRowLabel(old,[old]).includes(old.workflowId!));
 assert.match(attentionRowLabel({...a,state:'deferred'},[a]),/稍后处理$/);
});
test('same second, cross-year and absent-time collisions get a stable unique short task number',()=>{
 const a={...item('01','open','同名任务'),workflowId:'wf-abcdef112233',workflowCreatedAt:'2026-11-26T10:00:01.100Z'};
 const b={...a,id:'02',workflowId:'wf-fedcba112233',workflowCreatedAt:'2026-11-26T10:00:01.900Z'};
 const c={...a,id:'03',workflowId:'wf-aaaabb112233',workflowCreatedAt:'2025-11-26T10:00:01.100Z'};
 const rows=[a,b,c];const labels=rows.map(x=>attentionWorkflowLabel(x,rows));
 assert.equal(new Set(labels).size,3);assert.ok(labels.every(x=>x.includes('任务号')));
 assert.equal(attentionWorkflowLabel({...a,id:'second-attention'},rows),labels[0]);
 assert.deepEqual(rows.map(x=>attentionWorkflowLabel(x,[...rows].reverse())),labels);
 const missing=rows.map(x=>({...x,workflowName:undefined,workflowCreatedAt:undefined}));
 assert.equal(new Set(missing.map(x=>attentionWorkflowLabel(x,missing))).size,3);
});
