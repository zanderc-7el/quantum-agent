import test from 'node:test';
import assert from 'node:assert/strict';
import {isConfirmedExecutionQubit,topologyExecutionTarget} from '../src/lib/topologyExecutionTarget.ts';
const q=(id:string,status='confirmed',physicalId='P-'+id)=>({id,label:id,identityStatus:status,physicalId,parameters:{}});
const topology={nodes:[{resourceId:'Q1',designId:'design-A',x:0,y:0},{resourceId:'Q2',x:1,y:1},{resourceId:'ghost',x:2,y:2}],edges:[]};
test('graph-to-execution selection uses exact current physical identity, never an alias or unconfirmed label',()=>{
 const qubits=[q('Q1'),q('Q2','unresolved')];assert.deepEqual(topologyExecutionTarget(topology,qubits,'Q1'),{resourceId:'Q1'});
 for(const id of ['design-A','Q2','ghost','missing']){const answer=topologyExecutionTarget(topology,qubits,id);assert.equal(answer.resourceId,undefined);assert.ok(answer.reason);}
 assert.equal(topologyExecutionTarget(topology,[q('Q1'),q('Q1')],'Q1').resourceId,undefined);
});
test('a confirmed dropdown resource needs no diagram but graph selection requires finite unambiguous coordinates',()=>{
 assert.equal(isConfirmedExecutionQubit(q('Q1')),true);assert.equal(topologyExecutionTarget(undefined,[q('Q1')],'Q1').resourceId,undefined);
 for(const value of [undefined,q('Q1','unresolved'),q('Q1','confirmed',' '),{id:'Q1',parameters:{}}])assert.equal(isConfirmedExecutionQubit(value as any),false);
 for(const nodes of [[{resourceId:'Q1',x:NaN,y:1}],[{resourceId:'Q1',x:1,y:Infinity}],[topology.nodes[0],topology.nodes[0]]])assert.equal(topologyExecutionTarget({nodes,edges:[]},[q('Q1')],'Q1').resourceId,undefined);
});
