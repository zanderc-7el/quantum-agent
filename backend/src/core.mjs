import { randomUUID, createHash } from "node:crypto";
import { READOUT_S21_SKILL, decideReadoutS21 } from "./skills/readout-s21.mjs";
import { SPECTRUM_PROFILE, F01_SPECTRUM_SKILL, sampleSpectrum, analyzeSpectrum, decideSpectrum } from "./skills/f01-spectrum.mjs";
import { initialParameterState, currentParameter, buildCandidate, approveCandidate, publishCandidate, rollbackParameter } from "./parameters.mjs";
import { createSimulatedDevice } from "./device-adapter.mjs";
import { assertBlueprintCapability } from "./blueprint-policy.mjs";

export const PROFILE = Object.freeze({
  id: "sim-readout-v1", source: "deterministic-simulator", targetQubits: ["Q12"],
  centerHz: 6_000_000_000, minHz: 5_000_000_000, maxHz: 7_000_000_000,
  maxPoints: 601, resource: "sim-readout-chain-1",
});

export class ApiError extends Error {
  constructor(code, message, status = 400) { super(message); this.code = code; this.status = status; }
}

export function validatePlan(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new ApiError("INVALID_INPUT", "请求体必须是对象");
  const allowed = ["blueprintId", "blueprintVersion", "targetQubitId", "deviceId", "recipeId", "recipeVersion", "parameters", "motivation", "sourceReadoutRevision"];
  if (Object.keys(input).some(key => !allowed.includes(key))) throw new ApiError("INVALID_INPUT", "存在未知计划字段");
  try { assertBlueprintCapability({ blueprintId: input.blueprintId, blueprintVersion: input.blueprintVersion,
    recipeId: input.recipeId, target: input.targetQubitId }); }
  catch (error) { throw new ApiError(error.code ?? "BLUEPRINT_SCOPE_DENIED", error.message, 403); }
  if (input.targetQubitId !== "Q12" || input.deviceId !== PROFILE.id ||
      !["readout_resonator_scan", "f01_spectroscopy"].includes(input.recipeId) || input.recipeVersion !== 1)
    throw new ApiError("TOPOLOGY_UNRESOLVED", "模拟环境仅支持 Q12 与 sim-readout-v1");
  if (input.recipeId === "f01_spectroscopy" && (!Number.isInteger(input.sourceReadoutRevision) || input.sourceReadoutRevision < 2))
    throw new ApiError("INVALID_INPUT", "频谱计划需要已发布的读出参数版本");
  if (input.recipeId === "readout_resonator_scan" && "sourceReadoutRevision" in input)
    throw new ApiError("INVALID_INPUT", "读出扫描不能引用自身上游版本");
  if (typeof input.motivation !== "string" || input.motivation.trim().length < 4 || input.motivation.length > 500)
    throw new ApiError("INVALID_INPUT", "需提供 4–500 字符的实验意图说明");
  const p = input.parameters;
  if (!p || typeof p !== "object" || Array.isArray(p) || Object.keys(p).sort().join() !== "centerHz,points,spanHz")
    throw new ApiError("INVALID_INPUT", "参数仅允许 centerHz、spanHz、points");
  const range = input.recipeId === "f01_spectroscopy" ? SPECTRUM_PROFILE : PROFILE;
  if (![p.centerHz, p.spanHz, p.points].every(Number.isFinite) || !Number.isInteger(p.points) || p.points < 3 || p.points > range.maxPoints ||
      p.centerHz < range.minHz || p.centerHz > range.maxHz || p.spanHz <= 0 ||
      p.centerHz - p.spanHz / 2 < range.minHz || p.centerHz + p.spanHz / 2 > range.maxHz)
    throw new ApiError("INVALID_INPUT", "频率范围或采样点数超出模拟 Profile");
  return structuredClone(input);
}

export function sample(input) {
  const { centerHz, spanHz, points } = input.parameters;
  const resonanceHz = PROFILE.centerHz + 2_000_000;
  return Array.from({ length: points }, (_, index) => {
    const frequencyHz = centerHz - spanHz / 2 + spanHz * index / (points - 1);
    const detuning = (frequencyHz - resonanceHz) / 3_000_000;
    const magnitudeDb = -12 - 16 / (1 + detuning * detuning) + Math.sin(index * 1.7) * 0.025;
    return { frequencyHz, magnitudeDb: Number(magnitudeDb.toFixed(6)) };
  });
}

export function analyze(points) {
  if (points.length < 3 || points.some(p => !Number.isFinite(p.frequencyHz) || !Number.isFinite(p.magnitudeDb)))
    return { quality: "inconclusive", reason: "DATA_INCOMPLETE", candidateHz: null };
  let min = 0;
  for (let i = 1; i < points.length; i++) if (points[i].magnitudeDb < points[min].magnitudeDb) min = i;
  if (min === 0 || min === points.length - 1)
    return { quality: "inconclusive", reason: "BOUNDARY_MINIMUM", candidateHz: null };
  const depthDb = (points[0].magnitudeDb + points.at(-1).magnitudeDb) / 2 - points[min].magnitudeDb;
  if (depthDb < 3) return { quality: "inconclusive", reason: "NO_SIGNIFICANT_DIP", candidateHz: null };
  return { quality: "pass", reason: null, candidateHz: points[min].frequencyHz, depthDb: Number(depthDb.toFixed(3)),
    method: "discrete-minimum-demo-v1", note: "模拟候选；尚非物理拟合或可发布参数" };
}

export function makeService({ load, save, now = () => new Date().toISOString(), id = randomUUID,
  deviceFactory = options => createSimulatedDevice(options) }) {
  let state = load() ?? { schemaVersion: 1, plans: {}, runs: {}, idempotency: {} };
  state.parameters ??= initialParameterState(now);
  const device = deviceFactory({ readoutHz: currentParameter(state.parameters).valueHz });
  for (const run of Object.values(state.runs)) {
    const completed = new Set(run.events.filter(item => ["device.command_acknowledged", "device.error", "device.stop_unconfirmed", "device.command_outcome_unknown"].includes(item.kind))
      .map(item => item.payload.commandId).filter(Boolean));
    for (const request of run.events.filter(item => item.kind === "device.command_requested")) {
      if (!completed.has(request.payload.commandId))
        event(run, "device.command_outcome_unknown", { commandId: request.payload.commandId,
          command: request.payload.command, reason: "SERVICE_RESTARTED_BEFORE_RECEIPT" });
    }
  }
  for (const run of Object.values(state.runs)) if (["queued", "running"].includes(run.status)) {
    run.status = "failed"; run.failure = "SERVICE_RESTARTED"; run.finishedAt = now();
    finalizeArtifact(run, false);
    event(run, "measurement.finalized", run.artifact);
    event(run, "run.failed", { reason: "SERVICE_RESTARTED" });
    event(run, "run.status", { status: "failed" });
  }
  // A missing stop acknowledgement cannot be inferred from process restart.
  // Keep the resource reserved until a later explicit stop retry receives an acknowledgement.
  save(state);
  function commit() { save(state); }
  function event(run, kind, payload) {
    const item = { eventSeq: run.events.length + 1, runId: run.runId, attemptId: run.attemptId, kind, occurredAt: now(), payload };
    run.events.push(item);
    return item;
  }
  function finalizeArtifact(run, complete) {
    const canonical = JSON.stringify(run.points);
    run.artifact = { artifactId: `measurement-${run.attemptId}`, attemptId: run.attemptId,
      dataSource: "simulator", format: "json-points-v1", pointCount: run.points.length,
      sha256: createHash("sha256").update(canonical).digest("hex"), complete };
  }
  function plan(planId) { const value = state.plans[planId]; if (!value) throw new ApiError("NOT_FOUND", "未找到计划", 404); return value; }
  function run(runId) { const value = state.runs[runId]; if (!value) throw new ApiError("NOT_FOUND", "未找到运行", 404); return value; }
  function planRecord(input, provenance = null) {
    const validated = validatePlan(input);
    const baseline = currentParameter(state.parameters);
    if (validated.recipeId === "f01_spectroscopy" &&
        (validated.sourceReadoutRevision !== baseline.revision || !baseline.sourceArtifactSha256))
      throw new ApiError("STALE_VERSION", "频谱所需的已发布读出参数不存在或版本已变化", 409);
    const upstream = validated.recipeId === "f01_spectroscopy" ? {
      revision: baseline.revision, valueHz: baseline.valueHz, sourceRunId: baseline.sourceRunId,
      artifactSha256: baseline.sourceArtifactSha256 } : null;
    const value = { planId: id(), revision: 1, status: "preflight_passed", input: validated,
      resolvedTopologyRevision: 1, resolvedParameterRevision: baseline.revision, upstream,
      estimatedResources: [PROFILE.resource],
      preflight: { passed: true, checks: ["target", "range", "resource-profile"] },
      createdAt: now(), dataSource: "simulator", followupDepth: provenance?.followupDepth ?? 0,
      parentRunId: provenance?.parentRunId ?? null, sourceArtifactSha256: provenance?.sourceArtifactSha256 ?? null };
    state.plans[value.planId] = value;
    return value;
  }
  function idem(key, scope, payload, create) {
    if (typeof key !== "string" || key.length < 8 || key.length > 128) throw new ApiError("INVALID_INPUT", "需要 8–128 字符的 Idempotency-Key");
    const digest = createHash("sha256").update(JSON.stringify(payload)).digest("hex");
    const name = `${scope}:${key}`;
    const previous = state.idempotency[name];
    if (previous) {
      if (previous.digest !== digest) throw new ApiError("IDEMPOTENCY_CONFLICT", "幂等键对应不同请求", 409);
      return structuredClone(previous.response);
    }
    const response = create();
    state.idempotency[name] = { digest, response };
    commit();
    return structuredClone(response);
  }
  return {
    profile: () => PROFILE,
    device: () => device.descriptor(),
    skill: () => READOUT_S21_SKILL,
    spectrumSkill: () => F01_SPECTRUM_SKILL,
    parameterState: () => structuredClone(state.parameters),
    createCandidate(runId, key) { return idem(key, `candidate:${runId}`, {}, () => {
      const source = run(runId);
      const sourcePlan = plan(source.planId);
      validatePlan(sourcePlan.input);
      if (sourcePlan.input.recipeId !== "readout_resonator_scan")
        throw new ApiError("INVALID_TRANSITION", "该接口仅创建读出频率候选", 409);
      const candidate = buildCandidate({ sourceRun: source, parameterState: state.parameters, id, now });
      event(source, "parameter.candidate_created", { candidateId: candidate.candidateId,
        baselineRevision: candidate.baselineRevision, sourceArtifactSha256: candidate.sourceArtifactSha256 });
      return candidate;
    }); },
    approveCandidate(candidateId, actor, key) { return idem(key, `approve:${candidateId}`, { actor }, () => {
      const candidate = approveCandidate(state.parameters, candidateId, actor, now);
      event(run(candidate.sourceRunId), "parameter.candidate_approved", { candidateId, actor: candidate.approvedBy });
      return candidate;
    }); },
    publishCandidate(candidateId, expectedRevision, key) { return idem(key, `publish:${candidateId}`, { expectedRevision }, () => {
      const candidateSourceRunId = state.parameters.candidates[candidateId]?.sourceRunId;
      if (candidateSourceRunId) validatePlan(plan(run(candidateSourceRunId).planId).input);
      const proposed = structuredClone(state.parameters);
      const revision = publishCandidate(proposed, candidateId, expectedRevision, now);
      const previousHz = currentParameter(state.parameters).valueHz;
      device.writeReadoutFrequency(revision.valueHz);
      const measuredHz = device.readbackReadoutFrequency();
      if (measuredHz !== revision.valueHz) {
        device.writeReadoutFrequency(previousHz);
        throw new ApiError("READBACK_MISMATCH", "模拟设备回读与候选值不一致，参数版本未发布", 409);
      }
      state.parameters = proposed;
      const sourceRunId = proposed.candidates[candidateId].sourceRunId;
      event(run(sourceRunId), "parameter.simulator_published", { candidateId, revision: revision.revision,
        valueHz: revision.valueHz, sourceArtifactSha256: revision.sourceArtifactSha256,
        deviceReadbackHz: measuredHz });
      return revision;
    }); },
    readbackParameter() { const value = currentParameter(state.parameters);
      const readbackValueHz = device.readbackReadoutFrequency();
      return structuredClone({ ...value, readbackValueHz, match: readbackValueHz === value.valueHz,
        dataSource: "simulator", hardwareReadback: false, adapterId: device.descriptor().adapterId }); },
    rollbackParameter(targetRevision, expectedRevision, actor, key) {
      return idem(key, "rollback:readout_frequency", { targetRevision, expectedRevision, actor }, () => {
        const proposed = structuredClone(state.parameters);
        const revision = rollbackParameter(proposed, targetRevision, expectedRevision, actor, now);
        const previousHz = currentParameter(state.parameters).valueHz;
        device.writeReadoutFrequency(revision.valueHz);
        if (device.readbackReadoutFrequency() !== revision.valueHz) {
          device.writeReadoutFrequency(previousHz);
          throw new ApiError("READBACK_MISMATCH", "模拟设备回读与回滚目标不一致，参数版本未改变", 409);
        }
        state.parameters = proposed;
        return revision;
      });
    },
    createPlan(input, key) { return idem(key, "create-plan", input, () => {
      return planRecord(input);
    }); },
    getPlan: plan,
    createFollowupPlan(runId, key) { return idem(key, `followup:${runId}`, {}, () => {
      const source = run(runId);
      const parent = plan(source.planId);
      if (source.status !== "failed" || source.decision?.action !== "retry_wider_scan" || !source.artifact?.complete)
        throw new ApiError("INVALID_TRANSITION", "该运行没有可执行的扩大扫描建议", 409);
      if ((parent.followupDepth ?? 0) >= 4) throw new ApiError("RETRY_BUDGET_EXHAUSTED", "已达到模拟复测次数上限", 409);
      if (Object.values(state.plans).some(item => item.parentRunId === runId))
        throw new ApiError("STALE_VERSION", "后续计划已经创建", 409);
      const input = { ...parent.input, motivation: `边界结果复测；来源运行 ${runId}`,
        parameters: source.decision.proposal };
      const followup = planRecord(input, { followupDepth: (parent.followupDepth ?? 0) + 1,
        parentRunId: runId, sourceArtifactSha256: source.artifact.sha256 });
      event(source, "skill.followup_planned", { planId: followup.planId, action: source.decision.action,
        sourceArtifactSha256: source.artifact.sha256, parameters: followup.input.parameters });
      return followup;
    }); },
    start(planId, key) { return idem(key, `start:${planId}`, {}, () => {
      const p = plan(planId);
      validatePlan(p.input);
      if (p.input.recipeId === "f01_spectroscopy" && p.upstream.revision !== currentParameter(state.parameters).revision)
        throw new ApiError("STALE_VERSION", "上游读出参数版本已变化，请重新建频谱计划", 409);
      if (Object.values(state.runs).some(r => r.planId === planId)) throw new ApiError("STALE_VERSION", "计划已经启动", 409);
      if (Object.values(state.runs).some(r => ["queued", "running", "stopping"].includes(r.status))) throw new ApiError("RESOURCE_BUSY", "模拟读出资源被占用", 409);
      const value = { runId: id(), planId, attemptId: id(), status: "queued", revision: 1, events: [], points: [],
        analysis: null, failure: null, createdAt: now(), finishedAt: null, dataSource: "simulator", resource: PROFILE.resource };
      event(value, "run.status", { status: "queued" });
      state.runs[value.runId] = value;
      return { runId: value.runId, attemptId: value.attemptId, status: value.status, revision: value.revision };
    }); },
    getRun: run,
    getRunSummary(runId) {
      const { points, events, ...summary } = run(runId);
      return structuredClone({ ...summary, pointCount: points.length, lastEventSeq: events.length });
    },
    getMeasurements(attemptId) {
      const value = Object.values(state.runs).find(item => item.attemptId === attemptId);
      if (!value) throw new ApiError("NOT_FOUND", "未找到运行尝试", 404);
      return structuredClone({ attemptId, dataSource: "simulator", points: value.points, artifact: value.artifact ?? null,
        complete: value.artifact?.complete ?? false });
    },
    getAnalysis(attemptId) {
      const value = Object.values(state.runs).find(item => item.attemptId === attemptId);
      if (!value) throw new ApiError("NOT_FOUND", "未找到运行尝试", 404);
      return structuredClone({ attemptId, analysis: value.analysis });
    },
    events(runId, after = 0) { return run(runId).events.filter(e => e.eventSeq > after); },
    step(runId) {
      const value = run(runId);
      if (value.status === "queued") { value.status = "running"; value.revision++; event(value, "run.status", { status: "running" }); }
      if (value.status !== "running") return value;
      const experimentPlan = plan(value.planId);
      const spectroscopy = experimentPlan.input.recipeId === "f01_spectroscopy";
      const full = spectroscopy ? sampleSpectrum(experimentPlan.input) : sample(experimentPlan.input);
      const commandId = id();
      event(value, "device.command_requested", { commandId, command: "acquire_chunk", offset: value.points.length,
        requestedAt: now(), dataSource: "simulator" });
      commit();
      let acquired;
      try { acquired = device.acquireChunk({ full, offset: value.points.length, limit: 10, commandId }); }
      catch (cause) {
        value.status = "failed"; value.failure = "DEVICE_ACQUISITION_FAILED"; value.finishedAt = now(); value.revision++;
        finalizeArtifact(value, false);
        event(value, "device.error", { commandId, code: value.failure, detail: String(cause?.message ?? cause), adapterId: device.descriptor().adapterId });
        event(value, "measurement.finalized", value.artifact);
        event(value, "run.status", { status: "failed" });
        commit(); return structuredClone(value);
      }
      const next = acquired.points;
      event(value, "device.command_acknowledged", { commandId, receipt: acquired.receipt });
      value.points.push(...next);
      value.revision++;
      event(value, "measurement.chunk", { dataSource: "simulator", points: next, receipt: acquired.receipt,
        chunkIndex: Math.ceil(value.points.length / 10) - 1, isFinal: value.points.length === full.length });
      if (value.points.length === full.length) {
        finalizeArtifact(value, true);
        event(value, "measurement.finalized", value.artifact);
        value.analysis = spectroscopy ? analyzeSpectrum(value.points) : analyze(value.points);
        event(value, "analysis.completed", value.analysis);
        value.decision = spectroscopy
          ? decideSpectrum({ analysis: value.analysis, points: value.points, input: experimentPlan.input,
            artifact: value.artifact, followupDepth: experimentPlan.followupDepth ?? 0, upstream: experimentPlan.upstream })
          : decideReadoutS21({ analysis: value.analysis, points: value.points,
            input: experimentPlan.input, artifact: value.artifact,
            followupDepth: experimentPlan.followupDepth ?? 0 });
        event(value, "skill.decision", value.decision);
        value.status = value.analysis.quality === "pass" ? "completed" : "failed";
        value.finishedAt = now(); value.revision++;
        event(value, "run.status", { status: value.status });
      }
      commit(); return structuredClone(value);
    },
    stop(runId, key) { return idem(key, `stop:${runId}`, {}, () => {
      const value = run(runId);
      if (["completed", "failed", "stopped"].includes(value.status)) return { runId, status: value.status, stopConfirmed: value.status === "stopped" };
      value.status = "stopping"; value.revision++; event(value, "run.status", { status: "stopping" });
      const commandId = id();
      event(value, "device.command_requested", { commandId, command: "stop", requestedAt: now(), dataSource: "simulator" });
      commit();
      const receipt = device.stop({ commandId });
      if (!receipt.confirmed) {
        event(value, "device.stop_unconfirmed", { ...receipt, commandId });
        return { runId, status: "stopping", stopConfirmed: false };
      }
      event(value, "device.command_acknowledged", { commandId, receipt });
      value.status = "stopped"; value.finishedAt = now(); value.revision++;
      finalizeArtifact(value, false);
      event(value, "measurement.finalized", value.artifact);
      event(value, "device.stop_confirmed", { resource: PROFILE.resource, dataSource: "simulator", receipt });
      event(value, "run.status", { status: "stopped" });
      return { runId, status: "stopped", stopConfirmed: true };
    }); },
    snapshot: () => structuredClone(state),
  };
}
