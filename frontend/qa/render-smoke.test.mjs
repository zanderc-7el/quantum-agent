/**
 * Render-layer smoke test without a browser.
 *
 * Playwright/jsdom are unavailable in this workspace, so this test loads the
 * real components through Vite's SSR pipeline and renders them with
 * `react-dom/server`. It catches import errors, invalid hooks, crashes inside
 * render, and regressions in the key screens' visible text.
 *
 * Limits: effects do not run, so timers, EventSource, fetch and DOM mutation
 * are out of scope — those stay covered by the gateway/measurement tests. The
 * render is a server render, so client-only branches (drag, canvas) are not
 * exercised.
 */
import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import React from "react";
import { renderToString } from "react-dom/server";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SESSION_KEY = "qcal-demo-session-v1";

let server;
let App;
let WorkspaceApp;
let createMockGateway;
let ParameterPanel;
let createWorkflow;
let publishWorkflowParameters;

function storageWith(record) {
  return {
    store: new Map(record ? [[SESSION_KEY, JSON.stringify(record)]] : []),
    getItem(key) {
      return this.store.has(key) ? this.store.get(key) : null;
    },
    setItem(key, value) {
      this.store.set(key, String(value));
    },
    removeItem(key) {
      this.store.delete(key);
    },
  };
}

function loginAs(userId) {
  globalThis.sessionStorage = storageWith({ userId, loggedInAt: Date.now() });
}

function logout() {
  globalThis.sessionStorage = storageWith(null);
}

function workflowWithDoneNode() {
  const workflow = createWorkflow({
    name: "Render smoke workflow",
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
      ? { ...node, status: "success", attempt: 1, progress: 100 }
      : node,
  );
  return { ...workflow, nodes, status: "running" };
}

test.before(async () => {
  // App reads `location.hash` while initialising state; Node has no location.
  globalThis.location = { hash: "" };
  logout();
  server = await createServer({
    root,
    configFile: path.join(root, "vite.config.ts"),
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true, hmr: false },
  });
  ({ default: App, WorkspaceApp } = await server.ssrLoadModule("/src/App.tsx"));
  ({ default: ParameterPanel } = await server.ssrLoadModule(
    "/src/components/ParameterPanel.tsx",
  ));
  ({ createMockGateway } = await server.ssrLoadModule("/src/lib/gateway.ts"));
  ({ createWorkflow } = await server.ssrLoadModule("/src/lib/simulator.ts"));
  ({ publishWorkflowParameters } = await server.ssrLoadModule(
    "/src/lib/remoteMeasurements.ts",
  ));
});

test.after(async () => {
  await server?.close();
});

test("the logged-out screen renders its form and no workspace data", () => {
  logout();
  const html = renderToString(React.createElement(App));
  assert.match(html, /登录实验工作空间/);
  assert.match(html, /进入工作空间/);
  assert.match(html, /公开演示账号/);
  assert.doesNotMatch(html, /M7 单比特完整校准/);
});

test("a restored leader session renders the dashboard with seeded workflows", () => {
  loginAs("leader");
  const html = renderToString(React.createElement(App));
  assert.match(html, /M7 单比特完整校准/);
  assert.match(html, /工作看台/);
  assert.doesNotMatch(html, /登录实验工作空间/);
});

test("the operator login path renders the same workspace shell with its role", () => {
  loginAs("operator");
  const html = renderToString(React.createElement(App));
  assert.match(html, /class="app-shell[^"]*role-operator/);
  assert.match(html, /工作看台/);
  assert.match(html, /实验工作人员/);
  assert.doesNotMatch(html, /登录实验工作空间/);
});

test("the parameter panel renders recorded parameters and a verified readback", () => {
  const workflow = workflowWithDoneNode();
  const node = workflow.nodes[0];
  publishWorkflowParameters(workflow.id, [
    {
      nodeId: node.id,
      attempt: 1,
      stage: "completed",
      capturedAt: "2026-11-05T02:03:00.000Z",
      fidelity: 0.991,
      parameters: { Q12: { f01Ghz: 4.71, t1Us: 41.2 } },
      publication: {
        adapter: "simulator",
        controller: "simulated-controller-v1",
        status: "verified",
        verified: true,
        requested: { Q12: { f01Ghz: 4.71, t1Us: 41.2 } },
        readback: { Q12: { f01Ghz: 4.71, t1Us: 41.2 } },
        mismatches: [],
        publishedAt: "2026-11-05T02:03:01.000Z",
      },
      publicationVerified: true,
    },
  ]);

  const html = renderToString(
    React.createElement(ParameterPanel, { workflow, node, defaultOpen: true }),
  );
  assert.match(html, /下发与回读/);
  assert.match(html, /回读一致/);
  assert.match(html, /服务端记录/);
  assert.match(html, /4\.71/);
});

test("a readback mismatch is visible as a failure, not as success", () => {
  const workflow = workflowWithDoneNode();
  const node = workflow.nodes[0];
  publishWorkflowParameters(workflow.id, [
    {
      nodeId: node.id,
      attempt: 1,
      stage: "completed",
      capturedAt: "2026-11-05T02:03:00.000Z",
      fidelity: 0.991,
      parameters: { Q12: { f01Ghz: 4.71 } },
      publication: {
        adapter: "http",
        controller: "http://127.0.0.1:9000",
        status: "mismatch",
        verified: false,
        requested: { Q12: { f01Ghz: 4.71 } },
        readback: { Q12: { f01Ghz: 4.712 } },
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
        publishedAt: "2026-11-05T02:03:01.000Z",
      },
      publicationVerified: false,
    },
  ]);

  const html = renderToString(
    React.createElement(ParameterPanel, { workflow, node, defaultOpen: true }),
  );
  assert.match(html, /回读不一致/);
  assert.match(html, /回读值与下发值不一致/);
});

for (const route of ["agent", "blueprint"]) test(`product ${route} renders without a login session`, () => {
  logout();globalThis.location.hash = "#" + route;
  const html = renderToString(React.createElement(WorkspaceApp, { productMode:true, gateway:createMockGateway(), user:{id:"leader",username:"leader",displayName:"演示",role:"leader"}, onLogout(){} }));
  assert.match(html,/product-integrated/);
  assert.doesNotMatch(html,/登录实验工作空间|公开演示账号/);
  assert.match(html, route === "blueprint" ? /blueprint-page/ : /view-chat/);
  globalThis.location.hash = "";
});
