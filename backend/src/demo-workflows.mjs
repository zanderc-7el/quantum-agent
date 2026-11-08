import { createWorkflow, applyAction, setPolicy, tickWorkflow, isValidWorkflow, assertWorkspaceSize, MAX_WORKFLOWS } from '../../frontend/src/lib/simulator.ts';
import { ApiError } from './core.mjs';

const actions = ['start', 'pause', 'resume', 'approve', 'retry', 'abort'];
const policies = ['strict', 'threshold', 'autopilot'];
const copy = value => structuredClone(value);
const invalid = message => { throw new ApiError('INVALID_INPUT', message, 400); };
const record = value => value && typeof value === 'object' && !Array.isArray(value);
export const demoMetadata = { dataSource: 'simulated', hardwareConnected: false, authority: 'demo-server' };

/** Shared, unauthenticated local demo only. Never calls a device or remote service. */
export function makeDemoWorkflows({ load = () => null, save = () => {} } = {}) {
  const loaded = load();
  if (loaded !== null && (!record(loaded) || loaded.version !== 1 || !Array.isArray(loaded.workflows) || !loaded.workflows.every(isValidWorkflow))) {
    throw new Error('Invalid persisted demo workflow state; refusing to overwrite it');
  }
  const seed = () => ['single', 'two'].map(experiment => {
    const workflow = createWorkflow({ experiment, name: experiment === 'single' ? '服务端演示 · 单比特校准' : '服务端演示 · 双比特校准', machine: 'Simulated QPU', qubits: experiment === 'single' ? 1 : 2, target: experiment === 'single' ? 'Q12' : 'Q12,Q13', type: 'adaptive', policy: 'autopilot', scenario: 'normal' });
    workflow.events[0].message = '服务端预置模拟工作流；尚未启动，不连接真实设备。';
    return workflow;
  });
  let state = copy(loaded ?? { version: 1, workflows: seed() });
  if (loaded === null) save(copy(state));
  assertWorkspaceSize(state);
  // A restart must not silently resume simulated execution without the user's intent.
  const recovered = state.workflows.map(workflow => workflow.status === 'running' ? applyAction(workflow, 'pause') : workflow);
  if (recovered.some((workflow, index) => workflow !== state.workflows[index])) {
    state = { ...state, workflows: recovered }; save(copy(state));
  }
  const commit = next => {
    try { assertWorkspaceSize(next); } catch (error) { throw new ApiError('CAPACITY_EXCEEDED', error.message, 409); }
    save(copy(next)); // Persist first: failed writes do not acknowledge or advance state.
    state = next;
  };
  const find = id => {
    const workflow = state.workflows.find(item => item.id === id);
    if (!workflow) throw new ApiError('NOT_FOUND', '未找到模拟工作流。', 404);
    return workflow;
  };
  const update = (id, mutate) => {
    const previous = find(id);
    const workflow = mutate(previous);
    if (previous !== workflow) commit({ ...state, workflows: state.workflows.map(item => item.id === id ? workflow : item) });
    return copy(workflow);
  };
  return {
    list: () => copy(state.workflows),
    get: id => copy(find(id)),
    create(input) {
      if (!record(input)) invalid('工作流输入必须是对象。');
      const allowed = ['experiment', 'name', 'machine', 'qubits', 'target', 'type', 'policy', 'scenario', 'ownerId', 'owner'];
      if (Object.keys(input).some(key => !allowed.includes(key))) invalid('工作流包含不支持的字段。');
      if (!['name', 'machine', 'target'].every(key => typeof input[key] === 'string')) invalid('名称、机器和目标必须是文本。');
      if (input.ownerId !== undefined && (typeof input.ownerId !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(input.ownerId))) invalid('模拟所有者标识无效。');
      if (input.owner !== undefined && (typeof input.owner !== 'string' || !input.owner.trim() || input.owner.length > 500)) invalid('模拟所有者名称无效。');
      if (state.workflows.length >= MAX_WORKFLOWS) throw new ApiError('CAPACITY_EXCEEDED', '模拟工作区已达到任务上限。', 409);
      const { ownerId, owner, ...parameters } = input;
      let workflow;
      try { workflow = createWorkflow(parameters); } catch (error) { invalid(error.message); }
      if (ownerId) workflow.ownerId = ownerId;
      if (owner) workflow.owner = owner.trim();
      workflow.events[0].message = '已创建服务端模拟工作流；仅共享演示数据，不连接真实设备。';
      commit({ ...state, workflows: [workflow, ...state.workflows] });
      return copy(workflow);
    },
    act(id, action) {
      if (!actions.includes(action)) invalid('未知的模拟工作流操作。');
      return update(id, workflow => applyAction(workflow, action));
    },
    policy(id, policy) {
      if (!policies.includes(policy)) invalid('未知的模拟审批策略。');
      return update(id, workflow => setPolicy(workflow, policy));
    },
    advance(seconds = 1) {
      const workflows = state.workflows.map(workflow => tickWorkflow(workflow, seconds));
      if (workflows.some((workflow, index) => workflow !== state.workflows[index])) commit({ ...state, workflows });
    },
  };
}
