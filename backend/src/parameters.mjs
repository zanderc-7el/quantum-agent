import { ApiError, PROFILE } from "./core.mjs";

export function initialParameterState(now) {
  return { revisions: [{ revision: 1, targetQubitId: "Q12", name: "readout_frequency", valueHz: PROFILE.centerHz,
    source: "simulator-seed", publishedAt: now(), actor: "simulator" }], candidates: {} };
}
export function currentParameter(state) { return state.revisions.at(-1); }
export function validateActor(actor) {
  if (typeof actor !== "string" || actor.trim().length < 2 || actor.length > 80)
    throw new ApiError("INVALID_INPUT", "模拟审批人标识需为 2–80 字符");
  return actor.trim();
}
export function buildCandidate({ sourceRun, parameterState, id, now }) {
  if (sourceRun.status !== "completed" || sourceRun.decision?.action !== "accept_candidate" ||
      !sourceRun.artifact?.complete || !Number.isFinite(sourceRun.decision.candidateHz))
    throw new ApiError("QUALITY_GATE_FAILED", "运行未通过 Skill 质量门，不能生成候选参数", 409);
  if (Object.values(parameterState.candidates).some(c => c.sourceRunId === sourceRun.runId))
    throw new ApiError("STALE_VERSION", "该运行已生成候选参数", 409);
  const candidate = { candidateId: id(), targetQubitId: "Q12", name: "readout_frequency", unit: "Hz",
    valueHz: sourceRun.decision.candidateHz, baselineRevision: currentParameter(parameterState).revision,
    sourceRunId: sourceRun.runId, sourceAttemptId: sourceRun.attemptId,
    sourceArtifactSha256: sourceRun.artifact.sha256, analysisMethod: sourceRun.analysis.method,
    status: "proposed", createdAt: now(), approvedBy: null, approvedAt: null,
    publishedRevision: null, dataSource: "simulator" };
  parameterState.candidates[candidate.candidateId] = candidate;
  return candidate;
}
export function approveCandidate(parameterState, candidateId, actor, now) {
  const candidate = parameterState.candidates[candidateId];
  if (!candidate) throw new ApiError("NOT_FOUND", "未找到候选参数", 404);
  if (candidate.status !== "proposed") throw new ApiError("INVALID_TRANSITION", "候选参数不在待审批状态", 409);
  if (candidate.baselineRevision !== currentParameter(parameterState).revision)
    throw new ApiError("STALE_VERSION", "参数基线已更新，请重新评估候选值", 409);
  candidate.status = "approved"; candidate.approvedBy = validateActor(actor); candidate.approvedAt = now();
  return candidate;
}
export function publishCandidate(parameterState, candidateId, expectedRevision, now) {
  const candidate = parameterState.candidates[candidateId];
  if (!candidate) throw new ApiError("NOT_FOUND", "未找到候选参数", 404);
  if (candidate.status !== "approved") throw new ApiError("INVALID_TRANSITION", "候选参数尚未批准", 409);
  const current = currentParameter(parameterState);
  if (expectedRevision !== current.revision || candidate.baselineRevision !== current.revision)
    throw new ApiError("STALE_VERSION", "参数版本冲突", 409);
  if (candidate.valueHz < PROFILE.minHz || candidate.valueHz > PROFILE.maxHz)
    throw new ApiError("INVALID_INPUT", "候选值超出模拟 Profile");
  const revision = { revision: current.revision + 1, targetQubitId: "Q12", name: "readout_frequency",
    valueHz: candidate.valueHz, source: "simulator-candidate", sourceCandidateId: candidateId,
    sourceRunId: candidate.sourceRunId, sourceArtifactSha256: candidate.sourceArtifactSha256,
    publishedAt: now(), actor: candidate.approvedBy, dataSource: "simulator" };
  parameterState.revisions.push(revision);
  candidate.status = "published"; candidate.publishedRevision = revision.revision;
  return revision;
}
export function rollbackParameter(parameterState, targetRevision, expectedRevision, actor, now) {
  const current = currentParameter(parameterState);
  if (expectedRevision !== current.revision) throw new ApiError("STALE_VERSION", "参数版本冲突", 409);
  const target = parameterState.revisions.find(r => r.revision === targetRevision);
  if (!target || targetRevision === current.revision) throw new ApiError("INVALID_INPUT", "回滚目标版本无效");
  const revision = { revision: current.revision + 1, targetQubitId: "Q12", name: "readout_frequency",
    valueHz: target.valueHz, source: "simulator-rollback", rollbackToRevision: targetRevision,
    publishedAt: now(), actor: validateActor(actor), dataSource: "simulator" };
  parameterState.revisions.push(revision);
  return revision;
}
