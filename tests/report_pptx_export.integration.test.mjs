// Explicit opt-in: renders synthetic fixtures, never queries a platform database.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {deflateSync} from 'node:zlib';
import crypto from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';

const exec=promisify(execFile),project=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const enabled=process.env.REPORT_PPTX_INTEGRATION==='1';
const sha=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');

function png(rgb){
 const crc32=bytes=>{let crc=0xffffffff;for(const byte of bytes){crc^=byte;for(let bit=0;bit<8;bit++)crc=(crc>>>1)^(crc&1?0xedb88320:0);}return(crc^0xffffffff)>>>0;};
 const chunk=(type,bytes)=>{const content=Buffer.concat([Buffer.from(type),bytes]),length=Buffer.alloc(4),crc=Buffer.alloc(4);
  length.writeUInt32BE(bytes.length);crc.writeUInt32BE(crc32(content));return Buffer.concat([length,content,crc]);};
 const header=Buffer.alloc(13);header.writeUInt32BE(120);header.writeUInt32BE(60,4);header[8]=8;header[9]=2;
 const rows=Array.from({length:60},()=>Buffer.from([0,...Array.from({length:120},()=>rgb).flat()]));
 return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',deflateSync(Buffer.concat(rows))),chunk('IEND',Buffer.alloc(0))]);
}

function fixtureReport(root,id,image,passed,time){
 const artifact={id:'same-artifact-id',kind:'image',name:'OFFLINE TEST FIXTURE — NOT MEASUREMENT',sha256:image.hash};
 const result={dataKind:'historical',acquiredAt:time,analysisQuality:{accepted:passed},quality:{status:passed?'passed':'failed'},
  artifacts:[artifact],provenance:{freshAcquisition:false,profileHash:'acquisition-profile-'+id}};
 const verification={...result,...(!passed?{selectionResult:{...result,partial:true}}:{})};
 return {schema:'qcal-report/1',generatedAt:'2026-11-18T03:00:00Z',artifactRoot:root,
  workflow:{id,mode:'replay',revision:1,adapterId:'OFFLINE-FIXTURE-'+id,adapterProfileHash:sha(Buffer.from(id))},workflowComplete:false,
  nodes:[{node:{id:'same-q11-node',resourceId:'Q11',qubits:['Q11'],label:'离线测试夹具（非测量）',
   attempt:1,engineState:passed?'verified':'failed',result:verification},
   attempts:[{id:'same-attempt-id',number:1,state:passed?'verified':'execution_failed',result:verification}]}],
  artifactIndex:{[artifact.id]:{...artifact,path:image.file}}};
}

const zipInspection=String.raw`
import hashlib,json,sys,zipfile,xml.etree.ElementTree as ET
ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
with zipfile.ZipFile(sys.argv[1]) as z:
    slides=sorted([n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')],key=lambda n:int(n.rsplit('slide',1)[1][:-4]))
    notes=sorted([n for n in z.namelist() if n.startswith('ppt/notesSlides/notesSlide') and n.endswith('.xml')],key=lambda n:int(n.rsplit('notesSlide',1)[1][:-4]))
    print(json.dumps({'mediaHashes':[hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('ppt/media/')],
        'slides':[{'text':' '.join(ET.fromstring(z.read(n)).itertext()),'tables':len(ET.fromstring(z.read(n)).findall('.//a:tbl',ns))} for n in slides],
        'notes':[''.join(ET.fromstring(z.read(n)).itertext()) for n in notes]}))
`;

test('PPT bundle preserves independent task identity, source bytes, failed figures and single-task export',
 {skip:!enabled,timeout:300000},async t=>{
 assert.ok(process.env.RUNTIME_NODE_MODULES,'Set RUNTIME_NODE_MODULES');
 assert.ok(process.env.RUNTIME_PYTHON,'Set RUNTIME_PYTHON');
 const root=path.join(project,'reports','offline-tests',`bundle-${Date.now()}`);
 await fs.mkdir(root,{recursive:true});
 await fs.writeFile(path.join(root,'OFFLINE_FIXTURE_ONLY.txt'),'Synthetic export test. Not measured data or a user report.\n',{flag:'wx'});
 const files=[];
 for(const [name,color] of [['old',[170,30,30]],['new',[20,90,180]]]){
  const bytes=png(color),file=path.join(root,name+'.png');await fs.writeFile(file,bytes,{flag:'wx'});files.push({file,hash:sha(bytes)});
 }
 const old=fixtureReport(root,'OFFLINE-FIXTURE-old',files[0],true,'2026-11-17T22:00:00Z');
 const next=fixtureReport(root,'OFFLINE-FIXTURE-new',files[1],false,'2026-11-18T02:00:00Z');
 async function build(name,input,count){
  const snapshot=path.join(root,name+'.json'),directory=path.join(root,name);
  await fs.writeFile(snapshot,JSON.stringify(input),{flag:'wx'});
  await exec(process.execPath,[path.join(project,'scripts/export_report_pptx.mjs'),'--snapshot',snapshot,'--template',
   path.join(project,'scripts/report_assets/quantum_red_six_plot_template.pptx'),'--build-dir',directory,'--appendix-count',String(count)],
   {cwd:project,timeout:180000,maxBuffer:4*1024*1024});
  const audit=JSON.parse(await fs.readFile(path.join(directory,'source_coverage.json'),'utf8'));
  const inspected=JSON.parse((await exec(process.env.RUNTIME_PYTHON,['-c',zipInspection,path.join(directory,'candidate.pptx')],
   {timeout:30000,maxBuffer:4*1024*1024})).stdout);
  assert.equal(audit.slideAudit.length,inspected.slides.length);
  assert.equal(audit.slideAudit.length,inspected.notes.length);
  for(const [index,slide] of audit.slideAudit.entries()){
   if(slide.workflowId){
    assert.ok(inspected.slides[index].text.includes(slide.workflowId),'Visible task identity on every task page');
    assert.ok(inspected.notes[index].includes(slide.adapterProfileHash),'Full profile hash in notes');
    assert.ok(slide.figures.every(figure=>figure.workflowId===slide.workflowId),'No cross-task figures');
   }
   if(/测量进度|采集记录|多任务测量汇总/.test(slide.title))assert.equal(inspected.slides[index].tables,1);
  }
  return {audit,inspected};
 }
 const bundle=await build('bundle-16',{schema:'qcal-report-bundle/1',reports:[old,next]},16);
 assert.equal(bundle.audit.sourceFigureCount,3);
 assert.ok(!Object.hasOwn(bundle.audit,'workflowComplete'));
 assert.deepEqual(bundle.audit.workflows.map(w=>[w.workflowId,w.verified,w.total]),[['OFFLINE-FIXTURE-old',1,1],['OFFLINE-FIXTURE-new',0,1]]);
 assert.ok(files.every(file=>bundle.inspected.mediaHashes.includes(file.hash)),'Original source bytes from both tasks are embedded');
 const appendix=bundle.audit.slideAudit.filter(slide=>slide.title.includes('附录')).flatMap(slide=>slide.figures);
 assert.equal(appendix.length,3);
 assert.equal(appendix.filter(figure=>figure.quality==='未通过').length,2);
 assert.equal(appendix.filter(figure=>figure.acquisition==='部分数据').length,1);
 assert.equal(new Set(appendix.map(figure=>figure.key)).size,3);
 assert.deepEqual(new Set(appendix.map(figure=>figure.acquiredAt)),new Set(['2026-11-17T22:00:00Z','2026-11-18T02:00:00Z']));
 const single=await build('single-64',old,64);
 assert.equal(single.audit.workflowId,old.workflow.id);
 assert.equal(single.audit.sourceFigureCount,1);
 assert.ok(!Object.hasOwn(single.audit,'workflows'));
 assert.ok(!single.audit.slideAudit.some(slide=>slide.title.includes('多任务')));
 assert.ok(single.inspected.mediaHashes.includes(files[0].hash));
 // Verify that the actual exporter forwards task/node context to its tables,
 // selection captions and notes, not just the standalone pure projection.
 function pendingReport(id,status){
  const report=fixtureReport(root,id,files[0],true,'2026-11-18T02:30:00Z');
  const {node,attempts:[attempt]}=report.nodes[0];
  const result={dataKind:'historical',partial:true,verificationPending:true,
   quality:{status:'unverified'},artifacts:[],selectionResult:{...attempt.result,quality:{status:'unverified'}}};
  Object.assign(report.workflow,{mode:'real',status,currentNodeId:node.id});
  Object.assign(node,{engineState:'verifying',latestAttemptId:attempt.id,result});
  Object.assign(attempt,{nodeId:node.id,state:'running',result:structuredClone(result)});
  return report;
 }
 const running=pendingReport('OFFLINE-FIXTURE-running','running'),paused=pendingReport('OFFLINE-FIXTURE-paused','paused');
 const pending=await build('pending-context-16',{schema:'qcal-report-bundle/1',reports:[running,paused]},16);
 assert.deepEqual(pending.audit.workflows.map(w=>[w.verified,w.workflowComplete]),[[0,false],[0,false]]);
 for(const [id,status] of [[running.workflow.id,'首测完成，独立复测进行中'],[paused.workflow.id,'独立复测未完成']]){
  const index=pending.audit.slideAudit.findIndex(s=>s.workflowId===id&&s.title.includes('采集记录'));
  assert.ok(pending.inspected.slides[index].text.includes(status));
  const page=pending.audit.slideAudit.findIndex(s=>s.workflowId===id&&s.title.includes('候选测量'));
  assert.ok(pending.inspected.slides[page].text.includes(status));
  assert.ok(pending.inspected.slides[page].text.includes('待独立验收'));
 }
 assert.equal(pending.audit.sourceFigureCount,2);
 assert.ok(pending.inspected.mediaHashes.includes(files[0].hash));
 // A completed working-point task accepts exactly its ten newly acquired methods.
 // Seed source metadata may point at old evidence, but those bytes are never loaded.
 const working=fixtureReport(root,'OFFLINE-FIXTURE-working-point',files[1],true,'2026-11-18T04:00:00Z');
 Object.assign(working.workflow,{mode:'real',status:'completed',coverage:'working_point',
  seedReference:{schema:'working-point-seed/1',status:'seed_only',sourceId:'OFFLINE-old-reference',parameters:{drive_hz:1},
   sourceFiles:[{kind:'offline seed',name:'old.png',path:files[0].file,sha256:files[0].hash}],
   result:{artifacts:[{id:'not-a-new-artifact',kind:'image',sha256:files[0].hash}]}},
  completionScope:{selectedNodeCount:10,omittedProcedures:['ro','power','spectrum'],full13Fresh:false}});
 working.workflowComplete=true;
 const entry=working.nodes[0];
 Object.assign(entry.node.result,{dataKind:'real',provenance:{freshAcquisition:true}});
 working.nodes=Array.from({length:10},(_,i)=>{
  const item=structuredClone(entry);item.node.id='fresh-'+i;item.node.label='离线范围测试 '+i;
  item.attempts[0].id='fresh-attempt-'+i;
  if(i>0){item.node.result.artifacts=[];item.attempts[0].result.artifacts=[];}
  return item;
 });
 const scoped=await build('working-point-10',working,16);
 assert.equal(scoped.audit.workflowComplete,true);assert.equal(scoped.audit.sourceFigureCount,1);
 assert.deepEqual([scoped.audit.verified,scoped.audit.total,scoped.audit.full13Fresh,scoped.audit.seedStatus],[10,10,false,'seed_only']);
 const cover=scoped.inspected.slides[0].text;
 assert.ok(cover.includes('10 / 10'));assert.ok(cover.includes('所选 10 项脉冲范围验收通过'));
 assert.ok(!cover.includes('13 / 13'));assert.ok(!cover.includes('流程验收通过'));
 const sourcePage=scoped.audit.slideAudit.findIndex(s=>s.title.includes('工作点与本次测量范围'));
 assert.ok(sourcePage>=0);assert.ok(scoped.inspected.slides[sourcePage].text.includes('seed_only'));
 assert.ok(scoped.inspected.slides[sourcePage].text.includes('历史数据和通过数不计本次验收'));
 assert.ok(scoped.inspected.notes[sourcePage].includes('OFFLINE-old-reference'));
 assert.ok(scoped.inspected.notes[sourcePage].includes('"full13Fresh": false'));
 assert.ok(scoped.inspected.mediaHashes.includes(files[1].hash));
 assert.ok(!scoped.inspected.mediaHashes.includes(files[0].hash),'Seed figure is never imported as newly acquired evidence');
 const scopedPending=structuredClone(working);
 Object.assign(scopedPending.workflow,{id:'OFFLINE-FIXTURE-working-pending',status:'running',currentNodeId:scopedPending.nodes[0].node.id});
 scopedPending.workflowComplete=false;
 for(const [index,item] of scopedPending.nodes.entries()){
  if(index){item.node.attempt=0;item.node.engineState='pending';delete item.node.result;item.attempts=[];continue;}
  const attempt=item.attempts[0],selection=structuredClone(attempt.result);
  selection.quality={status:'unverified'};
  const placeholder={dataKind:'real',partial:true,verificationPending:true,artifacts:[],quality:{status:'unverified'},selectionResult:selection};
  Object.assign(item.node,{engineState:'verifying',latestAttemptId:attempt.id,result:placeholder});
  Object.assign(attempt,{nodeId:item.node.id,state:'running',result:structuredClone(placeholder)});
 }
 const pendingScope=await build('working-point-verification-placeholder',scopedPending,16);
 assert.deepEqual([pendingScope.audit.verified,pendingScope.audit.total,pendingScope.audit.workflowComplete],[0,10,false]);
 const acquisitionPage=pendingScope.audit.slideAudit.findIndex(s=>s.title.includes('采集记录'));
 assert.ok(pendingScope.inspected.slides[acquisitionPage].text.includes('首测完成，独立复测进行中'));
 assert.ok(pendingScope.inspected.slides[acquisitionPage].text.includes('尚未验收'));
 assert.ok(!pendingScope.inspected.slides[acquisitionPage].text.includes('来源待核对'));
 assert.equal(pendingScope.audit.sourceFigureCount,1);
 t.diagnostic('Retained offline fixtures and rendered PNGs: '+root);
 });
