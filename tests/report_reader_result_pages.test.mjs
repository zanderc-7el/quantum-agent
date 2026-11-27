import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import * as reportModel from '../scripts/report_pptx_model.mjs';
import {presentationSummary} from '../scripts/report_reader_layout.mjs';

const fixtureUrl=new URL('./fixtures/reader-unmeasured-nodes.json',import.meta.url);
const fixture=fs.existsSync(fixtureUrl)?JSON.parse(fs.readFileSync(fixtureUrl,'utf8')):null;
const privateFixtureOptions={skip:!fixture&&'Private regression fixture excluded from source release'};
const exporter=fs.readFileSync(new URL('../scripts/export_reader_pptx.mjs',import.meta.url),'utf8');
// Exercise the actual result-page loop without loading PowerPoint or changing a snapshot.
function resultPages(model,report){
 const start=exporter.indexOf('for(const n of '),end=exporter.indexOf('\nfor(const detail of diagnosticPages',start);
 assert.ok(start>0&&end>start);
 const pages=[];
 vm.runInNewContext('let s;'+exporter.slice(start,end),{model,report,readerResultNodes:reportModel.readerResultNodes,presentationSummary,
  page:(title,section)=>{const page={title,section,images:[]};pages.push(page);return page;},
  text:()=>{},picture:(page,figure)=>page.images.push(figure.artifactId)});
 return pages;
}
test('actual 18-page snapshot keeps ten unmeasured nodes in summary and emits only three result pages',privateFixtureOptions,()=>{
 const {model,report}=fixture,before=JSON.stringify(fixture);
 const originalResultPages=model.nodes.reduce((n,row)=>n+Math.max(1,Math.ceil(row.figures.length/6)),0);
 const allFigures=model.nodes.flatMap(n=>n.history.flatMap(a=>a.stages.flatMap(s=>s.figures)));
 assert.equal(1+Math.ceil(model.nodes.length/8)+originalResultPages+1+Math.ceil(allFigures.length/16),18);
 const pages=resultPages(model,report);
 assert.equal(pages.length,3,'Unmeasured nodes should not produce ten empty result pages');
 assert.equal(model.nodes.length,13,'Summary must retain all selected measurements');
 assert.equal(allFigures.length,9,'Every acquired historical image must remain in the appendix');
 assert.equal(pages.flatMap(p=>p.images).length,9);
 assert.equal(1+Math.ceil(model.nodes.length/8)+pages.length+1+Math.ceil(allFigures.length/16),8);
 assert.equal(JSON.stringify(fixture),before,'Presentation filtering must not change acquisition or acceptance records');
});
test('failed and partial figures, metrics-only results, and attempts without images retain result pages',()=>{
 const blank={label:'测量',qubits:'Q1',figures:[],metrics:[],history:[],status:'待测',summary:'待测',accepted:false};
 const nodes=[
  {...blank,id:'unmeasured'},
  {...blank,id:'failed',status:'未通过',figures:[{artifactId:'failed-image'}]},
  {...blank,id:'partial',status:'部分采集',figures:[{artifactId:'partial-image'}]},
  {...blank,id:'metric',metrics:[{label:'拟合 R²',value:'0.85'}]},
  {...blank,id:'history',history:[{attemptId:'old',number:1,stages:[{figures:[{artifactId:'old-image'}]}]}]},
  {...blank,id:'attempted'},
 ];
 const report={nodes:nodes.map(n=>({node:{id:n.id,attempt:n.id==='attempted'?1:0}}))};
 assert.equal(resultPages({nodes},report).length,5);
 assert.equal(nodes[1].accepted,false);assert.equal(nodes[2].accepted,false);
 assert.equal(nodes[4].history[0].stages[0].figures[0].artifactId,'old-image');
});
