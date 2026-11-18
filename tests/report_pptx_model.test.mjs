import test from 'node:test';
import assert from 'node:assert/strict';
import {projectReport,projectReports,stages,currentStatus} from '../scripts/report_pptx_model.mjs';

test('legacy decision exception preserves failed acquired data, and both independent stages',()=>{
 const image={id:'figure',kind:'image',sha256:'a'};
 const failed={id:'a',number:3,state:'execution_failed',error:'决策引用了当前节点之外的证据',
  result:{dataKind:'real',analysisQuality:{accepted:false},quality:{status:'failed'},artifacts:[image],provenance:{freshAcquisition:true}}};
 assert.equal(stages(failed)[0].acquisition,'已完成采集');
 assert.equal(stages(failed)[0].quality,'未通过');
 const repeated={...failed,id:'b',result:{...failed.result,quality:{status:'passed'},selectionResult:failed.result}};
 assert.deepEqual(stages(repeated).map(x=>x.role),['selection','verification']);
 const report={schema:'qcal-report/1',workflow:{mode:'real'},workflowComplete:false,nodes:[{node:{id:'power',attempt:4,engineState:'acquiring'},attempts:[failed,repeated]}]};
 const model=projectReport(report);
 assert.equal(model.figures.length,3);assert.equal(model.complete,false);assert.equal(model.rows[0].status,'采集中');
});

test('never-measured invalidated dependencies are pending; partial/stale/historical results cannot complete',()=>{
 assert.equal(currentStatus({attempt:0,engineState:'stale'}),'待测');
 const node={id:'n',attempt:1,engineState:'verified',result:{quality:{status:'passed'},dataKind:'real',provenance:{freshAcquisition:true}}};
 const report={schema:'qcal-report/1',workflow:{mode:'real'},workflowComplete:true,nodes:[{node,attempts:[]}]};
 assert.equal(projectReport(report).complete,true);
 for(const change of [{partial:true},{stale:true},{dataKind:'historical'},{provenance:{freshAcquisition:false}}]){
  assert.equal(projectReport({...report,nodes:[{node:{...node,result:{...node.result,...change}},attempts:[]}]}).complete,false);
 }
 assert.equal(projectReport({...report,workflow:{mode:'replay'}}).complete,false);
});

test('progress table respects actual dependency ordering and rejects cycles',()=>{
 const report={schema:'qcal-report/1',workflow:{mode:'real'},nodes:[{node:{id:'rabi',dependsOn:['s21']}},{node:{id:'s21'}}]};
 assert.deepEqual(projectReport(report).rows.map(x=>x.node.id),['s21','rabi']);
 report.nodes[1].node.dependsOn=['rabi'];assert.throws(()=>projectReport(report),/cycle/);
});

function task(id,{pending=false,profile='profile-'+id,time='2026-11-18T00:00:00Z',hash=id}={}){
 const result={dataKind:'real',acquiredAt:time,analysisQuality:{accepted:true},quality:{status:'passed'},
  artifacts:[{id:'shared-artifact-id',kind:'image',sha256:hash}],provenance:{freshAcquisition:true}};
 return {schema:'qcal-report/1',generatedAt:'2026-11-18T01:00:00Z',workflow:{id,mode:'real',adapterId:profile,adapterProfileHash:profile+'-hash'},
  workflowComplete:!pending,nodes:[{node:{id:'same-q11-node',resourceId:'Q11',attempt:pending?0:1,
   engineState:pending?'pending':'verified',...(!pending?{result}:{})},attempts:pending?[]:[{id:'same-attempt-id',number:1,result}]}]};
}

test('old pass never becomes a new pass across profiles; task times and totals stay independent',()=>{
 const old=task('old'),next=task('new',{pending:true}),input={schema:'qcal-report-bundle/1',reports:[old,next]};
 const before=JSON.stringify(input),projection=projectReports(input);
 assert.equal(projection.multiple,true);
 assert.deepEqual(projection.groups.map(g=>[g.identity.workflowId,g.projected.verified,g.projected.complete]),[['old',1,true],['new',0,false]]);
 assert.deepEqual(projection.groups.map(g=>g.identity.adapterProfileHash),['profile-old-hash','profile-new-hash']);
 assert.equal(projection.groups[0].identity.acquisitionFrom,'2026-11-18T00:00:00Z');
 assert.equal(projection.groups[1].identity.acquisitionFrom,null);
 assert.equal(projection.groups[1].identity.acquisitionTo,null);
 assert.equal(projection.groups[1].projected.rows[0].status,'待测');
 assert.ok(!Object.hasOwn(projection,'complete'));
 assert.equal(JSON.stringify(input),before);
 assert.equal(projectReports(old).multiple,false);
});

test('same artifact/attempt IDs in separate tasks retain distinct images and failed/partial stages',()=>{
 const old=task('old',{hash:'aaa',time:'2026-11-17T22:00:00Z'}),next=task('new',{hash:'bbb',time:'2026-11-18T02:00:00Z'});
 const failed={...next.nodes[0].attempts[0].result,acquiredAt:'2026-11-18T01:30:00Z',partial:true,quality:{status:'failed'}};
 next.nodes[0].attempts[0].result.selectionResult=failed;
 next.nodes[0].attempts[0].result.quality={status:'failed'};
 next.nodes[0].node.engineState='failed';next.workflowComplete=false;
 const {groups}=projectReports({schema:'qcal-report-bundle/1',reports:[old,next]});
 const figures=groups.flatMap(g=>g.projected.figures);
 assert.equal(figures.length,3);
 assert.equal(new Set(figures.map(f=>f.key)).size,3);
 assert.notEqual(figures[0].imageKey,figures[1].imageKey);
 assert.equal(new Map(figures.map(f=>[f.imageKey,f.artifact.sha256])).size,2);
 assert.deepEqual(figures.map(f=>f.workflowId),['old','new','new']);
 assert.deepEqual(figures.map(f=>f.stage.quality),['本阶段通过','未通过','未通过']);
 assert.equal(figures[1].stage.acquisition,'部分数据');
 assert.equal(groups[1].identity.acquisitionFrom,'2026-11-18T01:30:00Z');
 assert.equal(groups[1].identity.acquisitionTo,'2026-11-18T02:00:00Z');
 assert.equal(groups[0].identity.acquisitionTo,'2026-11-17T22:00:00Z');
});

test('bundle identity is mandatory and unique, with no silently skipped tasks',()=>{
 assert.throws(()=>projectReports({schema:'qcal-report-bundle/1',reports:[]}),/contain tasks/);
 assert.throws(()=>projectReports({schema:'qcal-report-bundle/1',reports:[task('a'),task('a')]}),/duplicate/);
 const missing=task('a');delete missing.workflow.id;
 assert.throws(()=>projectReports(missing),/Missing/);
 const malformed=task('b');malformed.schema='other';
 assert.throws(()=>projectReports({schema:'qcal-report-bundle/1',reports:[task('a'),malformed]}),/Expected frozen/);
});

test('native analysis pass remains separate from unverified selection and failed independent verification',()=>{
 const result={analysisQuality:{accepted:false},quality:{status:'failed'},selectionResult:{
  analysisQuality:{accepted:true},quality:{status:'unverified'}}};
 const [selection,verification]=stages({result});
 assert.equal(selection.analysis,'原生分析通过');
 assert.equal(selection.quality,'尚未验收');
 assert.equal(verification.analysis,'原生分析未通过');
 assert.equal(verification.quality,'未通过');
 assert.equal(stages({result:{partial:true,quality:{status:'unverified'}}})[0].analysis,null);
});
