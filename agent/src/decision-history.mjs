import path from 'node:path';

export const historyDefinition={name:'search_historical_measurements',inputSchema:{type:'object',properties:{limit:{type:'number'}},additionalProperties:false}};
const fields=['schema','runId','nodeId','revision','procedureId','resourceId'];
const id=v=>typeof v==='string'&&/^[A-Za-z0-9_.:-]{1,160}$/.test(v)&&!v.includes('..');
export function historyCapability(input){
 const b=input?.historicalEvidenceSearch;
 if(input?.mode!=='real'||!b||Object.keys(b).length!==fields.length||b.schema!=='qcal-historical-evidence-search/1'
  ||!Number.isSafeInteger(b.revision)||b.revision<0||b.runId!==input.runId||b.nodeId!==input.nodeId||b.revision!==input.revision
  ||!['runId','nodeId','procedureId','resourceId'].every(k=>id(b[k])))return null;
 return b;
}
export const historyGuidance='可主动调用 search_historical_measurements 检索本测项已登记且首测/独立复测通过的历史实测。服务按当前决策作用域筛选并验原始回执；无需知道旧任务编号。历史结果仅供诊断参考，必须核对 physicalIdentity、parameterDifferences、版本和首复实际参数。相同标签/机台路由不证明芯片或片端条件相同，不得自动采用旧校准或宣称本轮通过。历史 caseRef/sourceAttempt/sourceReferences 不能放入本轮 evidenceIds；下一动作仍只能用当前证据、allowedActions 和 parameterBounds。没有合格结果只表示本次有限查询无可用参考。';
export function createDecisionHistory({root,baseUrl='http://127.0.0.1:8765',request=globalThis.fetch}={}){
 const base=new URL(baseUrl),normalize=p=>path.resolve(String(p)).replaceAll('\\','/').toLowerCase();
 if(!root||base.protocol!=='http:'||base.hostname!=='127.0.0.1'||base.username||base.password||base.pathname!=='/'||base.search||base.hash)throw new Error('history_address_not_allowed');
 async function get(route){
  const response=await request(new URL(route,base),{method:'GET',redirect:'error',signal:AbortSignal.timeout(8000),headers:{Accept:'application/json'}});
  if(!response.ok)throw new Error('history_read_failed_'+response.status);
  let total=0;const chunks=[];for await(const part of response.body){total+=part.byteLength;if(total>15000)throw new Error('history_response_too_large');chunks.push(part);}
  try{return JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{throw new Error('history_response_invalid');}
 }
 return {async execute(args,input){
  const b=historyCapability(input),limit=args?.limit??2;
  if(!b)throw new Error('history_scope_not_allowed');
  if(!args||Object.keys(args).some(k=>k!=='limit')||!Number.isSafeInteger(limit)||limit<1||limit>3)throw new Error('history_arguments_invalid');
  const value=await get(`/api/workflows/${encodeURIComponent(b.runId)}/nodes/${encodeURIComponent(b.nodeId)}/historical-evidence?revision=${b.revision}&limit=${limit}`);
  if(value.projectId!=='qcal-local-f8-platform'||normalize(value.projectRoot)!==normalize(root))throw new Error('history_identity_mismatch');
  if(value.schema!=='qcal-historical-measurements/1'||!fields.every(k=>value.binding?.[k]===b[k])
   ||value.readOnly!==true||value.currentCalibration!==false||value.executionAuthorized!==false
   ||!Array.isArray(value.cases)||value.cases.length>limit)throw new Error('history_response_binding_mismatch');
  return {ok:true,...value};
 }};
}
