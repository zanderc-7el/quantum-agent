import test from 'node:test';
import assert from 'node:assert/strict';
import type { Workflow } from '../src/types.ts';
import { applyAction, createInitialState, createWorkflow, tickWorkflow } from '../src/lib/simulator.ts';
import { buildExperimentCsv, countExecutedWorkflows, EXPERIMENT_CSV_HISTORY_NOTE, isExecutedWorkflow } from '../src/lib/experimentCsv.ts';

const stamp = '2026-10-26T03:04:05.000Z';
function fresh(experiment: Workflow['experiment'] = 'single', anomaly = false) {
  return createWorkflow({ name: 'CSV实验', machine: 'M7', experiment, qubits: experiment === 'single' ? 1 : 2, target: experiment === 'single' ? 'Q12' : 'Q12–Q13', type: 'adaptive', policy: 'threshold', scenario: anomaly ? 'anomaly' : 'normal' });
}

// Independent RFC 4180 reader: it does not import writer helpers or split quoted rows on newline.
function parseCsv(text: string): Record<string, string>[] {
  assert.equal(text.charCodeAt(0), 0xfeff);
  const input = text.slice(1);
  const records: string[][] = [];
  let row: string[] = [], cell = '', quoted = false;
  for (let i = 0; i < input.length; i += 1) {
    const c = input[i];
    if (c === '"') {
      if (quoted && input[i + 1] === '"') { cell += '"'; i += 1; }
      else quoted = !quoted;
    } else if (!quoted && c === ',') { row.push(cell); cell = ''; }
    else if (!quoted && c === '\r' && input[i + 1] === '\n') { row.push(cell); records.push(row); row = []; cell = ''; i += 1; }
    else if (!quoted && (c === '\n' || c === '\r')) assert.fail('Record separator must be CRLF');
    else cell += c;
  }
  assert.equal(quoted, false);
  assert.equal(cell, '');
  assert.equal(row.length, 0);
  const [header, ...body] = records;
  assert.equal(header.length, 53);
  assert.equal(new Set(header).size, header.length);
  return body.map(record => {
    assert.equal(record.length, header.length);
    return Object.fromEntries(header.map((key, index) => [key, record[index]]));
  });
}

test('CSV includes all retained execution states, but excludes never-started draft, queue and canceled queue', () => {
  const draft = fresh();
  const queued = { ...fresh(), status: 'queued' as const };
  const canceledQueue = applyAction(queued, 'abort');
  assert.equal(canceledQueue.status, 'aborted');
  const running = applyAction(fresh(), 'start');
  const paused = applyAction(applyAction(fresh(), 'start'), 'pause');
  const attention = tickWorkflow(applyAction(fresh('single', true), 'start'), 1000);
  const failed = { ...attention, id: 'failed-run', status: 'failed' as const };
  const completed = tickWorkflow(applyAction(fresh('two'), 'start'), 1000);
  const aborted = applyAction(tickWorkflow(applyAction(fresh(), 'start'), 1), 'abort');
  const executed = [running, paused, attention, failed, completed, aborted];
  const workflows = [draft, queued, canceledQueue, ...executed];
  // Test the original and canceled queue as independent entries.
  canceledQueue.id = 'canceled-queue';
  for (const item of [draft, queued, canceledQueue]) assert.equal(isExecutedWorkflow(item), false);
  for (const item of executed) assert.equal(isExecutedWorkflow(item), true);
  const exported = buildExperimentCsv(workflows, stamp);
  assert.equal(exported.workflowCount, 6);
  assert.equal(countExecutedWorkflows(workflows), 6);
  assert.equal(exported.excludedWorkflowCount, 3);
  assert.equal(exported.nodeCount, 6 * 9 + 8);
  const rows = parseCsv(exported.content);
  assert.equal(rows.length, exported.rowCount);
  assert.deepEqual(new Set(rows.filter(row => row['记录类型'] === '任务').map(row => row['任务状态'])), new Set(['运行中 (running)', '已暂停 (paused)', '需要关注 (attention)', '失败 (failed)', '已完成 (completed)', '已终止 (aborted)']));
  assert.ok(rows.every(row => row['数据来源'] === 'simulated'));
  assert.equal(exported.mimeType, 'text/csv;charset=utf-8');
  assert.equal(exported.filename, '全部已运行实验_20261026T030405Z.csv');
});

test('CSV round-trips Unicode, commas, quotes and multiline metadata and events', () => {
  const workflow = applyAction(fresh(), 'start');
  workflow.name = '中文实验，量子, "校准"\n第二行';
  workflow.owner = '团队, "负责人"';
  workflow.ownerId = 'leader';
  workflow.importSourceId = 'original-01';
  workflow.nodes[0].detail = '节点详情\r\nA,B,"C"';
  workflow.events.push({ time: stamp, kind: 'warning', message: '异常：第一行\n第二行, "原值"' });
  const exported = buildExperimentCsv([workflow], stamp);
  const rows = parseCsv(exported.content);
  assert.ok(rows.every(row => row['任务名称'] === workflow.name));
  assert.ok(rows.every(row => row['负责人'] === workflow.owner));
  assert.ok(rows.every(row => row['负责人账号ID'] === 'leader'));
  assert.ok(rows.every(row => row['导入源任务ID'] === 'original-01'));
  assert.equal(rows.find(row => row['记录类型'] === '节点')!['节点详情'], workflow.nodes[0].detail);
  assert.equal(rows.filter(row => row['记录类型'] === '事件').at(-1)!['事件内容'], workflow.events.at(-1)!.message);
});

test('Spreadsheet formula-like text is inert, even after leading whitespace; numeric result values remain numeric', () => {
  const workflow = tickWorkflow(applyAction(fresh(), 'start'), 1000);
  workflow.name = '=HYPERLINK("https://example.invalid","x")';
  workflow.machine = '+SUM(1,2)';
  workflow.owner = '  @SUM(1,2)';
  workflow.nodes[0].label = '-1+1';
  workflow.events = ['\t=1+1', '\r=1+1', '\n=1+1', '  =1+1', '\u0000@SUM(1,2)'].map(message => ({ message, time: stamp, kind: 'info' as const }));
  const rows = parseCsv(buildExperimentCsv([workflow], stamp).content);
  assert.equal(rows[0]['任务名称'], `'${workflow.name}`);
  assert.equal(rows[0]['机器'], `'${workflow.machine}`);
  assert.equal(rows[0]['负责人'], `'${workflow.owner}`);
  assert.equal(rows.find(row => row['记录类型'] === '节点')!['节点名称'], "'-1+1");
  assert.ok(rows.filter(row => row['记录类型'] === '事件').every(row => row['事件内容'].startsWith("'")));
  const t1 = rows.find(row => row['记录类型'] === '模拟结果' && row['节点ID'] === 'T1Idle' && row['字段键'] === 'tau')!;
  assert.equal(t1['字段值'], '50.84');
  assert.equal(t1['单位'], 'μs');
});

test('Partial and interrupted experiments preserve progress but do not export preview or fabricated final metrics', () => {
  const running = tickWorkflow(applyAction(fresh(), 'start'), 1);
  const aborted = applyAction(running, 'abort');
  for (const workflow of [running, aborted]) {
    const rows = parseCsv(buildExperimentCsv([workflow], stamp).content);
    assert.equal(rows.filter(row => row['记录类型'] === '模拟结果').length, 0);
    const nodes = rows.filter(row => row['记录类型'] === '节点');
    assert.equal(nodes.length, 9);
    assert.equal(nodes[0]['节点进度(%)'], '25');
    assert.equal(nodes[0]['执行次数'], '1');
    assert.equal(nodes[0]['累计演示运行时间(s)'], '1');
    assert.equal(nodes[0]['已观察模拟样本数'], '5');
    assert.equal(nodes[0]['结果阶段'], workflow.status === 'aborted' ? 'interrupted' : 'partial');
    for (const node of nodes) {
      const raw = JSON.parse(node['原始参数JSON']);
      assert.equal(raw.results, null);
      assert.equal(raw.previewValues, null);
      assert.equal(raw.hardwareTelemetryConnected, false);
    }
    assert.ok(nodes.slice(1).every(row => row['节点是否已执行'] === 'false'));
    const readout = rows.find(row => row['记录类型'] === '配置参数' && row['节点ID'] === 'ReadoutFreqDip' && row['字段键'] === 'readout_freq')!;
    assert.equal(readout['字段值'], '6.742');
    assert.equal(readout['单位'], 'GHz');
    assert.equal(readout['参数来源'], '前端演示配置');
  }
});

test('All node results, accepted low values, retests, policy and retained event history are exported together', () => {
  const attention = tickWorkflow(applyAction(fresh('two', true), 'start'), 1000);
  const completed = tickWorkflow(applyAction(attention, 'approve'), 1000);
  const rows = parseCsv(buildExperimentCsv([completed], stamp).content);
  const initial = rows.find(row => row['记录类型'] === '模拟结果' && row['节点ID'] === 'CZXEBCal' && row['字段键'] === 'fidelity')!;
  const retest = rows.find(row => row['记录类型'] === '模拟结果' && row['节点ID'] === 'CZXEBCalRetest' && row['字段键'] === 'fidelity')!;
  assert.equal(initial['字段值'], '94.2');
  assert.match(initial['判定说明'], /接受不代表达标/);
  assert.equal(retest['字段值'], '99.12');
  assert.equal(retest['结果阶段'], 'ready');
  assert.equal(retest['审批策略'], '异常时审批 (threshold)');
  assert.equal(retest['流程类型'], '双比特 (two)');
  assert.equal(retest['编排类型'], '自适应 (adaptive)');
  assert.equal(retest['模拟场景'], '异常场景 (anomaly)');
  assert.equal(retest['节点状态'], '已完成或确认 (success)');
  assert.equal(retest['当前审批标记'], 'true');
  assert.equal(rows.filter(row => row['记录类型'] === '节点').length, 17);
  assert.deepEqual(rows.filter(row => row['记录类型'] === '事件').map(row => row['事件内容']), completed.events.map(event => event.message));
  const width = rows.find(row => row['记录类型'] === '配置参数' && row['节点ID'] === 'TTGAczDelicateG' && row['字段键'] === 'CZ_width')!;
  assert.equal(width['字段值'], '60');
  assert.equal(width['单位'], 'ns');
});

test('Retry snapshot reports attempts and history limitation without reconstructing a lost first-attempt metric', () => {
  const attention = tickWorkflow(applyAction(fresh('single', true), 'start'), 1000);
  const retried = applyAction(attention, 'retry');
  const exported = buildExperimentCsv([retried], stamp);
  const rows = parseCsv(exported.content);
  const node = rows.find(row => row['记录类型'] === '节点' && row['节点ID'] === 'ReadoutParaSweepByIQProb')!;
  assert.equal(node['执行次数'], '2');
  assert.equal(node['重试次数'], '1');
  assert.equal(node['重试迭代数'], '1');
  assert.equal(node['节点累计估算演示时间(s)'], '4');
  assert.equal(rows.filter(row => row['记录类型'] === '模拟结果' && row['节点ID'] === node['节点ID']).length, 0);
  assert.equal(exported.historyNote, EXPERIMENT_CSV_HISTORY_NOTE);
  assert.match(rows[0]['记录说明'], /重试前被覆盖的测量/);
  assert.match(rows[0]['记录说明'], /已删除任务/);
});

test('Export is deterministic, read-only, deduplicated per ID and never accumulates rows across calls', () => {
  const state = createInitialState();
  const before = JSON.stringify(state);
  const first = buildExperimentCsv(state.workflows, stamp);
  const second = buildExperimentCsv(state.workflows, stamp);
  assert.deepEqual(first, second);
  assert.equal(JSON.stringify(state), before);
  const duplicate = buildExperimentCsv([...state.workflows, ...state.workflows], stamp);
  assert.equal(duplicate.content, first.content);
  assert.equal(duplicate.workflowCount, first.workflowCount);
  assert.equal(duplicate.duplicateWorkflowCount, state.workflows.length);
  assert.equal(parseCsv(first.content).length, first.rowCount);
  assert.equal(first.workflowCount, 6, 'Seed completed, failed, paused, attention, running and aborted tasks have execution evidence');
  assert.equal(first.excludedWorkflowCount, 2, 'Seed draft and queue have no executed node');
  const empty = buildExperimentCsv([], stamp);
  assert.equal(empty.workflowCount, 0);
  assert.equal(empty.rowCount, 0);
  assert.deepEqual(parseCsv(empty.content), []);
  assert.throws(() => buildExperimentCsv([], 'invalid date'), /时间无效/);
});
