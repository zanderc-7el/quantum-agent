import http from "node:http";
import { makeDemoWorkflows, demoMetadata } from "./demo-workflows.mjs";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { makeService, ApiError } from "./core.mjs";
import { SIM_RECIPES, SIM_TOPOLOGY, selectBatch } from "./recipes.mjs";
import { AGENT_TOOLS, previewAgentGoal, reviewAgentProposal } from "./agent.mjs";
import { createSimulatedDevice } from "./device-adapter.mjs";
import { storageStatus } from "./storage-status.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dataFile = process.env.QCAL_DATA_FILE ?? path.join(root, "data", "state.json");
const allowedOrigins = new Set((process.env.QCAL_ALLOWED_ORIGINS ?? "http://127.0.0.1:5286,http://localhost:5286,http://127.0.0.1:5173,http://localhost:5173").split(",").map(origin => origin.trim()).filter(Boolean));
for (const origin of allowedOrigins) {
  const parsed = new URL(origin);
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.origin !== origin) throw new Error("QCAL_ALLOWED_ORIGINS 必须是完整、精确的 HTTP(S) 来源");
}
const host = process.env.QCAL_HOST ?? "127.0.0.1";
const port = Number(process.env.QCAL_PORT ?? 5290);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("QCAL_PORT 无效");
const simulatedFault = process.env.QCAL_SIM_FAULT ?? null;
if (simulatedFault && !["acquisition_interrupt", "stop_unconfirmed", "readback_mismatch"].includes(simulatedFault))
  throw new Error("QCAL_SIM_FAULT 无效");
fs.mkdirSync(path.dirname(dataFile), { recursive: true });
const service = makeService({
  deviceFactory: options => createSimulatedDevice({ ...options, fault: simulatedFault }),
  load: () => fs.existsSync(dataFile) ? JSON.parse(fs.readFileSync(dataFile, "utf8")) : null,
  save: state => {
    const temp = `${dataFile}.${process.pid}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(state), { flag: "w" });
    fs.renameSync(temp, dataFile);
  },
});

const demoDataFile = process.env.QCAL_DEMO_DATA_FILE ?? path.join(path.dirname(dataFile), "workflows.json");
fs.mkdirSync(path.dirname(demoDataFile), { recursive: true });
const demo = makeDemoWorkflows({
  load: () => fs.existsSync(demoDataFile) ? JSON.parse(fs.readFileSync(demoDataFile, "utf8")) : null,
  save: state => {
    const temp = `${demoDataFile}.${process.pid}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(state));
    fs.renameSync(temp, demoDataFile);
  },
});

async function body(req) {
  let data = "";
  for await (const chunk of req) {
    data += chunk;
    if (data.length > 65536) throw new ApiError("INVALID_INPUT", "请求体超过 64 KiB", 413);
  }
  try { return data ? JSON.parse(data) : {}; }
  catch { throw new ApiError("INVALID_INPUT", "请求体不是 JSON"); }
}
function send(res, status, value) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(JSON.stringify(value));
}
const server = http.createServer(async (req, res) => {
  const origin = req.headers.origin;
  if (origin && !allowedOrigins.has(origin)) {
    send(res, 403, { error: { code: "FORBIDDEN", message: "来源未获准" } }); return;
  }
  if (origin) {
    res.setHeader("access-control-allow-origin", origin);
    res.setHeader("access-control-allow-methods", "GET, POST, OPTIONS");
    res.setHeader("access-control-allow-headers", "content-type, idempotency-key");
  }
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }
  try {
    const url = new URL(req.url, `http://${req.headers.host ?? "127.0.0.1"}`);
    const parts = url.pathname.split("/").filter(Boolean);
    if (req.method === "GET" && url.pathname === "/health") return send(res, 200, { status: "ok", mode: "simulated-device-gateway", hardwareConnected: false,
      controllerConnected: true, adapterId: service.device().adapterId });
    if (req.method === "GET" && url.pathname === "/api/demo/storage-status") {
      const status = storageStatus({ dataFile, demoDataFile, experimentState: service.snapshot(), workflows: demo.list() });
      return send(res, status.status === "ok" ? 200 : 503, { ...demoMetadata, ...status });
    }
    if (parts[0] === "api" && parts[1] === "demo" && parts[2] === "workflows") {
      if (parts.length === 3 && req.method === "GET") return send(res, 200, { ...demoMetadata, workflows: demo.list() });
      if (parts.length === 3 && req.method === "POST") return send(res, 201, { ...demoMetadata, workflow: demo.create(await body(req)) });
      if (parts.length === 4 && req.method === "GET") return send(res, 200, { ...demoMetadata, workflow: demo.get(parts[3]) });
      if (parts.length === 5 && req.method === "POST") {
        const input = await body(req);
        if (!input || typeof input !== "object" || Array.isArray(input)) throw new ApiError("INVALID_INPUT", "请求必须是对象");
        if (parts[4] === "actions") return send(res, 200, { ...demoMetadata, workflow: demo.act(parts[3], input.action) });
        if (parts[4] === "policy") return send(res, 200, { ...demoMetadata, workflow: demo.policy(parts[3], input.policy) });
      }
      throw new ApiError("NOT_FOUND", "模拟工作流接口不存在", 404);
    }
    if (req.method === "GET" && url.pathname === "/api/v1/device-profile") return send(res, 200, service.profile());
    if (req.method === "GET" && url.pathname === "/api/v1/device-adapter") return send(res, 200, service.device());
    if (req.method === "GET" && url.pathname === "/api/v1/skills/readout-s21") return send(res, 200, service.skill());
    if (req.method === "GET" && url.pathname === "/api/v1/skills/f01-spectrum") return send(res, 200, service.spectrumSkill());
    if (req.method === "GET" && url.pathname === "/api/v1/topology") return send(res, 200, SIM_TOPOLOGY);
    if (req.method === "GET" && url.pathname === "/api/v1/recipes") return send(res, 200, SIM_RECIPES);
    if (req.method === "GET" && url.pathname === "/api/v1/agent/tools") return send(res, 200, AGENT_TOOLS);
    if (req.method === "POST" && url.pathname === "/api/v1/agent/plan-preview") {
      const input = await body(req);
      return send(res, 200, previewAgentGoal(input.goal, input.blueprintId, input.blueprintVersion));
    }
    if (req.method === "POST" && url.pathname === "/api/v1/agent/proposal-review")
      return send(res, 200, reviewAgentProposal(await body(req)));
    if (req.method === "POST" && url.pathname === "/api/v1/recipes/preview") {
      const input = await body(req);
      const recipe = SIM_RECIPES[input.recipe];
      if (!recipe || !input.results || typeof input.results !== "object" || Array.isArray(input.results) ||
          !Array.isArray(input.occupied)) throw new ApiError("INVALID_INPUT", "预览请求无效");
      return send(res, 200, { recipe: input.recipe, ready: selectBatch(recipe, input.results, input.occupied).map(item => item.id),
        dataSource: "simulator", executable: false });
    }
    if (req.method === "GET" && url.pathname === "/api/v1/parameters/readout-frequency") return send(res, 200, service.parameterState());
    if (req.method === "GET" && url.pathname === "/api/v1/parameters/readout-frequency/readback") return send(res, 200, service.readbackParameter());
    if (req.method === "POST" && url.pathname === "/api/v1/parameters/readout-frequency/rollback") {
      const input = await body(req);
      return send(res, 201, service.rollbackParameter(input.targetRevision, input.expectedRevision, input.actor, req.headers["idempotency-key"]));
    }
    if (req.method === "POST" && url.pathname === "/api/v1/plans") return send(res, 201, service.createPlan(await body(req), req.headers["idempotency-key"]));
    if (parts[0] === "api" && parts[1] === "v1" && parts[2] === "plans" && parts.length >= 4) {
      if (req.method === "GET" && parts.length === 4) return send(res, 200, service.getPlan(parts[3]));
      if (req.method === "POST" && parts.length === 5 && parts[4] === "runs") return send(res, 202, service.start(parts[3], req.headers["idempotency-key"]));
    }
    if (parts[0] === "api" && parts[1] === "v1" && parts[2] === "runs" && parts.length >= 4) {
      if (req.method === "GET" && parts.length === 4) return send(res, 200, service.getRun(parts[3]));
      if (req.method === "GET" && parts.length === 5 && parts[4] === "summary") return send(res, 200, service.getRunSummary(parts[3]));
      if (req.method === "GET" && parts.length === 5 && parts[4] === "events") {
        const after = Number(url.searchParams.get("after") ?? 0);
        if (!Number.isInteger(after) || after < 0) throw new ApiError("INVALID_INPUT", "after 必须为非负整数");
        return send(res, 200, { events: service.events(parts[3], after) });
      }
      if (req.method === "POST" && parts.length === 5 && parts[4] === "stop") return send(res, 200, service.stop(parts[3], req.headers["idempotency-key"]));
      if (req.method === "POST" && parts.length === 5 && parts[4] === "follow-up-plan") return send(res, 201, service.createFollowupPlan(parts[3], req.headers["idempotency-key"]));
      if (req.method === "POST" && parts.length === 5 && parts[4] === "parameter-candidate") return send(res, 201, service.createCandidate(parts[3], req.headers["idempotency-key"]));
    }
    if (parts[0] === "api" && parts[1] === "v1" && parts[2] === "parameter-candidates" && parts.length === 5 && req.method === "POST") {
      if (parts[4] === "approve") {
        const input = await body(req);
        return send(res, 200, service.approveCandidate(parts[3], input.actor, req.headers["idempotency-key"]));
      }
      if (parts[4] === "publish") {
        const input = await body(req);
        return send(res, 201, service.publishCandidate(parts[3], input.expectedRevision, req.headers["idempotency-key"]));
      }
    }
    if (parts[0] === "api" && parts[1] === "v1" && parts[2] === "attempts" && parts.length === 5 && req.method === "GET") {
      if (parts[4] === "measurements") return send(res, 200, service.getMeasurements(parts[3]));
      if (parts[4] === "analysis") return send(res, 200, service.getAnalysis(parts[3]));
    }
    throw new ApiError("NOT_FOUND", "接口不存在", 404);
  } catch (error) {
    if (error instanceof ApiError) return send(res, error.status, { error: { code: error.code, message: error.message, retryable: false } });
    console.error(error);
    send(res, 500, { error: { code: "INTERNAL_ERROR", message: "服务端错误", retryable: false } });
  }
});

setInterval(() => {
  for (const run of Object.values(service.snapshot().runs)) if (["queued", "running"].includes(run.status)) {
    try { service.step(run.runId); } catch (error) { console.error("simulation step failed", error); }
  }
}, 250);
setInterval(() => {
  try { demo.advance(1); } catch (error) { console.error("demo workflow step failed", error); }
}, 1000);
server.listen(port, host, () => console.log(`QCal simulator API: http://${host}:${port}`));
