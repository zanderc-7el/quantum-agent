// Runtime PowerPoint renderer: immutable local evidence, native tables, original figures.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
const args=Object.fromEntries(process.argv.slice(2).reduce((a,v,i,all)=>i%2?a:[...a,[v.replace(/^--/,''),all[i+1]]],[]));
for(const key of ['snapshot','reader','template','build-dir'])if(!args[key])throw Error(`Missing ${key}`);
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
function guard(p){const s=path.resolve(p);const f=s.replaceAll('\\','/').toLowerCase();if(f==='d:/qoskit-workspace'||f.startsWith('d:/qoskit-workspace/'))throw Error('Protected workspace');return s;}
async function input(p){return guard(await fs.realpath(guard(p)));}
const report=JSON.parse(await fs.readFile(await input(args.snapshot),'utf8'));
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
function page(title,section){const s=previous.duplicate();previous=s;clear(s);text(s,title,60,18,900,58,34);s.speakerNotes.text=JSON.stringify({workflowId:model.workflowId,revision:model.revision,section,sourceSnapshotSha256:hash(Buffer.from(JSON.stringify(report))),writingPolicy:model.policyVersion});audit.push({title,section,images:[]});return s;}
function table(s,values,widths){tablePages.push(audit.length);const t=s.tables.add({rows:values.length,columns:widths.length,left:62,top:120,width:1156,height:values.length*46,columnWidths:widths,values});t.cells.block({row:0,column:0,rowCount:values.length,columnCount:widths.length}).assign({textStyle:{typeface:'微软雅黑',fontSize:17,color:'#222222'},margins:{left:10,right:8,top:6,bottom:6},fill:'#FFFFFF',borders:{width:1,fill:'#DDDDDD'}});t.cells.block({row:0,column:0,rowCount:1,columnCount:widths.length}).assign({fill:'#F7E9E8',textStyle:{typeface:'微软雅黑',fontSize:17,bold:true,color:'#962222'}});}
function picture(s,f,x,y,w,h){const data=images.get(f.artifactId);if(!data)throw Error('Missing verified image');s.images.add({blob:data.bytes.buffer.slice(data.bytes.byteOffset,data.bytes.byteOffset+data.bytes.byteLength),contentType:data.mime,alt:f.caption,fit:'contain',position:{left:x,top:y,width:w,height:h}});audit.at(-1).images.push({id:f.artifactId,sha256:data.sha256,caption:f.caption});}
let s=page(model.title,'overview');
text(s,model.machine||'实验测量',65,137,1100,64,40);text(s,'测试人：Agent',65,224,1100,50,27);
text(s,model.summary,65,311,1100,86,31);text(s,model.scope,65,427,1100,80,23,'#555555');
text(s,model.sourceLabel,65,539,1100,40,23,'#992222');
text(s,'数据截至 '+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'medium'}).format(new Date(report.generatedAt)),65,611,1100,40,18,'#666666');
// Two-line metric cells expand when PowerPoint lays out the native table.
// Eight measurements leave room for those expanded rows and the footer.
for(let start=0;start<model.nodes.length;start+=8){
 s=page('测量结果汇总','summary');table(s,[['测量项目','主要结果','当前状态'],...model.nodes.slice(start,start+8).map(n=>[n.label,n.metrics.slice(0,2).map(m=>m.label+' '+m.value).join('\n')||'等待结果',n.status])],[290,566,300]);
 text(s,'读出指派、脉冲响应和单门保真度分别验收。',62,650,1156,34,18,'#555555');
}
for(const n of model.nodes){
 const groups=n.figures.length?Array.from({length:Math.ceil(n.figures.length/6)},(_,i)=>n.figures.slice(i*6,i*6+6)):[[]];
 for(const figures of groups){s=page(`${n.qubits} ${n.label}`,'result');text(s,n.status,62,92,1156,35,22,n.accepted?'#333333':'#9D2222');
  const cols=figures.length<=1?1:figures.length===2?2:3,rows=figures.length<=3?1:2,w=1156/cols,h=442/rows;
  figures.forEach((f,i)=>{const x=62+i%cols*w,y=141+Math.floor(i/cols)*h;text(s,f.caption,x,y,w-12,38,14,'#555555');picture(s,f,x,y+40,w-12,h-44);});
  if(!figures.length)text(s,'当前步骤尚无可展示的实测图。',62,257,1156,60,26,'#666666');
  text(s,n.metrics.slice(0,3).map(m=>m.label+' '+m.value).join('，'),62,596,1156,40,17,'#444444');
  // Acceptance qualification is next to the plot; long diagnostic details stay in the HTML report.
  text(s,n.accepted?'本轮诊断门槛及独立复测通过。':n.summary.slice(0,170),62,641,1156,58,17,n.accepted?'#555555':'#9D2222');
 }
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
await fs.writeFile(path.join(build,'export-audit.json'),JSON.stringify({schema:'reader-pptx/1',templateSha256:hash(templateBytes),fileSha256:hash(await fs.readFile(output)),slides:audit,tablePages,sourceFigureCount:allFigures.length,policyVersion:model.policyVersion,humanAccepted:false,roundTripPassed:true},null,2));
console.log(JSON.stringify({file:output,slideCount:audit.length,sourceFigureCount:allFigures.length}));
