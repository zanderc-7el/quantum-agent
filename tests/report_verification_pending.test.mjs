// Pure offline projection regressions. No database, model or hardware access.
import test from 'node:test';
import assert from 'node:assert/strict';
import {projectReport, stages, currentStatus, verificationProgress} from '../scripts/report_pptx_model.mjs';

const activeText='首测完成，独立复测进行中', unfinishedText='独立复测未完成';
function fixture() {
  const selection={dataKind:'real',acquiredAt:'2026-11-18T02:39:20Z',analysisQuality:{accepted:true},
    quality:{status:'unverified',reasons:['需要独立采集复测']},metrics:{contrast_snr:16.474625},
    artifacts:[{id:'offline-image',kind:'image',sha256:'offline-only'}],provenance:{freshAcquisition:true}};
  const result={dataKind:'real',partial:true,verificationPending:true,
    quality:{status:'unverified'},artifacts:[],selectionResult:selection};
  const attempt={id:'attempt-current',nodeId:'power',number:1,state:'running',result:structuredClone(result)};
  const node={id:'power',resourceId:'Q11',attempt:1,engineState:'verifying',latestAttemptId:attempt.id,result};
  const workflow={id:'offline-workflow',mode:'real',status:'running',currentNodeId:node.id};
  const report={schema:'qcal-report/1',workflow,workflowComplete:false,nodes:[{node,attempts:[attempt]}]};
  return {report,workflow,node,attempt};
}

test('current independent verification is distinct from partial data and never counts as passed',()=>{
  const {report,workflow,node,attempt}=fixture(), before=JSON.stringify(report);
  const projection=projectReport(report), [selection,verification]=stages(attempt,{workflow,node});
  assert.equal(projection.rows[0].status,activeText);
  assert.equal(selection.acquisition,'首测完成');
  assert.equal(selection.quality,'待独立验收');
  assert.equal(selection.analysis,'原生分析通过');
  assert.equal(verification.acquisition,activeText);
  assert.equal(verification.quality,'尚未验收');
  assert.equal(projection.verified,0);assert.equal(projection.complete,false);
  assert.equal(projection.figures.length,1);
  assert.equal(projection.figures[0].stage.verificationProgress,activeText);
  assert.equal(JSON.stringify(report),before);
});

test('paused, stopped and non-running tasks never present pending verification as active',async t=>{
  for(const status of ['paused','aborted','attention','failed','completed','queued','draft']) await t.test(status,()=>{
    const f=fixture();f.workflow.status=status;
    assert.equal(projectReport(f.report).rows[0].status,unfinishedText);
    assert.equal(stages(f.attempt,f).at(-1).acquisition,unfinishedText);
    assert.equal(projectReport(f.report).verified,0);
  });
});

test('current attempt, node and execution identity must agree before claiming progress',async t=>{
  const mutations={
    'old attempt ID': f=>{f.node.latestAttemptId='attempt-next';},
    'wrong attempt node': f=>{f.attempt.nodeId='another-node';},
    'wrong attempt number': f=>{f.attempt.number=0;},
    'other current node': f=>{f.workflow.currentNodeId='rabi';},
    'node no longer verifying': f=>{f.node.engineState='execution_failed';},
    'attempt ended': f=>{f.attempt.endedAt='2026-11-18T02:40:00Z';},
    'attempt aborted': f=>{f.attempt.state='aborted';},
    'attempt error': f=>{f.attempt.error='offline stopped';},
    'cancellation requested': f=>{f.workflow.cancelRequested='pause';},
    'historical mode': f=>{f.workflow.mode='replay';},
    'failed placeholder': f=>{f.attempt.result.quality.status='failed';},
    'missing attempt identity': f=>{delete f.attempt.id;},
    'missing attempt result': f=>{delete f.attempt.result;},
  };
  for(const [name,mutate] of Object.entries(mutations)) await t.test(name,()=>{
    const f=fixture();mutate(f);
    assert.equal(projectReport(f.report).rows[0].status,unfinishedText);
    assert.equal(projectReport(f.report).verified,0);
    assert.notEqual(verificationProgress(f.attempt,f),activeText);
  });
});

test('stale dependencies remain stale while the pending acquisition is explicitly unfinished',()=>{
  const f=fixture();f.node.engineState='stale';
  assert.equal(projectReport(f.report).rows[0].status,'需复测');
  assert.equal(stages(f.attempt,f).at(-1).acquisition,unfinishedText);
  assert.equal(projectReport(f.report).complete,false);
});

test('ordinary partial acquisitions are not relabeled as verification placeholders',()=>{
  for(const verificationPending of [undefined,false,1,'true']) {
    const result={partial:true,verificationPending,quality:{status:'unverified'}};
    assert.equal(currentStatus({attempt:1,result}),'部分数据');
    assert.equal(stages({result})[0].acquisition,'部分数据');
    assert.equal(verificationProgress({result}),null);
  }
  for(const selectionResult of [undefined,null,[],42]) {
    const result={partial:true,verificationPending:true,selectionResult};
    assert.equal(currentStatus({attempt:1,result}),'部分数据');
    assert.equal(verificationProgress({result}),null);
  }
});

test('incomplete or rejected selection does not support a completed-selection progress claim',()=>{
  for(const selectionChange of [{partial:true},{stale:true},{analysisQuality:{accepted:false}},{}]) {
    const f=fixture();
    if(Object.keys(selectionChange).length)Object.assign(f.attempt.result.selectionResult,selectionChange);
    else delete f.attempt.result.selectionResult.analysisQuality;
    assert.equal(projectReport(f.report).rows[0].status,unfinishedText);
    assert.notEqual(stages(f.attempt,f)[0].acquisition,'首测完成');
  }
});

test('historical pending attempt cannot inherit a later active verification',()=>{
  const f=fixture(), older=structuredClone(f.attempt);
  older.id='attempt-older';older.number=0;older.state='attention';
  f.report.nodes[0].attempts.unshift(older);
  const result=projectReport(f.report);
  assert.equal(result.rows[0].status,activeText);
  assert.equal(result.figures[0].stage.verificationProgress,unfinishedText);
  assert.equal(result.figures[1].stage.verificationProgress,activeText);
  assert.equal(result.verified,0);
});

test('standalone placeholder projection stays conservative without execution context',()=>{
  const f=fixture();
  assert.equal(currentStatus(f.node),unfinishedText);
  assert.equal(stages(f.attempt).at(-1).acquisition,unfinishedText);
});

test('completed independent results retain existing acceptance semantics',()=>{
  const f=fixture();
  f.node.result={dataKind:'real',analysisQuality:{accepted:true},quality:{status:'passed'},
    provenance:{freshAcquisition:true},selectionResult:f.node.result.selectionResult};
  f.node.engineState='verified';f.attempt.state='verified';f.attempt.result=structuredClone(f.node.result);
  f.workflow.status='completed';f.report.workflowComplete=true;
  assert.equal(projectReport(f.report).rows[0].status,'独立复测通过');
  assert.equal(projectReport(f.report).verified,1);assert.equal(projectReport(f.report).complete,true);
  assert.equal(stages(f.attempt,f).at(-1).quality,'本阶段通过');
});
