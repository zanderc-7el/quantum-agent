import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Workflow } from '../src/types.ts';
import { decisionBudgetRows } from '../src/lib/decisionBudget.ts';
import { workflowControls } from '../src/lib/workflowControls.ts';

const workflow = { id:'smart', mode:'real', policy:'autopilot', status:'attention', engineState:'quality_attention',
  currentNodeId:'n', budget:{mode:'accounting_only'}, reservedShots:9000000, iterations:221, maxAttemptsPerNode:null,
  nodes:[{id:'n', attempt:11, engineState:'quality_attention'}] } as unknown as Workflow;
const compiled = buildSync({entryPoints:[fileURLToPath(new URL('../src/components/WorkflowBudget.tsx',import.meta.url))],
  bundle:true, write:false, platform:'node', format:'cjs', packages:'external'}).outputFiles[0].text;
const component = {exports:{} as {default:Parameters<typeof createElement>[0]}};
new Function('require','module','exports',compiled)(createRequire(import.meta.url),component,component.exports);
const render = (w:Workflow) => renderToStaticMarkup(createElement(component.exports.default,{workflow:w}));

test('report distinguishes operation receipts, reservations, open operations and cumulative family counts',()=>{
  const doc = render({...workflow,shotAccounting:{confirmedShots:300,reservedShots:150000600,attempts:228,
    operations:2,unresolvedOperations:1,scienceChildren:2,unattributedReservedShots:0}});
  for(const text of ['回执确认 shots<strong>300','累计预留 shots<strong>150,000,600','累计尝试<strong>228','计数待核操作<strong>1','计入的科学子任务<strong>2'])
    assert.ok(doc.includes(text),text);
  assert.doesNotMatch(doc,/剩余 shots 预算|调整任务预算/);
  assert.match(render(workflow),/回执确认 shots<strong>未提供/);
  assert.match(render({...workflow,budget:undefined,maxTotalShots:10000000}),/剩余 shots 预算/);
});

test('no historical workflow or node ceiling supersedes pause and receipt reconciliation',()=>{
  assert.equal(workflowControls(workflow).retry,true);
  assert.equal(workflowControls(workflow).policy,false);
  assert.equal(workflowControls({...workflow,status:'running'}).pause,true);
  assert.equal(workflowControls({...workflow,engineState:'needs_reconciliation'}).retry,false);
  assert.equal(workflowControls({...workflow,maxAttemptsPerNode:10,budget:undefined}).retry,false);
});

test('estimated and verification upper bounds remain visible without a fictitious remaining amount',()=>{
  const look={version:'selection-verification-budget/1',selectionShots:100,verificationShotsUpperBound:200,jointShots:300,
    reservation:false,candidateKnown:false,actualVerificationPlanRechecked:true,
    verificationBasis:'same_sampling_counts_after_calibration_only',remainingShotsAtCheck:null,budgetMode:'accounting_only'};
  assert.deepEqual(decisionBudgetRows(look)?.map(row=>row.value),['100','200','300']);
  assert.equal(decisionBudgetRows({...look,budgetMode:undefined}),null);
  assert.equal(decisionBudgetRows({...look,verificationShotsUpperBound:201}),null);
});
