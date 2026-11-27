import test from 'node:test';
import assert from 'node:assert/strict';
import {workspaceRoute, workspaceHash} from '../src/lib/resultComparisonRoute.ts';

test('result links preserve the exact workflow, node and attempt on reload', () => {
  const route = {view: 'science' as const, comparisonSource: {workflowId: 'wf-1', nodeId: 'Q11/时间 Rabi', attemptId: 'attempt-1'}};
  assert.deepEqual(workspaceRoute(workspaceHash(route)), route);
});

test('ordinary links and incomplete result links cannot select another attempt', () => {
  assert.deepEqual(workspaceRoute('#blueprint?attempt=a'), {view: 'blueprint'});
  assert.deepEqual(workspaceRoute('#science?workflow=w&node=n'), {view: 'science'});
  assert.deepEqual(workspaceRoute(''), {view: 'agent'});
  assert.equal(workspaceHash({view: 'manual'}), '#manual');
});

test('signal evidence URLs restore their read-only destination on refresh', () => {
  const source = {workflowId:'task-1',nodeId:'Q11:s01',attemptId:'attempt-55',panel:'signals' as const};
  const route = {view:'science' as const,comparisonSource:source};
  assert.deepEqual(workspaceRoute(workspaceHash(route)),route);
  assert.deepEqual(workspaceRoute(workspaceHash(route).replace('panel=signals','panel=unsupported')),{view:'science'});
});
