// Local report builder. Only consumes an immutable report snapshot and images.
// RUNTIME_NODE_MODULES is returned by Codex load_workspace_dependencies.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import crypto from 'node:crypto';
import {projectReports,stages} from './report_pptx_model.mjs';

const args=Object.fromEntries(process.argv.slice(2).reduce((rows,value,index,array)=>index%2 ? rows : [...rows,[value.replace(/^--/,''),array[index+1]]],[]));
for(const key of ['snapshot','template','build-dir']) if(!args[key]) throw new Error(`Missing --${key}`);
const perPage=Number(args['appendix-count']||16);
if(![16,64].includes(perPage)) throw new Error('appendix-count must be 16 or 64');
const blocked='d:\\qoskit-workspace';
function guard(input) {
  const absolute=path.resolve(input), folded=absolute.toLowerCase();
  if(folded===blocked||folded.startsWith(blocked+'\\')) throw new Error('Protected workspace is outside report scope');
  return absolute;
}
async function inputPath(input) { const absolute=guard(input);return guard(await fs.realpath(absolute)); }
const snapshotPath=await inputPath(args.snapshot), templatePath=await inputPath(args.template), buildDir=guard(args['build-dir']);
const candidate=path.join(buildDir,'candidate.pptx');
try{await fs.access(candidate);throw new Error('Use a new build directory; candidate exists');}catch(e){if(e.code!=='ENOENT')throw e;}
await fs.mkdir(buildDir,{recursive:true});
await inputPath(buildDir);
const raw=await fs.readFile(snapshotPath), input=JSON.parse(raw.toString('utf8').replace(/^\uFEFF/,''));
const {groups,multiple}=projectReports(input);
if(groups.some(group=>group.projected.rows.some(item=>item.node.resourceId!=='Q11')))throw new Error('This reviewed report layout is scoped to Q11');
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const templateSha256=hash(await fs.readFile(templatePath));
if(templateSha256!=='731c954096ea62e8c3d49f9757ac54a5e2b2a4ce4006b0ec0502d6041eafd52b')throw new Error('Template differs from the reviewed quantum red template');
const images=new Map();
for(const {report,projected} of groups){
 const root=await inputPath(report.artifactRoot);
 for(const figure of projected.figures){
  const record=report.artifactIndex?.[figure.artifact.id];
  if(!record || record.sha256!==figure.artifact.sha256 || record.kind!=='image')throw new Error('Image registry binding mismatch: '+figure.artifact.id);
  const file=await inputPath(record.path), relative=path.relative(root,file);
  if(relative==='..'||relative.startsWith('..'+path.sep)||path.isAbsolute(relative))throw new Error('Image outside artifact root');
  if(!images.has(figure.imageKey)){
    const bytes=await fs.readFile(file);
    if(hash(bytes)!==record.sha256)throw new Error('Image bytes changed: '+figure.artifact.id);
    const ext=path.extname(file).toLowerCase();
    const mime={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}[ext];
    if(!mime)throw new Error('Unsupported scientific image format: '+ext);
    images.set(figure.imageKey,{file,bytes,mime,sha256:record.sha256});
  }
 }
}
const modules=process.env.RUNTIME_NODE_MODULES;
if(!modules)throw new Error('Set RUNTIME_NODE_MODULES to the bundled dependency path');
const {FileBlob,PresentationFile}=await import(pathToFileURL(path.join(modules,'@oai/artifact-tool/dist/artifact_tool.mjs')).href);
const presentation=await PresentationFile.importPptx(await FileBlob.load(templatePath));
presentation.slides.keep([0]);
const base=presentation.slides.items[0];
for(const type of ['shapes','tables','images','charts'])for(const item of [...base[type].items])base[type].deleteById(item.id);
base.speakerNotes.text='';
const slideAudit=[];
let previousSlide=base;
function text(slide,value,x,y,w,h,size=21,color='#252525',bold=false){
 const shape=slide.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 shape.text=String(value);shape.text.style={typeface:'微软雅黑',fontSize:size,color,bold,verticalAlignment:'middle'};
 return shape;
}
function createPage(title,notes={},group=null){
 const s=previousSlide.duplicate();previousSlide=s;
 for(const type of ['shapes','tables','images','charts'])for(const item of [...s[type].items])s[type].deleteById(item.id);
 text(s,title,60,18,900,58,36);
 s.speakerNotes.text=JSON.stringify({snapshot:snapshotPath,snapshotSha256:hash(raw),
  ...(group?{...group.identity,...(group.projected.scope?{completionScope:group.report.workflow.completionScope,
    seedReference:group.projected.scope.seedReference}: {})}:{workflows:groups.map(item=>item.identity)}),...notes},null,2);
 if(group)text(s,`${group.identity.workflowId}    任务配置 SHA256：${group.identity.adapterProfileHash?.slice(0,12)||'未记录'}`,62,694,1156,17,10,'#777777');
 slideAudit.push({title,...(group?group.identity:{workflows:groups.map(item=>item.identity)}),figures:[]});return s;
}
function picture(slide,figure,rect){
 const source=images.get(figure.imageKey), bytes=source.bytes;
 slide.images.add({blob:bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),contentType:source.mime,
  alt:`${figure.node.qubits} ${figure.node.label} attempt ${figure.attempt.number} ${figure.stage.label} ${figure.artifact.name}`,fit:'contain',position:rect});
 slideAudit.at(-1).figures.push({key:figure.key,artifactId:figure.artifact.id,path:source.file,sha256:source.sha256,
  workflowId:figure.workflowId,adapterId:figure.adapterId,adapterProfileHash:figure.adapterProfileHash,
  acquiredAt:figure.stage.result.acquiredAt??null,acquisitionProfileHash:figure.stage.result.provenance?.profileHash??null,
  role:figure.stage.role,acquisition:figure.stage.acquisition,analysis:figure.stage.analysis,
  quality:figure.stage.quality,source:figure.source});
}
function shortMetric(result){return Object.entries(result.metrics||{}).slice(0,3).map(([k,v])=>`${k}=${typeof v==='number'?Number(v.toPrecision(5)):v}`).join('  ');}
function localDate(value){return value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)):'未记录';}
function acquisitionRange(identity){return identity.acquisitionFrom?`${localDate(identity.acquisitionFrom)} 至 ${localDate(identity.acquisitionTo)}`:'尚无已存档采集时间';}

if(multiple)for(let start=0;start<groups.length;start+=4){
 const subset=groups.slice(start,start+4),s=createPage('Q11 多任务测量汇总');
 text(s,'各任务分别记录进展与验收结果',62,94,1156,32,23,'#555555');
 const values=[['任务','工作流 / 执行器','配置 SHA256\n前 12 位','已存档采集时间\n北京时间','独立复测通过'],...subset.map(({identity:i})=>[
  String(i.taskNumber),`${i.workflowId}\n${i.adapterId||'执行器未记录'}`,i.adapterProfileHash?.slice(0,12)||'未记录',
  i.acquisitionFrom?`${localDate(i.acquisitionFrom)}\n至 ${localDate(i.acquisitionTo)}`:'尚无已存档采集时间',
  `${i.verified} / ${i.total}\n${i.completionLabel||(i.workflowComplete?'流程验收通过':'流程尚未全部通过')}`])];
 const table=s.tables.add({rows:values.length,columns:5,left:62,top:154,width:1156,height:values.length*88,
  columnWidths:[80,430,166,290,190],values});
 table.cells.block({row:0,column:0,rowCount:values.length,columnCount:5}).assign({textStyle:{typeface:'微软雅黑',fontSize:16,color:'#222222'},
  margins:{left:10,right:8,top:8,bottom:8},fill:'#FFFFFF',borders:{width:1,fill:'#D8D8D8'}});
 table.cells.block({row:0,column:0,rowCount:1,columnCount:5}).assign({fill:'#F7E9E8',textStyle:{typeface:'微软雅黑',fontSize:16,bold:true,color:'#962222'}});
}

for(const group of groups){
const {report,projected,identity}=group;
const methodStages=(node,attempt)=>stages(attempt,{workflow:report.workflow,node});
const page=(title,notes={})=>createPage(multiple?`任务 ${identity.taskNumber} · ${title}`:title,notes,group);
let s=page(projected.title);
text(s,'八号机',65,136,1000,55,42);
text(s,'测试人：Agent',65,218,1000,44,27);
text(s,`${report.workflow.mode==='real'?'本轮真机测量':report.workflow.mode==='replay'?'历史实测回放':'记录模式：'+report.workflow.mode}  ${projected.verified} / ${projected.rows.length} 项独立复测通过`,65,292,1100,50,30);
text(s,projected.completionLabel,65,373,1100,52,25,'#AE2424');
if(projected.scope)text(s,'范围：Rabi 起的 10 项脉冲测量；参考工作点为 seed_only 初值。',65,425,1100,30,19,'#555555');
text(s,`执行器：${identity.adapterId||'未记录'}`,65,464,1100,34,20,'#555555');
text(s,`采集时间：${acquisitionRange(identity)}（北京时间）`,65,520,1100,44,18,'#666666');
text(s,`数据快照：${localDate(report.generatedAt)}（北京时间）`,65,577,1100,44,18,'#666666');

if(projected.scope){
 const scope=projected.scope,seed=scope.seedReference;
 const sources=[...(seed?.sourceFiles||[]),...(seed?.sourceReport?[{...seed.sourceReport,kind:'报告',name:path.basename(seed.sourceReport.path||'参考报告')}]:[])];
 for(let start=0;start<Math.max(1,sources.length);start+=4){
  s=page('Q11 工作点与本次测量范围',{completionScope:report.workflow.completionScope,seedReference:seed});
  text(s,scope.description,62,99,1156,62,22,'#444444');
  text(s,scope.seedMeaning,62,163,1156,44,21,'#AE2424');
  text(s,`参考点：${seed?.sourceId||'来源记录待核对'}`,62,213,1156,38,18,'#555555');
  const values=[['来源类型','来源文件','SHA256 前 12 位'],...sources.slice(start,start+4).map(source=>[
    source.kind||'参考记录',source.name||path.basename(source.path||'未记录'),source.sha256?.slice(0,12)||'未记录'])];
  const table=s.tables.add({rows:values.length,columns:3,left:62,top:275,width:1156,height:values.length*63,
    columnWidths:[170,766,220],values});
  table.cells.block({row:0,column:0,rowCount:values.length,columnCount:3}).assign({textStyle:{typeface:'微软雅黑',fontSize:17,color:'#222222'},
    margins:{left:12,right:8,top:6,bottom:4},fill:'#FFFFFF',borders:{width:1,fill:'#D8D8D8'}});
  table.cells.block({row:0,column:0,rowCount:1,columnCount:3}).assign({fill:'#F7E9E8',textStyle:{typeface:'微软雅黑',fontSize:17,bold:true,color:'#962222'}});
  text(s,scope.valid?'本次结论以所选 10 项的新采集和独立验收为准；原始来源与完整哈希见备注。':'范围或参考点记录待核对；本报告不据此声明范围验收完成。',62,615,1156,55,18,'#555555');
 }
}

s=page('Q11 测量进度');
const values=[['测项','当前状态','尝试次数'],...projected.rows.map(item=>[item.node.label,item.status,String(item.node.attempt||0)])];
const table=s.tables.add({rows:values.length,columns:3,left:62,top:106,width:1156,height:525,
 columnWidths:[590,390,176],values});
table.cells.block({row:0,column:0,rowCount:values.length,columnCount:3}).assign({
 textStyle:{typeface:'微软雅黑',fontSize:20,color:'#222222'},margins:{left:12,right:8,top:4,bottom:4},
 fill:'#FFFFFF',borders:{width:1,fill:'#D8D8D8'}});
table.cells.block({row:0,column:0,rowCount:1,columnCount:3}).assign({fill:'#F7E9E8',textStyle:{typeface:'微软雅黑',fontSize:20,bold:true,color:'#962222'}});

for(const item of projected.rows){
 const candidates=(item.attempts||[]).filter(a=>methodStages(item.node,a).some(stage=>(stage.result.artifacts||[]).some(x=>x.kind==='image')));
 if(!candidates.length)continue;
 const attempt=candidates.at(-1), figures=projected.figures.filter(f=>f.node.id===item.node.id&&f.attempt.id===attempt.id);
 // Main pages retain each stage's own analysis, acceptance and failure reasons.
 // A successful selection must not inherit the independent verification's outcome.
 for(const stage of methodStages(item.node,attempt)){
 const stageFigures=figures.filter(figure=>figure.stage.role===stage.role);
 for(let start=0;start<stageFigures.length;start+=6){
  const subset=stageFigures.slice(start,start+6);
  s=page(`Q11 ${item.node.label} · ${stage.label}`,{nodeId:item.node.id,currentNodeStatus:item.status,attempt:attempt.number,
   attemptState:attempt.state,attemptError:attempt.error,stages:methodStages(item.node,attempt).map(stage=>({role:stage.role,acquiredAt:stage.result.acquiredAt,quality:stage.result.quality,
    parameters:stage.result.parameters,provenance:stage.result.provenance,metrics:stage.result.metrics,supplementalQuality:stage.result.supplementalQuality}))});
  text(s,`第 ${attempt.number} 次尝试的已存档结果    当前节点：${item.status}`,62,86,1156,28,19,'#555555');
  const cols=subset.length<=2?2:3, rows=subset.length<=3?1:2;
  const cellW=1156/cols, cellH=490/rows;
  subset.forEach((f,i)=>{
   const x=62+(i%cols)*cellW,y=124+Math.floor(i/cols)*cellH;
   text(s,`${projected.scope?f.source+' · ':''}${f.stage.label}  ${f.stage.acquisition}`,x,y,cellW-10,24,16,'#444444');
   text(s,[f.stage.analysis,f.stage.quality].filter(Boolean).join(' · '),x,y+24,cellW-10,23,15,f.stage.quality==='未通过'?'#AE2424':'#555555');
   picture(s,f,{left:x,top:y+49,width:cellW-12,height:cellH-59});
  });
  const result=stage.result||{};
  const reason=(result.quality?.reasons||[]).join('；');
  const pendingNote=stage.role==='selection' && stage.verificationProgress ? stage.verificationProgress+'，节点尚未验收。' : null;
  text(s,pendingNote||reason||shortMetric(result),62,628,1156,56,17,reason?'#AE2424':'#555555');
 }
 }
}

const allAttempts=projected.rows.flatMap(item=>(item.attempts||[]).map(attempt=>({node:item.node,attempt})));
for(let start=0;start<allAttempts.length;start+=10){
 const subset=allAttempts.slice(start,start+10);
 s=page(`Q11 采集记录 ${Math.floor(start/10)+1}`,{attempts:subset.map(({node,attempt})=>({nodeId:node.id,
  attemptId:attempt.id,number:attempt.number,state:attempt.state,error:attempt.error,startedAt:attempt.startedAt,endedAt:attempt.endedAt,
  operationId:attempt.operationId,verificationOperationId:attempt.verificationOperationId,
  stages:methodStages(node,attempt).map(stage=>({role:stage.role,acquiredAt:stage.result.acquiredAt,analysis:stage.analysis,
   quality:stage.result.quality,provenance:stage.result.provenance})),
  artifacts:methodStages(node,attempt).flatMap(stage=>stage.result.artifacts||[])}))});
 const values=[['测项 / 尝试','采集记录','验收'],...subset.map(({node,attempt})=>{
  const result=attempt.result, stage=methodStages(node,attempt).at(-1);
  return [`${node.label} / ${attempt.number}`,stage?stage.acquisition:attempt.state==='running'?'进行中，尚无最终结果':'尚无结果',
   projected.scope&&stage?stage.quality:result?.quality?.status==='passed'?'通过':result?.quality?.status==='failed'?'未通过':'尚未验收'];
 })];
 const table=s.tables.add({rows:values.length,columns:3,left:62,top:115,width:1156,height:Math.min(470,values.length*45),columnWidths:[500,440,216],values});
 table.cells.block({row:0,column:0,rowCount:values.length,columnCount:3}).assign({textStyle:{typeface:'微软雅黑',fontSize:21,color:'#222222'},
  margins:{left:12,right:8,top:6,bottom:4},fill:'#FFFFFF',borders:{width:1,fill:'#D8D8D8'}});
 table.cells.block({row:0,column:0,rowCount:1,columnCount:3}).assign({fill:'#F7E9E8',textStyle:{typeface:'微软雅黑',fontSize:21,bold:true,color:'#962222'}});
}

// Full attempts and both measurement roles are retained, including failed images.
for(let start=0;start<projected.figures.length;start+=perPage){
 const subset=projected.figures.slice(start,start+perPage), cols=perPage===16?4:8, rows=cols;
 s=page(`Q11 ${projected.scope?'本任务登记图':'全部实测图'}附录 ${Math.floor(start/perPage)+1}`,{figurePolicy:'Unmodified source images; each caption records attempt, measurement role, completeness and quality.'});
 const cellW=1160/cols,cellH=580/rows;
 subset.forEach((f,i)=>{
  const x=60+(i%cols)*cellW,y=100+Math.floor(i/cols)*cellH;
  text(s,`${f.node.label} #${f.attempt.number} ${f.stage.label}`,x,y,cellW-8,18,perPage===16?12:8);
  text(s,`${f.source} ${f.stage.acquisition} ${f.stage.quality}`,x,y+17,cellW-8,17,perPage===16?11:8,'#666666');
  picture(s,f,{left:x,top:y+35,width:cellW-10,height:cellH-39});
 });
}
}

presentation.slides.keep(Array.from({length:presentation.slides.items.length-1},(_,i)=>i+1));
const referenced=new Set(presentation.getReferencedImageIds());
for(const asset of [...presentation.getImageAssetItems()])if(!referenced.has(asset.id))presentation.deleteImageAsset(asset.id);
await (await PresentationFile.exportPptx(presentation)).save(candidate);
await fs.mkdir(path.join(buildDir,'renders'),{recursive:true});
for(const [i,slide] of presentation.slides.items.entries()){
 const png=await presentation.export({slide,format:'png',scale:1});
 await fs.writeFile(path.join(buildDir,'renders',`slide-${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await png.arrayBuffer()));
}
const identity=groups[0].identity,sourceFigureCount=groups.reduce((count,group)=>count+group.projected.figures.length,0);
const audit={schema:'qcal-pptx-export/1',...(multiple?{workflows:groups.map(group=>group.identity)}:
 {workflowId:identity.workflowId,revision:identity.revision,workflowComplete:identity.workflowComplete,
  ...(identity.coverage==='working_point'?{coverage:identity.coverage,full13Fresh:false,
   verified:identity.verified,total:identity.total,selectedNodeCount:identity.selectedNodeCount,
   completionLabel:identity.completionLabel,seedStatus:identity.seedStatus,seedSourceId:identity.seedSourceId}: {})}),
 snapshot:snapshotPath,snapshotSha256:hash(raw),template:templatePath,templateSha256,
 candidate,sourceFigureCount,slideAudit,
 status:'DRAFT_REQUIRES_VISUAL_REVIEW_AND_FINALIZATION'};
await fs.writeFile(path.join(buildDir,'source_coverage.json'),JSON.stringify(audit,null,2)+'\n');
console.log(JSON.stringify({candidate,slides:slideAudit.length,figures:sourceFigureCount,
 ...(multiple?{workflows:groups.map(group=>group.identity)}:{workflowComplete:identity.workflowComplete})}));
