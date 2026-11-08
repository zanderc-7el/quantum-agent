import test from "node:test";
import assert from "node:assert/strict";
import { makeService, validatePlan, sample, analyze, ApiError, PROFILE } from "../src/core.mjs";
import { decideReadoutS21 } from "../src/skills/readout-s21.mjs";

const input = () => ({ blueprintId: "single", blueprintVersion: 1, targetQubitId: "Q12", deviceId: PROFILE.id, recipeId: "readout_resonator_scan", recipeVersion: 1,
  motivation: "验证 Q12 读出谐振频率",
  parameters: { centerHz: PROFILE.centerHz, spanHz: 40_000_000, points: 41 } });
function fixture() {
  let persisted;
  let counter = 0;
  const service = makeService({ load: () => persisted, save: value => { persisted = structuredClone(value); },
    now: () => "2026-11-05T08:00:00Z", id: () => `id-${++counter}` });
  return { service, persisted: () => persisted };
}

test("validates profile range and refuses unknown fields", () => {
  assert.deepEqual(validatePlan(input()), input());
  assert.throws(() => validatePlan({ ...input(), blueprintId: "unknown" }), e => e.code === "BLUEPRINT_VERSION_UNSUPPORTED");
  assert.throws(() => validatePlan({ ...input(), blueprintVersion: 2 }), e => e.code === "BLUEPRINT_VERSION_UNSUPPORTED");
  assert.throws(() => validatePlan({ ...input(), blueprintId: "single", targetQubitId: "Q13" }), e => e.code === "BLUEPRINT_SCOPE_DENIED");
  assert.throws(() => validatePlan({ ...input(), blueprintId: undefined }), e => e.code === "BLUEPRINT_VERSION_UNSUPPORTED");
  assert.throws(() => validatePlan({ ...input(), extra: true }), e => e instanceof ApiError && e.code === "INVALID_INPUT");
  assert.throws(() => validatePlan({ ...input(), parameters: { ...input().parameters, spanHz: 3_000_000_000 } }), e => e.code === "INVALID_INPUT");
});

test("a run preserves chunks, analysis, sequence and idempotency", () => {
  const { service, persisted } = fixture();
  const p = service.createPlan(input(), "plan-key-123");
  assert.equal(service.createPlan(input(), "plan-key-123").planId, p.planId);
  assert.throws(() => service.createPlan({ ...input(), parameters: { ...input().parameters, points: 51 } }, "plan-key-123"), e => e.code === "IDEMPOTENCY_CONFLICT");
  const started = service.start(p.planId, "start-key-123");
  assert.equal(service.start(p.planId, "start-key-123").runId, started.runId);
  while (["queued", "running"].includes(service.getRun(started.runId).status)) service.step(started.runId);
  const run = service.getRun(started.runId);
  assert.equal(run.status, "completed");
  assert.equal(run.points.length, 41);
  assert.equal(run.analysis.quality, "pass");
  assert.equal(run.analysis.method, "discrete-minimum-demo-v1");
  assert.equal(run.decision.action, "accept_candidate");
  assert.equal(run.decision.publishAllowed, false);
  assert.equal(run.decision.evidence.artifactSha256, run.artifact.sha256);
  assert.equal(run.artifact.complete, true);
  assert.equal(run.artifact.pointCount, 41);
  assert.equal(run.artifact.sha256.length, 64);
  assert.equal(service.getMeasurements(run.attemptId).artifact.sha256, run.artifact.sha256);
  assert.equal(service.getRunSummary(run.runId).pointCount, 41);
  assert.equal(service.getRunSummary(run.runId).points, undefined);
  assert.equal(service.getAnalysis(run.attemptId).analysis.quality, "pass");
  assert.deepEqual(run.events.map(e => e.eventSeq), Array.from({ length: run.events.length }, (_, i) => i + 1));
  assert.deepEqual(service.events(run.runId, run.events.length - 1), [run.events.at(-1)]);
  assert.equal(persisted().runs[run.runId].status, "completed");
});

test("resource contention and stop confirmation", () => {
  const { service } = fixture();
  const p1 = service.createPlan(input(), "plan-one-123");
  const p2 = service.createPlan(input(), "plan-two-123");
  const r1 = service.start(p1.planId, "start-one-123");
  assert.throws(() => service.start(p2.planId, "start-two-123"), e => e.code === "RESOURCE_BUSY");
  service.step(r1.runId);
  const stopped = service.stop(r1.runId, "stop-one-123");
  assert.equal(stopped.stopConfirmed, true);
  assert.equal(service.getRun(r1.runId).status, "stopped");
  assert.equal(service.getRun(r1.runId).points.length, 10);
  assert.equal(service.getMeasurements(r1.attemptId).artifact.complete, false);
  assert.equal(service.start(p2.planId, "start-two-123").status, "queued");
});

test("incomplete or boundary result cannot become a candidate", () => {
  assert.equal(analyze([]).quality, "inconclusive");
  assert.equal(analyze(sample({ parameters: { centerHz: 5_000_100_000, spanHz: 200_000, points: 11 } })).quality, "inconclusive");
  const boundaryInput = { ...input(), parameters: { centerHz: 6_000_000_000, spanHz: 200_000, points: 11 } };
  const boundaryPoints = sample(boundaryInput);
  const decision = decideReadoutS21({ analysis: analyze(boundaryPoints), points: boundaryPoints,
    input: boundaryInput, artifact: { complete: true, artifactId: "a", sha256: "hash" } });
  assert.equal(decision.action, "retry_wider_scan");
  assert.equal(decision.proposal.spanHz, 400_000);
  assert.equal(decideReadoutS21({ analysis: analyze(boundaryPoints), points: boundaryPoints,
    input: boundaryInput, artifact: { complete: true, artifactId: "a", sha256: "hash" }, followupDepth: 4 }).reason,
    "RETRY_BUDGET_EXHAUSTED");
  assert.equal(decideReadoutS21({ analysis: null, points: [], input: input(), artifact: null }).action, "human_review");
});

test("restart marks unfinished run failed", () => {
  const { service, persisted } = fixture();
  const p = service.createPlan(input(), "plan-restart-123");
  const r = service.start(p.planId, "start-restart-123");
  const restored = makeService({ load: persisted, save: () => {}, now: () => "2026-11-05T08:01:00Z" });
  assert.equal(restored.getRun(r.runId).status, "failed");
  assert.equal(restored.getRun(r.runId).failure, "SERVICE_RESTARTED");
  assert.equal(restored.events(r.runId).at(-1).payload.status, "failed");
});

test("restart records an unresolved device command once without replaying it", () => {
  const { service, persisted } = fixture();
  const p = service.createPlan(input(), "pending-command-plan-123");
  const r = service.start(p.planId, "pending-command-run-123");
  const saved = structuredClone(persisted());
  saved.runs[r.runId].events.push({ eventSeq: 2, runId: r.runId, attemptId: r.attemptId,
    kind: "device.command_requested", occurredAt: "2026-11-05T08:00:00Z",
    payload: { commandId: "pending-command-1", command: "acquire_chunk", offset: 0 } });
  let recovered;
  const restored = makeService({ load: () => saved, save: value => { recovered = structuredClone(value); },
    now: () => "2026-11-05T08:01:00Z" });
  assert.equal(restored.getRun(r.runId).status, "failed");
  assert.equal(restored.events(r.runId).filter(item => item.kind === "device.command_outcome_unknown").length, 1);
  makeService({ load: () => recovered, save: () => {}, now: () => "2026-11-05T08:02:00Z" });
  assert.equal(recovered.runs[r.runId].events.filter(item => item.kind === "device.command_outcome_unknown").length, 1);
});

test("an old persisted plan cannot bypass blueprint scope at execution", () => {
  const { service, persisted } = fixture();
  const created = service.createPlan(input(), "legacy-plan-123");
  const saved = structuredClone(persisted());
  delete saved.plans[created.planId].input.blueprintId;
  delete saved.plans[created.planId].input.blueprintVersion;
  const restored = makeService({ load: () => saved, save: () => {} });
  assert.throws(() => restored.start(created.planId, "legacy-start-123"),
    error => error.code === "BLUEPRINT_VERSION_UNSUPPORTED");
});

test("typed S21 retry edge creates an auditable bounded follow-up chain", () => {
  const { service } = fixture();
  const narrow = { ...input(), parameters: { centerHz: PROFILE.centerHz, spanHz: 1_000_000, points: 11 } };
  let plan = service.createPlan(narrow, "narrow-plan-123");
  let attempts = 0;
  while (true) {
    const started = service.start(plan.planId, `retry-start-${attempts}-123`);
    while (["queued", "running"].includes(service.getRun(started.runId).status)) service.step(started.runId);
    const result = service.getRun(started.runId);
    if (result.decision.action === "accept_candidate") {
      assert.ok(attempts > 0);
      assert.equal(result.status, "completed");
      break;
    }
    assert.equal(result.decision.action, "retry_wider_scan");
    const followup = service.createFollowupPlan(started.runId, `followup-${attempts}-123`);
    assert.equal(service.createFollowupPlan(started.runId, `followup-${attempts}-123`).planId, followup.planId);
    assert.equal(followup.parentRunId, started.runId);
    assert.equal(followup.sourceArtifactSha256, result.artifact.sha256);
    assert.equal(followup.followupDepth, attempts + 1);
    assert.equal(followup.input.parameters.spanHz, plan.input.parameters.spanHz * 2);
    assert.ok(service.events(started.runId).some(event => event.kind === "skill.followup_planned"));
    assert.throws(() => service.createFollowupPlan(started.runId, `different-${attempts}-123`), error => error.code === "STALE_VERSION");
    plan = followup;
    attempts++;
    assert.ok(attempts <= 4);
  }
});

test("simulated parameter candidate requires gate, approval, revision check and preserves rollback history", () => {
  const { service } = fixture();
  const failedPlan = service.createPlan({ ...input(), parameters: { centerHz: PROFILE.centerHz, spanHz: 1_000_000, points: 11 } }, "parameter-fail-plan-123");
  const failedRun = service.start(failedPlan.planId, "parameter-fail-run-123");
  while (["queued", "running"].includes(service.getRun(failedRun.runId).status)) service.step(failedRun.runId);
  assert.throws(() => service.createCandidate(failedRun.runId, "parameter-fail-candidate-123"), e => e.code === "QUALITY_GATE_FAILED");

  const goodPlan = service.createPlan(input(), "parameter-good-plan-123");
  const goodRun = service.start(goodPlan.planId, "parameter-good-run-123");
  while (["queued", "running"].includes(service.getRun(goodRun.runId).status)) service.step(goodRun.runId);
  const candidate = service.createCandidate(goodRun.runId, "parameter-candidate-123");
  assert.equal(candidate.baselineRevision, 1);
  assert.equal(service.createCandidate(goodRun.runId, "parameter-candidate-123").candidateId, candidate.candidateId);
  assert.throws(() => service.publishCandidate(candidate.candidateId, 1, "parameter-unapproved-123"), e => e.code === "INVALID_TRANSITION");
  const approved = service.approveCandidate(candidate.candidateId, "lab-reviewer", "parameter-approve-123");
  assert.equal(approved.status, "approved");
  assert.throws(() => service.publishCandidate(candidate.candidateId, 2, "parameter-stale-123"), e => e.code === "STALE_VERSION");
  const published = service.publishCandidate(candidate.candidateId, 1, "parameter-publish-123");
  assert.equal(published.revision, 2);
  assert.equal(service.readbackParameter().hardwareReadback, false);
  assert.equal(service.readbackParameter().valueHz, candidate.valueHz);
  assert.equal(service.parameterState().revisions.length, 2);
  assert.equal(service.parameterState().candidates[candidate.candidateId].status, "published");
  assert.throws(() => service.rollbackParameter(1, 1, "lab-reviewer", "parameter-rollback-stale-123"), e => e.code === "STALE_VERSION");
  const rolledBack = service.rollbackParameter(1, 2, "lab-reviewer", "parameter-rollback-123");
  assert.equal(rolledBack.revision, 3);
  assert.equal(rolledBack.valueHz, PROFILE.centerHz);
  assert.equal(service.parameterState().revisions.length, 3);
});
