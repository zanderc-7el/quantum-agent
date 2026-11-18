import test from 'node:test';
import assert from 'node:assert/strict';
import type { Workflow } from '../src/types.ts';
import { applyAction, createWorkflow, tickWorkflow } from '../src/lib/simulator.ts';
import { buildParameterCode } from '../src/lib/parameterCode.ts';

function fresh(experiment: Workflow['experiment'] = 'single', anomaly = false): Workflow {
  return createWorkflow({ name: '代码参数验收', machine: 'M7', experiment, qubits: experiment === 'single' ? 1 : 2, target: experiment === 'single' ? 'Q12' : 'Q12–Q13', type: 'adaptive', policy: 'threshold', scenario: anomaly ? 'anomaly' : 'normal' });
}

// Independent JSONC reader: comments are recognized only outside JSON string literals.
function parseJSONC(text: string) {
  let output = '';
  let quoted = false;
  let escaped = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (!quoted && char === '/' && text[i + 1] === '/') {
      while (i < text.length && text[i] !== '\n') i++;
      output += '\n';
      continue;
    }
    output += char;
    if (escaped) escaped = false;
    else if (quoted && char === '\\') escaped = true;
    else if (char === '"') quoted = !quoted;
  }
  return JSON.parse(output);
}

test('code document has configured scalars and honest null results before execution', () => {
  const document = buildParameterCode(fresh())!;
  const data = parseJSONC(document.text);
  assert.equal(document.filename, 'ReadoutFreqDip.parameters.jsonc');
  assert.equal(document.stage, 'preview');
  assert.equal(document.nodeId, 'ReadoutFreqDip');
  assert.equal(data.data_source, 'simulated');
  assert.equal(data.hardware_telemetry_connected, false);
  assert.equal(data.read_only, true);
  assert.equal(data.inputs.readout_freq, 6.742);
  assert.equal(data.inputs.readout_duration, 1000);
  assert.equal(data.runtime.attempt, 0);
  assert.equal(data.runtime.observed_samples, 0);
  assert.equal(data.results, null);
  assert.equal(data.quality.assessment, '尚无最终判定');
  assert.ok(!document.text.includes('previewValues'));
  assert.ok(!document.text.includes('center_frequency'));
  assert.match(document.lines.find(line => line.id === 'inputs.readout_freq')!.text, /\/\/ GHz · 读取频率/);
});

test('real simulation ticks update current values using stable field paths', () => {
  const initial = fresh();
  const stale = initial.nodes[0];
  const running = tickWorkflow(applyAction(initial, 'start'), 1);
  const first = buildParameterCode(running, stale)!;
  const second = buildParameterCode(tickWorkflow(running, 1), stale)!;
  const a = parseJSONC(first.text);
  const b = parseJSONC(second.text);
  assert.equal(a.runtime.progress, 25);
  assert.equal(b.runtime.progress, 50);
  assert.equal(a.runtime.node_elapsed_seconds, 1);
  assert.equal(b.runtime.node_elapsed_seconds, 2);
  assert.equal(a.runtime.observed_samples, 5);
  assert.equal(b.runtime.observed_samples, 9);
  assert.equal(a.results, null);
  assert.equal(b.results, null);
  assert.deepEqual(first.lines.map(line => line.id), second.lines.map(line => line.id));
  const previous = new Map(first.lines.map(line => [line.id, line.text]));
  const changed = second.lines.filter(line => previous.get(line.id) !== line.text).map(line => line.id);
  assert.deepEqual(changed, ['runtime.progress', 'runtime.workflow_progress', 'runtime.workflow_elapsed_seconds', 'runtime.node_elapsed_seconds', 'runtime.observed_samples']);
});

test('completed nodes show available measurements and distinguish accepted low results', () => {
  const attention = tickWorkflow(applyAction(fresh('single', true), 'start'), 1000);
  const before = buildParameterCode(attention)!;
  const data = parseJSONC(before.text);
  assert.equal(before.stage, 'ready');
  assert.equal(data.node.experiment, 'ReadoutParaSweepByIQProb');
  assert.equal(data.results.fidelity, 94.2);
  assert.equal(data.quality.threshold, '保真度 ≥ 98%');
  assert.match(data.quality.assessment, /低于演示阈值/);
  const approved = applyAction(attention, 'approve');
  const accepted = parseJSONC(buildParameterCode(approved, attention.nodes.find(node => node.id === before.nodeId))!.text);
  assert.equal(accepted.results.fidelity, 94.2);
  assert.equal(accepted.quality.accepted_low_metric, true);
  assert.match(accepted.quality.assessment, /接受不代表达标/);
});

test('paused and aborted executions retain progress without inventing final data', () => {
  const running = tickWorkflow(applyAction(fresh(), 'start'), 1.5);
  const paused = applyAction(running, 'pause');
  const pausedData = parseJSONC(buildParameterCode(paused)!.text);
  assert.equal(pausedData.runtime.status, '已暂停');
  assert.equal(pausedData.runtime.progress, 37.5);
  assert.equal(pausedData.results, null);
  const aborted = buildParameterCode(applyAction(paused, 'abort'))!;
  const abortedData = parseJSONC(aborted.text);
  assert.equal(aborted.stage, 'interrupted');
  assert.equal(abortedData.runtime.progress, 37.5);
  assert.equal(abortedData.results, null);
});

test('every node emits valid JSONC and lossless syntax tokens with unique line IDs', () => {
  for (const experiment of ['single', 'two'] as const) {
    const workflow = tickWorkflow(applyAction(fresh(experiment), 'start'), 1000);
    for (const node of workflow.nodes) {
      const document = buildParameterCode(workflow, node)!;
      const data = parseJSONC(document.text);
      assert.equal(data.node.id, node.id);
      assert.equal(document.lines.map(line => line.text).join('\n'), document.text);
      assert.equal(new Set(document.lines.map(line => line.id)).size, document.lines.length);
      for (const line of document.lines) {
        assert.equal(line.tokens.map(token => token.text).join(''), line.text);
        assert.ok(!/[\n\r\u2028\u2029]/.test(line.text));
      }
      assert.ok(document.lines.some(line => line.tokens.some(token => token.kind === 'key')));
      assert.ok(document.lines.some(line => line.tokens.some(token => token.kind === 'number')));
      assert.ok(document.lines.some(line => line.tokens.some(token => token.kind === 'boolean')));
    }
  }
});

test('Unicode, quotes, control characters and URL slashes remain data, not code or comments', () => {
  const workflow = fresh();
  workflow.name = 'https://example.test/"quoted"\\path\n换行\r\u2028\u2029';
  workflow.machine = 'M7 // not a comment\n"results": {"fake": true}';
  workflow.target = 'Q12\t<script> & 中文';
  workflow.nodes[0].qubits = workflow.target;
  workflow.nodes[0].name = '../unsafe\\读取\n"实验"\u2028';
  workflow.nodes[0].label = '标签\r\n// injected';
  const document = buildParameterCode(workflow)!;
  const parsed = parseJSONC(document.text);
  assert.equal(parsed.workflow.name, workflow.name);
  assert.equal(parsed.workflow.machine, workflow.machine);
  assert.equal(parsed.inputs.target, workflow.target);
  assert.equal(parsed.node.label, workflow.nodes[0].label);
  assert.equal(parsed.node.experiment, workflow.nodes[0].name);
  assert.equal(parsed.results, null);
  assert.match(document.filename, /^[\p{L}\p{N}_-]+\.parameters\.jsonc$/u);
  assert.ok(document.lines.every(line => !/[\r\n\u2028\u2029]/.test(line.text)));
  const nameLine = document.lines.find(line => line.id === 'workflow.name')!;
  const nameValue = nameLine.tokens.find(token => token.kind === 'string')!;
  assert.equal(JSON.parse(nameValue.text), workflow.name);
});

test('formatting does not mutate workflow or stale selected nodes and handles empty workflows', () => {
  const workflow = fresh();
  const previous = JSON.stringify(workflow);
  const stale = { ...workflow.nodes[0], progress: 99, attempt: 42 };
  const document = buildParameterCode(workflow, stale)!;
  assert.equal(parseJSONC(document.text).runtime.progress, 0);
  assert.equal(stale.progress, 99);
  assert.equal(JSON.stringify(workflow), previous);
  assert.equal(buildParameterCode({ ...workflow, nodes: [] }), null);
});
