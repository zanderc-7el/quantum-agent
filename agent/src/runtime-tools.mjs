import path from 'node:path';
import {validateDocumentArguments,documentDirectory,documentExcerpt,finishDocumentPage} from './document-tools.mjs';

const id=value=>typeof value==='string'&&/^[A-Za-z0-9_.:-]{1,160}$/.test(value)&&!value.includes('..');
const secret=/api.?key|token|password|secret|authorization|cookie|credential/i;
const redact=value=>value.replace(/\bBearer\s+[^\s,;"']+/gi,'Bearer [redacted]')
 .replace(/["']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|token)["']?\s*[:=]\s*["']?[^\s,;"'}]+/gi,'[credential redacted]')
 .replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,'[private key redacted]')
 .replace(/\b(?:sk|ghp|github_pat)[_-][A-Za-z0-9_-]{20,}\b/g,'[credential redacted]')
 .replace(/https?:\/\/[^\s/:]+:[^\s/@]+@/gi,'[credentials redacted]@');
const selected=(value,keys)=>Object.fromEntries(keys.filter(k=>value?.[k]!==undefined).map(k=>[k,value[k]]));
const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);

function decimalScale(value,places){
 const [coefficient,exponent='0']=String(value).toLowerCase().split('e');
 const negative=coefficient.startsWith('-'),unsigned=negative?coefficient.slice(1):coefficient;
 const [whole,fraction='']=unsigned.split('.'),digits=whole+fraction;
 const point=whole.length+Number(exponent)-places;
 let output=point<=0?'0.'+'0'.repeat(-point)+digits:
  point>=digits.length?digits+'0'.repeat(point-digits.length):digits.slice(0,point)+'.'+digits.slice(point);
 output=output.replace(/^0+(?=\d)/,'');
 if(output.includes('.'))output=output.replace(/0+$/,'').replace(/\.$/,'');
 return (negative?'-':'')+output;
}

function frequencyUnits(parameters){
 const entries=Object.entries(parameters).filter(([key,value])=>/_hz$/i.test(key)&&!secret.test(key)&&typeof value==='number'&&Number.isFinite(value));
 if(!entries.length)return {};
 // Shift the decimal representation already returned by the controller; binary
 // division/toFixed would round it again. This adds no measurement precision.
 return {frequencyUnits:{basis:'decimal_scaling_of_returned_Hz_value',measurementPrecisionAdded:false,
  values:Object.fromEntries(entries.map(([key,value])=>[key,{Hz:String(value),MHz:decimalScale(value,6),GHz:decimalScale(value,9)}]))}};
}

function parameterView(parameters,{workflowId,nodeId,route,field,meaning,...metadata}){
 return {status:record(parameters)?'available':'unavailable',workflowId,nodeId,meaning,
  source:{path:route,field},...metadata,parametersComplete:false,
  ...(record(parameters)?{parameters,...frequencyUnits(parameters)}:{reason:'effective_parameters_unavailable'})};
}

function effectiveParameters(detail,workflowId,nodeId,route,metadata={}){
 return parameterView(detail?.parameters,{workflowId,nodeId,route,field:'parameters',
  meaning:'effective_configuration_not_instrument_readback',...metadata});
}

function prioritizeNodeDetail(detail,workflowId,nodeId,route){
 const latest=detail.attempts.find(a=>a.id===detail.node.latestAttemptId);
 // The detail's top-level parameters are freshly composed by the controller.
 // node.parameters and attempt.parameters are saved snapshots, not fallbacks.
 return {effectiveParameters:effectiveParameters(detail,workflowId,nodeId,route),
  recordedNodeParameters:parameterView(detail.node.parameters,{workflowId,nodeId,route,field:'node.parameters',
   meaning:'recorded_node_snapshot',...selected(detail.node,['parameterVersions'])}),
  latestAttemptParameters:latest?parameterView(latest.parameters,{workflowId,nodeId,route,field:'attempts.parameters',
   meaning:'attempt_dispatch_snapshot',attemptId:latest.id,...selected(latest,['state','operationId','parameterVersions'])}):null,
  node:{...selected(detail.node,['id','resourceId','procedureId','status','engineState','latestAttemptId','parameters','parameterVersions']),...detail.node},
  ...Object.fromEntries(Object.entries(detail).filter(([key])=>key!=='node'))};
}

function markParameterCompleteness(original,output){
 for(const key of ['currentEffectiveParameters','effectiveParameters','recordedNodeParameters','latestAttemptParameters']){
  const before=original?.[key],after=output?.[key];
  if(before?.status!=='available'||!record(after))continue;
  after.parametersComplete=JSON.stringify(before.parameters)===JSON.stringify(after.parameters);
  if(!after.parametersComplete)after.status='partial';
  if(before.frequencyUnits){
   const complete=JSON.stringify(before.frequencyUnits)===JSON.stringify(after.frequencyUnits);
   after.frequencyConversionStatus=complete?'complete':'omitted_or_partial';
   if(!complete)delete after.frequencyUnits;
  }
 }
}

function summarizeNode(n){
 return {...selected(n,['id','procedureId','status','engineState']),dependsOn:n.dependsOn??[],
  attempt:n.attempt??null,...selected(n,['latestAttemptId'])};
}

function currentDiagnostic(w){
 const current=w.nodes?.find(n=>n.id===w.currentNodeId);
 if(!current)return null;
 const byId=new Map(w.nodes.map(n=>[n.id,n])),prerequisites={satisfied:[],unsatisfied:[],unknown:[]};
 for(const dependencyId of current.dependsOn??[]){
  const dependency=byId.get(dependencyId);
  if(!dependency||typeof dependency.engineState!=='string')prerequisites.unknown.push(dependencyId);
  // Match the controller scheduler: dependency readiness is verified state,
  // not array position, a method's static definition, or a prior success label.
  else prerequisites[dependency.engineState==='verified'?'satisfied':'unsatisfied'].push(
   selected(dependency,['id','procedureId','status','engineState']));
 }
 return {...selected(current,['id','procedureId','status','engineState','detail','latestAttemptId']),
  qualityStatus:current.result?.quality?.status??null,qualityReasons:current.result?.quality?.reasons??[],
  ...selected(current.result,['dataKind','partial','stale','verificationPending']),
  freshAcquisition:current.result?.provenance?.freshAcquisition===true,prerequisites,
  allPrerequisitesVerified:prerequisites.unknown.length===0&&prerequisites.unsatisfied.length===0};
}

function summarizeWorkflow(w,currentEffectiveParameters){
 const nodes=(w.nodes??[]).map(summarizeNode);
 // A complete status/dependency index comes before optional metadata. Never
 // let one node's curves or telemetry consume the other nodes' status budget.
 return {...selected(w,['id','revision','mode','policy','status','engineState','currentNodeId','resourceIds']),
  ...(currentEffectiveParameters?{currentEffectiveParameters}:{}),
  nodeSummary:{total:nodes.length,returned:0,complete:false},nodes,currentNodeDiagnostic:currentDiagnostic(w),
  ...selected(w,['name','coverage','cancelRequested','configurationIssue','hardwareConfirmed','adapterId','adapterProfileHash','chipVersionId','deploymentVersionId']),
  events:(w.events??[]).slice(-4).map(e=>selected(e,['type','eventType','createdAt','message'])),
  detailAccess:{tool:'inspect_workflow',workflowId:w.id,requires:'nodeId'}};
}

function markNodeCompleteness(original,output){
 if(Array.isArray(original)&&Array.isArray(output)){
  output.forEach((item,index)=>markNodeCompleteness(original[index],item));return;
 }
 if(!original?.nodeSummary||!output||typeof output!=='object')return;
 const expected=new Map(original.nodes.map(node=>[node.id,node]));
 // Drop a partially serialized final row instead of presenting missing fields
 // as known-empty state. The counts explicitly expose an incomplete index.
 output.nodes=(output.nodes??[]).filter(node=>node&&JSON.stringify(node)===JSON.stringify(expected.get(node.id)));
 output.nodeSummary={total:original.nodes.length,returned:output.nodes.length,complete:output.nodes.length===original.nodes.length};
}

function summarizeReport(report){
 const workflow=report.workflow;
 const nodes=report.nodes.map(row=>{
  const node=row.node,result=node.result??{};
  // Match human_reports.accepted: a passing historical fit alone is not a
  // current independent verification or a complete workflow.
  const accepted=workflow.mode==='real'&&result.dataKind==='real'&&result.provenance?.freshAcquisition===true
    &&node.engineState==='verified'&&result.quality?.status==='passed'&&!result.partial&&!result.stale&&!result.verificationPending;
  const figures=[];
  for(const attempt of row.attempts??[]){
   const stages=attempt.result?.selectionResult?[['selection',attempt.result.selectionResult],['verification',attempt.result]]:[['acquisition',attempt.result??{}]];
   for(const [stage,value] of stages)for(const artifact of value.artifacts??[]){
    if(artifact.kind==='image')figures.push({artifactId:artifact.id,attemptId:attempt.id,stage,...selected(artifact,['sha256'])});
   }
  }
  return {...selected(node,['id','label','procedureId','status','engineState','latestAttemptId']),accepted,
   ...selected(result,['dataKind','quality','partial','stale','verificationPending','metrics']),
   attemptCount:(row.attempts??[]).length,figureCount:figures.length,figures};
 });
 return {...selected(report,['schema','generatedAt','source','workflowComplete']),
  workflow:selected(workflow,['id','revision','mode','status','engineState','resourceIds','coverage']),
  summary:{total:nodes.length,accepted:nodes.filter(n=>n.accepted).length,notAccepted:nodes.filter(n=>!n.accepted).length,
   figures:nodes.reduce((sum,node)=>sum+node.figureCount,0)},nodes};
}

function bounded(value){
 let truncated=false,remaining=8500;
 const visit=(item,depth=0)=>{
  if(remaining<=0||depth>10){truncated=true;return '[truncated]';}
  if(typeof item==='string'){item=redact(item);const length=Math.min(item.length,800,remaining);remaining-=length;if(length<item.length)truncated=true;return item.slice(0,length);}
  if(Array.isArray(item)){const out=[];for(const row of item.slice(0,30)){if(remaining<100){truncated=true;break;}remaining-=10;out.push(visit(row,depth+1));}if(out.length<item.length)truncated=true;return out;}
  if(item&&typeof item==='object'){const out={};for(const [key,v] of Object.entries(item)){if(secret.test(key))continue;if(remaining<100){truncated=true;break;}remaining-=key.length+8;out[key]=visit(v,depth+1);}return out;}
  remaining-=20;return item;
 };
 let data=visit(value),budget=8500;
 while(JSON.stringify(data).length>11000){budget=Math.floor(budget/2);remaining=budget;truncated=true;data=visit(value);}
 return {data,truncated};
}

export function createRuntimeTools({root,baseUrl='http://127.0.0.1:8765',request=globalThis.fetch}={}){
 const base=new URL(baseUrl);
 if(!root||base.protocol!=='http:'||base.hostname!=='127.0.0.1'||base.username||base.password||base.pathname!=='/'||base.search||base.hash)throw new Error('runtime_address_not_allowed');
 const normalize=p=>path.resolve(String(p)).replaceAll('\\','/').toLowerCase();
 async function get(route){
  const response=await request(new URL(route,base),{method:'GET',redirect:'error',signal:AbortSignal.timeout(8000),headers:{Accept:'application/json'}});
  if(!response.ok)throw new Error('runtime_read_failed_'+response.status);
  let raw='';const decoder=new TextDecoder();for await(const chunk of response.body){raw+=decoder.decode(chunk,{stream:true});if(raw.length>8000000)throw new Error('runtime_response_too_large');}raw+=decoder.decode();
  try{return JSON.parse(raw);}catch{throw new Error('runtime_response_invalid');}
 }
 async function execute(name,args={},context={}){
  let route,kind=args.kind;
  const documentRead=name==='list_documents'||name==='read_document';
  if(documentRead){
   validateDocumentArguments(args,name==='read_document');
   // Global uploads are a workspace capability, not an expansion of campaign
   // or workflow scope. Attached-only reads remain in Brain's context branch.
   if(context.scope?.kind!=='workspace')throw new Error('runtime_scope_not_allowed');
   route='/api/chip-imports'+(name==='read_document'?'/'+encodeURIComponent(args.documentId):'');
  }else if(name==='inspect_runtime'){
   if(Object.keys(args).some(k=>k!=='kind')||!['health','catalog','resources','workflows','attention'].includes(kind))throw new Error('runtime_tool_arguments_invalid');
   route='/api/'+({health:'health',catalog:'catalog',resources:'resources',workflows:'workflows',attention:'attention-items'}[kind]);
  }else if(name==='inspect_workflow'){
   if(Object.keys(args).some(k=>!['workflowId','nodeId'].includes(k))||!id(args.workflowId)||(args.nodeId!==undefined&&!id(args.nodeId)))throw new Error('runtime_tool_arguments_invalid');
   route='/api/workflows/'+encodeURIComponent(args.workflowId)+(args.nodeId?'/nodes/'+encodeURIComponent(args.nodeId):'');
  }else if(name==='inspect_configuration'){
   if(Object.keys(args).some(k=>!['kind','id'].includes(k))||!['chip','deployment'].includes(kind)||!id(args.id))throw new Error('runtime_tool_arguments_invalid');
   route='/api/'+(kind==='chip'?'chips':'deployments')+'/'+encodeURIComponent(args.id);
  }else if(name==='inspect_report'){
   if(Object.keys(args).some(k=>k!=='workflowId')||!id(args.workflowId))throw new Error('runtime_tool_arguments_invalid');
   route='/api/workflows/'+encodeURIComponent(args.workflowId)+'/report';
  }else if(name==='inspect_report_export'){
   if(Object.keys(args).some(k=>k!=='exportId')||!id(args.exportId))throw new Error('runtime_tool_arguments_invalid');
   route='/api/report-exports/'+encodeURIComponent(args.exportId);
  }else throw new Error('runtime_tool_not_allowed');
  const scope=context.scope;
  if(!scope||!['workspace','campaign'].includes(scope.kind)||(scope.kind==='campaign'&&!id(scope.id)))throw new Error('runtime_scope_not_allowed');
  const health=await get('/api/health');
  if(health.projectId!=='qcal-local-f8-platform'||normalize(health.projectRoot)!==normalize(root))throw new Error('runtime_identity_mismatch');
  let campaign;
  if(scope.kind==='campaign'){
   campaign=await get('/api/campaigns/'+encodeURIComponent(scope.id));
   if(campaign?.id!==scope.id||!Array.isArray(campaign.children))throw new Error('runtime_response_invalid');
   if(['inspect_workflow','inspect_report'].includes(name)&&!campaign.children?.some(c=>c.workflowId===args.workflowId))throw new Error('runtime_scope_not_allowed');
   if(name==='inspect_configuration'&&args.id!==campaign[kind==='chip'?'chipVersionId':'deploymentVersionId'])throw new Error('runtime_scope_not_allowed');
   if(name==='inspect_runtime'&&['catalog','resources'].includes(kind))throw new Error('runtime_scope_not_allowed');
  }
  let data=route==='/api/health'?health:await get(route);
  if(!data||typeof data!=='object'||data.error)throw new Error('runtime_response_invalid');
  if(name==='list_documents'){
   if(!Array.isArray(data)||data.some(d=>!id(d?.id)||typeof d.filename!=='string'))throw new Error('runtime_response_invalid');
   data=documentDirectory(data,args);
  }
  if(name==='read_document'){
   if(data.id!==args.documentId||typeof data.filename!=='string'||!Array.isArray(data.blocks))throw new Error('runtime_response_invalid');
   data=documentExcerpt(data,args);
  }
  if(name==='inspect_workflow'){
   if(args.nodeId){if(data.node?.id!==args.nodeId||!Array.isArray(data.attempts))throw new Error('runtime_response_invalid');}
   else if(data.id!==args.workflowId||typeof data.status!=='string'||!Array.isArray(data.nodes))throw new Error('runtime_response_invalid');
  }
  if(name==='inspect_configuration'&&data.id!==args.id)throw new Error('runtime_response_invalid');
  if(name==='inspect_report'){
   if(data.schema!=='qcal-report/1'||data.workflow?.id!==args.workflowId||!Array.isArray(data.nodes)||data.nodes.some(row=>!row.node||typeof row.node.id!=='string'))throw new Error('runtime_response_invalid');
   data=summarizeReport(data);
  }
  if(name==='inspect_report_export'){
   if(data.id!==args.exportId||!id(data.workflowId)||!['queued','processing','completed','failed','interrupted'].includes(data.status))throw new Error('runtime_response_invalid');
   if(campaign&&!campaign.children.some(c=>c.workflowId===data.workflowId))throw new Error('runtime_scope_not_allowed');
   data=selected(data,['id','workflowId','revision','status','format','appendixCount','createdAt','endedAt','snapshotSha256','sha256','bytes','filename','error',...(data.status==='completed'?['downloadUrl']:[])]);
   if(data.downloadUrl!==undefined&&data.downloadUrl!==route+'/file')throw new Error('runtime_response_invalid');
  }
  if(name==='inspect_runtime'&&['workflows','attention','resources'].includes(kind)&&!Array.isArray(data.items??data))throw new Error('runtime_response_invalid');
  if(name==='inspect_runtime'){
   if(kind==='health')data=selected(data,['status','capabilities','hardware','version']);
   if(kind==='workflows')data=(data.items??data).filter(w=>!campaign||campaign.children?.some(c=>c.workflowId===w.id)).map(w=>summarizeWorkflow(w));
   if(kind==='attention'&&campaign)data=(data.items??data).filter(a=>campaign.children?.some(c=>c.workflowId===a.workflowId));
  }
  if(name==='inspect_workflow'&&args.nodeId)data=prioritizeNodeDetail(data,args.workflowId,args.nodeId,route);
  if(name==='inspect_workflow'&&!args.nodeId){
   const currentId=data.currentNodeId;
   let current;
   if(currentId){
    const detailRoute=route+'/nodes/'+encodeURIComponent(currentId);
    const metadata={workflowRevisionBeforeRead:data.revision,snapshotConsistency:'separate_read'};
    current=effectiveParameters(null,args.workflowId,currentId,detailRoute,metadata);
    // Never let an untrusted response choose another task or arbitrary route.
    if(id(currentId)&&data.nodes.some(n=>n.id===currentId)){
     try{
      const detail=await get(detailRoute);
      if(!detail||detail.error||detail.node?.id!==currentId||!Array.isArray(detail.attempts))throw new Error('runtime_response_invalid');
      current=effectiveParameters(detail,args.workflowId,currentId,detailRoute,metadata);
     }catch{current.reason='effective_parameter_read_failed';}
    }
   }
   data=summarizeWorkflow(data,current);
  }
  const result=bounded(data);
  markParameterCompleteness(data,result.data);
  if(documentRead)finishDocumentPage(data,result.data);
  const workflowSummary=name==='inspect_workflow'&&!args.nodeId||name==='inspect_runtime'&&kind==='workflows';
  if(workflowSummary)markNodeCompleteness(data,result.data);
  return {ok:true,tool:name,checkedAt:new Date().toISOString(),source:{path:route},...result,
   ...(workflowSummary?{detailsOmitted:true,truncated:true}: {})};
 }
 return {execute};
}
