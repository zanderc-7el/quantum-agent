import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const realRequire = createRequire(import.meta.url);
const bundles = new Map<string, string>();
function harness(file: string) {
  if (!bundles.has(file)) bundles.set(file, buildSync({ entryPoints: [fileURLToPath(new URL(`../src/components/${file}.tsx`, import.meta.url))], bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' } }).outputFiles[0].text);
  const states = new Map<any, any[]>(), effects: (() => void)[] = [];
  let slots: any[] = [], cursor = 0;
  const hooks = { ...React,
    useState(initial: any) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial; const owned = slots; return [owned[index], (value: any) => { owned[index] = typeof value === 'function' ? value(owned[index]) : value; }]; },
    useRef(initial: any) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useMemo(factory: () => any) { return factory(); }, useCallback(callback: any) { return callback; },
    useEffect(fn: any, deps: any[]) { const index = cursor++, owned = slots; if (!owned[index] || deps.some((value, i) => value !== owned[index].deps[i])) { owned[index]?.cleanup?.(); owned[index] = { deps }; effects.push(() => { owned[index].cleanup = fn(); }); } },
  };
  const module = { exports: {} as Record<string, any> };
  new Function('require', 'module', 'exports', bundles.get(file)!)((id: string) => id === 'react' ? hooks : realRequire(id), module, module.exports);
  return { exports: module.exports,
    render(type: any, props: any) { slots = states.get(type) ?? []; states.set(type, slots); cursor = 0; const tree = type(props); while (effects.length) effects.shift()!(); return tree; },
    close() { for (const state of states.values()) for (const slot of state) slot?.cleanup?.(); },
  };
}
function elements(tree: any): any[] { return !tree?.props ? [] : [tree, ...[tree.props.children].flat(Infinity).flatMap(elements)]; }
function text(tree: any): string { return typeof tree === 'string' || typeof tree === 'number' ? String(tree) : tree?.props ? [tree.props.children].flat(Infinity).map(text).join('') : ''; }
const button = (tree: any, label: string) => elements(tree).find(item => item.type === 'button' && text(item) === label)?.props;
const child = (tree: any, name: string) => elements(tree).find(item => item.type?.name === name);
const tick = () => new Promise<void>(resolve => setImmediate(resolve));
function fixture() {
  const result = { dataKind: 'real', sourceRunId: 'attempt-current/verify', acquiredAt: '2026-11-19T05:26:18+08:00', quality: { status: 'failed', reasons: ['quality_attention'] }, metrics: {}, artifacts: [{ id: 'raw', kind: 'raw', name: 'capture', sha256: 'a'.repeat(64) }], series: [{ name: 'IQ', fit: false, x: [0, 1, 2], y: [.1, .2, .3] }] };
  const node = { id: 'Q11:echo', resourceId: 'Q11', procedureId: 'sq_echo', latestAttemptId: 'attempt-later', result, name: 'Echo', label: 'Echo', qubits: 'Q11', detail: '', status: 'pending', engineState: 'idle', progress: 0, attempt: 3, capability: 'available', dependsOn: [] };
  const workflow = { id: 'wf-Q11', name: 'History', revision: 1, mode: 'real', policy: 'strict', status: 'paused', engineState: 'paused', target: 'Q11', type: 'adaptive', nodes: [node], currentNodeId: node.id, events: [] };
  const attempt = { id: 'attempt-history', nodeId: node.id, mode: 'real', procedureId: 'sq_echo', state: 'quality_attention', number: 1, startedAt: result.acquiredAt, parameters: {}, parameterVersions: {}, result: { ...result, sourceRunId: 'attempt-history/verify' } };
  const detail = { node, attempts: [attempt], rules: [], decisions: [], workflowRevision: 1 };
  const sent: any[] = [], reads: any[] = [];
  const props = { gateway: { async node(...args: any[]) { reads.push(args); return detail; } }, workflow, workflows: [workflow], node, busy: false, onCompareResult: (source: any) => sent.push(source), onBusy() {}, onParameterDirty() {}, onAction() { throw new Error('Must not dispatch'); }, onCreate() { throw new Error('Must not create'); }, onSelectWorkflow() {}, onSelectNode() {}, onBlueprint() {} };
  return { result, node, workflow, attempt, detail, sent, reads, props };
}
function clickResult(h: ReturnType<typeof harness>, element: any) {
  const tree = h.render(element.type, element.props), entry = child(tree, 'ResultCompareButton');
  assert.ok(entry); const buttonTree = h.render(entry.type, entry.props);
  assert.equal(text(buttonTree), '与仿真对照'); buttonTree.props.onClick();
}

test('result selection/verification panels expose one callback on the actual main verification only', () => {
  const h = harness('RemotePanels'), f = fixture();
  const result = { ...f.result, selectionResult: { ...f.result, sourceRunId: 'attempt-current/measure' } };
  const props = { result, comparisonSource: { workflowId: f.workflow.id, nodeId: f.node.id, attemptId: 'attempt-current' }, onCompareResult: f.props.onCompareResult };
  const tree = h.render(h.exports.RealResult, props), sections = elements(tree).filter(item => item.type === 'section');
  assert.equal(child(sections[0], 'ResultCompareButton'), undefined);
  const entry = child(sections[1], 'ResultCompareButton'); assert.equal(entry.props.result.sourceRunId, 'attempt-current/verify');
  const rendered = h.render(entry.type, entry.props); rendered.props.onClick();
  assert.deepEqual(f.sent, [props.comparisonSource]);
  const html = renderToStaticMarkup(React.createElement(h.exports.RealResult, props));
  assert.equal((html.match(/与仿真对照/g) ?? []).length, 1);
  for (const changed of [{ ...props, onCompareResult: undefined }, { ...props, result: undefined }, { ...props, comparisonSource: { ...props.comparisonSource, attemptId: 'attempt-other' } }, { ...props, result: { ...result, verificationPending: true } }]) {
    assert.doesNotMatch(renderToStaticMarkup(React.createElement(h.exports.RealResult, changed)), /与仿真对照/);
  }
  h.close();
});

test('manual current and historical entries pass exactly the viewed source without running an experiment', async () => {
  const h = harness('ManualExperimentWorkspace'), f = fixture();
  let tree = h.render(h.exports.default, f.props);
  button(tree, '下一步').onClick(); tree = h.render(h.exports.default, f.props);
  const configuration = child(tree, 'ManualConfiguration'); assert.ok(configuration); assert.equal(configuration.props.onCompareResult, f.props.onCompareResult);
  tree = h.render(configuration.type, configuration.props); await tick();
  button(tree, '实验结果').onClick(); tree = h.render(configuration.type, configuration.props);
  clickResult(h, child(tree, 'RealResult'));
  button(tree, '执行记录').onClick(); tree = h.render(configuration.type, configuration.props);
  const history = child(tree, 'ManualHistory'); const historyTree = h.render(history.type, history.props);
  clickResult(h, child(historyTree, 'RealResult'));
  assert.deepEqual(f.sent, [{ workflowId: 'wf-Q11', nodeId: 'Q11:echo', attemptId: 'attempt-current' }, { workflowId: 'wf-Q11', nodeId: 'Q11:echo', attemptId: 'attempt-history' }]);
  assert.deepEqual(f.reads, [['wf-Q11', 'Q11:echo']]);
  const changed = { ...configuration.props, node: { ...f.node, id: 'Q18:echo' } };
  tree = h.render(configuration.type, changed);
  const switched = child(tree, 'ManualHistory');
  assert.equal(child(h.render(switched.type, switched.props), 'RealResult'), undefined, 'old history disappears immediately on node change');
  h.close();
});

test('blueprint history preserves selected attempt and rejects another-node history record', async () => {
  const h = harness('RemotePanels'), f = fixture();
  let tree = h.render(h.exports.RealNodeInspector, f.props); await tick();
  button(tree, '执行历史').onClick(); tree = h.render(h.exports.RealNodeInspector, f.props);
  clickResult(h, child(tree, 'RealResult'));
  assert.deepEqual(f.sent, [{ workflowId: 'wf-Q11', nodeId: 'Q11:echo', attemptId: 'attempt-history' }]);
  f.attempt.nodeId = 'Q18:echo'; tree = h.render(h.exports.RealNodeInspector, f.props);
  assert.equal(child(tree, 'RealResult').props.comparisonSource, undefined);
  assert.deepEqual(f.reads, [['wf-Q11', 'Q11:echo']]); h.close();
});

test('verified result exposes one signal action bound to the displayed main attempt',()=>{
 const h=harness('RemotePanels'),f=fixture();
 const source={workflowId:f.workflow.id,nodeId:f.node.id,attemptId:'attempt-current',panel:'signals'};
 const result={...f.result,selectionResult:{...f.result,sourceRunId:'attempt-current/measure'}};
 const props={result,signalSource:source,onCompareResult:f.props.onCompareResult};
 const tree=h.render(h.exports.RealResult,props);const sections=elements(tree).filter(x=>x.type==='section');
 assert.ok(!elements(sections[0]).some(x=>x.type==='button'&&x.props.children==='查看信号与反馈'));
 button(sections[1],'查看信号与反馈').onClick();assert.deepEqual(f.sent,[source]);
 assert.doesNotMatch(renderToStaticMarkup(React.createElement(h.exports.RealResult,{...props,signalSource:{...source,attemptId:'attempt-other'}})),/查看信号与反馈/);
 h.close();
});
