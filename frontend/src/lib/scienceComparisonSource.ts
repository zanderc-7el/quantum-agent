import type {NodeDetail} from '../types';
import type {WorkspaceGateway} from './workspaceGateway';
import type {ResultComparisonSource} from './resultComparisonSource';
import {createScienceGateway} from './scienceGateway';
import type {ScienceRecord} from './scienceGateway';

function canonical(value:unknown):string {
 if(Array.isArray(value))return `[${value.map(canonical).join(',')}]`;
 if(value&&typeof value==='object')return `{${Object.entries(value).filter(([,v])=>v!==undefined).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>`${JSON.stringify(k)}:${canonical(v)}`).join(',')}}`;
 return JSON.stringify(value)??'null';
}

function rawEvidence(artifacts:unknown) {
 if(!Array.isArray(artifacts))throw new Error('该次采集缺少可追查的原始文件');
 const files=new Map<string,{id:string;sha256:string;kind:'raw'}>();
 for(const item of artifacts){
  if(item?.kind!=='raw')continue;
  if(typeof item.id!=='string'||!item.id||typeof item.sha256!=='string'||!/^[a-f0-9]{64}$/i.test(item.sha256))throw new Error('该次采集原始文件的来源不完整');
  if(files.has(item.id)&&files.get(item.id)!.sha256!==item.sha256)throw new Error('该次采集原始文件的来源不一致');
  files.set(item.id,{id:item.id,sha256:item.sha256,kind:'raw'});
 }
 return [...files.values()].sort((a,b)=>a.id.localeCompare(b.id));
}

function checkedAttempt(detail:NodeDetail,source:ResultComparisonSource){
 const attempts=detail?.attempts?.filter(a=>a.id===source.attemptId)??[];
 if(detail?.node?.id!==source.nodeId||attempts.length!==1||attempts[0].nodeId!==source.nodeId)throw new Error('未找到该节点的指定采集，请从测量结果重新打开');
 const attempt=attempts[0],result=attempt.result as any;
 if(!detail.node.resourceId||!attempt.procedureId||attempt.procedureId!==detail.node.procedureId)throw new Error('该次采集的比特或实验来源不完整');
 if(attempt.mode!=='real'||result?.dataKind!=='real'||typeof result.acquiredAt!=='string'||!result.acquiredAt)throw new Error('该次结果尚无可关联的真实采集时间');
 if(result.verificationPending)throw new Error('该次独立复测尚未完成，请完成后从主结果打开');
 if(!rawEvidence(result.artifacts).length)throw new Error('该次采集缺少可追查的原始文件');
 const evidence=rawEvidence([...result.artifacts,...(result.selectionResult?.artifacts??[])]);
 return {resourceId:detail.node.resourceId,procedureId:attempt.procedureId,acquiredAt:result.acquiredAt,
  parameters:result.parameters??attempt.parameters??{},partial:!!result.partial,quality:result.quality??{},evidence,
  independentlyVerified:attempt.state==='verified'&&result.quality?.status==='passed'&&!result.partial};
}

function sameReference(record:ScienceRecord,source:ResultComparisonSource){
 return record.kind==='measurement'&&record.reference?.workflowId===source.workflowId&&record.reference?.attemptId===source.attemptId;
}

function matchesMainResult(record:ScienceRecord,source:ResultComparisonSource,expected:ReturnType<typeof checkedAttempt>){
 if(!sameReference(record,source)||record.summary===true||record.dataKind!=='real'||record.scope?.workflowId!==source.workflowId)return false;
 const result=record.result;
 if(!result||result.dataKind!=='real')return false;
 let evidence;
 try { evidence=rawEvidence(record.evidence); } catch { return false; }
 const actual={resourceId:record.scope.resourceId,procedureId:result.procedureId,acquiredAt:result.acquiredAt,
  parameters:result.parameters??{},partial:!!result.partial,quality:result.quality??{},evidence,
  independentlyVerified:record.provenance?.independentlyVerified};
 return canonical(actual)===canonical(expected);
}

/** Resolve the selected attempt's main result, never an earlier selection snapshot or latest attempt. */
export async function loadComparisonSource(gateway:WorkspaceGateway,source:ResultComparisonSource,isCurrent:()=>boolean=()=>true):Promise<ScienceRecord>{
 const active=()=>{if(!isCurrent())throw new Error('已切换采集来源');};
 if([source.workflowId,source.nodeId,source.attemptId].some(v=>typeof v!=='string'||!v.trim()))throw new Error('节点采集来源不完整，请重新打开');
 const api=createScienceGateway(gateway);
 const nodePath=`/workflows/${encodeURIComponent(source.workflowId)}/nodes/${encodeURIComponent(source.nodeId)}`;
 const detail=await gateway.get<NodeDetail>(nodePath);active();
 const expected=checkedAttempt(detail,source),cursors=new Set<string>(),loaded=new Set<string>();
 let cursor:string|undefined;
 const readOnly = source.panel === 'signals';
 let match:ScienceRecord|undefined;
 for(;;){
  const page=await api.recordsPage(source.workflowId,cursor);active();
  if(!Array.isArray(page.records)||typeof page.hasMore!=='boolean')throw new Error('采集来源列表不完整，请重试');
  for(const summary of page.records){
   if(!sameReference(summary,source)||loaded.has(summary.id))continue;
   loaded.add(summary.id);
   const record=await api.record(summary.id);active();
   if(record.id!==summary.id||record.summary===true||!sameReference(record,source))throw new Error('返回记录与所选采集来源不一致');
   if(!matchesMainResult(record,source,expected)||(readOnly&&record.source!=='acquisition'))continue;
   if(record.evidenceIntact===false)throw new Error('所选采集的来源证据已失效，不能用于对照');
   if(!readOnly)return record;
   if(match)throw new Error('找到多条与本次主结果一致的归档，请核对来源记录');
   match=record;
  }
  if(!page.hasMore)break;
  if(!page.nextCursor||cursors.has(page.nextCursor))throw new Error('采集来源分页不完整，请重试；未自动关联其他采集');
  cursor=page.nextCursor;cursors.add(cursor);
 }
 if(readOnly){
  if(!match){
   const query=new URLSearchParams({workflowId:source.workflowId,nodeId:source.nodeId,attemptId:source.attemptId});
   const view=await gateway.get<ScienceRecord>('/science/attempt-signals?'+query);active();
   const bound=view?.readOnlyAttempt;
   if(!bound||bound.workflowId!==source.workflowId||bound.nodeId!==source.nodeId||bound.attemptId!==source.attemptId||
      !bound.operationId||view.source!=='acquisition'||!matchesMainResult(view,source,expected))
    throw new Error('本次测量结果尚无匹配的已登记采集归档或完成回执');
   match=view;
  }
  const fresh=await gateway.get<NodeDetail>(nodePath);active();
  if(canonical(checkedAttempt(fresh,source))!==canonical(expected))throw new Error('本次采集结果已更新，请重新打开');
  return match;
 }
 // Re-read before this reversible evidence-only write. The revision closes the
 // race between a selection result and the independently acquired main result.
 const fresh=await gateway.get<NodeDetail>(nodePath);active();
 if(canonical(checkedAttempt(fresh,source))!==canonical(expected))throw new Error('该次采集结果刚更新，请重试载入');
 if(fresh.workflowRevision===undefined||!Number.isSafeInteger(fresh.workflowRevision)||fresh.workflowRevision<0)throw new Error('采集来源版本不完整，请重新打开');
 const body={kind:'measurement',input:{},scope:{workflowId:source.workflowId,resourceId:expected.resourceId},
  reference:{workflowId:source.workflowId,attemptId:source.attemptId},expectedRevision:fresh.workflowRevision};
 const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(body)));active();
 const operationId='science-comparison-bind/v1/'+[...new Uint8Array(hash)].map(v=>v.toString(16).padStart(2,'0')).join('');
 const record=await api.save(body.kind,body.input,body.scope,{reference:body.reference,expectedRevision:body.expectedRevision,operationId});active();
 if(!matchesMainResult(record,source,expected)||record.evidenceIntact===false)throw new Error('关联结果与指定采集来源不一致，请重新打开');
 return record;
}
