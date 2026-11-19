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
export type WorkflowCoverage = "main" | "full" | "working_point";
export interface WorkingPointSeedReference {
  schema: "working-point-seed/1";
  status: "seed_only";
  sourceId: string;
  parameters: Record<string, unknown>;
  sourceFiles: { kind: string; name: string; path: string; sha256: string }[];
  sourceReport?: { path: string; sha256: string };
  meaning: string;
}
export interface WorkingPointCoverage {
  available: boolean;
  reason: string;
  resourceIds: string[];
  adapterProfileIds: string[];
  selectedNodeCount: number;
  seedStatus: "seed_only";
  label: string;
}
export interface ReadoutRevalidation {
  id: string; state: string; resourceId: string; sourceAttemptId: string;
  optimizerNodeId: string; rabiNodeId: string; origin?: string;
  validationStatus?: string; withdrawalOperationId?: string; lastFixedAttemptId?: string; endedAt?: string;
  values: { readout_hz: number; readout_amp: number };
  parameterVersions: Record<string, number>;
}
export interface ReadoutWithdrawal {
  ruleVersion: string; operationId: string; source: 'operator_withdrawal';
  validationStatus: string; revalidationId: string; reason: string; createdAt: string;
  before: Pick<ReadoutRevalidation, 'values' | 'parameterVersions'>;
  after: Pick<ReadoutRevalidation, 'values' | 'parameterVersions'>;
  sourceEvidence: { optimizerAttemptId: string; baselineIqAttemptId: string; operations: { operationId: string; rawSha256: string[]; receiptSha256?: string }[] };
  invalidatedNodes: string[];
}
export interface ReadoutWithdrawalRequest {
  operationId: string; expectedRevision: number; revalidationId: string; sourceAttemptId: string; reason: string;
}
export interface ExperimentNode {
  id: string;
  resourceId?: string;
  name: string;
  label: string;
  status: NodeStatus;
  progress: number;
  attempt: number;
  qubits: string;
  detail: string;
  ownerId?: string;
  procedureId?: string;
  capability?: "available" | "unavailable" | "historical_only";
  engineState?: string;
  parameters?: Record<string, unknown>;
  parameterVersions?: Record<string, number>;
  result?: MeasurementResult;
  latestAttemptId?: string;
  rules?: Rule[];
  dependsOn?: string[];
  telemetry?: { rowNumber?: number; completedRows?: number; totalRows?: number; jobIndex?: number; jobCount?: number; jobProgress?: number; progress?: number; progressBasis?: string; message?: string; prob_hz?: number; drive_hz?: number; [key: string]: unknown };
  editableParameterKeys?: string[];
}
export interface Workflow {
  /** Task-scoped handoff authorization; not a live device connection receipt. */
  hardwareConfirmed?: boolean;
  /** Backend acquisition-attempt timestamps; elapsed includes pauses and model waits. */
  firstAcquisitionAt?: string | null;
  finishedAt?: string | null;
  mode?: "replay" | "real";
  coverage?: WorkflowCoverage;
  seedReference?: WorkingPointSeedReference;
  completionScope?: { selectedNodeCount: number; omittedProcedures: string[]; full13Fresh: boolean; meaning: string };
  revision?: number;
  engineState?: string;
  blueprintVersion?: string;
  edges?: { from: string; to: string; kind?: string }[];
  resourceIds?: string[];
  currentNodeId?: string;
  resultSummary?: unknown;
  maxAttemptsPerNode?: number;
  maxWorkflowAttempts?: number;
  supplementalAcceptance?: { policyVersion: string; policyHash: string; installedAt: string; reason: string; applicableProcedures: string[] };
  readoutRevalidations?: Record<string, ReadoutRevalidation>;
  rbDiagnostic?: RbDiagnosticMarker;
  lastReadoutWithdrawal?: ReadoutWithdrawal;
  maxTotalShots?: number;
  reservedShots?: number;
  adapterId?: string;
  adapter?: AdapterIdentity;
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
  mode?: "replay" | "real";
  coverage?: WorkflowCoverage;
  resourceIds?: string[];
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
  seq?: number;
}
export interface Artifact { id: string; name: string; kind: "image" | "raw" | "json" | "text"; url: string; sha256: string; sourcePath?: string; }
export interface SupplementalQuality {
  policyVersion: string; policyHash: string; accepted: boolean;
  receipts: Partial<Record<'selection' | 'verification' | 'cross', { status: 'passed' | 'failed' | 'unverified'; accepted: boolean; reasons: string[]; checks: unknown[]; evidenceHash: string; registeredEvidence?: unknown }>>;
}
export interface MeasurementResult {
  selectionResult?: MeasurementResult;
  supplementalQuality?: SupplementalQuality;
  stale?: boolean;
  partial?: boolean;
  verificationPending?: boolean;
  dataKind: "historical" | "real";
  sourceRunId?: string; acquiredAt?: string;
  metrics: Record<string, number | null>; units?: Record<string, string>;
  quality: { status: "passed" | "failed" | "unverified"; reasons: string[] };
  artifacts: Artifact[];
  series?: { name: string; x: number[]; y: number[]; xUnit?: string; yUnit?: string; fit?: boolean }[];
  parameters?: Record<string, unknown>; provenance?: unknown; methodLabel?: string;
}
export interface Rule {
  id: string; version: string | number; title: string; scope: unknown;
  trigger: unknown; action: unknown; verification: unknown; evidence: string[];
  policyStatus?: string; publishedAt?: string; sourceSha256?: string;
}
export interface Attempt { id: string; nodeId: string; number: number; state: string; startedAt: string; endedAt?: string; parameters: Record<string, unknown>; parameterVersions: Record<string, number>; result?: MeasurementResult; error?: unknown; operationId: string; mode: string; procedureId?: string; }
export interface SelectionVerificationBudgetLookahead {
  version: "selection-verification-budget/1";
  selectionShots: number; verificationShotsUpperBound: number; jointShots: number; remainingShotsAtCheck: number;
  verificationBasis: "same_sampling_counts_after_calibration_only" | "arclight_single_setting_same_shots_captures_blocks";
  reservation: false; candidateKnown: false; actualVerificationPlanRechecked: true;
}
export interface RbDiagnosticMarker {
  id: string; commandOperationId: string; policyVersion: "rb-after-cpmg-diagnostic/1";
  state: "queued" | "running" | "completed" | "failed" | "interrupted" | "invalidated";
  sourceNodeId: string; sourceAttemptId: string; targetNodeId: string; attemptId: string | null;
  sourceProofSha256: string; physicalParameterVersions: Record<string, number>;
  requestedAt: string; finishedAt?: string; resultStatus?: string; publication: "disabled_diagnostic_only";
}
export interface RbDiagnosticInspection {
  schema: "qcal-rb-diagnostic-inspection/1"; workflowId: string; revision: number;
  policyVersion: "rb-after-cpmg-diagnostic/1"; eligible: boolean; reasons: { code: string; message: string }[];
  sourceNodeId?: string; sourceAttemptId?: string; targetNodeId?: string; sourceProofSha256?: string;
  physicalParameterVersions?: Record<string, number>; budgetLookahead?: SelectionVerificationBudgetLookahead;
  marker: RbDiagnosticMarker | null;
}
export interface RbDiagnosticRequest {
  action: "queue_rb_diagnostic"; operationId: string; expectedRevision: number; nodeId: string; attemptId: string;
  policyVersion: "rb-after-cpmg-diagnostic/1"; sourceProofSha256: string;
}
export interface Decision { budgetLookahead?: SelectionVerificationBudgetLookahead; id: string; nodeId: string; attemptId?: string; createdAt: string; source: "rule" | "model" | "operator"; action: string; reason: string; evidenceIds: string[]; ruleId?: string; parameterChanges?: Record<string, unknown>; targetNodeId?: string; invalidatedNodes?: string[]; status?: string; }
export interface NodeDetail { node: ExperimentNode; attempts: Attempt[]; decisions: Decision[]; rules: Rule[]; parameterBounds?: ParameterBounds; parameters?: Record<string, unknown>; }
export interface Resource { id: string; label: string; engineKey?: string; [key: string]: unknown; }
export interface AdapterIdentity { id?: string; name?: string }
export interface ParameterBound { min: number; max: number; unit?: string; label?: string; integer?: boolean; enum?: number[] }
export type ParameterBounds = Record<string, ParameterBound>;
export interface Catalog { blueprint: { version: string; nodes: ExperimentNode[]; edges: NonNullable<Workflow["edges"]> }; resources: Resource[]; rules: Rule[]; capabilities: unknown; adapter?: AdapterIdentity; parameterBounds?: ParameterBounds; coverage?: { realDefault?: WorkflowCoverage; main?: string; full?: string; working_point?: string }; workingPointCoverage?: WorkingPointCoverage; stages?: { id: string; label: string; capability?: string; reason?: string; requiresHardwareConfirmation?: boolean; blueprintVersion?: string; adapter?: AdapterIdentity }[]; }
export type RemoteAction = WorkflowAction | "backtrack" | "confirm_hardware" | "reconcile" | "decide";
export type WorkflowAction =
  | "start"
  | "pause"
  | "resume"
  | "approve"
  | "retry"
  | "abort";
