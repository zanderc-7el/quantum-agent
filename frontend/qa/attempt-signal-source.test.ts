import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {buildSync} from 'esbuild';
const js=buildSync({entryPoints:[fileURLToPath(new URL('../src/lib/scienceComparisonSource.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs'}).outputFiles[0].text;
const m={exports:{} as any};new Function('require','module','exports',js)(createRequire(import.meta.url),m,m.exports);
const src={workflowId:'w',nodeId:'Q11:s3',attemptId:'a2',panel:'signals'};
const f={id:'raw',kind:'raw',sha256:'a'.repeat(64)},q={status:'failed'};
const raw={dataKind:'real',acquiredAt:'time',quality:q,parameters:{drive_hz:5e9},artifacts:[f]};
const detail={node:{id:src.nodeId,procedureId:'sq_rabi_amplitude',resourceId:'Q11'},attempts:[{id:'a2',nodeId:src.nodeId,mode:'real',procedureId:'sq_rabi_amplitude',state:'attention',result:raw}]};
const real={kind:'measurement',id:'view',source:'acquisition',scope:{workflowId:'w',resourceId:'Q11'},reference:{workflowId:'w',attemptId:'a2'},
 dataKind:'real',provenance:{independentlyVerified:false},result:{...raw,procedureId:'sq_rabi_amplitude'},evidence:[f],readOnlyAttempt:{...src,operationId:'a2/measure'}};
function load(view:any,later=detail){const paths:string[]=[];return m.exports.loadComparisonSource({get:async(p:string)=>{
 paths.push(p);if(p.includes('/nodes/'))return paths.length===1?detail:later;
 if(p.startsWith('/science/attempt-signals?')){const args=new URLSearchParams(p.split('?')[1]);assert.deepEqual(Object.fromEntries(args),{workflowId:'w',nodeId:'Q11:s3',attemptId:'a2'});return view;}
 return {records:[],hasMore:false};
 },write:()=>assert.fail('must remain read-only')},src);}
test('historical failed selection resolves without a separately saved scientific record',async()=>assert.equal((await load(real)).id,'view'));
test('linked history refuses a foreign operation scope, changed source or partially loaded result', async()=>{
 for(const v of [{...real,readOnlyAttempt:{...real.readOnlyAttempt,nodeId:'other'}},{...real,reference:{workflowId:'w',attemptId:'a1'}},{...real,summary:true}])
  await assert.rejects(load(v),/回执/);
 await assert.rejects(load(real,{...detail,attempts:[{...detail.attempts[0],result:{...raw,acquiredAt:'later'}}]}),/更新/);
});
