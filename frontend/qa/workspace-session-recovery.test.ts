import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkspaceGateway} from '../src/lib/workspaceGateway.ts';
const response=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status});
test('explicit pre-dispatch invalid session refreshes once and preserves frozen operation body',async()=>{
 const payload={operationId:'same',message:'只测当前步骤',intent:'execute'};
 const writes:RequestInit[]=[];const refreshes:(boolean|undefined)[]=[];
 const api=createWorkspaceGateway(async(refresh?:boolean)=>{refreshes.push(refresh);return {token:refresh?'fresh':'old'};},(async(_url,init)=>{
  writes.push(init!);if(writes.length===1){payload.operationId='changed';return response({error:{code:'INVALID_SESSION',message:'expired'}},401);}return response({ok:true});
 }) as typeof fetch);
 assert.deepEqual(await api.write('/conversations/c/messages',payload),{ok:true});
 assert.deepEqual(refreshes,[undefined,true]);assert.equal(writes.length,2);assert.equal(writes[0].body,writes[1].body);
 assert.equal(JSON.parse(String(writes[1].body)).operationId,'same');assert.equal((writes[1].headers as Record<string,string>)['X-Qcal-Token'],'fresh');
});
test('other auth errors, lost receipts and repeated invalid-session never loop',async()=>{
 for(const [status,code,expected] of [[401,'OTHER',1],[500,'UNKNOWN',1],[401,'INVALID_SESSION',2]] as const){
  let sends=0;const api=createWorkspaceGateway(async()=>({token:'test'}),(async()=>{sends++;return response({error:{code,message:'failure'}},status);}) as typeof fetch);
  await assert.rejects(api.write('/conversations/c/messages',{operationId:'same'}));assert.equal(sends,expected);
 }
});
