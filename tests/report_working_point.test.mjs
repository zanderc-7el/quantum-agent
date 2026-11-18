import test from 'node:test';
import assert from 'node:assert/strict';
import {projectReport,projectReports,stages} from '../scripts/report_pptx_model.mjs';

function fixture(){
 const nodes=Array.from({length:10},(_,i)=>{
  const result={dataKind:'real',acquiredAt:'2026-11-18T05:00:00Z',provenance:{freshAcquisition:true},
   quality:{status:'passed'},analysisQuality:{accepted:true},artifacts:[{id:'new-'+i,kind:'image'}]};
  return {node:{id:'n'+i,resourceId:'Q11',label:'Offline method '+i,attempt:1,engineState:'verified',result},
   attempts:[{id:'a'+i,number:1,result}]};
 });
 return {schema:'qcal-report/1',workflowComplete:true,workflow:{id:'offline-working-point',mode:'real',coverage:'working_point',
  seedReference:{schema:'working-point-seed/1',status:'seed_only',sourceId:'offline-seed',
   sourceFiles:[{kind:'historical',name:'old.json',path:'not-opened/old.json',sha256:'a'.repeat(64)}],
   // An accidental extra historical-result field is metadata only, never traversed as new data.
   result:{dataKind:'historical',quality:{status:'passed'},artifacts:[{id:'old-seed-image',kind:'image'}]}},
  completionScope:{selectedNodeCount:10,omittedProcedures:['ro','power','spectrum'],full13Fresh:false}},nodes};
}

test('ten fresh accepted nodes complete only the selected pulse scope',()=>{
 const report=fixture(),before=JSON.stringify(report),p=projectReport(report),bundle=projectReports(report);
 assert.equal(p.verified,10);assert.equal(p.rows.length,10);assert.equal(p.complete,true);
 assert.equal(p.title,'Q11 工作点复刻实测结果');
 assert.equal(p.completionLabel,'所选 10 项脉冲范围验收通过');
 assert.equal(p.scope.full13Fresh,false);assert.equal(p.scope.selectedNodeCount,10);
 assert.match(p.scope.seedMeaning,/旧工作点仅作初值/);
 assert.equal(bundle.groups[0].identity.total,10);assert.equal(bundle.groups[0].identity.full13Fresh,false);
 assert.equal(bundle.groups[0].identity.seedStatus,'seed_only');
 assert.equal(bundle.groups[0].identity.seedSourceId,'offline-seed');
 assert.equal(p.figures.length,10);assert.ok(!p.figures.some(f=>f.artifact.id==='old-seed-image'));
 assert.equal(JSON.stringify(report),before);
});

test('historical accepted node cannot become a new pass, new figure or new acquisition time',()=>{
 const report=fixture(),old=report.nodes[0].node.result;
 Object.assign(old,{dataKind:'historical',provenance:{freshAcquisition:false},acquiredAt:'2026-11-10T00:00:00Z'});
 const p=projectReport(report),identity=projectReports(report).groups[0].identity;
 assert.equal(p.verified,9);assert.equal(p.complete,false);
 assert.equal(p.rows[0].status,'历史参考（不计本次通过）');
 assert.equal(p.figures[0].source,'历史数据');
 assert.equal(p.figures[0].stage.quality,'历史记录（不计本次验收）');
 assert.equal(identity.acquisitionFrom,'2026-11-18T05:00:00Z');
 assert.equal(identity.acquisitionTo,'2026-11-18T05:00:00Z');
 assert.equal(old.quality.status,'passed');
});

test('missing freshness never contributes to new working-point acceptance',()=>{
 for(const resultChange of [{dataKind:'historical'},{provenance:{}},{provenance:{freshAcquisition:false}}]){
  const report=fixture();Object.assign(report.nodes[0].node.result,resultChange);
  const p=projectReport(report);assert.equal(p.verified,9);assert.equal(p.complete,false);
  assert.ok(['历史参考（不计本次通过）','来源待核对'].includes(p.rows[0].status));
 }
});

test('selected-scope metadata, seed-only status and actual node count must agree for completion',()=>{
 for(const change of [r=>r.nodes.pop(),r=>r.workflow.completionScope.selectedNodeCount=13,
  r=>r.workflow.completionScope.full13Fresh=true,r=>delete r.workflow.seedReference,
  r=>r.workflow.seedReference.status='accepted',r=>r.workflowComplete=false]){
  const report=fixture();change(report);assert.equal(projectReport(report).complete,false);
 }
});

test('fresh failed, partial and stale nodes still prevent scope acceptance',()=>{
 for(const change of [{quality:{status:'failed'}},{partial:true},{stale:true}]){
  const report=fixture();Object.assign(report.nodes[0].node.result,change);
  const p=projectReport(report);assert.equal(p.verified,9);assert.equal(p.complete,false);
 }
});

test('a 13-node full task and a ten-node scoped task keep separate completion totals',()=>{
 const full=fixture(),scoped=fixture();full.workflow.id='offline-full';full.workflow.coverage='full';
 for(let i=10;i<13;i++){const item=structuredClone(full.nodes[0]);item.node.id='n'+i;full.nodes.push(item);}
 const {groups}=projectReports({schema:'qcal-report-bundle/1',reports:[full,scoped]});
 assert.deepEqual(groups.map(g=>[g.identity.verified,g.identity.total]),[[13,13],[10,10]]);
 assert.equal(groups[0].projected.completionLabel,'流程验收通过');
 assert.equal(groups[1].projected.completionLabel,'所选 10 项脉冲范围验收通过');
 assert.equal(groups[1].identity.full13Fresh,false);
 assert.equal(groups[1].projected.figures.length,10);
});

test('main/full and replay keep historical stage semantics; reference-only scope has no new timestamps',()=>{
 const report=fixture();for(const item of report.nodes)Object.assign(item.node.result,{dataKind:'historical',provenance:{freshAcquisition:false}});
 const scoped=projectReports(report).groups[0];
 assert.equal(scoped.projected.verified,0);assert.equal(scoped.identity.acquisitionFrom,null);
 for(const coverage of ['main','full',undefined]){
  report.workflow.coverage=coverage;
  const p=projectReport(report);assert.equal(p.scope,null);assert.equal(p.verified,10);assert.equal(p.complete,false);
  assert.equal(stages(report.nodes[0].attempts[0],{workflow:report.workflow})[0].quality,'本阶段通过');
 }
});
