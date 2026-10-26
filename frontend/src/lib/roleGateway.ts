import type { User } from "./auth.ts";
import { canPerformAction, canChangePolicy, canImportWorkspace, canResetWorkspace, canCreateWorkflow } from "./access.ts";
import { readSession } from "./auth.ts";
import { createMockGateway } from "./gateway.ts";
import type { WorkflowGateway, MockWorkflowGateway, MockGatewayDependencies } from "./gateway.ts";

/** Demonstration permissions. A real backend must independently authenticate every request. */
export function createRoleGateway(user: User, injected?: WorkflowGateway, dependencies?: MockGatewayDependencies, sessionValid: () => boolean = () => readSession()?.id === user.id): WorkflowGateway {
  const base = injected ?? createMockGateway({ ...dependencies, actor: user });
  const denied = () => { throw new Error("当前账号没有此操作权限：工作人员仅能操作本人任务，审批与团队管理由负责人完成。"); };
  const guard = () => { if (!sessionValid()) throw new Error("登录已过期，请重新登录后操作。"); };
  const gateway: WorkflowGateway = {
    mode: base.mode,
    list: () => base.list(),
    get: (id) => base.get(id),
    subscribe: (listener) => base.subscribe(listener),
    async create(input) { guard(); if (!canCreateWorkflow(user)) return denied(); return base.create({ ...input, policy: user.role === "operator" ? "threshold" : input.policy }); },
    async act(id, action) {
      guard();
      const workflow = await base.get(id);
      guard();
      if (!canPerformAction(user, workflow, action)) return denied();
      return base.act(id, action);
    },
    async policy(id, policy) {
      guard();
      const workflow = await base.get(id);
      guard();
      if (!canChangePolicy(user, workflow)) return denied();
      return base.policy(id, policy);
    },
    ...(base.importWorkflows ? { async importWorkflows(json: string) {
      guard();
      if (!canImportWorkspace(user)) return denied();
      return base.importWorkflows!(json);
    } } : {}),
  };
  if (base.mode === "mock" && "snapshot" in base) {
    const mock = base as MockWorkflowGateway;
    return Object.assign(gateway, {
      snapshot: () => mock.snapshot(),
      advance: (seconds: number) => { guard(); mock.advance(seconds); },
      resetDemo: () => { guard(); if (!canResetWorkspace(user)) return denied(); mock.resetDemo(); },
    });
  }
  return gateway;
}
