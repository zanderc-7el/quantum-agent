// Runtime PowerPoint renderer: immutable local evidence, native tables, original figures.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { readerResultNodes } from './report_pptx_model.mjs';
import { acquisitionPages, diagnosticPages, presentationSummary } from './report_reader_layout.mjs';
const args=Object.fromEntries(process.argv.slice(2).reduce((a,v,i,all)=>i%2?a:[...a,[v.replace(/^--/,''),all[i+1]]],[]));
for(const key of ['snapshot','reader','template','build-dir'])if(!args[key])throw Error(`Missing ${key}`);
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
function guard(p){const s=path.resolve(p);const f=s.replaceAll('\\','/').toLowerCase();if(f==='d:/qoskit-workspace'||f.startsWith('d:/qoskit-workspace/'))throw Error('Protected workspace');return s;}
async function input(p){return guard(await fs.realpath(guard(p)));}
const snapshotBytes=await fs.readFile(await input(args.snapshot));
const sourceSnapshotSha256=hash(snapshotBytes);
if(args['snapshot-sha256'] && args['snapshot-sha256']!==sourceSnapshotSha256)throw Error('Frozen report snapshot hash differs');
const report=JSON.parse(snapshotBytes.toString('utf8'));
const model=JSON.parse(await fs.readFile(await input(args.reader),'utf8'));
if(report.workflow.id!==model.workflowId||report.workflow.revision!==model.revision)throw Error('Reader snapshot mismatch');
const build=guard(args['build-dir']);await fs.mkdir(build,{recursive:true});await input(build);
const output=path.join(build,'report.pptx');try{await fs.access(output);throw Error('Output already exists');}catch(e){if(e.code!=='ENOENT')throw e;}
const template=await input(args.template),templateBytes=await fs.readFile(template);
if(hash(templateBytes)!=='731c954096ea62e8c3d49f9757ac54a5e2b2a4ce4006b0ec0502d6041eafd52b')throw Error('Unreviewed quantum template');
const perPage=Number(args['appendix-count']||16);if(![16,64].includes(perPage))throw Error('Invalid appendix size');
const images=new Map(),root=await input(report.artifactRoot);
const allFigures=model.nodes.flatMap(n=>n.history.flatMap(a=>a.stages.flatMap(s=>s.figures)));
for(const figure of allFigures){
 const record=report.artifactIndex?.[figure.artifactId];if(!record||record.kind!=='image')throw Error('Unregistered source');
 const filename=await input(record.path),relative=path.relative(root,filename);
 if(relative==='..'||relative.startsWith('..'+path.sep)||path.isAbsolute(relative))throw Error('Source outside root');
 const bytes=await fs.readFile(filename);if(hash(bytes)!==record.sha256)throw Error('Image hash mismatch');
 const ext=path.extname(filename).toLowerCase(),mime={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}[ext];
 if(!mime)throw Error('Unsupported image');images.set(figure.artifactId,{bytes,mime,sha256:record.sha256});
}
const modules=process.env.RUNTIME_NODE_MODULES;if(!modules)throw Error('Missing Artifact Tool runtime');
const {FileBlob,PresentationFile}=await import(pathToFileURL(path.join(modules,'@oai/artifact-tool/dist/artifact_tool.mjs')).href);
const presentation=await PresentationFile.importPptx(await FileBlob.load(template));presentation.slides.keep([0]);
let previous=presentation.slides.items[0];const audit=[];const tablePages=[];
function clear(s){for(const kind of ['shapes','tables','images','charts'])for(const item of [...s[kind].items])s[kind].deleteById(item.id);s.speakerNotes.text='';}
clear(previous);
function text(s,value,x,y,w,h,size=22,color='#222222',bold=false){const a=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});a.text=String(value);a.text.style={typeface:'微软雅黑',fontSize:size,color,bold,verticalAlignment:'middle'};}
function page(title,section){const s=previous.duplicate();previous=s;clear(s);text(s,title,60,18,900,58,34);s.speakerNotes.text=JSON.stringify({workflowId:model.workflowId,revision:model.revision,section,sourceSnapshotSha256,writingPolicy:model.policyVersion});audit.push({title,section,images:[]});return s;}
function table(s,values,widths,{rowHeight=46,fontSize=17}={}){tablePages.push(audit.length);const t=s.tables.add({rows:values.length,columns:widths.length,left:62,top:120,width:1156,height:values.length*rowHeight,columnWidths:widths,values});t.cells.block({row:0,column:0,rowCount:values.length,columnCount:widths.length}).assign({textStyle:{typeface:'微软雅黑',fontSize,color:'#222222'},margins:{left:10,right:8,top:6,bottom:6},fill:'#FFFFFF',borders:{width:1,fill:'#DDDDDD'}});t.cells.block({row:0,column:0,rowCount:1,columnCount:widths.length}).assign({fill:'#F7E9E8',textStyle:{typeface:'微软雅黑',fontSize,bold:true,color:'#962222'}});}
function picture(s,f,x,y,w,h){const data=images.get(f.artifactId);if(!data)throw Error('Missing verified image');s.images.add({blob:data.bytes.buffer.slice(data.bytes.byteOffset,data.bytes.byteOffset+data.bytes.byteLength),contentType:data.mime,alt:f.caption,fit:'contain',position:{left:x,top:y,width:w,height:h}});audit.at(-1).images.push({id:f.artifactId,sha256:data.sha256,caption:f.caption});}
let s=page(model.title,'overview');
text(s,model.machine||'实验测量',65,137,1100,64,40);text(s,'测试人：Agent',65,224,1100,50,27);
text(s,model.summary,65,311,1100,86,31);text(s,model.scope,65,427,1100,80,23,'#555555');
text(s,model.sourceLabel,65,539,1100,40,23,'#992222');
text(s,'数据截至 '+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'medium'}).format(new Date(report.generatedAt)),65,611,1100,40,18,'#666666');
if(model.smartGate){
 s=page('整形 X/2 门校准','smart-gate');
 table(s,[['实验','本轮状态'],...model.smartGate.steps.map(item=>[item.label,item.status])],[760,396]);
 if(model.smartGate.verified){
  s=page('X/2 门条件保真度','smart-gate-fidelity');
  const percent=value=>(100*value).toFixed(3)+'%';
  table(s,[['采集','X/2 门保真度','95% 条件置信区间'],...model.smartGate.fidelity.map(item=>[item.role,percent(item.value),item.interval95.map(percent).join(' – ')])],[240,386,530]);
  text(s,'区间由配对序列的 bootstrap 得到，系统模型误差与泄漏另行评估。',62,470,1156,72,22);
  const sources=model.smartGate.fidelity;
  s.speakerNotes.text=JSON.stringify({...JSON.parse(s.speakerNotes.text),sources});
  audit.at(-1).sources=sources;
 }
}
if(model.workpoints?.length){
 for(const point of model.workpoints){
  s=page(`${point.resourceId} 已提交参数快照`,'workpoint');
  const labels={readout_hz:['读出频率',1e-9,'GHz'],readout_amp:['读出幅度',1,'a.u.'],readout_width_s:['读出时长',1e6,'μs'],drive_hz:['驱动频率',1e-9,'GHz'],drive_amp:['驱动幅度',1,'a.u.'],drive_length_s:['门长',1e9,'ns']};
  table(s,[['参数','账本值','修订'],...Object.entries(point.parameters).map(([key,p])=>{const [label,scale,unit]=labels[key];return[label,`${Number((p.value*scale).toPrecision(9))} ${unit}`,String(p.revision)];})],[400,550,206]);
  text(s,'账本可能保留已失效的旧值，本页不认定当前有效校准，也不是仪器读回。\n各轮实际参数见采集轮次表；当前资格以测项验收状态为准。',62,485,1156,90,21,'#555555');
  if(point.qualificationSummary)text(s,point.qualificationSummary,62,590,1156,85,20,'#9D2222');
 }
}
// Two-line metric cells expand when PowerPoint lays out the native table.
// Eight measurements leave room for those expanded rows and the footer.
for(let start=0;start<model.nodes.length;start+=8){
 s=page('测量结果汇总','summary');table(s,[['测量项目','主要结果','当前状态'],...model.nodes.slice(start,start+8).map(n=>[n.label,n.metrics.slice(0,2).map(m=>m.label+' '+m.value).join('\n')||'等待结果',n.status])],[290,566,300]);
 text(s,'读出指派、脉冲响应和单门保真度分别验收。',62,650,1156,34,18,'#555555');
}
for(const n of readerResultNodes(model,report)){
 const groups=n.figures.length?Array.from({length:Math.ceil(n.figures.length/6)},(_,i)=>n.figures.slice(i*6,i*6+6)):[[]];
 for(const figures of groups){s=page(`${n.qubits} ${n.label}`,'result');text(s,n.status+(n.acquisitionSummary?' · '+n.acquisitionSummary:''),62,92,1156,43,18,n.accepted?'#333333':'#9D2222');
  const cols=figures.length<=1?1:figures.length===2?2:3,rows=figures.length<=3?1:2,w=1156/cols,h=442/rows;
  figures.forEach((f,i)=>{const x=62+i%cols*w,y=141+Math.floor(i/cols)*h;text(s,f.caption,x,y,w-12,38,14,'#555555');picture(s,f,x,y+40,w-12,h-44);});
  if(!figures.length)text(s,'当前步骤尚无可展示的实测图。',62,257,1156,60,26,'#666666');
  text(s,n.metrics.slice(0,3).map(m=>m.label+' '+m.value).join('，'),62,588,1156,48,16,'#444444');
  // Full text is paginated below when it cannot fit here; never silently truncate a limitation.
  text(s,presentationSummary(n),62,641,1156,58,17,n.accepted?'#555555':'#9D2222');
 }
}
for(const detail of diagnosticPages(model.nodes)){
 s=page(`${detail.node.label} · 诊断与候选归属`,'diagnostic');
 text(s,detail.text,62,135,1156,460,23,detail.node.accepted?'#444444':'#9D2222');
 audit.at(-1).nodeId=detail.node.id;audit.at(-1).diagnosticPart=detail.index;
}
for(const [index,rows] of acquisitionPages(model).entries()){
 s=page(`采集轮次与工作点 ${index+1}`,'acquisition-history');
 table(s,[['时间 / 测项','轮次 / 截至报告的状态','本轮实际归档参数'],...rows.map(r=>[`${r.time}\n${r.resourceId} ${r.label}`,`第${r.number}次 · ${r.role}\n${r.status}`,r.workpoint])],[210,260,686],{rowHeight:96,fontSize:15});
 text(s,'北京时间；幅度为无量纲比例。扫描参数按实际扫描解释。完整尝试及操作标识保留于本页备注。',62,637,1156,48,17,'#555555');
 const sources=rows.map(r=>({nodeId:r.nodeId,attemptId:r.attemptId,operationId:r.operationId,acquiredAt:r.acquiredAt,parameterSource:r.parameterSource}));
 s.speakerNotes.text=JSON.stringify({...JSON.parse(s.speakerNotes.text),sources});audit.at(-1).sources=sources;
}
if(allFigures.length){s=page('附录：历次采集图','appendix-cover');text(s,'每张图保留所属测项、尝试次数和验收状态。',65,237,1100,90,30);text(s,`共 ${allFigures.length} 张登记图，以下每页最多 ${perPage} 张。`,65,379,1100,70,25,'#555555');}
for(let start=0;start<allFigures.length;start+=perPage){s=page(`历次采集图 ${Math.floor(start/perPage)+1}`,'appendix');const cols=Math.sqrt(perPage),w=1156/cols,h=590/cols;allFigures.slice(start,start+perPage).forEach((f,i)=>{const x=62+i%cols*w,y=100+Math.floor(i/cols)*h;text(s,f.caption,x,y,w-8,perPage===16?34:24,perPage===16?11:7.5,f.status==='未通过'?'#9D2222':'#555555');picture(s,f,x,y+(perPage===16?36:25),w-10,h-(perPage===16?40:29));});}
presentation.slides.keep(Array.from({length:presentation.slides.items.length-1},(_,i)=>i+1));
const referenced=new Set(presentation.getReferencedImageIds());for(const a of [...presentation.getImageAssetItems()])if(!referenced.has(a.id))presentation.deleteImageAsset(a.id);
await(await PresentationFile.exportPptx(presentation)).save(output);
// Round-trip the delivered bytes; renders are available for review, not presented as a human acceptance.
const final=await PresentationFile.importPptx(await FileBlob.load(output));if(final.slides.items.length!==audit.length)throw Error('PPT import count mismatch');
await fs.mkdir(path.join(build,'renders'),{recursive:true});
for(const [i,slide] of final.slides.items.entries()){const png=await final.export({slide,format:'png',scale:1});await fs.writeFile(path.join(build,'renders',`slide-${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await png.arrayBuffer()));}
await fs.writeFile(path.join(build,'export-audit.json'),JSON.stringify({schema:'reader-pptx/1',sourceSnapshotSha256,workflowId:model.workflowId,revision:model.revision,templateSha256:hash(templateBytes),fileSha256:hash(await fs.readFile(output)),slides:audit,tablePages,sourceFigureCount:allFigures.length,acquisitionCount:model.acquisitionHistory?.length??0,policyVersion:model.policyVersion,humanAccepted:false,roundTripPassed:true},null,2));
console.log(JSON.stringify({file:output,slideCount:audit.length,sourceFigureCount:allFigures.length}));
