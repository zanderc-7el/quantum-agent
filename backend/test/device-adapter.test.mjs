import test from "node:test";
import assert from "node:assert/strict";
import { makeService, PROFILE } from "../src/core.mjs";
import { createSimulatedDevice } from "../src/device-adapter.mjs";

const planInput = () => ({ blueprintId: "single", blueprintVersion: 1, targetQubitId: "Q12", deviceId: PROFILE.id, recipeId: "readout_resonator_scan",
  recipeVersion: 1, motivation: "模拟已接入设备控制器的读出扫描",
  parameters: { centerHz: PROFILE.centerHz, spanHz: 40_000_000, points: 41 } });
function setup(fault) {
  let saved;
  let counter = 0;
  const options = { load: () => saved, save: value => { saved = structuredClone(value); },
    id: () => `adapter-id-${++counter}`, deviceFactory: input => createSimulatedDevice({ ...input, fault }) };
  return { service: makeService(options), restore: () => makeService(options) };
}
function start(service, suffix) {
  const plan = service.createPlan(planInput(), `adapter-plan-${suffix}`);
  return service.start(plan.planId, `adapter-start-${suffix}`);
}

test("device gateway receipts accompany acquired chunks and disclose simulation", () => {
  const { service } = setup(null);
  assert.equal(service.device().hardwareConnected, false);
  const run = start(service, "normal-123");
  while (["queued", "running"].includes(service.getRun(run.runId).status)) service.step(run.runId);
  const result = service.getRun(run.runId);
  assert.equal(result.status, "completed");
  assert.equal(result.events.find(item => item.kind === "measurement.chunk").payload.receipt.command, "acquire_chunk");
  const requested = result.events.filter(item => item.kind === "device.command_requested");
  const acknowledged = result.events.filter(item => item.kind === "device.command_acknowledged");
  assert.equal(requested.length, acknowledged.length);
  assert.ok(requested.length > 0);
  assert.equal(requested[0].payload.commandId, acknowledged[0].payload.commandId);
});

test("acquisition interruption finalizes incomplete evidence and releases the resource", () => {
  const { service } = setup("acquisition_interrupt");
  const first = start(service, "interrupt-123");
  service.step(first.runId);
  service.step(first.runId);
  const result = service.getRun(first.runId);
  assert.equal(result.status, "failed");
  assert.equal(result.failure, "DEVICE_ACQUISITION_FAILED");
  assert.equal(result.artifact.complete, false);
  assert.equal(result.points.length, 10);
  assert.ok(result.events.some(item => item.kind === "device.error"));
  assert.equal(start(service, "after-interrupt-123").status, "queued");
});

test("unconfirmed stop retains resource through restart until acknowledged", () => {
  const fixture = setup("stop_unconfirmed");
  const run = start(fixture.service, "stop-123");
  const first = fixture.service.stop(run.runId, "adapter-stop-first-123");
  assert.equal(first.stopConfirmed, false);
  assert.equal(fixture.service.getRun(run.runId).status, "stopping");
  assert.throws(() => start(fixture.service, "blocked-123"), error => error.code === "RESOURCE_BUSY");
  const restored = fixture.restore();
  assert.equal(restored.getRun(run.runId).status, "stopping");
  assert.throws(() => start(restored, "restart-blocked-123"), error => error.code === "RESOURCE_BUSY");
  assert.equal(restored.stop(run.runId, "adapter-stop-retry-123").stopConfirmed, false);
  assert.equal(restored.stop(run.runId, "adapter-stop-retry-again-123").stopConfirmed, true);
  assert.equal(start(restored, "released-123").status, "queued");
});

test("readback mismatch prevents parameter revision publication", () => {
  const { service } = setup("readback_mismatch");
  const run = start(service, "readback-123");
  while (["queued", "running"].includes(service.getRun(run.runId).status)) service.step(run.runId);
  const candidate = service.createCandidate(run.runId, "adapter-candidate-123");
  service.approveCandidate(candidate.candidateId, "lab-reviewer", "adapter-approve-123");
  assert.throws(() => service.publishCandidate(candidate.candidateId, 1, "adapter-publish-123"),
    error => error.code === "READBACK_MISMATCH");
  assert.equal(service.parameterState().revisions.length, 1);
  assert.equal(service.parameterState().candidates[candidate.candidateId].status, "approved");
});
