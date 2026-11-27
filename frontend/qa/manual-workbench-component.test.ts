import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import * as React from 'react';
import type { Workflow } from '../src/types.ts';

// Render the real workspace's pure tree and handlers with in-memory hook state.
// No DOM, network, browser, effects, or hardware is involved.
const bundle = buildSync({ entryPoints: [fileURLToPath(new URL('../src/components/ManualExperimentWorkspace.tsx', import.meta.url))], bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react', 'react/jsx-runtime'], loader: { '.css': 'empty' } }).outputFiles[0].text;
const realRequire = createRequire(import.meta.url);
function harness(runEffects=false) {
  const slots: any[] = []; let cursor = 0;
  const effects:(()=>void)[]=[];
  const hooks = { ...React,
    useState(initial: any) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial; return [slots[index], (value: any) => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef(initial: any) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useMemo(factory: () => any) { const index = cursor++; return slots[index] ??= factory(); },
    useCallback(callback: any) { return callback; },
    useEffect(fn:any,deps:any[]) {if(!runEffects)return;const index=cursor++;if(!slots[index]||deps.some((value:any,n:number)=>value!==slots[index].deps[n])){slots[index]?.cleanup?.();slots[index]={deps};effects.push(()=>slots[index].cleanup=fn());}},
  };
  const module = { exports: {} as Record<string, any> };
  new Function('require', 'module', 'exports', bundle)((id: string) => id === 'react' ? hooks : realRequire(id), module, module.exports);
  return (props: any) => { cursor = 0;const tree=module.exports.default(props);while(effects.length)effects.shift()!();return tree; };
}
function elements(tree: any): any[] {
  if (!tree || typeof tree !== 'object' || !tree.props) return [];
  return [tree, ...[tree.props.children].flat(Infinity).flatMap(elements)];
}
function text(tree: any): string {
  if (typeof tree === 'string' || typeof tree === 'number') return String(tree);
  return tree?.props ? [tree.props.children].flat(Infinity).map(text).join('') : '';
}
const button = (tree: any, label: string) => elements(tree).find(item => item.type === 'button' && text(item) === label)?.props;
const workflow = (id: string): Workflow => ({ id, name: id, machine: '八号机', target: 'Q11', qubits: 1, type: 'adaptive', policy: 'strict', scenario: 'normal', mode: 'real', experiment: 'single', status: 'running', engineState: 'acquiring', owner: '', createdAt: '', progress: 0, elapsed: 0, iterations: 0, events: [], approved: false, currentNodeId: 'n1', nodes: [{ id: 'n1', name: 'S21', label: '找腔', qubits: 'Q11', detail: '', status: 'running', engineState: 'acquiring', progress: 0, attempt: 1, dependsOn: [], capability: 'available' }] });

test('workspace stop requests stay task scoped while another task receipt is pending', async () => {
  const render = harness(); const a = workflow('A'), b = workflow('B');
  const sent: unknown[][] = []; const finish: Array<() => void> = [];
  const base = { gateway: {}, workflows: [a, b], busy: false, onBusy() {}, onCreate() {}, onSelectWorkflow() {}, onSelectNode() {}, onBlueprint() {}, onParameterDirty() {} };
  const props = (value: Workflow) => ({ ...base, workflow: value, node: value.nodes[0], onAction: (...args: unknown[]) => { sent.push([value.id, ...args]); return new Promise<void>(resolve => finish.push(resolve)); } });
  button(render(props(a)), '终止').onClick();
  assert.equal(button(render(props(a)), '正在请求终止…').disabled, true);
  let tree = render(props(b));
  assert.equal(button(tree, '暂停').disabled, false);
  assert.equal(button(tree, '终止').disabled, false);
  button(tree, '暂停').onClick();
  tree = render(props(b));
  assert.equal(button(tree, '正在请求暂停…').disabled, true);
  button(tree, '终止').onClick();
  button(render(props(b)), '正在请求终止…').onClick();
  assert.deepEqual(sent, [['A', 'abort', undefined], ['B', 'pause', undefined], ['B', 'abort', undefined]]);
  finish[0](); finish[1](); await Promise.resolve(); await Promise.resolve();
  assert.equal(button(render(props(b)), '正在请求终止…').disabled, true);
  finish[2](); await Promise.resolve(); await Promise.resolve();
  assert.equal(button(render(props(b)), '终止').disabled, false);
});

test('new manual experiments expose a separate measured bias profile without opening a device',()=>{
 const render=harness();const base={gateway:{},workspaceApi:{get:async()=>[]},workflows:[],busy:false,onBusy(){},onCreate(){throw new Error('no create');},onSelectWorkflow(){},onSelectNode(){},onBlueprint(){},onParameterDirty(){}};
 let tree=render(base);const flux=button(tree,'偏置扫描 / 工作点核查');assert.ok(flux);flux.onClick();tree=render(base);
 const setup=elements(tree).find(item=>item.props.executionProfile==='science-flux');assert.ok(setup);
 assert.equal(setup.props.disabled,false);
});

test('explicitly reopening the same workflow exits creation without relying on changed workflow identity',()=>{
 const render=harness(true),wf=workflow('same');const props={gateway:{},workflows:[wf],workflow:wf,node:wf.nodes[0],busy:false,workflowOpenToken:0,onBusy(){},onCreate(){},onSelectWorkflow(){},onSelectNode(){},onBlueprint(){},onParameterDirty(){}};
 let tree=render(props);elements(tree).find(n=>n.type==='select'&&n.props['aria-label']==='手动实验任务').props.onChange({target:{value:''}});
 tree=render(props);assert.ok(text(tree).includes('新建实验'));
 render({...props,workflowOpenToken:1});tree=render({...props,workflowOpenToken:1});
 assert.equal(elements(tree).find(n=>n.type==='input'&&n.props['aria-label']==='实验名称').props.value,'same');
});

test('point introduction shows useful units and keeps source identifiers in technical details',()=>{
 const render=harness();const point={recordId:'science-point',sourceRecordId:'science-original',purpose:'idle',resourceId:'Q11',fluxChannel:'Z_Q11',bias:.02,biasUnit:'V',readoutFrequencyHz:7010100000};
 const tree=render({gateway:{},workflows:[],busy:false,workingPoint:point,onBusy(){},onCreate(){},onSelectWorkflow(){},onSelectNode(){},onBlueprint(){},onParameterDirty(){}});
 const panel=elements(tree).find(n=>n.props['aria-label']==='带入的闲置点或工作点');
 assert.ok(!elements(panel).filter(n=>n.type==='h3'||n.type==='p').some(n=>text(n).includes('science-')));
 assert.ok(text(panel).includes('7.0101 GHz'));assert.ok(text(panel).includes('点位来源技术详情'));
});
