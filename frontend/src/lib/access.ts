import type { Workflow, WorkflowAction } from "../types.ts";
import { isKnownUser } from "./auth.ts";
import type { User } from "./auth.ts";

/** Existing owner display text is not an authorization identity. */
export type OwnedWorkflow = Workflow & { ownerId?: string };
type Actor = User | null | undefined;

export function canManageTeam(user: Actor): boolean {
  return isKnownUser(user) && user.role === "leader";
}
export function canCreateWorkflow(user: Actor): boolean {
  return isKnownUser(user);
}
export function canViewWorkflow(user: Actor, _workflow?: OwnedWorkflow | null): boolean {
  return isKnownUser(user);
}
export function canExportWorkspace(user: Actor): boolean {
  return isKnownUser(user);
}
export function canOperateWorkflow(user: Actor, workflow: OwnedWorkflow | null | undefined): boolean {
  if (!workflow || !isKnownUser(user)) return false;
  return user.role === "leader" || workflow.ownerId === user.id;
}
export function canApproveWorkflow(user: Actor, _workflow?: OwnedWorkflow | null): boolean {
  return canManageTeam(user);
}
export function canChangePolicy(user: Actor, _workflow?: OwnedWorkflow | null): boolean {
  return canManageTeam(user);
}
export function canImportWorkspace(user: Actor): boolean {
  return canManageTeam(user);
}
export function canResetWorkspace(user: Actor): boolean {
  return canManageTeam(user);
}
/** Status transitions remain the simulator/gateway's responsibility. */
export function canPerformAction(
  user: Actor,
  workflow: OwnedWorkflow | null | undefined,
  action: WorkflowAction,
): boolean {
  if (!workflow || !canOperateWorkflow(user, workflow)) return false;
  if (action === "approve") return canApproveWorkflow(user, workflow);
  // Rejection at an approval gate is also a leader decision. A failed task may
  // still be retried by its operator without authorizing an approval outcome.
  if (action === "retry") return workflow.status === "attention" ? canApproveWorkflow(user, workflow) : true;
  return ["start", "pause", "resume", "abort"].includes(action);
}
