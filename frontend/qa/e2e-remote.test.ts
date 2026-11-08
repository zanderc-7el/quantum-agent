/**
 * HTTP end-to-end check against a running QuantaMind backend.
 *
 * Playwright browsers cannot be installed in every environment, so this test
 * exercises the same contract the browser uses (login → create → start →
 * results → refresh → logout) with plain fetch. It is opt-in:
 *
 *   QUANTAMIND_E2E_BASE_URL=http://127.0.0.1:18789 npm test
 *
 * Optional: QUANTAMIND_E2E_USERNAME / QUANTAMIND_E2E_PASSWORD.
 */
import test from "node:test";
import assert from "node:assert/strict";

const baseUrl = (process.env.QUANTAMIND_E2E_BASE_URL || "").replace(/\/+$/, "");
const username = process.env.QUANTAMIND_E2E_USERNAME || "leader";
const password = process.env.QUANTAMIND_E2E_PASSWORD || "Leader123!";
const skip = baseUrl
  ? false
  : "set QUANTAMIND_E2E_BASE_URL to run the cross-repo HTTP E2E";

interface Envelope<T> {
  success: boolean;
  data: T;
  error: { code?: string; message?: string } | null;
}

async function call<T>(
  path: string,
  init: {
    method?: string;
    token?: string | null;
    body?: unknown;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.token) headers.Authorization = `Bearer ${init.token}`;
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${baseUrl}${path}`, {
    method: init.method ?? "GET",
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  const payload = (await response.json()) as Envelope<T>;
  if (!response.ok || !payload.success)
    throw new Error(
      `${init.method ?? "GET"} ${path} -> ${response.status} ${
        payload.error?.message ?? ""
      }`.trim(),
    );
  return payload.data;
}

interface E2ETarget {
  metric: string;
  ideal: number;
  unit: string;
  direction: string;
}

interface E2EResult {
  nodeId: string;
  attempt: number;
  metrics?: {
    evaluation?: {
      criterion?: unknown;
      target?: E2ETarget | null;
      targetSource?: string | null;
    };
  };
}

interface FrozenTargetResult extends E2EResult {
  metrics: {
    evaluation: {
      criterion?: unknown;
      target: E2ETarget;
      targetSource?: string | null;
    };
  };
}

async function waitForMeasurement(
  token: string,
  workflowId: string,
  timeoutMs = 20_000,
): Promise<number> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const { items } = await call<{ items: unknown[] }>(
      `/api/v1/calibration/workflows/${encodeURIComponent(workflowId)}/results`,
      { token },
    );
    if (items.length > 0) return items.length;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(
    "no server measurement was recorded within 20s; is the calibration worker running?",
  );
}

/** Wait for a quality-bearing result whose evaluation froze a design target. */
async function waitForFrozenTarget(
  token: string,
  workflowId: string,
  timeoutMs = 120_000,
): Promise<FrozenTargetResult> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const { items } = await call<{ items: E2EResult[] }>(
      `/api/v1/calibration/workflows/${encodeURIComponent(workflowId)}/results`,
      { token },
    );
    const frozen = items.find((item) => item.metrics?.evaluation?.target);
    if (frozen?.metrics?.evaluation)
      return frozen as FrozenTargetResult;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(
    "no result with a frozen design target within 120s; is the calibration worker running?",
  );
}

test("remote E2E: login, create, run, read measurements, refresh and logout", { skip }, async () => {
  const login = await call<{
    access_token: string;
    refresh_token: string;
    user: { user_id: string; role_id: string };
  }>("/api/v1/auth/login", {
    method: "POST",
    body: { username, password },
  });
  assert.ok(login.access_token);
  assert.ok(login.refresh_token);
  assert.ok(login.user.user_id);

  const me = await call<{ user_id: string; role_id: string }>(
    "/api/v1/auth/me",
    { token: login.access_token },
  );
  assert.equal(me.user_id, login.user.user_id);

  const created = await call<{
    workflow: { id: string; revision?: number; nodes: unknown[] };
    revision: number;
  }>("/api/v1/calibration/workflows", {
    method: "POST",
    token: login.access_token,
    body: {
      name: `E2E ${new Date().toISOString()}`,
      machine: "M7",
      experiment: "single",
      qubits: 1,
      target: "Q12",
      type: "adaptive",
      policy: "threshold",
      scenario: "normal",
    },
  });
  const workflowId = created.workflow.id;
  assert.ok(workflowId);

  // The binding carries both the Skill target and the machine's effective one.
  const binding = await call<{
    skill: { skillId: string; version: string };
    effectiveSteps?: {
      nodeId: string;
      target?: E2ETarget | null;
      effectiveTarget?: E2ETarget | null;
      targetSource?: string | null;
    }[];
    targets?: {
      nodeId: string;
      target: E2ETarget | null;
      source: string | null;
    }[];
  }>(
    `/api/v1/calibration/workflows/${encodeURIComponent(workflowId)}/skill`,
    { token: login.access_token },
  );
  assert.equal(binding.skill.skillId, "single-qubit-calibration");
  assert.equal(binding.skill.version, "1.1.0");
  const t1Step = binding.effectiveSteps?.find((step) => step.nodeId === "T1Idle");
  assert.ok(t1Step, "the binding exposes effective steps");
  const t1Target = t1Step.target;
  assert.ok(t1Target, "the Skill publishes a T1 design target");
  assert.equal(typeof t1Target.ideal, "number");
  assert.ok(["device", "skill"].includes(t1Step.targetSource ?? ""));
  assert.equal(
    t1Step.effectiveTarget?.ideal,
    t1Target.ideal,
    "M7 has no T1 target override, so the effective target is the Skill target",
  );
  const flatT1 = binding.targets?.find((item) => item.nodeId === "T1Idle");
  assert.equal(flatT1?.target?.ideal, t1Target.ideal);
  assert.equal(flatT1?.source, t1Step.targetSource);

  const started = await call<{ revision: number; workflow: { status: string } }>(
    `/api/v1/calibration/workflows/${encodeURIComponent(workflowId)}/actions`,
    {
      method: "POST",
      token: login.access_token,
      body: {
        action: "start",
        expectedRevision: created.revision,
        requestId: `e2e-${Date.now()}`,
      },
    },
  );
  assert.ok(["queued", "running"].includes(started.workflow.status));

  const resultCount = await waitForMeasurement(login.access_token, workflowId);
  assert.ok(resultCount > 0);

  // A quality result freezes the target it was judged against.
  const frozen = await waitForFrozenTarget(login.access_token, workflowId);
  const evaluation = frozen.metrics.evaluation;
  assert.equal(typeof evaluation.target.ideal, "number");
  assert.ok(["device", "skill"].includes(evaluation.targetSource ?? ""));
  assert.ok(evaluation.criterion, "the criterion is frozen next to the target");

  const refreshed = await call<{
    access_token: string;
    refresh_token: string;
  }>("/api/v1/auth/refresh", {
    method: "POST",
    body: { refreshToken: login.refresh_token },
  });
  assert.ok(refreshed.access_token);
  const afterRefresh = await call<{ user_id: string }>("/api/v1/auth/me", {
    token: refreshed.access_token,
  });
  assert.equal(afterRefresh.user_id, login.user.user_id);

  // Stop the workflow so the E2E run does not leave a long-running task.
  // The revision moved while nodes completed, so re-read it first.
  const current = await call<{ revision: number }>(
    `/api/v1/calibration/workflows/${encodeURIComponent(workflowId)}`,
    { token: refreshed.access_token },
  );
  await call(
    `/api/v1/calibration/workflows/${encodeURIComponent(workflowId)}/actions`,
    {
      method: "POST",
      token: refreshed.access_token,
      body: {
        action: "abort",
        expectedRevision: current.revision,
        requestId: `e2e-abort-${Date.now()}`,
      },
    },
  );

  await call("/api/v1/auth/logout", {
    method: "POST",
    token: refreshed.access_token,
  });
  const anonymous = await call<{ role_id: string }>("/api/v1/auth/me", {
    token: refreshed.access_token,
  });
  assert.equal(anonymous.role_id, "anonymous");
});
