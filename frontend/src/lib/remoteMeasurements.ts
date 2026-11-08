/**
 * Server-measured node results, cached for the read-only chart/report views.
 *
 * The workflow payload intentionally carries no measurement values: the
 * backend keeps node results in their own append-only table so the strict
 * `Workflow` validator stays unchanged. These helpers fetch
 * `GET /workflows/{id}/results`, normalize the adapter metrics (fidelity is
 * stored as 0..1), and publish them to a per-workflow cache that the pure
 * measurement selectors consult.
 *
 * The cache is only populated in remote mode. Mock workspaces therefore keep
 * their deterministic demo values, and the node:test suite (which never
 * fetches) sees exactly the same numbers as before.
 */
import { apiRequest } from "./apiClient.ts";
import type { MetricTarget as WireMetricTarget } from "./remoteInsights.ts";

const BASE = "/api/v1/calibration";
const MAX_CACHED_WORKFLOWS = 32;

export interface NodeResultPayload {
  id: string;
  workflowId: string;
  nodeId: string;
  attempt: number;
  stage: string;
  metrics: Record<string, unknown>;
  rawRef: string | null;
  createdAt: string;
}

export interface QubitParameters {
  f01Ghz?: number;
  readoutGhz?: number;
  t1Us?: number;
  t2Us?: number;
  [key: string]: unknown;
}

/** The acceptance criterion frozen into a recorded evaluation. */
export interface RecordedCriterion {
  metric: string;
  comparator: string;
  threshold: number;
  unit: string;
  description: string;
}

export interface ServerNodeMeasurement {
  nodeId: string;
  attempt: number;
  stage: string;
  metrics: Record<string, unknown>;
  parameters: Record<string, QubitParameters>;
  /** Percent, converted from the adapter's 0..1 fraction when needed. */
  fidelityPercent: number | null;
  t1Us: number | null;
  t2Us: number | null;
  centerGhz: number | null;
  readoutGhz: number | null;
  errorUs: number | null;
  r2: number | null;
  /** Frozen acceptance verdict, when the backend recorded one. */
  passed: boolean | null;
  /** The criterion the verdict was judged against, when recorded. */
  criterion: RecordedCriterion | null;
  /** The design target frozen alongside the verdict, when the backend recorded one. */
  evaluationTarget: WireMetricTarget | null;
  /** "skill" or "device": which policy supplied the frozen target. */
  evaluationTargetSource: string | null;
  reason: string | null;
  adapter: string | null;
  simulated: boolean | null;
  capturedAt: string;
}

export interface WorkflowMeasurementSnapshot {
  workflowId: string;
  results: ServerNodeMeasurement[];
  latestByNode: Record<string, ServerNodeMeasurement>;
  loadedAt: number;
}

function number(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function objectOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function fidelityPercent(value: unknown): number | null {
  const raw = number(value);
  if (raw === null) return null;
  // Adapters report 0..1; legacy metrics may already be a percentage.
  const percent = raw <= 1.5 ? raw * 100 : raw;
  return Math.round(percent * 10000) / 10000;
}

function boolOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function criterionOf(value: unknown): RecordedCriterion | null {
  const record = objectOf(value);
  if (typeof record.metric !== "string") return null;
  const threshold = number(record.threshold);
  if (threshold === null) return null;
  return {
    metric: record.metric,
    comparator: typeof record.comparator === "string" ? record.comparator : ">=",
    threshold,
    unit: typeof record.unit === "string" ? record.unit : "",
    description:
      typeof record.description === "string" ? record.description : "",
  };
}

function targetOf(value: unknown): WireMetricTarget | null {
  const record = objectOf(value);
  if (typeof record.metric !== "string") return null;
  const ideal = number(record.ideal);
  if (ideal === null) return null;
  return {
    metric: record.metric,
    ideal,
    unit: typeof record.unit === "string" ? record.unit : "",
    direction:
      typeof record.direction === "string" ? record.direction : "higher",
    tolerance: number(record.tolerance),
    description:
      typeof record.description === "string" ? record.description : "",
  };
}

function normalize(payload: NodeResultPayload): ServerNodeMeasurement {
  const metrics = objectOf(payload.metrics);
  const evaluation = objectOf(metrics.evaluation);
  const parameters = objectOf(metrics.parameters) as Record<
    string,
    QubitParameters
  >;
  const adapter =
    typeof metrics.adapter === "string" ? metrics.adapter : null;
  let t1Us: number | null = null;
  let t2Us: number | null = null;
  let centerGhz: number | null = null;
  let readoutGhz: number | null = null;
  for (const entry of Object.values(parameters)) {
    t1Us ??= number(entry?.t1Us) ?? null;
    t2Us ??= number(entry?.t2Us) ?? null;
    centerGhz ??= number(entry?.f01Ghz) ?? null;
    readoutGhz ??= number(entry?.readoutGhz) ?? null;
  }
  return {
    nodeId: payload.nodeId,
    attempt: payload.attempt,
    stage: payload.stage,
    metrics,
    parameters,
    fidelityPercent: fidelityPercent(metrics.fidelity),
    t1Us: number(metrics.t1Us) ?? t1Us,
    t2Us: number(metrics.t2Us) ?? t2Us,
    centerGhz: number(metrics.centerGhz) ?? centerGhz,
    readoutGhz: number(metrics.readoutGhz) ?? readoutGhz,
    errorUs: number(metrics.error) ?? null,
    r2: number(metrics.r2) ?? null,
    passed: boolOrNull(evaluation.passed),
    criterion: criterionOf(evaluation.criterion),
    evaluationTarget: targetOf(evaluation.target),
    evaluationTargetSource:
      typeof evaluation.targetSource === "string"
        ? evaluation.targetSource
        : null,
    reason:
      typeof evaluation.reason === "string"
        ? evaluation.reason
        : typeof metrics.reason === "string"
          ? metrics.reason
          : null,
    adapter,
    simulated: boolOrNull(metrics.simulated),
    capturedAt: payload.createdAt,
  };
}

/** Latest attempt wins; equal attempts keep the newest recorded result. */
function newest(
  current: ServerNodeMeasurement | undefined,
  candidate: ServerNodeMeasurement,
): ServerNodeMeasurement {
  if (!current) return candidate;
  if (candidate.attempt !== current.attempt)
    return candidate.attempt > current.attempt ? candidate : current;
  return candidate.capturedAt >= current.capturedAt ? candidate : current;
}

export function buildSnapshot(
  workflowId: string,
  payloads: NodeResultPayload[],
  loadedAt = Date.now(),
): WorkflowMeasurementSnapshot {
  const results = payloads.map((payload) => normalize(payload));
  const latestByNode: Record<string, ServerNodeMeasurement> = {};
  for (const result of results) {
    latestByNode[result.nodeId] = newest(latestByNode[result.nodeId], result);
  }
  return { workflowId, results, latestByNode, loadedAt };
}

const snapshots = new Map<string, WorkflowMeasurementSnapshot>();
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

export function publishWorkflowResults(
  workflowId: string,
  payloads: NodeResultPayload[],
): WorkflowMeasurementSnapshot {
  const snapshot = buildSnapshot(workflowId, payloads);
  snapshots.set(workflowId, snapshot);
  trim();
  emit();
  return snapshot;
}

export function clearWorkflowResults(workflowId?: string): void {
  if (workflowId) {
    snapshots.delete(workflowId);
    parameterSnapshots.delete(workflowId);
  } else {
    snapshots.clear();
    parameterSnapshots.clear();
  }
  emit();
}

export function getWorkflowResults(
  workflowId: string | undefined,
): WorkflowMeasurementSnapshot | undefined {
  return workflowId ? snapshots.get(workflowId) : undefined;
}

export function subscribeWorkflowResults(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function latestServerMeasurement(
  workflowId: string | undefined,
  nodeId: string | undefined,
): ServerNodeMeasurement | undefined {
  if (!workflowId || !nodeId) return undefined;
  return snapshots.get(workflowId)?.latestByNode[nodeId];
}

/** Parameters for a qubit across every recorded node, newest result first. */
export function serverQubitParameters(
  workflowId: string | undefined,
  qubit: string,
): QubitParameters | undefined {
  const snapshot = getWorkflowResults(workflowId);
  if (!snapshot) return undefined;
  const wanted = qubit.toUpperCase();
  const ordered = [...snapshot.results].sort(
    (a, b) => b.capturedAt.localeCompare(a.capturedAt) || b.attempt - a.attempt,
  );
  for (const result of ordered) {
    for (const [key, value] of Object.entries(result.parameters)) {
      if (key.toUpperCase() === wanted) return value;
    }
  }
  return undefined;
}

export async function fetchWorkflowResults(
  workflowId: string,
): Promise<NodeResultPayload[]> {
  const payload = await apiRequest<{ items: NodeResultPayload[] }>(
    `${BASE}/workflows/${encodeURIComponent(workflowId)}/results`,
  );
  return Array.isArray(payload.items) ? payload.items : [];
}

// ---------------------------------------------------- parameter publication

/**
 * One field a controller failed to echo back after a parameter write. The
 * backend only reports requested fields, so an extra readback field is not a
 * mismatch.
 */
export interface PublicationMismatch {
  qubit: string | null;
  field: string | null;
  requested: number | null;
  readback: number | null;
  reason: string;
  message: string | null;
}

export interface PublicationPayload {
  adapter: string;
  controller: string;
  status: string;
  verified: boolean;
  requested: Record<string, QubitParameters>;
  readback: Record<string, QubitParameters> | null;
  mismatches: PublicationMismatch[];
  publishedAt: string;
}

/** `GET /workflows/{id}/parameters` item: latest attempt per node. */
export interface NodeParameterPayload {
  nodeId: string;
  attempt: number;
  stage: string;
  capturedAt: string;
  fidelity: number | null;
  parameters: Record<string, QubitParameters>;
  publication: PublicationPayload | null;
  publicationVerified: boolean | null;
}

export interface NodeParameterSnapshot {
  workflowId: string;
  items: NodeParameterPayload[];
  latestByNode: Record<string, NodeParameterPayload>;
  loadedAt: number;
}

function publicationOf(value: unknown): PublicationPayload | null {
  const record = objectOf(value);
  if (typeof record.adapter !== "string") return null;
  if (typeof record.controller !== "string") return null;
  const requested = objectOf(record.requested) as Record<
    string,
    QubitParameters
  >;
  const readbackValue = objectOf(record.readback);
  const readback =
    Object.keys(readbackValue).length > 0
      ? (readbackValue as Record<string, QubitParameters>)
      : null;
  const mismatches = Array.isArray(record.mismatches)
    ? record.mismatches
        .map((item) => objectOf(item))
        .filter((item) => typeof item.reason === "string")
        .map((item) => ({
          qubit: typeof item.qubit === "string" ? item.qubit : null,
          field: typeof item.field === "string" ? item.field : null,
          requested: number(item.requested),
          readback: number(item.readback),
          reason: item.reason as string,
          message: typeof item.message === "string" ? item.message : null,
        }))
    : [];
  return {
    adapter: record.adapter,
    controller: record.controller,
    status:
      typeof record.status === "string"
        ? record.status
        : record.verified === true
          ? "verified"
          : "mismatch",
    verified: record.verified === true,
    requested,
    readback,
    mismatches,
    publishedAt:
      typeof record.publishedAt === "string" ? record.publishedAt : "",
  };
}

function normalizeParameters(
  payload: NodeParameterPayload,
): NodeParameterPayload {
  return {
    nodeId: payload.nodeId,
    attempt: payload.attempt,
    stage: payload.stage,
    capturedAt: payload.capturedAt,
    fidelity: number(payload.fidelity),
    parameters: objectOf(payload.parameters) as Record<string, QubitParameters>,
    publication: publicationOf(payload.publication),
    publicationVerified: boolOrNull(payload.publicationVerified),
  };
}

export function buildParameterSnapshot(
  workflowId: string,
  payloads: NodeParameterPayload[],
  loadedAt = Date.now(),
): NodeParameterSnapshot {
  const items = payloads.map((payload) => normalizeParameters(payload));
  const latestByNode: Record<string, NodeParameterPayload> = {};
  for (const item of items) {
    const current = latestByNode[item.nodeId];
    if (
      !current ||
      item.attempt > current.attempt ||
      (item.attempt === current.attempt && item.capturedAt >= current.capturedAt)
    ) {
      latestByNode[item.nodeId] = item;
    }
  }
  return { workflowId, items, latestByNode, loadedAt };
}

const parameterSnapshots = new Map<string, NodeParameterSnapshot>();

export function publishWorkflowParameters(
  workflowId: string,
  payloads: NodeParameterPayload[],
): NodeParameterSnapshot {
  const snapshot = buildParameterSnapshot(workflowId, payloads);
  parameterSnapshots.set(workflowId, snapshot);
  while (parameterSnapshots.size > MAX_CACHED_WORKFLOWS) {
    const oldest = [...parameterSnapshots.values()].sort(
      (a, b) => a.loadedAt - b.loadedAt,
    )[0];
    if (!oldest) break;
    parameterSnapshots.delete(oldest.workflowId);
  }
  emit();
  return snapshot;
}

export function getWorkflowParameters(
  workflowId: string | undefined,
): NodeParameterSnapshot | undefined {
  return workflowId ? parameterSnapshots.get(workflowId) : undefined;
}

/** Latest parameter snapshot for one node, regardless of the current attempt. */
export function latestNodeParameters(
  workflowId: string | undefined,
  nodeId: string | undefined,
): NodeParameterPayload | undefined {
  if (!workflowId || !nodeId) return undefined;
  return parameterSnapshots.get(workflowId)?.latestByNode[nodeId];
}

/**
 * The publication record for one node, only when it belongs to the attempt
 * being displayed. A retry must not show the previous attempt's readback.
 */
export function latestNodePublication(
  workflowId: string | undefined,
  nodeId: string | undefined,
  attempt: number,
): PublicationPayload | null {
  const item = latestNodeParameters(workflowId, nodeId);
  if (!item || item.attempt !== attempt) return null;
  return item.publication;
}

export async function fetchWorkflowParameters(
  workflowId: string,
): Promise<NodeParameterPayload[]> {
  const payload = await apiRequest<{ items: NodeParameterPayload[] }>(
    `${BASE}/workflows/${encodeURIComponent(workflowId)}/parameters`,
  );
  return Array.isArray(payload.items) ? payload.items : [];
}
