import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
import * as React from 'react';

const fixture=JSON.parse(readFileSync(new URL('./fixtures/external-predistortion-test-only.json',import.meta.url),'utf8'));
const file=()=>new File([fixture.documentText],'TEST_ONLY-external-channel.json',{type:'application/json'});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(exportName='default'){
 const compiled=buildSync({entryPoints:[fileURLToPath(new URL('../src/components/ExternalPredistortionPanel.tsx',import.meta.url))],bundle:true,platform:'node',format:'cjs',write:false,packages:'external'}).outputFiles[0].text;
 const states=new Map<string,any[]>();let slots:any[]=[],cursor=0;const effects:(()=>void)[]=[];
 const hooks={...React,useState(initial:any){const index=cursor++,own=slots;own[index]??={value:typeof initial==='function'?initial():initial};return [own[index].value,(value:any)=>{own[index].value=typeof value==='function'?value(own[index].value):value;}];},useRef(initial:any){return slots[cursor++]??={current:initial};},useEffect(fn:any,deps:any[]){const index=cursor++,own=slots;if(!own[index]||deps.some((v,i)=>v!==own[index].deps[i])){own[index]?.cleanup?.();own[index]={deps};effects.push(()=>{own[index].cleanup=fn();});}}};
 const module={exports:{} as any},require=createRequire(import.meta.url);new Function('require','module','exports',compiled)((id:string)=>id==='react'?hooks:require(id),module,module.exports);
 function expand(node:any,path:string):any{if(Array.isArray(node))return node.map((child,index)=>expand(child,`${path}.${child?.key??index}`));if(!node?.props)return node;if(typeof node.type==='function'){const key=`${path}:${node.type.name}:${node.key??''}`;slots=states.get(key)??[];states.set(key,slots);cursor=0;const out=node.type(node.props);while(effects.length)effects.shift()!();return expand(out,key);}return {...node,props:{...node.props,children:expand(node.props.children,path+'.children')}};}
 return (props:any)=>expand(React.createElement(module.exports[exportName],props),'root');
}
const nodes=(node:any):any[]=>Array.isArray(node)?node.flatMap(nodes):node?.props?[node,...nodes(node.props.children)]:[];
const text=(node:any):string=>Array.isArray(node)?node.map(text).join(''):node?.props?text(node.props.children):typeof node==='string'||typeof node==='number'?String(node):'';
const visibleText=(node:any):string=>Array.isArray(node)?node.map(visibleText).join(''):node?.type==='details'?'':node?.props?visibleText(node.props.children):typeof node==='string'||typeof node==='number'?String(node):'';
function field(tree:any,label:string){const node=nodes(tree).find(node=>node.props['aria-label']===label);assert.ok(node,`missing ${label}`);return node.props;}
function button(tree:any,label:string){const node=nodes(tree).find(node=>node.type==='button'&&text(node)===label);assert.ok(node,`missing ${label}`);return node.props;}
const Plot=({series,label}:any)=>React.createElement('figure',{'data-series':series,'aria-label':label});
const base=()=>({scope:fixture.scope,records:[],gateway:{write:async()=>structuredClone(fixture.result)},refresh:async()=>{},loadRecord(){},Plot});
const fullRecord=(input:any)=>({id:'external-record',kind:'predistortion_comparison',createdAt:'2026-11-26T10:00:00+08:00',scope:fixture.scope,input,result:structuredClone(fixture.result),dataKind:'provided_data',physicalAccepted:false});
function upload(tree:any,selected=file()){field(tree,'导入预补偿外部 JSON').onChange({target:{files:[selected],value:selected.name}});}

test('external comparison renders time and frequency on separate axes with explicit fixture identity and folded provenance',()=>{
 const render=harness('ExternalPredistortionResult');const result=structuredClone(fixture.result);
 result.transfer.magnitude[0]=0;result.transfer.phaseRad[0]=null;
 const tree=render({result,Plot});assert.match(visibleText(tree),/测试夹具 · 非实测/);assert.match(visibleText(tree),/未作本机采集或校准验收/);
 assert.doesNotMatch(visibleText(tree),/documentSha256|contentSha256|acquisitionId/);
 const wave=field(tree,'同测点目标与补偿前后响应')['data-series'];assert.equal(wave.length,3);assert.ok(wave.every((s:any)=>s.xUnit==='s'&&s.yUnit==='V'&&s.source==='test_fixture'));
 assert.deepEqual(wave[1].y,result.series[1].values);
 const amplitude=field(tree,'外部频响 |H(f)|')['data-series'][0],phase=field(tree,'外部频响相位')['data-series'][0];
 assert.equal(amplitude.xUnit,'Hz');assert.equal(amplitude.y[0],0);assert.equal(phase.y[0],null);assert.equal(phase.yUnit,'°');
 assert.ok(nodes(tree).some(n=>n.type==='details'&&n.props.open===undefined&&text(n).includes(result.documentSha256)));
});

test('target identity follows its series ID rather than its displayed label',()=>{
 const render=harness('ExternalPredistortionResult');const result=structuredClone(fixture.result);result.dataOrigin='external_measurement';result.series[0].label='指定目标';
 const tree=render({result,Plot});const wave=field(tree,'同测点目标与补偿前后响应')['data-series'];
 assert.equal(wave[0].source,'provided_target');assert.equal(wave[1].source,'external_measurement');
 assert.ok(field(tree,'补偿前后波形误差')['data-series'].every((s:any)=>s.source==='external_comparison'));
});

test('an unavailable reduction percentage remains unavailable and explains a zero baseline',()=>{
 const render=harness('ExternalPredistortionResult');const result=structuredClone(fixture.result);
 result.metrics.rmseReductionPercent=null;result.metrics.rmseReductionPercentUnavailableReason='baseline_zero';
 const tree=render({result,Plot});assert.match(visibleText(tree),/基线误差为零，比例不适用/);assert.doesNotMatch(visibleText(tree),/均方根误差降低0%/);
});

test('file input previews original BOM bytes then saves only the external comparison and can reopen the saved record',async()=>{
 const render=harness(),calls:any[]=[];let saved:any;let rows:any[]=[];
 const props=()=>({...base(),records:rows,gateway:{write:async(path:string,body:any)=>{calls.push({path,body:structuredClone(body)});if(path==='/science/records'){saved=fullRecord(body.input);return saved;}return structuredClone(fixture.result);}},refresh:async()=>{rows=[{...saved,summary:true,result:{fileName:saved.result.fileName}}];}});
 let tree=render(props());assert.equal(button(tree,'保存比较').disabled,true);upload(tree);await tick();tree=render(props());assert.equal(button(tree,'保存比较').disabled,false);
 assert.equal(calls[0].path,'/science/channel-comparison-preview');assert.deepEqual(calls[0].body.scope,fixture.scope);
 assert.equal(Buffer.from(calls[0].body.input.documentBase64,'base64').toString('utf8'),fixture.documentText);
 button(tree,'保存比较').onClick();await tick();tree=render(props());assert.equal(calls[1].path,'/science/records');assert.equal(calls[1].body.kind,'predistortion_comparison');
 assert.deepEqual(calls[1].body.input,calls[0].body.input);assert.equal(saved.physicalAccepted,false);assert.equal(button(tree,'保存比较').disabled,true);
 assert.match(text(tree),/测试夹具 · 非实测/);assert.ok(calls.every(c=>c.path.startsWith('/science/')));
 const reopened=harness();const reads:string[]=[];const reopenProps={...base(),records:[{...saved,summary:true,result:{fileName:saved.result.fileName}}],loadRecord:(id:string)=>{reads.push(id);}};
 tree=reopened(reopenProps);field(tree,'外部预补偿比较记录').onChange({target:{value:saved.id}});tree=reopened(reopenProps);assert.deepEqual(reads,[saved.id]);
 tree=reopened({...reopenProps,records:[saved]});assert.ok(field(tree,'同测点目标与补偿前后响应'));assert.ok(button(tree,'下载原始文件'));
});

test('failed comparison clears the previous preview and cannot be saved as a valid result',async()=>{
 const render=harness();let rejected=false;const calls:string[]=[];const props={...base(),gateway:{write:async(path:string)=>{calls.push(path);if(rejected)throw new Error('通道、测点、单位或仪器条件不同');return structuredClone(fixture.result);}}};
 let tree=render(props);upload(tree);await tick();tree=render(props);assert.equal(button(tree,'保存比较').disabled,false);
 rejected=true;upload(tree);await tick();tree=render(props);assert.equal(button(tree,'保存比较').disabled,true);assert.match(text(tree),/通道、测点、单位或仪器条件不同/);
 assert.equal(nodes(tree).filter(n=>n.type==='figure').length,0);button(tree,'保存比较').onClick();await tick();assert.equal(calls.filter(path=>path==='/science/records').length,0);
});

test('bad extension, UTF-8 and files over 512 KiB are rejected before any service request',async()=>{
 for(const selected of [new File(['{}'],'x.csv'),new File([new Uint8Array([255])],'bad.json'),new File(['x'.repeat(512*1024+1)],'large.json')]){
  const render=harness();let writes=0;const props={...base(),gateway:{write:async()=>{writes++;return fixture.result;}}};let tree=render(props);upload(tree,selected);await tick();tree=render(props);
  assert.equal(writes,0);assert.equal(button(tree,'保存比较').disabled,true);assert.ok(nodes(tree).some(n=>n.props.role==='alert'));
 }
});

test('uncertain save retries the identical operation and duplicate clicks never create concurrent saves',async()=>{
 const render=harness();const saves:any[]=[];let finish:(value:any)=>void=()=>{};let attempt=0;
 const props={...base(),gateway:{write:async(path:string,body:any)=>{if(path!=='/science/records')return fixture.result;saves.push(structuredClone(body));if(++attempt===1)throw new Error('连接中断，回执未知');return new Promise(resolve=>{finish=resolve;});}}};
 let tree=render(props);upload(tree);await tick();tree=render(props);button(tree,'保存比较').onClick();await tick();tree=render(props);assert.equal(button(tree,'保存比较').disabled,false);
 const save=button(tree,'保存比较').onClick;save();save();assert.equal(saves.length,2);assert.deepEqual(saves[0],saves[1]);
 finish(fullRecord(saves[1].input));await tick();tree=render(props);assert.equal(button(tree,'保存比较').disabled,true);
});

test('switching bound qubit discards a late preview and never saves it under the new resource',async()=>{
 const render=harness();let finish:(value:any)=>void=()=>{};const calls:any[]=[];
 const props={...base(),gateway:{write:(path:string,body:any)=>{calls.push({path,body});return new Promise(resolve=>{finish=resolve;});}}};
 let tree=render(props);upload(tree);await tick();tree=render({...props,scope:{...fixture.scope,resourceId:'TEST_ONLY-Q18'}});
 finish(fixture.result);await tick();tree=render({...props,scope:{...fixture.scope,resourceId:'TEST_ONLY-Q18'}});assert.equal(button(tree,'保存比较').disabled,true);assert.equal(nodes(tree).filter(n=>n.type==='figure').length,0);
 button(tree,'保存比较').onClick();await tick();assert.equal(calls.length,1);assert.equal(calls[0].body.scope.resourceId,'TEST_ONLY-Q11');
});

test('unbound and multi-qubit contexts do not guess a target for external import',async()=>{
 const render=harness();const writes:any[]=[];const props={...base(),scope:{workflowId:fixture.scope.workflowId},resourceIds:['TEST_ONLY-Q11','TEST_ONLY-Q18'],gateway:{write:async(path:string,body:any)=>{writes.push({path,body});return fixture.result;}}};
 let tree=render(props);assert.equal(field(tree,'导入预补偿外部 JSON').disabled,true);upload(tree);await tick();assert.equal(writes.length,0);
 field(tree,'外部数据关联比特').onChange({target:{value:'TEST_ONLY-Q18'}});tree=render(props);tree=render(props);assert.equal(field(tree,'导入预补偿外部 JSON').disabled,false);
 upload(tree);await tick();assert.equal(writes[0].body.scope.resourceId,'TEST_ONLY-Q18');
});

test('template and archived original download preserve their own documents and do not dispatch measurements',async()=>{
 const render=harness(),blobs:Blob[]=[],links:any[]=[],paths:string[]=[];const originalDocument=globalThis.document;const create=URL.createObjectURL,revoke=URL.revokeObjectURL;
 const saved=fullRecord({fileName:'TEST_ONLY-source.json',documentBase64:Buffer.from(fixture.documentText).toString('base64')});
 try{
  URL.createObjectURL=(blob:Blob)=>{blobs.push(blob);return 'blob:test';};URL.revokeObjectURL=()=>{};
  (globalThis as any).document={createElement(){const link={href:'',download:'',click(){links.push({...this});}};return link;}};
  const props={...base(),records:[saved],gateway:{write:async(path:string,body:any)=>{paths.push(path);assert.deepEqual(body.scope,fixture.scope);return {schema:'qcal-external-channel-comparison/1',scope:fixture.scope};}}};
  let tree=render(props);button(tree,'下载外部数据格式').onClick();await tick();tree=render(props);assert.equal(links[0].download,'预补偿外部数据格式_待填写.json');assert.deepEqual(JSON.parse(await blobs[0].text()).scope,fixture.scope);
  field(tree,'外部预补偿比较记录').onChange({target:{value:saved.id}});tree=render(props);button(tree,'下载原始文件').onClick();assert.equal(links[1].download,'TEST_ONLY-source.json');
  assert.deepEqual(Buffer.from(await blobs[1].arrayBuffer()),Buffer.from(fixture.documentText));assert.deepEqual(paths,['/science/channel-comparison-template']);
 }finally{URL.createObjectURL=create;URL.revokeObjectURL=revoke;(globalThis as any).document=originalDocument;}
});

test('failed source integrity cannot display an accepted comparison plot',()=>{
 const render=harness();const saved={...fullRecord({}),evidenceIntact:false};const props={...base(),records:[saved]};let tree=render(props);
 field(tree,'外部预补偿比较记录').onChange({target:{value:saved.id}});tree=render(props);assert.match(text(tree),/记录来源校验未通过/);assert.equal(nodes(tree).filter(n=>n.type==='figure').length,0);
});

test('reopening or switching Q18 cannot display a Q11 comparison with the same file and measurement-point label',()=>{
 const render=harness();const q11=fullRecord({});const q18={...fullRecord({}),id:'q18-record',scope:{...fixture.scope,resourceId:'TEST_ONLY-Q18'}};
 const props={...base(),records:[q11,q18],scope:q18.scope};let tree=render(props);
 assert.deepEqual(nodes(tree).filter(n=>n.type==='option'&&n.props.value).map(n=>n.props.value),['q18-record']);
 field(tree,'外部预补偿比较记录').onChange({target:{value:q11.id}});tree=render(props);assert.equal(nodes(tree).filter(n=>n.type==='figure').length,0);
 field(tree,'外部预补偿比较记录').onChange({target:{value:q18.id}});tree=render(props);assert.ok(field(tree,'同测点目标与补偿前后响应'));
 tree=render({...props,scope:fixture.scope});assert.equal(nodes(tree).filter(n=>n.type==='figure').length,0);
});

test('a refreshed invalid summary revokes the just-saved local result immediately',async()=>{
 const render=harness();let rows:any[]=[];let saved:any;
 const props=()=>({...base(),records:rows,gateway:{write:async(path:string,body:any)=>{if(path!=='/science/records')return fixture.result;saved=fullRecord(body.input);return saved;}},refresh:async()=>{rows=[{...saved,summary:true,evidenceIntact:false,result:{fileName:saved.result.fileName}}];}});
 let tree=render(props());upload(tree);await tick();tree=render(props());button(tree,'保存比较').onClick();await tick();tree=render(props());
 assert.match(text(tree),/记录来源校验未通过/);assert.equal(nodes(tree).filter(n=>n.type==='figure').length,0);
});

test('a stale save callback cannot save the previous pending file after opening another record',async()=>{
 const render=harness();const saved=fullRecord({});const writes:string[]=[];const props={...base(),records:[saved],gateway:{write:async(path:string)=>{writes.push(path);return fixture.result;}}};
 let tree=render(props);upload(tree);await tick();tree=render(props);const save=button(tree,'保存比较').onClick;
 field(tree,'外部预补偿比较记录').onChange({target:{value:saved.id}});save();await tick();assert.deepEqual(writes,['/science/channel-comparison-preview']);
});
