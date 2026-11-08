/**
 * Design targets and acceptance floors bound to the selected workflow's Skill.
 *
 * The workflow payload deliberately carries no acceptance policy: the backend
 * resolves a workflow's Skill and device profile separately, and the frontend
 * caches that resolution beside the recorded measurements. The comparison
 * module consults this cache so it can show the *design target* the backend
 * actually assigned to a step, instead of a front-end demo constant.
 *
 * Targets and acceptance criteria are distinct on purpose:
 *
 *   - `MetricTarget` is the ideal a good calibration should reach.
 *   - `AcceptanceCriterion` is the floor that triggers a human decision.
 *
 * They are resolved together here only because both come from the same
 * binding; they are never derived from one another.
 *
 * The cache is only populated in remote mode. A missing entry (mock mode,
 * fetch in flight, backend unreachable) leaves the demo targets in place.
 */
import type {
  AcceptanceCriterion,
  EffectiveSkillStep,
  MetricTarget,
  SkillBinding,
  SkillStep,
} from "./remoteInsights.ts";

export interface ResolvedTarget {
  target: MetricTarget;
  source: "device" | "skill";
}

export interface ResolvedAcceptance {
  criterion: AcceptanceCriterion;
  source: "device" | "skill";
  deviceOverride: boolean;
}

export interface WorkflowTargetSnapshot {
  workflowId: string;
  binding: SkillBinding;
  targetsByNode: Record<string, ResolvedTarget>;
  acceptanceByNode: Record<string, ResolvedAcceptance>;
  loadedAt: number;
}

const MAX_CACHED_WORKFLOWS = 32;

function acceptanceOf(
  step: SkillStep,
  binding: SkillBinding,
): ResolvedAcceptance | undefined {
  if (!step.acceptance) return undefined;
  const device = binding.device;
  const override = device?.acceptanceOverrides?.[step.nodeId];
  if (override) {
    return { criterion: override, source: "device", deviceOverride: true };
  }
  if (step.acceptance.metric === "fidelity" && device?.fidelityCriterion) {
    return {
      criterion: device.fidelityCriterion,
      source: "device",
      deviceOverride: true,
    };
  }
  return { criterion: step.acceptance, source: "skill", deviceOverride: false };
}

function targetOf(step: EffectiveSkillStep): ResolvedTarget | undefined {
  // Skill v1.1.0 sends the already-resolved target and its source. A binding
  // from an older backend has no `effectiveTarget`, so the Skill's own target
  // is the effective one (those profiles predate target overrides).
  const effective =
    step.effectiveTarget !== undefined ? step.effectiveTarget : step.target;
  if (!effective) return undefined;
  return {
    target: effective,
    source: step.targetSource === "device" ? "device" : "skill",
  };
}

/**
 * Flatten a binding into the two per-node indexes the comparison module needs.
 * `effectiveSteps` is authoritative; the flat `targets` index and the Skill's
 * own steps are only a fallback for bindings that predate it.
 */
export function buildWorkflowTargets(
  workflowId: string,
  binding: SkillBinding,
  loadedAt = Date.now(),
): WorkflowTargetSnapshot {
  const targetsByNode: Record<string, ResolvedTarget> = {};
  const acceptanceByNode: Record<string, ResolvedAcceptance> = {};

  const effectiveSteps = binding.effectiveSteps ?? [];
  if (effectiveSteps.length > 0) {
    for (const step of effectiveSteps) {
      const target = targetOf(step);
      if (target) targetsByNode[step.nodeId] = target;
      // `effectiveAcceptance` is the criterion this workflow's device applies;
      // older bindings only carry the Skill's own acceptance.
      const criterion = step.effectiveAcceptance ?? step.acceptance;
      if (criterion) {
        acceptanceByNode[step.nodeId] = {
          criterion,
          source: step.deviceOverride ? "device" : "skill",
          deviceOverride: Boolean(step.deviceOverride),
        };
      }
    }
    return { workflowId, binding, targetsByNode, acceptanceByNode, loadedAt };
  }

  const flat = new Map(
    (binding.targets ?? []).map((entry) => [entry.nodeId, entry]),
  );
  for (const step of binding.skill?.steps ?? []) {
    const entry = flat.get(step.nodeId);
    if (entry?.target) {
      targetsByNode[step.nodeId] = {
        target: entry.target,
        source: entry.source === "device" ? "device" : "skill",
      };
    } else if (step.target) {
      targetsByNode[step.nodeId] = { target: step.target, source: "skill" };
    }
    const acceptance = acceptanceOf(step, binding);
    if (acceptance) acceptanceByNode[step.nodeId] = acceptance;
  }

  return { workflowId, binding, targetsByNode, acceptanceByNode, loadedAt };
}

const snapshots = new Map<string, WorkflowTargetSnapshot>();
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function trim(): void {
  while (snapshots.size > MAX_CACHED_WORKFLOWS) {
    const oldest = [...snapshots.values()].sort(
      (a, b) => a.loadedAt - b.loadedAt,
    )[0];
    if (!oldest) return;
    snapshots.delete(oldest.workflowId);
  }
}

export function publishWorkflowBinding(
  workflowId: string,
  binding: SkillBinding,
): WorkflowTargetSnapshot {
  const snapshot = buildWorkflowTargets(workflowId, binding);
  snapshots.set(workflowId, snapshot);
  trim();
  emit();
  return snapshot;
}

export function clearWorkflowTargets(workflowId?: string): void {
  if (workflowId) snapshots.delete(workflowId);
  else snapshots.clear();
  emit();
}

export function getWorkflowTargets(
  workflowId: string | undefined,
): WorkflowTargetSnapshot | undefined {
  return workflowId ? snapshots.get(workflowId) : undefined;
}

export function subscribeWorkflowTargets(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function targetForWorkflowNode(
  workflowId: string | undefined,
  nodeId: string | undefined,
): ResolvedTarget | undefined {
  if (!workflowId || !nodeId) return undefined;
  return snapshots.get(workflowId)?.targetsByNode[nodeId];
}

export function acceptanceForWorkflowNode(
  workflowId: string | undefined,
  nodeId: string | undefined,
): ResolvedAcceptance | undefined {
  if (!workflowId || !nodeId) return undefined;
  return snapshots.get(workflowId)?.acceptanceByNode[nodeId];
}
