import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';

function harness(component='ScienceExecutionSetup'){
 const entry=new URL(`../src/components/${component}.tsx`,import.meta.url);assert.ok(existsSync(entry),'science execution component is missing');
 const compiled=buildSync({entryPoints:[fileURLToPath(entry)],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
 const slots:any[]=[];let cursor=0;const effects:(()=>void)[]=[];
 const hooks={...React,useState(initial:any){const n=cursor++;slots[n]??={value:typeof initial==='function'?initial():initial};return [slots[n].value,(value:any)=>{slots[n].value=typeof value==='function'?value(slots[n].value):value;}];},useRef(value:any){return slots[cursor++]??={current:value};},useCallback(fn:any){return fn;},useMemo(fn:any,deps:any[]){const n=cursor++;if(!slots[n]||deps.some((d,i)=>d!==slots[n].deps[i]))slots[n]={value:fn(),deps};return slots[n].value;},useEffect(fn:any,deps:any[]){const n=cursor++;if(!slots[n]||deps.some((d,i)=>d!==slots[n].deps[i])){slots[n]?.cleanup?.();slots[n]={deps};effects.push(()=>{slots[n].cleanup=fn();});}}};
 const module={exports:{} as any};const require=createRequire(import.meta.url);new Function('require','module','exports',compiled)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
 return {render(props:any){cursor=0;const tree=module.exports.default(props);while(effects.length)effects.shift()!();return tree;}};
}
function all(tree:any):any[]{return tree&&typeof tree==='object'&&tree.props?[tree,...[tree.props.children].flat(Infinity).flatMap(all)]:[];}
function text(tree:any):string{return typeof tree==='string'||typeof tree==='number'?String(tree):tree?.props?[tree.props.children].flat(Infinity).map(text).join(''):'';}
function field(tree:any,label:string){const value=all(tree).find(x=>x.props['aria-label']===label);assert.ok(value,'missing field '+label);return value.props;}
const base={chips:[{id:'chip',name:'测试芯片',versions:[{id:'c1',qubits:[{id:'Q1',label:'比特一',identityStatus:'confirmed',physicalId:'P1',parameters:{}}]}]}],deployments:[{id:'deployment',name:'测试接线',versions:[{id:'d1'},{id:'d2'}]}],name:'实验',onNameChange(){},disabled:false,onDraftChange(){}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('actual science setup loads no catalog until published chip wiring and qubit are selected',async()=>{
 const view=harness(),calls:string[]=[];const props={...base,api:{get:async(path:string)=>{calls.push(path);return path.startsWith('/science')?{records:[]}:{};}}};
 let tree=view.render(props);assert.deepEqual(calls,[]);assert.match(text(tree),/先选择已发布芯片/);
 field(tree,'科学实验芯片版本').onChange({target:{value:'c1'}});tree=view.render(props);
 field(tree,'科学实验接线版本').onChange({target:{value:'d1'}});tree=view.render(props);assert.deepEqual(calls,[]);
 field(tree,'科学实验目标比特').onChange({target:{value:'Q1'}});view.render(props);await tick();
 assert.equal(calls.filter(p=>p.startsWith('/catalog?')).length,1);assert.match(calls[0],/executionProfile=science-v3/);
 assert.ok(calls.includes('/science/records?summary=true'),'source picker must request bounded summaries');
});
test('workspace keeps classic creation default and retries the frozen V3 draft after uncertain response',async()=>{
 const view=harness('ManualExperimentWorkspace'),sent:any[]=[];
 const created={id:'created',manualExperiment:{nodeId:'node'},nodes:[{id:'node',procedureId:'sq_drag_scan'}]};
 const props={gateway:{},workspaceApi:{get:async()=>[]},workflows:[],busy:false,onBusy(){},onSelectWorkflow(){},onSelectNode(){},onAction(){},onBlueprint(){},onParameterDirty(){},onCreate:async(input:any)=>{sent.push(structuredClone(input));if(sent.length===1)throw new TypeError('network uncertain');return created;}};
 let tree=view.render(props);assert.ok(field(tree,'新建实验类型'));
 const button=(tree:any,label:string)=>{const found=all(tree).find(x=>x.type==='button'&&text(x).includes(label));assert.ok(found,'missing button '+label);return found.props;};
 button(tree,'形状 / DRAG / SHELVE 实测').onClick();tree=view.render(props);
 const setup=all(tree).find(x=>typeof x.props.onDraftChange==='function');assert.ok(setup);
 const draft={name:'形状实验',machine:'八号机',target:'Q18',qubits:1,type:'adaptive',policy:'strict',scenario:'normal',mode:'real',executionProfile:'science-v3',chipVersionId:'chip',deploymentVersionId:'wiring',resourceIds:['Q18'],manualProcedureId:'sq_drag_scan',manualParameters:{pulse_shape:'CosPulse'},manualScienceReferences:[{role:'pi01',recordId:'source'}]};
 setup.props.onDraftChange(draft);tree=view.render(props);button(tree,'下一步').onClick();await tick();tree=view.render(props);
 assert.equal(sent.length,1);assert.equal(sent[0].executionProfile,'science-v3');assert.equal(sent[0].manualParameters.pulse_shape,'CosPulse');
 setup.props.onDraftChange({...draft,name:'changed after uncertain response'});tree=view.render(props);button(tree,'核查创建结果').onClick();await tick();
 assert.equal(sent.length,2);assert.deepEqual(sent[1],sent[0]);
});
test('a late old wiring catalog cannot replace the current methods or source choices',async()=>{
 const view=harness(),pending:{path:string,resolve:(value:any)=>void}[]=[];
 const props={...base,api:{get:(path:string)=>path.startsWith('/science')?Promise.resolve({records:[]}):new Promise(resolve=>pending.push({path,resolve}))}};
 let tree=view.render(props);field(tree,'科学实验芯片版本').onChange({target:{value:'c1'}});tree=view.render(props);field(tree,'科学实验接线版本').onChange({target:{value:'d1'}});tree=view.render(props);field(tree,'科学实验目标比特').onChange({target:{value:'Q1'}});tree=view.render(props);
 field(tree,'科学实验接线版本').onChange({target:{value:'d2'}});view.render(props);
 const catalog=(wiring:string,label:string)=>({executionProfile:'science-v3',configurationBinding:{chipVersionId:'c1',deploymentVersionId:wiring,resourceId:'Q1'},manualMethods:[{procedureId:'method',label,capability:'unavailable',scienceSourceRoles:[],parameterBounds:{}}],preflight:{staticReady:true}});
 pending[1].resolve(catalog('d2','新接线目录'));await tick();tree=view.render(props);assert.match(text(tree),/新接线目录/);
 pending[0].resolve(catalog('d1','旧接线目录'));await tick();tree=view.render(props);assert.match(text(tree),/新接线目录/);assert.doesNotMatch(text(tree),/旧接线目录/);
});
test('observed resource setup names the uncertainty and sends source workflow without invented versions',async()=>{
 const view=harness(),calls:string[]=[];
 const props={...base,workflows:[{id:'origin',name:'真实读出任务',mode:'real',resourceIds:['Q11'],nodes:[{procedureId:'ro_iq01',engineState:'verified'}]}],api:{get:async(path:string)=>{calls.push(path);return path.startsWith('/science')?{records:[]}:{};}}};
 let tree=view.render(props);const toggle=all(tree).find(x=>x.type==='button'&&text(x).includes('已观测资源'));assert.ok(toggle);toggle.props.onClick();tree=view.render(props);
 assert.match(text(tree),/版图未确认/);field(tree,'实测资源来源任务').onChange({target:{value:'origin'}});tree=view.render(props);field(tree,'科学实验目标比特').onChange({target:{value:'Q11'}});view.render(props);await tick();
 const path=calls.find(p=>p.startsWith('/catalog?'))!;assert.match(path,/sourceWorkflowId=origin/);assert.doesNotMatch(path,/chipVersionId|deploymentVersionId/);
});

test('formal half-gate setup switches to empirical ns inputs without a SHELVE source',async()=>{
 const view=harness();let submitted:any;
 const scope={chipVersionId:'c1',deploymentVersionId:'d1',resourceId:'Q1'};
 const source={id:'readout',kind:'measurement',dataKind:'real',scope,reference:{},evidenceCurrent:true,evidenceIntact:true,provenance:{independentlyVerified:true},result:{procedureId:'ro_iq01',quality:{status:'passed'}}};
 const method={procedureId:'sq_drag_half_scan',label:'半门 DRAG',capability:'available',scienceSourceRoles:['readout','anharmonicity'],scienceSourceRolesByMode:{normalized_alpha:['readout','anharmonicity'],empirical_beta:['readout']},scienceSourceMethods:{readout:['ro_iq01'],anharmonicity:['sq_shelve_spectrum12']},parameterKeysByMode:{normalized_alpha:['drag_mode','drag_lambda1'],empirical_beta:['drag_mode','drag_beta_s','beta_start_s','beta_stop_s']},parameterBounds:{drag_mode:{enum:['normalized_alpha','empirical_beta'],default:'normalized_alpha'},drag_lambda1:{min:-10,max:10,default:0},drag_beta_s:{min:-1e-6,max:1e-6,unit:'s'},beta_start_s:{min:-1e-6,max:1e-6,unit:'s'},beta_stop_s:{min:-1e-6,max:1e-6,unit:'s'}}};
 const props={...base,executionProfile:'science-v3-irb',onDraftChange(value:any){submitted=value;},api:{get:async(path:string)=>path.startsWith('/science')?{records:[source]}:{executionProfile:'science-v3-irb',configurationBinding:scope,manualMethods:[method],preflight:{staticReady:true}}}};
 let tree=view.render(props);field(tree,'科学实验芯片版本').onChange({target:{value:'c1'}});tree=view.render(props);field(tree,'科学实验接线版本').onChange({target:{value:'d1'}});tree=view.render(props);field(tree,'科学实验目标比特').onChange({target:{value:'Q1'}});view.render(props);await tick();tree=view.render(props);
 field(tree,'科学实验方法').onChange({target:{value:'sq_drag_half_scan'}});tree=view.render(props);assert.ok(field(tree,'前置来源：非谐性来源'));
 field(tree,'DRAG 系数方案').onChange({target:{value:'empirical_beta'}});tree=view.render(props);
 assert.ok(!all(tree).some(x=>x.props['aria-label']==='前置来源：非谐性来源'));
 assert.ok(!all(tree).some(x=>x.props['aria-label']==='DRAG 系数 λ'));
 assert.match(text(tree),/经验导数系数/);
 for(const [name,value] of [['导数系数 β / ns','0.25'],['β 扫描起点 / ns','-1'],['β 扫描终点 / ns','1'],['前置来源：读出标定','readout']]){field(tree,name).onChange({target:{value}});tree=view.render(props);}
 assert.equal(submitted.manualParameters.drag_beta_s,2.5e-10);
 assert.deepEqual(submitted.manualScienceReferences,[{role:'readout',recordId:'readout'}]);
 assert.equal('drag_lambda1' in submitted.manualParameters,false);
});

test('IRB source selection loads and locks the accepted empirical gate before creation',async()=>{
 const view=harness();let submitted:any;const calls:string[]=[];
 const scope={chipVersionId:'c1',deploymentVersionId:'d1',resourceId:'Q1'};
 const scan={id:'scan',kind:'measurement',dataKind:'real',scope,reference:{},evidenceCurrent:true,evidenceIntact:true,provenance:{independentlyVerified:true},result:{procedureId:'sq_drag_half_scan',quality:{status:'passed'},parameters:{drag_mode:'empirical_beta',pulse_length_s:24e-9,shape_pi_amp:.6},targetPulseIdentity:{sha256:'gate',identity:{dragMode:'empirical_beta',betaSeconds:-.75e-9,amplitude:.3}}}};
 const method={procedureId:'sq_drag_irb',label:'同门IRB',capability:'available',scienceSourceRoles:['drag','anharmonicity'],scienceSourceRolesByMode:{normalized_alpha:['drag','anharmonicity'],empirical_beta:['drag']},scienceSourceMethods:{drag:['sq_drag_half_scan'],anharmonicity:['sq_shelve_spectrum12']},parameterKeysByMode:{normalized_alpha:['drag_mode','drag_lambda1','shape_pi_amp','pulse_length_s'],empirical_beta:['drag_mode','drag_beta_s','shape_pi_amp','pulse_length_s']},parameterBounds:{drag_mode:{enum:['normalized_alpha','empirical_beta'],default:'normalized_alpha'},drag_lambda1:{min:-10,max:10,default:0},shape_pi_amp:{min:.0001,max:.99,default:.4},pulse_length_s:{min:4e-9,max:2e-6,default:20e-9},drag_beta_s:{min:-1e-6,max:1e-6,unit:'s'}}};
 const props={...base,executionProfile:'science-v3-irb',onDraftChange(value:any){submitted=value;},api:{get:async(path:string)=>{calls.push(path);return path==='/science/records/scan'?scan:path.startsWith('/science')?{records:[{...scan,summary:true}]}:{executionProfile:'science-v3-irb',configurationBinding:scope,manualMethods:[method],preflight:{staticReady:true}};}}};
 let tree=view.render(props);field(tree,'科学实验芯片版本').onChange({target:{value:'c1'}});tree=view.render(props);field(tree,'科学实验接线版本').onChange({target:{value:'d1'}});tree=view.render(props);field(tree,'科学实验目标比特').onChange({target:{value:'Q1'}});view.render(props);await tick();tree=view.render(props);
 field(tree,'科学实验方法').onChange({target:{value:'sq_drag_irb'}});tree=view.render(props);
 field(tree,'前置来源：同一 X/2 DRAG 系数扫描与复测').onChange({target:{value:'scan'}});view.render(props);await tick();tree=view.render(props);
 assert.ok(calls.includes('/science/records/scan'));
 assert.equal(field(tree,'DRAG 系数方案').value,'empirical_beta');assert.equal(field(tree,'DRAG 系数方案').disabled,true);
 assert.equal(field(tree,'导数系数 β / ns').value,'-0.75');assert.equal(field(tree,'导数系数 β / ns').disabled,true);
 assert.equal(field(tree,'目标 π 幅度').value,'0.6');assert.equal(field(tree,'目标 π 幅度').disabled,true);
 assert.equal(submitted.manualParameters.drag_beta_s,-.75e-9);assert.equal(submitted.manualParameters.shape_pi_amp,.6);
 field(tree,'前置来源：同一 X/2 DRAG 系数扫描与复测').onChange({target:{value:''}});tree=view.render(props);
 assert.equal(field(tree,'DRAG 系数方案').disabled,false);assert.equal(submitted,undefined);
});
