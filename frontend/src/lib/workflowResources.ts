import type { AdapterIdentity, Catalog, ExperimentNode, NodeDetail, Resource, Workflow } from "../types.ts";

export function staleNodeLabel(node: ExperimentNode) {
  return node.attempt === 0 && !node.result ? "等待前置测量" : "依赖变化 · 待复测";
}

export function selectableResources(catalog: Catalog | undefined, mode: "replay" | "real" = "replay"): Resource[] {
  if (!catalog) return [];
  if (mode === "replay") return catalog.resources;
  const ids = (catalog.capabilities as { resources?: unknown })?.resources;
  if (!Array.isArray(ids)) return [];
  const supported = new Set(ids.filter((id): id is string => typeof id === "string"));
  return catalog.resources.filter(resource => supported.has(resource.id));
}

export function adapterIdentity(catalog?: Catalog, workflow?: Workflow, health?: { adapter?: AdapterIdentity; capabilities?: unknown }): AdapterIdentity | undefined {
  if (workflow?.mode === "replay") return workflow.adapter;
  const active = catalog?.adapter ?? health?.adapter;
  if (workflow?.mode === "real" && workflow.adapterId) {
    if (workflow.adapter?.id === workflow.adapterId) return workflow.adapter;
    return active?.id === workflow.adapterId ? active : { id: workflow.adapterId };
  }
  if (workflow?.adapter) return workflow.adapter;
  if (active) return active;
  const id = (catalog?.capabilities as { id?: unknown })?.id ?? (health?.capabilities as { id?: unknown })?.id;
  return typeof id === "string" && id ? { id } : undefined;
}

export function pulseStageLabel(catalog: Catalog, workflow: Workflow) {
  if (workflow.mode === "replay") return "历史实测回放";
  const adapter = adapterIdentity(catalog, workflow);
  return adapter ? `${adapter.name ?? adapter.id} 脉冲测量` : "脉冲测量";
}

export function displayedNodeParameters(workflow: Workflow, node: ExperimentNode, detail?: NodeDetail) {
  if (workflow.mode !== "replay") return detail?.parameters ?? node.parameters ?? {};
  const candidates = [node.result?.parameters, node.parameters, detail?.node?.result?.parameters, detail?.node?.parameters];
  return candidates.find(value => value && Object.keys(value).length) ?? {};
}

export function workflowBudget(workflow: Workflow) {
  const finite = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
  const limit = finite(workflow.maxTotalShots), reserved = finite(workflow.reservedShots);
  const attempts = finite(workflow.iterations), attemptLimit = finite(workflow.maxWorkflowAttempts), nodeLimit = finite(workflow.maxAttemptsPerNode);
  return { limit, reserved, attempts, attemptLimit, nodeLimit, remaining: limit !== undefined && reserved !== undefined ? Math.max(0, limit - reserved) : undefined };
}
