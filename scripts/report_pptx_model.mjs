// Pure presentation projection. Does not access files, services or hardware.
const freshResult = result => result?.dataKind === 'real' && result.provenance?.freshAcquisition === true;
const historicalResult = result => result?.dataKind === 'historical' || result?.provenance?.freshAcquisition === false;
const pendingVerification = result => result?.verificationPending === true
  && result.selectionResult !== null && typeof result.selectionResult === 'object'
  && !Array.isArray(result.selectionResult);

/** Unmeasured nodes remain in the summary; result pages need evidence or an attempted acquisition. */
export function readerResultNodes(model, report) {
  const attempts = new Map((report.nodes || []).map(item => [item.node.id, item.node.attempt || 0]));
  return model.nodes.filter(node => node.figures?.length || node.metrics?.length
    || node.history?.length || attempts.get(node.id) > 0);
}

function selectionComplete(result) {
  return pendingVerification(result) && result.selectionResult.partial !== true
    && result.selectionResult.stale !== true && result.selectionResult.analysisQuality?.accepted === true;
}

/** The controller's empty verification placeholder has no acquisition provenance yet. */
function unacquiredVerification(stage, verification) {
  const result=stage.result,selection=result.selectionResult;
  const emptyMetrics=result.metrics==null || (typeof result.metrics==='object'
    && !Array.isArray(result.metrics) && Object.keys(result.metrics).length===0);
  return stage.role==='verification' && verification !== null
    && result.dataKind==='real' && result.partial===true && result.quality?.status==='unverified'
    && result.provenance==null && !result.acquiredAt && !result.analysisQuality
    && emptyMetrics && Array.isArray(result.artifacts) && result.artifacts.length===0
    && freshResult(selection) && selectionComplete(result) && Boolean(selection.acquiredAt)
    && Array.isArray(selection.artifacts) && selection.artifacts.length>0;
}

/** A persisted placeholder alone cannot prove that a verification is still running. */
export function verificationProgress(attempt, {workflow, node} = {}) {
  if (!pendingVerification(attempt?.result)) return null;
  const active = workflow?.mode === 'real' && workflow.status === 'running'
    && !workflow.cancelRequested && workflow.currentNodeId === node?.id
    && node?.engineState === 'verifying' && !node.result?.stale
    && pendingVerification(node.result) && node.result.quality?.status === 'unverified'
    && typeof attempt.id === 'string' && attempt.id.length > 0 && node.latestAttemptId === attempt.id
    && attempt.nodeId === node.id && attempt.number === node.attempt
    && attempt.state === 'running' && !attempt.endedAt && !attempt.error
    && attempt.result.quality?.status === 'unverified' && !attempt.result.stale
    && selectionComplete(attempt.result) && selectionComplete(node.result);
  return active ? '首测完成，独立复测进行中' : '独立复测未完成';
}

export function stages(attempt, context = {}) {
  const result = attempt.result;
  if (!result) return [];
  const verification = verificationProgress(attempt, context);
  const rows = result.selectionResult
    ? [{role:'selection', label:'候选测量', result:result.selectionResult}, {role:'verification',label:'独立复测',result}]
    : [{role:'measurement',label:'本次采集',result}];
  return rows.map(stage => ({...stage, verificationProgress:verification,
    acquisition:stage.role === 'verification' && verification ? verification
    : stage.role === 'selection' && verification && selectionComplete(result) ? '首测完成'
    : stage.result.partial === true ? '部分数据'
    : stage.result.analysisQuality ? '已完成采集' : '采集完整性待确认',
    analysis:stage.result.analysisQuality?.accepted===true?'原生分析通过'
      :stage.result.analysisQuality?.accepted===false?'原生分析未通过':null,
    quality:context.workflow?.coverage === 'working_point' && unacquiredVerification(stage,verification) ? '尚未验收'
      :context.workflow?.coverage === 'working_point' && !freshResult(stage.result)
      ? historicalResult(stage.result) ? '历史记录（不计本次验收）' : '来源待核对'
      :stage.role === 'selection' && verification && selectionComplete(result) ? '待独立验收'
      :stage.result.quality?.status === 'passed' ? '本阶段通过'
      : stage.result.quality?.status === 'failed' ? '未通过' : '尚未验收'}));
}

export function currentStatus(node, context = {}) {
  if (!node.attempt) return '待测';
  if (pendingVerification(node.result)) {
    if (node.engineState === 'stale' || node.result.stale) return '需复测';
    return verificationProgress(context.attempt, {...context, node}) || '独立复测未完成';
  }
  if (node.result?.partial) return '部分数据';
  if (node.engineState === 'stale' || node.result?.stale) return '需复测';
  if (context.workflow?.coverage === 'working_point' && node.result && !freshResult(node.result)) {
    return historicalResult(node.result) ? '历史参考（不计本次通过）' : '来源待核对';
  }
  if (node.engineState === 'verified' && node.result?.quality?.status === 'passed') return '独立复测通过';
  if (['acquiring','verifying'].includes(node.engineState)) return node.engineState === 'verifying' ? '独立复测中' : '采集中';
  if (node.result?.quality?.status === 'failed') return '未通过';
  return '待处理';
}

export function projectReport(report) {
  if (report?.schema !== 'qcal-report/1' || !report.workflow || !Array.isArray(report.nodes)) throw new Error('Expected frozen qcal-report/1');
  const pending=[...report.nodes],ordered=[],seen=new Set();
  while(pending.length){
    const index=pending.findIndex(item=>(item.node.dependsOn||[]).every(id=>seen.has(id)||!report.nodes.some(row=>row.node.id===id)));
    if(index<0)throw new Error('Report contains a dependency cycle');
    const [item]=pending.splice(index,1);ordered.push(item);seen.add(item.node.id);
  }
  const rows=ordered.map(item=>({...item,status:currentStatus(item.node,{workflow:report.workflow,
    attempt:item.attempts?.find(attempt=>attempt.id===item.node.latestAttemptId)})}));
  const verified=rows.filter(item=>item.status==='独立复测通过').length;
  const scoped=report.workflow.coverage==='working_point';
  const scope=scoped?{kind:'working_point',selectedNodeCount:report.workflow.completionScope?.selectedNodeCount,
    actualNodeCount:rows.length,full13Fresh:false,seedReference:report.workflow.seedReference??null,
    description:'从 Rabi 开始的 10 项脉冲测量；读出找腔、功率扫描和比特定频沿用参考初值。',
    seedMeaning:'seed_only：旧工作点仅作初值，历史数据和通过数不计本次验收。',
    valid:report.workflow.completionScope?.selectedNodeCount===10 && rows.length===10
      && report.workflow.completionScope?.full13Fresh===false && report.workflow.seedReference?.status==='seed_only'}:null;
  const complete=report.workflow.mode==='real' && report.workflowComplete===true && rows.length>0 && verified===rows.length
    && rows.every(item=>freshResult(item.node.result)) && (!scope || scope.valid);
  const figures=[];
  for(const item of rows) for(const attempt of item.attempts||[]) for(const stage of stages(attempt,{workflow:report.workflow,node:item.node})) {
    for(const artifact of stage.result.artifacts||[]) if(artifact.kind==='image') figures.push({
      workflowId:report.workflow.id, adapterId:report.workflow.adapterId, adapterProfileHash:report.workflow.adapterProfileHash,
      node:item.node, attempt, stage, artifact, imageKey:JSON.stringify([report.workflow.id,artifact.id]),
      source:report.workflow.mode==='replay' || stage.result.dataKind==='historical' || stage.result.provenance?.freshAcquisition===false ? '历史数据'
        :stage.result.dataKind==='real' && stage.result.provenance?.freshAcquisition===true?'本轮实测':'来源待核对',
      key:JSON.stringify([report.workflow.id,item.node.id,attempt.id,stage.role,artifact.id])});
  }
  return {rows,figures,verified,complete,scope,
    completionLabel:scope ? complete?'所选 10 项脉冲范围验收通过':'所选脉冲范围尚未全部通过'
      :complete?'流程验收通过':'流程尚未全部通过',
    title:scope ? complete?'Q11 工作点复刻实测结果':'Q11 工作点复刻阶段报告'
      :complete?'Q11 单比特实测结果':'Q11 单比特实测阶段报告'};
}

/** A bundle is a sequence of independent task reports, never one merged run. */
export function projectReports(input) {
  const reports=input?.schema==='qcal-report-bundle/1'?input.reports:[input];
  if(!Array.isArray(reports)||!reports.length)throw new Error('Report bundle must contain tasks');
  const seen=new Set();
  const groups=reports.map((report,index)=>{
    const id=report?.workflow?.id;
    if(typeof id!=='string'||!id.trim()||seen.has(id))throw new Error('Missing or duplicate workflow identity');
    seen.add(id);
    const projected=projectReport(report);
    const times=projected.rows.flatMap(item=>(item.attempts||[]).flatMap(attempt=>stages(attempt,{workflow:report.workflow,node:item.node})
      .filter(stage=>!projected.scope || freshResult(stage.result))
      .map(stage=>stage.result.acquiredAt))).filter(value=>typeof value==='string'&&Number.isFinite(Date.parse(value)))
      .sort((a,b)=>Date.parse(a)-Date.parse(b));
    return {report,projected,identity:{workflowId:id,taskNumber:index+1,revision:report.workflow.revision,
      adapterId:report.workflow.adapterId,adapterProfileHash:report.workflow.adapterProfileHash,
      generatedAt:report.generatedAt,mode:report.workflow.mode,
      acquisitionFrom:times[0]??null,acquisitionTo:times.at(-1)??null,
      verified:projected.verified,total:projected.rows.length,workflowComplete:projected.complete,
      ...(projected.scope?{coverage:'working_point',full13Fresh:false,completionLabel:projected.completionLabel,
        selectedNodeCount:projected.scope.selectedNodeCount,seedStatus:projected.scope.seedReference?.status??null,
        seedSourceId:projected.scope.seedReference?.sourceId??null}: {})}};
  });
  return {groups,multiple:groups.length>1};
}
