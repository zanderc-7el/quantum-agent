import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';

const bundle=buildSync({entryPoints:[fileURLToPath(new URL('../src/lib/scienceComparisonSource.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs'}).outputFiles[0].text;
const module={exports:{} as any};new Function('require','module','exports',bundle)(createRequire(import.meta.url),module,module.exports);
const {loadComparisonSource}=module.exports;
const source={workflowId:'workflow / old',nodeId:'Q18:time',attemptId:'historical-attempt'};
const raw=(id:string)=>({id,kind:'raw',sha256:(id==='v'?'a':'b').repeat(64)});
const result={dataKind:'real',acquiredAt:'2026-11-18T12:00:00Z',parameters:{drive_amp:.3},partial:false,
 quality:{status:'passed',reasons:[]},artifacts:[raw('v')],selectionResult:{artifacts:[raw('s')]},
 series:[{name:'original',x:[0,1],y:[.1,.2],xUnit:'s',yUnit:'IQ a.u.'}]};
const attempt={id:source.attemptId,nodeId:source.nodeId,mode:'real',state:'verified',procedureId:'sq_rabi_time',result};
const detail={workflowRevision:17,node:{id:source.nodeId,resourceId:'Q18',procedureId:'sq_rabi_time',latestAttemptId:'newer-attempt'},attempts:[attempt]};
const record=(id='exact')=>({id,kind:'measurement',dataKind:'real',summary:false,
 scope:{workflowId:source.workflowId,resourceId:'Q18'},reference:{workflowId:source.workflowId,attemptId:source.attemptId},input:{},
 result:{...result,procedureId:'sq_rabi_time'},evidence:[raw('v'),raw('s')],evidenceIntact:true,
 provenance:{independentlyVerified:true}});
const page=(records:any[],nextCursor:string|null=null)=>({records,hasMore:!!nextCursor,nextCursor});
const nodePath=`/workflows/${encodeURIComponent(source.workflowId)}/nodes/${encodeURIComponent(source.nodeId)}`;

test('deep link searches every page and binds historical attempt rather than latest or old selection',async()=>{
 const calls:any[]=[];const exact=record();const old={...record('old-selection'),result:{...result,acquiredAt:'2026-11-18T11:59:00Z'},evidence:[raw('s')]};
 const gateway={get:async(path:string)=>{calls.push(path);if(path===nodePath)return detail;
  if(path==='/science/records/exact')return exact;if(path==='/science/records/old-selection')return old;
  if(path.includes('cursor=older'))return page([{...exact,summary:true}]);
  return page([{...record('other-task'),reference:{workflowId:'another',attemptId:source.attemptId}},{...old,summary:true}],'older');},write:async()=>assert.fail('existing exact record must be reused')};
 const answer=await loadComparisonSource(gateway,source);
 assert.equal(answer.id,'exact');assert.ok(calls.includes(nodePath));assert.ok(calls.some((p:string)=>p.includes('cursor=older')));
 assert.ok(calls.filter((p:string)=>p.includes('/science/records?')).every((p:string)=>p.includes('workflowId=workflow+%2F+old')));
});

test('a valid source beyond one thousand pages remains reachable without creating another record',async()=>{
 let pages=0;const exact=record();
 const gateway={get:async(path:string)=>{if(path===nodePath)return detail;if(path==='/science/records/exact')return exact;
  pages++;return pages<=1001?page([],String(pages)):page([{...exact,summary:true}]);},write:async()=>assert.fail('must finish valid pagination')};
 assert.equal((await loadComparisonSource(gateway,source)).id,'exact');assert.equal(pages,1002);
});

test('missing exact main result uses the standard evidence bind with frozen revision and repeatable operation',async()=>{
 const bodies:any[]=[];const gateway={get:async(path:string)=>path===nodePath?detail:page([]),write:async(path:string,body:any)=>{assert.equal(path,'/science/records');bodies.push(body);return record('bound');}};
 assert.equal((await loadComparisonSource(gateway,source)).id,'bound');await loadComparisonSource(gateway,source);
 assert.deepEqual(bodies[0],bodies[1]);assert.match(bodies[0].operationId,/^science-comparison-bind\/v1\/[a-f0-9]{64}$/);
 assert.deepEqual(bodies[0].scope,{workflowId:source.workflowId,resourceId:'Q18'});
 assert.deepEqual(bodies[0].reference,{workflowId:source.workflowId,attemptId:source.attemptId});
 assert.equal(bodies[0].expectedRevision,17);assert.equal(bodies[0].kind,'measurement');assert.deepEqual(bodies[0].input,{});
});

test('wrong node, missing historical attempt, mock and incomplete evidence never fall back or save',async()=>{
 for(const changed of [{...detail,node:{...detail.node,id:'another'}},{...detail,attempts:[{...attempt,id:'newer-attempt'}]},
  {...detail,attempts:[{...attempt,nodeId:'another'}]},{...detail,attempts:[{...attempt,mode:'mock'}]},
  {...detail,attempts:[{...attempt,result:{...result,verificationPending:true}}]},
  {...detail,attempts:[{...attempt,result:{...result,artifacts:[],selectionResult:null}}]}]){
  let writes=0;await assert.rejects(loadComparisonSource({get:async()=>changed,write:async()=>{writes++;}},source));assert.equal(writes,0);
 }
});

test('partial, parameter, quality and independent verification changes cannot reuse a previous snapshot',async()=>{
 for(const change of [{result:{...result,partial:true}},{result:{...result,parameters:{drive_amp:.4}}},
  {result:{...result,quality:{status:'unverified',reasons:[]}}},{provenance:{independentlyVerified:false}}]){
  const old={...record('stale-main'),...change};let writes=0;
  const gateway={get:async(path:string)=>path===nodePath?detail:path==='/science/records/stale-main'?old:page([{...old,summary:true}]),
   write:async()=>{writes++;return record('current-main');}};
  assert.equal((await loadComparisonSource(gateway,source)).id,'current-main');assert.equal(writes,1);
 }
});

test('incomplete pagination, unavailable detail and invalid returned evidence stop without an alternate source',async()=>{
 for(const response of [{records:[],hasMore:true,nextCursor:null},{records:[],hasMore:true,nextCursor:'loop'}]){
  let writes=0;await assert.rejects(loadComparisonSource({get:async(path:string)=>path===nodePath?detail:response,write:async()=>{writes++;}},source));assert.equal(writes,0);
 }
 await assert.rejects(loadComparisonSource({get:async(path:string)=>{if(path===nodePath)return detail;if(path.includes('/records/exact'))throw new Error('详情缺失');return page([{...record(),summary:true}]);},write:async()=>assert.fail('failed detail is not absence')},source),/详情缺失/);
 await assert.rejects(loadComparisonSource({get:async(path:string)=>path===nodePath?detail:page([]),write:async()=>({...record(),reference:{workflowId:'wrong',attemptId:source.attemptId}})},source),/来源/);
});

test('changed main result before binding and cancelled navigation cannot publish or write',async()=>{
 let reads=0,writes=0;await assert.rejects(loadComparisonSource({get:async(path:string)=>path===nodePath?(++reads===1?detail:{...detail,attempts:[{...attempt,result:{...result,acquiredAt:'2026-11-18T12:01:00Z'}}]}):page([]),write:async()=>{writes++;}},source),/更新/);assert.equal(writes,0);
 let active=true;await assert.rejects(loadComparisonSource({get:async()=>{active=false;return detail;},write:async()=>{writes++;}},source,()=>active));assert.equal(writes,0);
});

test('post-bind response is checked against frozen main evidence and exact full identity',async()=>{
 const invalid=[{...record(),evidenceIntact:false},{...record(),summary:true},{...record(),evidence:[raw('s')]},
  {...record(),scope:{workflowId:source.workflowId,resourceId:'Q11'}}];
 for(const value of invalid)await assert.rejects(loadComparisonSource({get:async(path:string)=>path===nodePath?detail:page([]),write:async()=>value},source),/来源/);
});

test('signal resolution requires exactly one matching registered main result across all pages and never writes',async()=>{
 const src={...source,panel:'signals'};
 let writes=0;
 const lookup=(ids:string[])=>loadComparisonSource({
  get:async(path:string)=>{
   if(path===nodePath)return detail;
   if(path.includes('/records/'))return {...record(path.split('/').pop()!),source:'acquisition'};
   const second=path.includes('cursor=continue');
   return page((second?ids.slice(1):ids.slice(0,1)).map(id=>({...record(id),summary:true})),second?null:'continue');
  },
  write:async()=>{writes++;throw new Error('Unexpected write');}
 },src);
 assert.equal((await lookup(['unique'])).id,'unique');
 await assert.rejects(lookup([]),/尚无匹配/);
 await assert.rejects(lookup(['first','second']),/多条/);
 assert.equal(writes,0);
});
test('a signal link refuses cross-task results or a result that changes during the lookup',async()=>{
 const src={...source,panel:'signals'};
 let detailReads=0;
 const gateway={get:async(path:string)=>{
  if(path===nodePath)return ++detailReads===1?detail:{...detail,attempts:[{...attempt,result:{...result,parameters:{drive_amp:.9}}}]};
  if(path.endsWith('/exact'))return {...record(),source:'acquisition'};
  return page([{...record(),summary:true}]);
 },write:async()=>assert.fail('read-only preview')};
 await assert.rejects(loadComparisonSource(gateway,src),/结果已更新/);
 const foreign={...record(),scope:{workflowId:'other',resourceId:'Q18'}};
 await assert.rejects(loadComparisonSource({...gateway,get:async(path:string)=>path===nodePath?detail:path.endsWith('/exact')?foreign:page([{...record(),summary:true}])},src),/尚无匹配/);
});
