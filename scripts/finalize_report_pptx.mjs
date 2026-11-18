import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const args=Object.fromEntries(process.argv.slice(2).reduce((rows,value,index,array)=>index%2?rows:[...rows,[value.replace(/^--/,''),array[index+1]]],[]));
for(const name of ['build-dir','workspace','output'])if(!args[name])throw new Error('Missing --'+name);
for(const name of ['PRESENTATION_SKILL_DIR','RUNTIME_PYTHON'])if(!process.env[name])throw new Error('Missing '+name);
const root=path.resolve(args.workspace),build=path.resolve(args['build-dir']),output=path.resolve(args.output);
const inside=(child,parent)=>{const r=path.relative(parent,child);return r!=='..'&&!r.startsWith('..'+path.sep)&&!path.isAbsolute(r);};
if(!inside(build,root)||!inside(output,root))throw new Error('Build and output must be inside workspace');
const protectedRoot='d:\\qoskit-workspace';
if([root,build,output].some(p=>p.toLowerCase()===protectedRoot||p.toLowerCase().startsWith(protectedRoot+'\\')))throw new Error('Protected workspace');
const audit=JSON.parse(await fs.readFile(path.join(build,'source_coverage.json'),'utf8'));
const skill=process.env.PRESENTATION_SKILL_DIR;
const {finalizePresentation}=await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')).href);
await fs.mkdir(path.dirname(output),{recursive:true});
const tableOwners=audit.slideAudit.flatMap((slide,index)=>/测量进度|采集记录|多任务测量汇总/.test(slide.title)?[index+1]:[]);
const result=await finalizePresentation({workspaceDir:root,candidatePath:path.join(build,'candidate.pptx'),finalPath:output,
 pythonExecutable:process.env.RUNTIME_PYTHON,
 integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
 layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
 layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit',...tableOwners.flatMap(n=>['--require-native-table-slide',String(n)])],
 explicitTotalSlideCount:audit.slideAudit.length,requiredNativeTableOwnerSlides:tableOwners,requiredNativeChartOwnerSlides:[],
 fontPolicy:{basis:'reference',families:['微软雅黑'],referencePath:audit.template,referenceSha256:audit.templateSha256},
 verifyArtifactToolImport:true,receiptPath:path.join(build,'final_validation.json')});
console.log(JSON.stringify(result));
