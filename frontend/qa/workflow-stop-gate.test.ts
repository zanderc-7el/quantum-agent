import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkflowStopGate} from '../src/lib/workflowStopGate.ts';

test('abort upgrades a pending pause; duplicate clicks and stale finally cannot unlock the abort',()=>{
  const gate=createWorkflowStopGate();
  const pause=gate.begin('wf1','pause')!;
  assert.equal(gate.begin('wf1','pause'),undefined);
  const abort=gate.begin('wf1','abort')!;
  assert.equal(gate.owns(pause),false);
  assert.equal(gate.finish(pause),false);
  assert.equal(gate.current('wf1'),abort);
  assert.equal(gate.begin('wf1','abort'),undefined);
  assert.equal(gate.begin('wf1','pause'),undefined);
  assert.equal(gate.finish(abort),true);
  assert.equal(gate.current('wf1'),undefined);
});

test('pending stop requests are isolated by workflow',()=>{
  const gate=createWorkflowStopGate();
  const first=gate.begin('wf1','abort')!;
  const second=gate.begin('wf2','pause')!;
  assert.equal(gate.finish(first),true);
  assert.equal(gate.current('wf2'),second);
  assert.equal(gate.owns(second),true);
});
