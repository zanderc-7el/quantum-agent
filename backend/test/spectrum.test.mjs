import test from "node:test";
import assert from "node:assert/strict";
import { makeService, PROFILE, ApiError } from "../src/core.mjs";
import { sampleSpectrum, analyzeSpectrum } from "../src/skills/f01-spectrum.mjs";

const readout = () => ({ blueprintId: "single", blueprintVersion: 1, targetQubitId: "Q12", deviceId: PROFILE.id, recipeId: "readout_resonator_scan",
  recipeVersion: 1, motivation: "建立读出频率上游参数",
  parameters: { centerHz: PROFILE.centerHz, spanHz: 40_000_000, points: 41 } });
const spectrum = revision => ({ blueprintId: "single", blueprintVersion: 1, targetQubitId: "Q12", deviceId: PROFILE.id, recipeId: "f01_spectroscopy",
  recipeVersion: 1, motivation: "基于已发布读出频率寻找 F01", sourceReadoutRevision: revision,
  parameters: { centerHz: 5_100_000_000, spanHz: 100_000_000, points: 101 } });
function setup() {
  let saved;
  let counter = 0;
  const service = makeService({ load: () => saved, save: state => { saved = structuredClone(state); },
    now: () => "2026-11-05T08:00:00Z", id: () => `id-${++counter}` });
  return service;
}
function finish(service, planId, key) {
  const run = service.start(planId, key);
  while (["queued", "running"].includes(service.getRun(run.runId).status)) service.step(run.runId);
  return service.getRun(run.runId);
}

test("spectroscopy requires a published, evidence-linked S21 revision", () => {
  const service = setup();
  assert.throws(() => service.createPlan(spectrum(2), "spectrum-too-early-123"), e => e instanceof ApiError && e.code === "STALE_VERSION");
  const s21 = finish(service, service.createPlan(readout(), "s21-plan-123").planId, "s21-run-123");
  const candidate = service.createCandidate(s21.runId, "s21-candidate-123");
  service.approveCandidate(candidate.candidateId, "lab-reviewer", "s21-approve-123");
  const published = service.publishCandidate(candidate.candidateId, 1, "s21-publish-123");
  const plan = service.createPlan(spectrum(published.revision), "spectrum-plan-123");
  assert.equal(plan.upstream.revision, 2);
  assert.equal(plan.upstream.sourceRunId, s21.runId);
  assert.equal(plan.upstream.artifactSha256, s21.artifact.sha256);
  const result = finish(service, plan.planId, "spectrum-run-123");
  assert.equal(result.status, "completed");
  assert.equal(result.analysis.quality, "pass");
  assert.equal(result.analysis.method, "discrete-maximum-demo-v1");
  assert.equal(result.decision.evidence.sourceReadoutRevision, 2);
  assert.equal(result.decision.evidence.sourceReadoutArtifactSha256, s21.artifact.sha256);
  assert.equal(result.decision.publishAllowed, false);
  assert.throws(() => service.createCandidate(result.runId, "bad-spectrum-candidate-123"), e => e.code === "INVALID_TRANSITION");
});

test("stale upstream revision blocks a spectroscopy plan before execution", () => {
  const service = setup();
  const s21 = finish(service, service.createPlan(readout(), "stale-s21-plan-123").planId, "stale-s21-run-123");
  const c = service.createCandidate(s21.runId, "stale-s21-candidate-123");
  service.approveCandidate(c.candidateId, "lab-reviewer", "stale-s21-approve-123");
  service.publishCandidate(c.candidateId, 1, "stale-s21-publish-123");
  const plan = service.createPlan(spectrum(2), "stale-spectrum-plan-123");
  service.rollbackParameter(1, 2, "lab-reviewer", "stale-rollback-123");
  assert.throws(() => service.start(plan.planId, "stale-spectrum-run-123"), e => e.code === "STALE_VERSION");
});

test("weak or boundary spectrum remains inconclusive", () => {
  const p = spectrum(2);
  assert.equal(analyzeSpectrum(sampleSpectrum(p)).quality, "pass");
  const edge = { ...p, parameters: { centerHz: 5_080_000_000, spanHz: 10_000_000, points: 11 } };
  assert.equal(analyzeSpectrum(sampleSpectrum(edge)).reason, "BOUNDARY_MAXIMUM");
});

test("boundary F01 result proposes a traceable wider follow-up", () => {
  const service = setup();
  const s21 = finish(service, service.createPlan(readout(), "edge-s21-plan-123").planId, "edge-s21-run-123");
  const candidate = service.createCandidate(s21.runId, "edge-s21-candidate-123");
  service.approveCandidate(candidate.candidateId, "lab-reviewer", "edge-s21-approve-123");
  service.publishCandidate(candidate.candidateId, 1, "edge-s21-publish-123");
  const input = spectrum(2);
  input.parameters = { centerHz: 5_080_000_000, spanHz: 10_000_000, points: 11 };
  const first = finish(service, service.createPlan(input, "edge-spectrum-plan-123").planId, "edge-spectrum-run-123");
  assert.equal(first.status, "failed");
  assert.equal(first.decision.action, "retry_wider_scan");
  const followup = service.createFollowupPlan(first.runId, "edge-spectrum-followup-123");
  assert.equal(followup.parentRunId, first.runId);
  assert.equal(followup.sourceArtifactSha256, first.artifact.sha256);
  assert.equal(followup.input.sourceReadoutRevision, 2);
  assert.equal(followup.upstream.artifactSha256, s21.artifact.sha256);
  assert.equal(followup.input.parameters.spanHz, 20_000_000);
});
