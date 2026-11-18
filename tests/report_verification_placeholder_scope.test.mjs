import test from 'node:test';
import assert from 'node:assert/strict';
import {projectReport,stages} from '../scripts/report_pptx_model.mjs';

function fixture(){
 const selection={dataKind:'real',acquiredAt:'2026-11-18T08:00:00Z',analysisQuality:{accepted:true},
  quality:{status:'unverified'},artifacts:[{id:'OFFLINE-selection-image',kind:'image'}],provenance:{freshAcquisition:true}};
 const result={dataKind:'real',partial:true,verificationPending:true,metrics:{},artifacts:[],
  quality:{status:'unverified',reasons:['首测已完成；独立复测尚未验收']},selectionResult:selection};
 const attempt={id:'OFFLINE-a',nodeId:'rabi',number:1,state:'running',result:structuredClone(result)};
 const node={id:'rabi',attempt:1,latestAttemptId:attempt.id,engineState:'verifying',result};
 const workflow={id:'OFFLINE-w',mode:'real',coverage:'working_point',status:'running',currentNodeId:node.id};
 const report={schema:'qcal-report/1',workflow,workflowComplete:false,nodes:[{node,attempts:[attempt]}]};
 return {report,workflow,node,attempt};
}

test('working-point empty verification placeholder is unaccepted, not unknown-origin data',()=>{
 const f=fixture(),before=JSON.stringify(f.report);
 const [selection,verification]=stages(f.attempt,f),projected=projectReport(f.report);
 assert.equal(selection.quality,'待独立验收');assert.equal(selection.analysis,'原生分析通过');
 assert.equal(verification.quality,'尚未验收');assert.equal(verification.acquisition,'首测完成，独立复测进行中');
 assert.equal(projected.rows[0].status,'首测完成，独立复测进行中');
 assert.equal(projected.verified,0);assert.equal(projected.complete,false);assert.equal(projected.figures.length,1);
 assert.equal(JSON.stringify(f.report),before);
});

test('paused, aborted and attention placeholders stay unaccepted without claiming an active verification',async t=>{
 for(const status of ['paused','aborted','attention','failed','completed','queued','draft'])await t.test(status,()=>{
  const f=fixture();f.workflow.status=status;
  const stage=stages(f.attempt,f).at(-1),p=projectReport(f.report);
  assert.equal(stage.quality,'尚未验收');assert.equal(stage.acquisition,'独立复测未完成');
  assert.equal(p.verified,0);assert.equal(p.complete,false);
 });
});

test('historical, ordinary partial and acquired unknown-origin data retain their source labels',async t=>{
 const variants={
  historical:r=>{r.dataKind='historical';},
  'recorded historical provenance':r=>{r.provenance={freshAcquisition:false};},
  'ordinary partial':r=>{r.verificationPending=false;},
  'acquired timestamp':r=>{r.acquiredAt='2026-11-18T08:00:01Z';},
  'acquired image':r=>{r.artifacts=[{id:'OFFLINE-unknown-image',kind:'image'}];},
  'analysis present':r=>{r.analysisQuality={accepted:true};},
  'unknown provenance object':r=>{r.provenance={};},
  'not partial':r=>{r.partial=false;},
  'explicit failed quality':r=>{r.quality.status='failed';},
  'nonempty top-level metrics':r=>{r.metrics={contrast_snr:7};},
  'malformed metrics':r=>{r.metrics=[];},
  'empty selection':r=>{r.selectionResult={};},
  'historical selection':r=>{r.selectionResult.dataKind='historical';r.selectionResult.provenance={freshAcquisition:false};},
  'partial selection':r=>{r.selectionResult.partial=true;},
  'stale selection':r=>{r.selectionResult.stale=true;},
  'unknown-origin selection':r=>{delete r.selectionResult.provenance;},
  'missing selection time':r=>{delete r.selectionResult.acquiredAt;},
  'missing selection artifacts':r=>{r.selectionResult.artifacts=[];},
  'native incomplete selection':r=>{r.selectionResult.analysisQuality={accepted:false};},
 };
 for(const [name,mutate] of Object.entries(variants))await t.test(name,()=>{
  const f=fixture();mutate(f.attempt.result);f.node.result=structuredClone(f.attempt.result);
  const before=JSON.stringify(f.report),stage=stages(f.attempt,f).at(-1);
  assert.equal(stage.quality,['historical','recorded historical provenance'].includes(name)?'历史记录（不计本次验收）':'来源待核对');
  assert.equal(projectReport(f.report).verified,0);assert.equal(JSON.stringify(f.report),before);
 });
});

test('completed fresh verification remains accepted and main/full placeholder labels stay unchanged',()=>{
 for(const coverage of ['main','full',undefined]){
  const f=fixture();f.workflow.coverage=coverage;assert.equal(stages(f.attempt,f).at(-1).quality,'尚未验收');
 }
 const f=fixture();f.attempt.result={dataKind:'real',acquiredAt:'2026-11-18T08:02:00Z',provenance:{freshAcquisition:true},
  quality:{status:'passed'},analysisQuality:{accepted:true},artifacts:[],selectionResult:f.attempt.result.selectionResult};
 f.node.result=structuredClone(f.attempt.result);f.node.engineState='verified';f.attempt.state='verified';
 assert.equal(stages(f.attempt,f).at(-1).quality,'本阶段通过');
 assert.equal(projectReport(f.report).verified,1);
});
