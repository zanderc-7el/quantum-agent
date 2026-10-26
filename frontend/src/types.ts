export type WorkflowStatus =
  | "draft"
  | "queued"
  | "running"
  | "attention"
  | "paused"
  | "completed"
  | "failed"
  | "aborted";
export type NodeStatus =
  | "success"
  | "running"
  | "pending"
  | "attention"
  | "abandoned";
export type Policy = "strict" | "threshold" | "autopilot";
export type Scenario = "normal" | "anomaly";
export interface ExperimentNode {
  id: string;
  name: string;
  label: string;
  status: NodeStatus;
  progress: number;
  attempt: number;
  qubits: string;
  detail: string;
}
export interface Workflow {
  /** Original source ID used to make workspace imports idempotent. */
  importSourceId?: string;
  experiment: "single" | "two";
  id: string;
  name: string;
  machine: string;
  qubits: number;
  target: string;
  type: "adaptive" | "fixed";
  status: WorkflowStatus;
  progress: number;
  owner: string;
  /** Stable account identity; legacy sample tasks intentionally have no ownerId. */
  ownerId?: string;
  createdAt: string;
  policy: Policy;
  scenario: Scenario;
  elapsed: number;
  iterations: number;
  nodes: ExperimentNode[];
  events: {
    time: string;
    message: string;
    kind: "info" | "success" | "warning";
  }[];
  approved: boolean;
}
export interface CreateWorkflowInput {
  experiment?: "single" | "two";
  name: string;
  machine: string;
  qubits: number;
  target: string;
  type: "adaptive" | "fixed";
  policy: Policy;
  scenario: Scenario;
}
export interface AppState {
  version: 1;
  workflows: Workflow[];
}
export type WorkflowAction =
  | "start"
  | "pause"
  | "resume"
  | "approve"
  | "retry"
  | "abort";
