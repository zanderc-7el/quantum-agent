import test from 'node:test';
import assert from 'node:assert/strict';
import type { Workflow } from '../src/types.ts';
import { applyAction, createWorkflow, tickWorkflow } from '../src/lib/simulator.ts';
import { buildNodeParameters, selectParameterNode } from '../src/lib/nodeParameters.ts';
import { nodeMeasurements } from '../src/lib/measurements.ts';

function fresh(experiment: Workflow['experiment'] = 'single', anomaly = false): Workflow {
  return createWorkflow({ name: '参数展示验收', machine: 'M7', experiment, qubits: experiment === 'single' ? 1 : 2, target: experiment === 'single' ? 'Q12' : 'Q12–Q13', type: 'adaptive', policy: 'threshold', scenario: anomaly ? 'anomaly' : 'normal' });
}

test('unexecuted parameters are explicitly preview values and never fabricated measurements', () => {
  const workflow = fresh();
  const snapshot = buildNodeParameters(workflow)!;
  assert.equal(snapshot.stage, 'preview');
  assert.match(snapshot.stageLabel, /尚未执行/);
  assert.equal(snapshot.runtime.observedSamples, 0);
  assert.equal(snapshot.runtime.attempt, 0);
  assert.equal(snapshot.raw.results, null);
  assert.ok(snapshot.resultPreview.length > 0);
  assert.equal(snapshot.raw.hardwareTelemetryConnected, false);
  assert.equal(snapshot.raw.dataSource, 'simulated');
  assert.equal(snapshot.raw.platform, '超导量子自进化测控平台');
  assert.ok(snapshot.inputs.every(item => item.source));
  assert.equal(snapshot.assessment, '尚无最终判定');
});

test('the selected node resolves from fresh workflow props instead of stale node snapshots', () => {
  const draft = fresh();
  const stale = draft.nodes[0];
  const running = tickWorkflow(applyAction(draft, 'start'), 1);
  const first = buildNodeParameters(running, stale)!;
  assert.equal(first.runtime.attempt, 1);
  assert.equal(first.runtime.progress, 25);
  assert.equal(first.runtime.nodeElapsedDemoSeconds, 1);
  assert.equal(first.runtime.observedSamples, 5);
  assert.equal(first.raw.results, null);
  assert.equal(first.raw.previewValues, null);
  const progressed = tickWorkflow(running, 1);
  const second = buildNodeParameters(progressed, stale)!;
  assert.equal(second.runtime.progress, 50);
  assert.equal(second.runtime.nodeElapsedDemoSeconds, 2);
  assert.equal(second.runtime.observedSamples, 9);
  assert.equal(stale.progress, 0);
  assert.equal(first.runtime.progress, 25);
});

test('automatic node selection follows attention, running and finally first node', () => {
  const draft = fresh('two', true);
  assert.equal(selectParameterNode(draft)?.id, 'ReadoutFreqDip');
  const attention = tickWorkflow(applyAction(draft, 'start'), 1000);
  assert.equal(selectParameterNode(attention)?.id, 'CZXEBCal');
  const retried = applyAction(attention, 'retry');
  const snapshot = buildNodeParameters(retried)!;
  assert.equal(snapshot.node.id, 'CZXEBCal');
  assert.equal(snapshot.runtime.attempt, 2);
  assert.equal(snapshot.runtime.retries, 1);
  assert.equal(snapshot.runtime.progress, 0);
  assert.equal(snapshot.runtime.nodeElapsedDemoSeconds, 4);
  assert.equal(snapshot.raw.results, null);
});

test('pause and abort display retained execution state without inventing final results', () => {
  const running = tickWorkflow(applyAction(fresh(), 'start'), 1.5);
  const paused = applyAction(running, 'pause');
  const snapshot = buildNodeParameters(paused)!;
  assert.equal(snapshot.status, '已暂停');
  assert.equal(snapshot.runtime.progress, 37.5);
  assert.equal(buildNodeParameters(tickWorkflow(paused, 100))!.runtime.workflowElapsedDemoSeconds, 1.5);
  const aborted = applyAction(paused, 'abort');
  const interrupted = buildNodeParameters(aborted)!;
  assert.equal(interrupted.stage, 'interrupted');
  assert.equal(interrupted.raw.results, null);
  assert.equal(interrupted.runtime.progress, 37.5);
});

test('low-result approval keeps the original failed metric and labels accepted instead of passed', () => {
  const attention = tickWorkflow(applyAction(fresh('single', true), 'start'), 1000);
  const anomalyNode = selectParameterNode(attention)!;
  const before = buildNodeParameters(attention, anomalyNode)!;
  assert.equal(before.results.find(item => item.key === 'fidelity')?.value, 94.2);
  assert.match(before.assessment, /低于演示阈值/);
  const accepted = applyAction(attention, 'approve');
  const after = buildNodeParameters(accepted, anomalyNode)!;
  assert.equal(after.acceptedLowMetric, true);
  assert.match(after.assessment, /接受不代表达标/);
  assert.equal(after.results.find(item => item.key === 'fidelity')?.value, 94.2);
  const completed = tickWorkflow(accepted, 1000);
  const retest = completed.nodes.find(item => item.id === 'IqToProbabilityRetest')!;
  assert.equal(buildNodeParameters(completed, retest)!.results.find(item => item.key === 'fidelity')?.value, 98.9);
  assert.equal(buildNodeParameters(completed, anomalyNode)!.results.find(item => item.key === 'fidelity')?.value, 94.2);
});

test('completed metric values share measurement source and preserve correct units', () => {
  const completed = tickWorkflow(applyAction(fresh('two'), 'start'), 1000);
  for (const id of ['T1Idle', 'T2CPMG', 'RB1QCal', 'CZXEBCal']) {
    const node = completed.nodes.find(item => item.id === id)!;
    const snapshot = buildNodeParameters(completed, node)!;
    const expected = nodeMeasurements(completed, node);
    const coherence = id === 'T1Idle' || id === 'T2CPMG';
    const metric = snapshot.results.find(item => item.key === (coherence ? 'tau' : 'fidelity'))!;
    assert.equal(metric.value, coherence ? expected.tau : expected.fidelity);
    assert.equal(metric.unit, coherence ? 'μs' : '%');
    assert.equal(snapshot.stage, 'ready');
    assert.equal(snapshot.runtime.observedSamples, snapshot.runtime.plannedSamples);
  }
  const cz = completed.nodes.find(item => item.id === 'TTGAczDelicateG')!;
  const width = buildNodeParameters(completed, cz)!.inputs.find(item => item.key === 'CZ_width')!;
  assert.equal(width.value, 60);
  assert.equal(width.unit, 'ns');
  assert.equal(width.source, '参考流程指定值');
});

test('snapshot generation preserves inputs, yields serializable JSON and handles empty task data', () => {
  const workflow = fresh();
  const before = JSON.stringify(workflow);
  const snapshot = buildNodeParameters(workflow)!;
  assert.deepEqual(JSON.parse(JSON.stringify(snapshot.raw)).runtime, snapshot.runtime);
  assert.equal(JSON.stringify(workflow), before);
  assert.equal(buildNodeParameters({ ...workflow, nodes: [] }), null);
});
