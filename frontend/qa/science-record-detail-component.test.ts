import {existsSync,readFileSync} from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';

function harness(){
 const compiled=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ScienceWorkbench.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
 const scopes=new Map<string,any[]>();let slots:any[]=[],cursor=0;const effects:(()=>void)[]=[];
 const hooks={...React,useState(initial:any){const n=cursor++,active=slots;active[n]??={value:typeof initial==='function'?initial():initial};return [active[n].value,(value:any)=>{active[n].value=typeof value==='function'?value(active[n].value):value;}];},useRef(value:any){return slots[cursor++]??={current:value};},useMemo(fn:any,deps:any[]){const n=cursor++;if(!slots[n]||deps.some((d,i)=>d!==slots[n].deps[i]))slots[n]={value:fn(),deps};return slots[n].value;},useCallback(fn:any,deps:any[]){return hooks.useMemo(()=>fn,deps);},useEffect(fn:any,deps:any[]){const n=cursor++,active=slots;if(!active[n]||deps.some((d,i)=>d!==active[n].deps[i])){active[n]?.cleanup?.();active[n]={deps};effects.push(()=>{active[n].cleanup=fn();});}}};
 const module={exports:{} as any},require=createRequire(import.meta.url);new Function('require','module','exports',compiled)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
 function renderNode(node:any,key:string){slots=scopes.get(key)??[];scopes.set(key,slots);cursor=0;const tree=node.type(node.props);while(effects.length)effects.shift()!();return tree;}
 return {render(props:any){return renderNode(module.exports.default(props),'workspace');},renderNode};
}
function all(tree:any):any[]{return tree&&typeof tree==='object'&&tree.props?[tree,...[tree.props.children].flat(Infinity).flatMap(all)]:[];}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('Bloch normal entry imports historical CPMG N2 and native phase without adopting its failed fit',{skip:!existsSync(new URL('./fixtures/bloch-historical-records.json',import.meta.url))&&'Private regression fixture excluded from source release'},async()=>{
 const records=JSON.parse(readFileSync(new URL('./fixtures/bloch-historical-records.json',import.meta.url),'utf8')).records;
 records.push({id:'model-not-acquired',kind:'predistortion',dataKind:'simulation',scope:{},input:{},result:{source:'model'}});
 records.push({id:'fixture-not-real',kind:'measurement',dataKind:'fixture',scope:{},input:{},result:{source:'fixture'}});
 const cpmg=records.find((r:any)=>r.result.procedureId==='sq_cpmg'),t1=records.find((r:any)=>r.result.procedureId==='sq_t1');
 const original=JSON.stringify(records),writes:any[]=[];
 const props={gateway:{get:async(path:string)=>path.includes('/overview')?{}:{records,hasMore:false},write:async(path:string,body:any)=>{assert.equal(path,'/science/preview');writes.push(body);return {trajectory:[],series:[]};}}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.type==='button'&&n.props.children==='布洛赫演化').props.onClick();tree=view.render(props);
 const panel=all(tree).find(n=>n.type?.name==='BlochPanel');let content=view.renderNode(panel,'bloch');
 all(content).find(n=>n.type==='select'&&n.props.value==='x').props.onChange({target:{value:'cpmg'}});content=view.renderNode(panel,'bloch');
 const select=()=>all(content).find(n=>n.props.label==='关联同一比特的已有测量记录');
 assert.ok(!select().props.records.some((r:any)=>r.id==='model-not-acquired'));
 assert.ok(!select().props.records.some((r:any)=>r.id==='fixture-not-real'));
 select().props.onChange(cpmg.id);content=view.renderNode(panel,'bloch');content=view.renderNode(panel,'bloch');
 assert.equal(all(content).find(n=>n.props.label==='π 脉冲数 N').props.value,2);
 assert.equal(all(content).find(n=>n.props.label==='原扫描等待上限').props.value,40000);
 assert.equal(all(content).find(n=>n.props.label==='T₂(N)').props.value,30000,'failed fit must stay outside model defaults');
 assert.match(JSON.stringify(content),/未通过验收/);
 all(content).find(n=>n.props.label==='π 脉冲数 N').props.onChange(3);content=view.renderNode(panel,'bloch');
 await all(content).find(n=>n.type?.name==='Actions').props.onPreview();await tick();
 assert.equal(writes[0].input.piPulses,3);assert.equal(writes[0].input.readoutSign,-1);assert.equal(writes[0].input.replaySource.recordId,cpmg.id);
 assert.ok(writes[0].input.replaySource.manualOverrides.includes('piPulses'));
 content=view.renderNode(panel,'bloch');select().props.onChange(t1.id);content=view.renderNode(panel,'bloch');content=view.renderNode(panel,'bloch');
 assert.equal(all(content).find(n=>n.props.label==='T₁').props.value,t1.result.metrics.t1_s*1e9);
 assert.ok(!all(content).some(n=>n.props.label==='π 脉冲数 N'));
 assert.equal(JSON.stringify(records),original);
});

test('normal signal entry reads actual compiled samples on demand and keeps ADC and missing target separate',{skip:!existsSync(new URL('../qa-artifacts/r19-p102-contract-fixture.json',import.meta.url))&&'Generated QA artifact excluded from source release'},async()=>{
 const response=JSON.parse(readFileSync(new URL('../qa-artifacts/r19-p102-contract-fixture.json',import.meta.url),'utf8'));
 const record={id:response.recordId,kind:'measurement',source:'acquisition',reference:{workflowId:response.workflowId,attemptId:response.attemptId},scope:{workflowId:response.workflowId,resourceId:'Q11'},input:{},
  result:{procedureId:'sq_rabi_time',source:'acquisition',dataKind:'real',parameters:{drive_amp:.3},acquiredAt:'2026-11-18T12:26:27Z',
   sourceSeries:[{id:'adc',label:'ADC projection',quantity:'iq_amplitude',unit:'ADC',x:[0,1e-6],xUnit:'s',values:[.1,.5]}]}};
 const original=JSON.stringify(record),calls:string[]=[];
 const props={gateway:{get:async(path:string)=>{calls.push(path);return path.includes('/pulse-samples')?response:path.includes('/overview')?{}:{records:[record],hasMore:false};},write:()=>assert.fail('view only')}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.type==='button'&&n.props.children==='信号与预补偿').props.onClick();tree=view.render(props);
 const panel=()=>all(tree).find(n=>n.type?.name==='SignalPanel');let content=view.renderNode(panel(),'signal');
 all(content).find(n=>n.props.label==='信号来源记录').props.onChange(record.id);content=view.renderNode(panel(),'signal');
 const bound=all(content).find(n=>n.type?.name==='SignalRecordEvidence');assert.ok(bound,'normal entry must bind the archive viewer');
 const boundView=view.renderNode(bound,'signal-record');
 assert.match(JSON.stringify(boundView),/目标主包络/);assert.match(JSON.stringify(boundView),/未单独归档/);
 assert.match(JSON.stringify(boundView),/ADC 读回/);assert.match(JSON.stringify(boundView),/独立实测的片端响应/);
 const acquisition=all(boundView).find(n=>n.type?.name==='ScienceAcquisitionEvidence');assert.ok(acquisition);
 let evidence=view.renderNode(acquisition,'signal-pulse');assert.equal(calls.filter(p=>p.includes('/pulse-samples')).length,0);
 await all(evidence).find(n=>n.type==='button'&&n.props.children==='查看脉冲样点').props.onClick();evidence=view.renderNode(acquisition,'signal-pulse');
 assert.deepEqual(calls.filter(p=>p.includes('/pulse-samples')),[`/science/records/${record.id}/pulse-samples`]);
 const plots=all(evidence).filter(n=>n.type?.name==='Plot');assert.equal(plots.length,2);
 assert.equal(plots[1].props.label,'单路 RF 编译样点');assert.deepEqual(plots[0].props.series[0].y,response.samples.i);
 assert.ok(all(boundView).some(n=>n.props.result===record.result),'ADC main result must remain visible without reinterpretation');
 assert.equal(JSON.stringify(record),original);
 const selected=all(content).find(n=>n.props.label==='信号来源记录');selected.props.onChange('missing');content=view.renderNode(panel(),'signal');
 assert.ok(!all(content).some(n=>n.type?.name==='SignalRecordEvidence'),'changing the selected source unmounts old pulse state');
});

test('signal entry keeps model records separate and offers no acquisition-byte lookup',async()=>{
 const record={id:'model',kind:'predistortion',source:'model',scope:{},input:{target:[0,1]},result:{source:'model',dataKind:'simulation',series:[]}};
 const props={gateway:{get:async(path:string)=>path.includes('/overview')?{}:{records:[record],hasMore:false},write:()=>assert.fail('view only')}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.type==='button'&&n.props.children==='信号与预补偿').props.onClick();tree=view.render(props);
 const panel=()=>all(tree).find(n=>n.type?.name==='SignalPanel');let content=view.renderNode(panel(),'signal');
 all(content).find(n=>n.props.label==='信号来源记录').props.onChange(record.id);content=view.renderNode(panel(),'signal');
 const bound=all(content).find(n=>n.type?.name==='SignalRecordEvidence');assert.ok(bound);
 const evidence=view.renderNode(bound,'model-evidence');
 assert.ok(!all(evidence).some(n=>n.type?.name==='ScienceAcquisitionEvidence'));
 assert.ok(all(evidence).some(n=>n.props.result===record.result));
 assert.match(JSON.stringify(evidence),/未绑定本机采集/);
});

test('actual record-library entry opens archived baseband and RF graphs plus registered downloads',async()=>{
 const record={id:'r11-irb',kind:'measurement',scope:{resourceId:'Q11'},input:{},dataKind:'real',result:{procedureId:'sq_drag_irb',source:'acquisition',gateCharacterization:{sourceReferences:[{role:'drag',recordId:'scan'}]}}};
 const response={schema:'qcal-archived-pulse-samples/1',recordId:record.id,stage:'verification',stages:[{id:'verification',label:'独立复测'}],currentEvidence:true,rows:[{rowId:12,condition:'interleaved'}],segments:[{id:'xy2',channel:'xy'}],selected:{rowId:12,segmentId:'xy2'},segment:{id:'xy2',basebandI:{sha256:'i-sha'},basebandQ:{sha256:'q-sha'}},samples:{timeNs:[0,.25,.5],i:[0,.2,0],q:[.1,0,-.1],rfTimeNs:[0,.25,.5],rf:[0,-.3,.1],basebandSampleCount:3,rfSampleCount:3,decimation:'exact-index-subsampling',maxDisplaySamples:2048},upload:{status:'matched',receipts:[],deviceWaveformReadback:false},artifacts:[{id:'wave',name:'preflight_0012.npz',sha256:'wave-sha'}]};
 const calls:string[]=[];const props={gateway:{get:async(path:string)=>{calls.push(path);return path.endsWith('/pulse-samples')?response:path.includes('/overview')?{}:{records:[record],hasMore:false};}}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.props.label==='查看保存记录').props.onChange(record.id);tree=view.render(props);
 const panel=all(tree).find(n=>n.type?.name==='ScienceAcquisitionEvidence');assert.ok(panel,'record library must expose the pulse panel');
 let content=view.renderNode(panel,'pulse-evidence');
 await all(content).find(n=>n.type==='button'&&n.props.children==='查看脉冲样点').props.onClick();content=view.renderNode(panel,'pulse-evidence');
 assert.ok(calls.includes('/science/records/r11-irb/pulse-samples'));
 const plots=all(content).filter(n=>n.type?.name==='Plot');assert.equal(plots.length,2);
 const baseband=view.renderNode(plots[0],'baseband-plot');const rf=view.renderNode(plots[1],'rf-plot');
 assert.equal(all(baseband).filter(n=>n.type==='path').length,2);assert.equal(all(rf).filter(n=>n.type==='path').length,1);
 assert.ok(all(baseband).some(n=>n.type==='svg'&&n.props['aria-label'].includes('行 12 / xy2')));
 assert.equal(all(content).find(n=>n.type==='a').props.href,'/api/artifacts/wave');
 assert.ok(!calls.some(path=>path.includes('/workflows')||path.includes('/native-preview')));
});

test('SHELVE panel loads genuine comparison detail separately from manual count calculation',async()=>{
 const summary={id:'real-shelve',summary:true,kind:'measurement',scope:{},input:{},dataKind:'real',result:{procedureId:'sq_shelve_readout_compare',source:'acquisition'}};
 const manual={id:'manual',kind:'shelve',scope:{},input:{counts:[[9,1],[1,9]]},result:{source:'provided_counts'}};
 const other={id:'rabi',kind:'measurement',scope:{},input:{},dataKind:'real',result:{procedureId:'sq_rabi_time'}};
 const full={...summary,summary:false,result:{...summary.result,shelveStage:'verification',comparison:{off:{counts:[[9,1],[1,9]]},on:{counts:[[10,0],[1,9]]}}}};
 const calls:string[]=[];const props={gateway:{get:async(path:string)=>{calls.push(path);if(path.includes('/records/real-shelve'))return full;return path.includes('/overview')?{}:{records:[summary,manual,other],hasMore:false};}}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.type==='button'&&n.props.children==='SHELVE 读出').props.onClick();tree=view.render(props);
 const panel=()=>all(tree).find(n=>n.type?.name==='ShelvePanel');let content=view.renderNode(panel(),'shelve');
 const selector=()=>all(content).find(n=>n.props.label==='真实 SHELVE 留出读出记录');assert.ok(selector());
 let select=view.renderNode(selector(),'real-shelve-select');
 assert.deepEqual(all(select).filter(n=>n.type==='option'&&n.props.value).map(n=>n.props.value),['real-shelve']);
 all(select).find(n=>n.type==='select').props.onChange({target:{value:'real-shelve'}});
 content=view.renderNode(panel(),'shelve');view.renderNode(selector(),'real-shelve-select');await tick();tree=view.render(props);content=view.renderNode(panel(),'shelve');
 const shown=all(content).find(n=>n.props.result===full.result);assert.ok(shown);
 const result=view.renderNode(shown,'shelve-result');assert.ok(all(result).some(n=>n.type?.name==='ScienceShelveResult'));
 assert.equal(calls.filter(p=>p.includes('/records/real-shelve')).length,1);
 const manualCells=all(content).filter(n=>typeof n.props.onChange==='function'&&String(n.props.label).startsWith('制备 |'));
 assert.equal(manualCells.length,8);assert.ok(manualCells.every(n=>n.props.value===0));
 assert.ok(!calls.some(p=>p.includes('run_step')||p.includes('/workflows')));
});

test('different-grid cavity overlay never labels same-index samples as the same frequency',async()=>{
 const result={procedureId:'sq_flux_point_recheck',scienceFlux:{sourcePoint:{originalCut:{frequenciesHz:[7e9,7.001e9,7.002e9],responseMagnitude:[.4,.1,.5]}},points:[{phase:'local_cavity',frequenciesHz:[7.0005e9,7.001e9,7.0015e9],responseMagnitude:[.3,.12,.35]}]}};
 const record={id:'r',kind:'measurement',scope:{},input:{},result};
 const props={gateway:{get:async(path:string)=>path.includes('/overview')?{}:{records:[record],hasMore:false}}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.props.label==='查看保存记录').props.onChange('r');tree=view.render(props);
 const content=view.renderNode(all(tree).find(n=>n.props.result===result),'result');
 const compare=view.renderNode(all(content).find(n=>n.type?.name==='ScienceFluxComparison'),'comparison');
 const plotNode=all(compare).find(n=>n.props.label==='同偏置新旧局部腔谱对照');let plot=view.renderNode(plotNode,'overlay');
 const first=all(plot).find(n=>n.type==='rect'&&n.props.onMouseEnter);
 assert.ok(JSON.stringify(first.props.children).includes('此频点未采样'));
 first.props.onMouseEnter();plot=view.renderNode(plotNode,'overlay');
 assert.ok(JSON.stringify(all(plot).find(n=>n.props.className==='science-point')).includes('此频点未采样'));
 assert.equal(all(plot).filter(n=>n.type==='circle'&&n.props.r===5).length,1);
});

test('saved nonzero bias point returns to its original cut and keeps native ADC units',async()=>{
 const grid={biasValues:[-.02,0,.02],frequencyHz:[7e9,7.001e9,7.002e9],amplitude:[[1,2,3],[4,5,6],[7,8,9]],biasUnit:'V',unit:'ADC'};
 const scan={id:'scan',kind:'measurement',scope:{},input:{},result:{procedureId:'ro_flux_scan',source:'acquisition',grid,scienceFlux:{route:{lineId:'Z1'},points:[]}}};
 const point={id:'point',kind:'working_point',scope:{},input:{sourceRecordId:'scan',selectedBiasIndex:2,selectedFrequencyIndex:1},result:{point:{sourceRecordId:'scan',purpose:'idle',bias:.02,biasUnit:'V',readoutFrequencyHz:7.001e9}}};
 const props={gateway:{get:async(path:string)=>path.includes('/overview')?{}:{records:[scan,point],hasMore:false}}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.type==='button'&&n.props.children==='磁通与工作点').props.onClick();tree=view.render(props);
 const panel=()=>all(tree).find(n=>n.type?.name==='FluxPanel');let content=view.renderNode(panel(),'flux');
 all(content).find(n=>n.props.label==='已保存点位').props.onChange('point');content=view.renderNode(panel(),'flux');
 all(content).find(n=>n.type==='button'&&n.props.children==='回看原始谱线').props.onClick();content=view.renderNode(panel(),'flux');
 assert.equal(all(content).find(n=>n.props['aria-label']==='磁通截线').props.value,2);
 const plot=all(content).find(n=>String(n.props.label??'').startsWith('原始截线'));
 assert.deepEqual(plot.props.series[0].y,[7,8,9]);assert.equal(plot.props.series[0].yUnit,'ADC');assert.equal(plot.props.cursor,1);
});

test('scientific metrics retain values while naming amplitude frequency and model records accurately',async()=>{
 const record={id:'m',kind:'simulation',scope:{},input:{},result:{source:'simulation',metrics:{amplitude_oscillation_frequency:2.5,contrast_snr:11,cycles:1.2,relative_stderr:.03,unknown_metric:7}}};
 const props={gateway:{get:async(path:string)=>path.includes('/overview')?{}:{records:[record],hasMore:false}}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 let select=view.renderNode(all(tree).find(n=>n.props.label==='查看保存记录'),'select');
 assert.ok(JSON.stringify(select).includes('模型预测'));
 assert.ok(!all(select).some(n=>n.type==='option'&&String(n.props.children).includes('simulation')));
 all(select).find(n=>n.type==='select').props.onChange({target:{value:'m'}});tree=view.render(props);
 const result=view.renderNode(all(tree).find(n=>n.props.result===record.result),'result');
 const output=JSON.stringify(result);
 for(const label of ['幅度轴振荡频率 / (a.u.)⁻¹','对比度信噪比','扫描窗内振荡周期数','相对标准误差'])assert.ok(output.includes(label),label);
 assert.ok(output.includes('unknown_metric'));assert.equal(record.result.metrics.relative_stderr,.03);
});

test('pulse current ledger load preserves edited draft across workflow updates and late responses',async()=>{
 const workflow:any={id:'wf',name:'Q11',target:'Q11',resourceIds:['Q11'],revision:590,currentNodeId:'node',nodes:[{id:'node',label:'定频',resourceId:'Q11',parameters:{readout_hz:7009510000}}]};
 const calls:string[]=[];let pending:((detail:any)=>void)|undefined;let captured:any;
 const detail={node:{id:'node'},workflowRevision:590,parameterVersions:{readout_hz:4},parameters:{readout_hz:7009610000,readout_amp:.2,readout_width_s:2e-6,drive_hz:5e9,drive_amp:.3,drive_length_s:20e-9}};
 const props:any={workflow,gateway:{get:async(path:string)=>{calls.push(path);if(path.includes('/nodes/'))return pending?new Promise(resolve=>{pending=resolve;}):detail;if(path.includes('/overview'))return {};return {records:[],hasMore:false};},write:async(_path:string,body:any)=>{captured=body;return {id:'saved',result:{}};}}};
 const view=harness();let tree=view.render(props);const pulse=()=>all(tree).find(n=>n.type?.name==='PulsePanel');
 let panel=view.renderNode(pulse(),'pulse');await tick();tree=view.render(props);panel=view.renderNode(pulse(),'pulse');
 const field=(label:string)=>all(panel).find(n=>n.props.label===label&&typeof n.props.onChange==='function');
 assert.equal(field('频率').props.value,5e9);field('频率').props.onChange(5.1e9);
 tree=view.render({...props,workflow:{...workflow,revision:591}});panel=view.renderNode(all(tree).find(n=>n.type?.name==='PulsePanel'),'pulse');
 assert.equal(field('频率').props.value,5.1e9);assert.equal(calls.filter(p=>p.includes('/nodes/')).length,1);
 await all(panel).find(n=>n.props.onSave).props.onSave();await tick();
 assert.equal(captured.input.readoutFrequencyHz,7009610000);assert.equal(captured.input.frequencyHz,5.1e9);assert.equal(captured.input.phaseDeg,null);
 assert.equal(captured.input.parameterSource.modifiedLocally,true);
 pending=()=>{};all(panel).find(n=>n.type==='button'&&n.props.children==='用当前参数替换草稿').props.onClick();
 field('频率').props.onChange(5.2e9);pending!({...detail,parameters:{...detail.parameters,drive_hz:4e9}});await tick();
 panel=view.renderNode(all(tree).find(n=>n.type?.name==='PulsePanel'),'pulse');assert.equal(field('频率').props.value,5.2e9);
});
test('workbench loads only selected detail, retains complete source and does not refetch across renders',async()=>{
 const summary={id:'source',summary:true,kind:'measurement',scope:{},input:{},result:{source:'acquisition',dataKind:'real',parameters:{drive_hz:5e9}}};
 const full={...summary,summary:false,input:{phaseRad:.3},result:{...summary.result,series:[{id:'trace',values:[1,2]}]},dependencyProof:{closureSha256:'kept'}};
 const calls:string[]=[];const props={gateway:{get:async(path:string)=>{calls.push(path);if(path.includes('/records/source'))return full;if(path.includes('/overview'))return {counts:{records:1}};return {records:[summary],summary:true,hasMore:false};}}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 assert.ok(calls.some(path=>path.includes('summary=true')));assert.ok(!calls.some(path=>path.includes('/records/source')));
 const selector=()=>all(tree).find(node=>node.props.label==='查看保存记录');assert.ok(selector());
 let selected=view.renderNode(selector(),'library');all(selected).find(node=>node.type==='select').props.onChange({target:{value:'source'}});
 tree=view.render(props);view.renderNode(selector(),'library');await tick();tree=view.render(props);view.renderNode(selector(),'library');await tick();
 for(let n=0;n<4;n++){tree=view.render(props);view.renderNode(selector(),'library');}
 assert.equal(calls.filter(path=>path.includes('/records/source')).length,1);
 const details=all(tree).find(node=>node.props.title==='冻结输入与完整来源');assert.ok(details);assert.deepEqual(details.props.value,full);
 const plotResult=all(tree).find(node=>node.props.result===full.result);assert.ok(plotResult);
});

test('comparison panel waits for full source detail and preserves its curve selector',async()=>{
 const summary={id:'source',summary:true,kind:'measurement',scope:{},input:{},dataKind:'real',result:{procedureId:'sq_rabi_amplitude'}};
 const trace={id:'original',label:'原始 ADC 曲线',quantity:'iq_amplitude',unit:'ADC',values:[.1,.3],x:[0,1],xUnit:'a.u.'};
 const full={...summary,summary:false,input:{drive_hz:5e9},result:{...summary.result,sourceSeries:[trace]}};
 const calls:string[]=[];let finish!:(record:any)=>void;
 const props={gateway:{get:async(path:string)=>{calls.push(path);if(path.includes('/records/source'))return new Promise(resolve=>finish=resolve);if(path.includes('/overview'))return {};return {records:[summary],summary:true,hasMore:false};}}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(node=>node.type==='button'&&node.props.children==='仿真实测对照').props.onClick();tree=view.render(props);
 const panel=()=>all(tree).find(node=>node.type?.name==='ComparePanel');let content=view.renderNode(panel(),'compare');
 const source=()=>all(content).find(node=>node.props.label==='原始采集记录');
 let selector=view.renderNode(source(),'compare-source');all(selector).find(node=>node.type==='select').props.onChange({target:{value:'source'}});
 content=view.renderNode(panel(),'compare');view.renderNode(source(),'compare-source');await tick();
 assert.ok(!all(content).some(node=>node.type==='option'&&node.props.value==='original'));
 finish(full);await tick();tree=view.render(props);content=view.renderNode(panel(),'compare');
 assert.ok(all(content).some(node=>node.type==='option'&&node.props.value==='original'));
 assert.equal(calls.filter(path=>path.includes('/records/source')).length,1);
});


test('R09 calibration import preserves file bytes and comparison mask can restore every point',async()=>{
 const source={id:'real-rabi',kind:'measurement',dataKind:'real',scope:{workflowId:'w',resourceId:'Q11'},input:{},result:{procedureId:'sq_rabi_time',axes:{x:{name:'Time',unit:'s',values:[0,1]}},series:[{id:'trace-0',label:'IQ',quantity:'iq_amplitude',unit:'IQ a.u.',values:[.1,.5]}]}};
 const model={id:'mapped-model',kind:'simulation',scope:source.scope,input:{sourceRecordId:source.id},result:{axes:source.result.axes,series:[{id:'observation',label:'external-calibrated model',quantity:'iq_amplitude',unit:'IQ a.u.',values:[.1,.4]}]}};
 const calls:any[]=[];const gateway={get:async(path:string)=>path.includes('/overview')?{}:{records:[source,model],hasMore:false},write:async(path:string,body:any)=>{calls.push({path,body});if(path==='/science/records')return {id:'imported',kind:'readout_calibration',input:body.input};const masked=body.excludedLeftPoints??[];return {axes:source.result.axes,series:[{...source.result.series[0],values:[0,1].map((x,i)=>masked.includes(x)?null:source.result.series[0].values[i])},model.result.series[0],{id:'difference',values:[0,1].map((x,i)=>masked.includes(x)?null:i===0?0:.1)}],metrics:{comparedPoints:2-masked.length},view:{excludedLeftPoints:masked}};}};
 const props={gateway};const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.type==='button'&&n.props.children==='仿真实测对照').props.onClick();tree=view.render(props);
 const panel=()=>all(tree).find(n=>n.type?.name==='ComparePanel');let content=view.renderNode(panel(),'r09');
 all(content).find(n=>n.props.label==='原始采集记录').props.onChange(source.id);content=view.renderNode(panel(),'r09');
 const importNode=all(content).find(n=>n.type?.name==='ReadoutCalibrationImport');let importer=view.renderNode(importNode,'r09-import');
 const bytes=Uint8Array.from([0xef,0xbb,0xbf,0x7b,0x7d]);
 const fileInput=all(importer).find(n=>n.props['aria-label']==='导入读出标定 JSON');fileInput.props.onChange({target:{files:[{name:'BOM.json',size:5,arrayBuffer:async()=>bytes.buffer}],value:'x'}});await tick();await tick();
 const saved=calls.find(c=>c.path==='/science/records');assert.equal(saved.body.kind,'readout_calibration');assert.deepEqual([...Buffer.from(saved.body.input.documentBase64,'base64')],[...bytes]);assert.equal(saved.body.input.sourceSeriesId,'trace-0');assert.equal(saved.body.input.sourceRecordId,source.id);
 tree=view.render(props);content=view.renderNode(panel(),'r09');all(content).find(n=>n.props.label==='第二条保存记录（模型或复测）').props.onChange(model.id);content=view.renderNode(panel(),'r09');
 all(content).find(n=>n.type==='button'&&n.props.children==='检查条件并比较').props.onClick();await tick();content=view.renderNode(panel(),'r09');
 all(content).find(n=>n.type==='button'&&n.props.children==='暂不比较此点').props.onClick();await tick();content=view.renderNode(panel(),'r09');
 assert.deepEqual(calls.at(-1).body.excludedLeftPoints,[0]);assert.ok(JSON.stringify(content).includes('缺点'));
 all(content).find(n=>n.type==='button'&&n.props.children==='恢复此点').props.onClick();await tick();content=view.renderNode(panel(),'r09');assert.deepEqual(calls.at(-1).body.excludedLeftPoints,[]);
 assert.ok(calls.every(c=>!c.path.includes('/workflows')&&!c.path.includes('run_step')));
});


test('production six historical summaries distinguish actual method before loading details',{skip:!existsSync(new URL('./fixtures/science-historical-six-records.json',import.meta.url))&&'Private regression fixture excluded from source release'},async()=>{
 const records=JSON.parse(readFileSync(new URL('./fixtures/science-historical-six-records.json',import.meta.url),'utf8'));
 const expected=['CPMG · T₂(N)','Spin Echo · T₂','幅度 Rabi','Ramsey · T₂*','T₁ 能量弛豫','时间 Rabi'];
 const calls:string[]=[];const props={gateway:{get:async(path:string)=>{calls.push(path);return path.includes('/overview')?{}:{records,hasMore:false};}}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.type==='button'&&n.props.children==='仿真实测对照').props.onClick();tree=view.render(props);
 const panel=view.renderNode(all(tree).find(n=>n.type?.name==='ComparePanel'),'formal-compare');
 const selector=view.renderNode(all(panel).find(n=>n.props.label==='原始采集记录'),'formal-select');
 const options=all(selector).filter(n=>n.type==='option'&&n.props.value);
 assert.equal(options.length,6);
 options.forEach((option,index)=>{const text=[option.props.children].flat(Infinity).join('');assert.ok(text.includes(expected[index]),text);assert.ok(text.includes('Q11'),text);assert.ok(text.includes('归档于'),text);assert.ok(!text.includes('原始采集时间'),text);});
 assert.ok(!calls.some(p=>/\/records\/science-/.test(p)),'method labels require no detail fetch');
});


test('Bloch normal preview exposes T1 preparation, selected evolution and readout protocol',async()=>{
 const result={kind:'bloch',sequence:'t1',initialState:'1',trajectory:[{t:0,x:0,y:0,z:-1,p1:1},{t:1000,x:0,y:0,z:-.9,p1:.95}],segments:[],metrics:{piPulses:0},series:[]};
 const writes:any[]=[];const props={gateway:{get:async(path:string)=>path.includes('/overview')?{}:{records:[],hasMore:false},write:async(path:string,body:any)=>{assert.equal(path,'/science/preview');writes.push(body);return result;}}};
 const view=harness();view.render(props);await tick();let tree=view.render(props);
 all(tree).find(n=>n.type==='button'&&n.props.children==='布洛赫演化').props.onClick();tree=view.render(props);
 const panel=all(tree).find(n=>n.type?.name==='BlochPanel');let content=view.renderNode(panel,'r30');
 assert.ok(!all(content).some(n=>n.type?.name==='BlochProtocolTimeline'),'no inferred timeline before preview');
 all(content).find(n=>n.type==='select'&&n.props.value==='x').props.onChange({target:{value:'t1'}});content=view.renderNode(panel,'r30');
 await all(content).find(n=>n.type?.name==='Actions').props.onPreview();await tick();content=view.renderNode(panel,'r30');
 const timeline=all(content).find(n=>n.type?.name==='BlochProtocolTimeline');assert.ok(timeline,'normal Bloch preview must include complete steps, even with no refocus segments');
 assert.equal(timeline.props.result,result);assert.equal(writes.length,1);
 const rendered=view.renderNode(timeline,'r30-steps');const text=JSON.stringify(rendered);
 assert.match(text,/制备/);assert.match(text,/等待/);assert.match(text,/末端读出/);assert.match(text,/示意/);
 all(content).find(n=>n.props.label==='总演化时间').props.onChange(2000);content=view.renderNode(panel,'r30');
 assert.ok(!all(content).some(n=>n.type?.name==='BlochProtocolTimeline'),'editing invalidates the old result and steps together');
});
