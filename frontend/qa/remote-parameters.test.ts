import test from "node:test";
import assert from "node:assert/strict";
import type { Workflow } from "../src/types.ts";
import { createWorkflow } from "../src/lib/simulator.ts";
import { buildNodeParameters, selectParameterNode } from "../src/lib/nodeParameters.ts";
import {
  buildParameterSnapshot,
  clearWorkflowResults,
  latestNodePublication,
  publishWorkflowParameters,
} from "../src/lib/remoteMeasurements.ts";
import type {
  NodeParameterPayload,
  PublicationPayload,
} from "../src/lib/remoteMeasurements.ts";

function workflowWithFirstNodeDone(): Workflow {
  const workflow = createWorkflow({
    name: "Remote parameters QA",
    machine: "M7",
    experiment: "single",
    qubits: 1,
    target: "Q12",
    type: "adaptive",
    policy: "threshold",
    scenario: "normal",
  });
  const nodes = workflow.nodes.map((node, index) =>
    index === 0
      ? { ...node, status: "success" as const, attempt: 1, progress: 100 }
      : node,
  );
  return { ...workflow, nodes, status: "running" };
}

function publication(
  overrides: Partial<PublicationPayload> = {},
): PublicationPayload {
  return {
    adapter: "simulator",
    controller: "simulated-controller-v1",
    status: "verified",
    verified: true,
    requested: { Q12: { f01Ghz: 4.71, t1Us: 41.2 } },
    readback: { Q12: { f01Ghz: 4.71, t1Us: 41.2 } },
    mismatches: [],
    publishedAt: "2026-11-05T02:03:01.000Z",
    ...overrides,
  };
}

function item(
  nodeId: string,
  overrides: Partial<NodeParameterPayload> = {},
): NodeParameterPayload {
  return {
    nodeId,
    attempt: 1,
    stage: "completed",
    capturedAt: "2026-11-05T02:03:00.000Z",
    fidelity: 0.991,
    parameters: {
      Q12: {
        f01Ghz: 4.71,
        readoutGhz: 6.9,
        t1Us: 41.2,
        t2Us: 38.5,
        customSetting: 7,
      },
    },
    publication: publication(),
    publicationVerified: true,
    ...overrides,
  };
}

test("parameter snapshots normalize the publication and keep the newest attempt", () => {
  const snapshot = buildParameterSnapshot("wf-qa", [
    item("T1Idle", { attempt: 1, capturedAt: "2026-11-05T01:00:00.000Z" }),
    item("T1Idle", {
      attempt: 2,
      capturedAt: "2026-11-05T02:00:00.000Z",
      publicationVerified: false,
      publication: publication({ verified: false, status: "mismatch" }),
    }),
  ]);
  assert.equal(snapshot.items.length, 2);
  const latest = snapshot.latestByNode.T1Idle;
  assert.equal(latest.attempt, 2);
  assert.equal(latest.publicationVerified, false);
  assert.equal(latest.publication?.verified, false);
  assert.equal(latest.parameters.Q12.t1Us, 41.2);
});

test("a publication with no readback is never normalized as verified", () => {
  const snapshot = buildParameterSnapshot("wf-qa", [
    item("T1Idle", {
      publication: publication({
        verified: false,
        status: "mismatch",
        readback: null,
        mismatches: [
          {
            qubit: null,
            field: null,
            requested: null,
            readback: null,
            reason: "readback_unavailable",
            message: "controller offline",
          },
        ],
      }),
      publicationVerified: false,
    }),
  ]);
  const record = snapshot.latestByNode.T1Idle.publication!;
  assert.equal(record.verified, false);
  assert.equal(record.readback, null);
  assert.equal(record.mismatches[0].reason, "readback_unavailable");
  assert.equal(record.mismatches[0].message, "controller offline");
});

test("recorded parameters and the readback verdict drive the parameter panel", () => {
  const workflow = workflowWithFirstNodeDone();
  const node = selectParameterNode(workflow)!;
  publishWorkflowParameters(workflow.id, [item(node.id)]);

  const snapshot = buildNodeParameters(workflow)!;
  assert.equal(snapshot.dataSource, "server");
  assert.equal(snapshot.publicationVerified, true);
  assert.equal(snapshot.publication?.adapter, "simulator");
  assert.equal(snapshot.raw.hardwareTelemetryConnected, false);
  assert.equal(snapshot.raw.serverParameters?.Q12.t1Us, 41.2);
  assert.deepEqual(
    snapshot.results.map((value) => value.key),
    [
      "server_Q12_f01Ghz",
      "server_Q12_readoutGhz",
      "server_Q12_t1Us",
      "server_Q12_t2Us",
      "server_Q12_customSetting",
    ],
  );
  assert.ok(snapshot.results.every((value) => value.source === "服务端参数"));
  clearWorkflowResults();
});

test("a real controller publication marks hardware telemetry as connected", () => {
  const workflow = workflowWithFirstNodeDone();
  const node = selectParameterNode(workflow)!;
  publishWorkflowParameters(workflow.id, [
    item(node.id, {
      publication: publication({
        adapter: "http",
        controller: "http://127.0.0.1:9000",
      }),
    }),
  ]);
  const snapshot = buildNodeParameters(workflow)!;
  assert.equal(snapshot.raw.hardwareTelemetryConnected, true);
  assert.equal(snapshot.publication?.controller, "http://127.0.0.1:9000");
  clearWorkflowResults();
});

test("a readback mismatch is surfaced and never treated as accepted", () => {
  const workflow = workflowWithFirstNodeDone();
  const node = selectParameterNode(workflow)!;
  publishWorkflowParameters(workflow.id, [
    item(node.id, {
      publication: publication({
        verified: false,
        status: "mismatch",
        readback: { Q12: { f01Ghz: 4.712, t1Us: 41.2 } },
        mismatches: [
          {
            qubit: "Q12",
            field: "f01Ghz",
            requested: 4.71,
            readback: 4.712,
            reason: "value_mismatch",
            message: null,
          },
        ],
      }),
      publicationVerified: false,
    }),
  ]);
  const snapshot = buildNodeParameters(workflow)!;
  assert.equal(snapshot.publicationVerified, false);
  assert.equal(snapshot.publication?.mismatches[0].reason, "value_mismatch");
  assert.equal(snapshot.publication?.mismatches[0].requested, 4.71);
  clearWorkflowResults();
});

test("a stale publication from the previous attempt is not shown for a retry", () => {
  const workflow = workflowWithFirstNodeDone();
  const node = selectParameterNode(workflow)!;
  publishWorkflowParameters(workflow.id, [item(node.id, { attempt: 1 })]);
  const retried: Workflow = {
    ...workflow,
    nodes: workflow.nodes.map((current) =>
      current.id === node.id
        ? { ...current, status: "running" as const, attempt: 2, progress: 20 }
        : current,
    ),
  };
  const snapshot = buildNodeParameters(retried)!;
  assert.equal(snapshot.dataSource, "simulated");
  assert.equal(snapshot.publication, null);
  assert.equal(latestNodePublication(workflow.id, node.id, 2), null);
  assert.equal(
    latestNodePublication(workflow.id, node.id, 1)?.verified,
    true,
  );
  clearWorkflowResults();
});

test("an unexecuted node stays an explicit demo preview", () => {
  const workflow = createWorkflow({
    name: "Preview",
    machine: "M7",
    experiment: "single",
    qubits: 1,
    target: "Q12",
    type: "adaptive",
    policy: "threshold",
    scenario: "normal",
  });
  const snapshot = buildNodeParameters(workflow)!;
  assert.equal(snapshot.stage, "preview");
  assert.equal(snapshot.dataSource, "simulated");
  assert.equal(snapshot.publication, null);
  assert.equal(snapshot.raw.serverParameters, null);
  assert.ok(snapshot.raw.previewValues);
});
