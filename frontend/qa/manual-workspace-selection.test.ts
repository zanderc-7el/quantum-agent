import test from 'node:test';
import assert from 'node:assert/strict';
import { manualDraftInput, selectedManualWorkflow, manualSelectionKey } from '../src/lib/manualWorkspaceSelection.ts';
import type { CreateWorkflowInput, Workflow } from '../src/types.ts';

const workflow = (id:string, mode:Workflow['mode'], policy:Workflow['policy']) => ({id,mode,policy} as Workflow);
test('manual workspace never implicitly changes the selected intelligent task', () => {
  const automatic=workflow('current','real','threshold');
  const manual=workflow('manual','real','strict');
  const replay=workflow('replay','replay','strict');
  const before=JSON.stringify([automatic,manual,replay]);
  assert.equal(selectedManualWorkflow([automatic,manual,replay],'current'),manual);
  assert.equal(selectedManualWorkflow([automatic,replay],'current'),undefined);
  assert.equal(JSON.stringify([automatic,manual,replay]),before);
});
test('selected manual task persists independently of more recent tasks', () => {
  const recent=workflow('recent','real','strict'),old=workflow('old','real','strict');
  assert.equal(selectedManualWorkflow([recent,old],'old'),old);
  assert.notEqual(manualSelectionKey('project-A'),manualSelectionKey('project-B'));
});
test('manual draft creation always preserves the single-step execution boundary', () => {
  const raw={name:'人工 S21',resourceIds:['Q18'],mode:'replay',policy:'autopilot',experiment:'two',coverage:'main',qubits:2} as CreateWorkflowInput;
  const created=manualDraftInput(raw);
  assert.equal(created.mode,'real'); assert.equal(created.policy,'strict');
  assert.equal(created.experiment,'single'); assert.equal(created.coverage,'full');
  assert.equal(created.qubits,1); assert.deepEqual(created.resourceIds,['Q18']);
  assert.equal(raw.policy,'autopilot');
});
