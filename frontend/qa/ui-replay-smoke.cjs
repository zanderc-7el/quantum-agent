/* Isolated headless acceptance test. Only creates explicitly named replay tasks. */
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { chromium } = require(process.env.QCAL_PLAYWRIGHT_ROOT || "C:/Program Files/WindowsApps/OpenAI.CodexPrimaryRuntime.v26-1112-611-0_26.1112.611.0_x64__3k8sg7r9htsxt/dependencies/node/node_modules/playwright");
const artifacts = path.resolve(__dirname, "../qa-artifacts");
fs.mkdirSync(artifacts, { recursive: true });
const output = { startedAt: new Date().toISOString(), base: "http://127.0.0.1:8765", tests: [], tasks: [], consoleErrors: [], pageErrors: [], networkErrors: [], failedRequests: [], screenshots: [] };
let browser, page;
async function screenshot(name) { const file = path.join(artifacts, name + ".png"); await page.screenshot({ path: file, fullPage: true, animations: "disabled" }); output.screenshots.push(file); }
async function check(name, fn) { await fn(); output.tests.push({ name, passed: true }); console.log("PASS", name); }
async function getTask(id) { const response = await page.request.get(`${output.base}/api/workflows/${encodeURIComponent(id)}`); assert.equal(response.status(), 200); return response.json(); }
async function waitTask(id, predicate, timeout = 18000) { const end = Date.now() + timeout; while (Date.now() < end) { const workflow = await getTask(id); if (predicate(workflow)) return workflow; await page.waitForTimeout(200); } throw new Error(`Task condition timed out: ${id}`); }

(async () => {
  browser = await chromium.launch({ executablePath: process.env.QCAL_BROWSER_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true, args: ["--disable-background-networking"] });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, locale: "zh-CN" });
  page = await context.newPage();
  page.on("console", message => { if (message.type() === "error") output.consoleErrors.push(message.text()); });
  page.on("pageerror", error => output.pageErrors.push(String(error)));
  page.on("response", response => { if (response.status() >= 400) output.networkErrors.push({ status: response.status(), url: response.url() }); });
  page.on("requestfailed", request => output.failedRequests.push({ url: request.url(), error: request.failure()?.errorText }));
  await page.goto(output.base, { waitUntil: "domcontentloaded" });
  await check("default entry connects to backend session and SSE without demo login", async () => { await page.locator(".rp-live.live").waitFor(); await page.getByRole("heading", { name: "让每一步实验都有据可循" }).waitFor(); assert.equal(await page.getByText("Leader123!", { exact: false }).count(), 0); await screenshot("01-dashboard"); });
  for (const resourceId of ["Q11", "Q18"]) {
    await page.getByRole("button", { name: "实验任务", exact: false }).first().click();
    await page.getByRole("button", { name: "新建工作流", exact: true }).last().click();
    const dialog = page.getByRole("dialog", { name: "新建工作流" });
    await dialog.getByLabel("运行方式", { exact: true }).selectOption("replay");
    await dialog.getByLabel("目标资源", { exact: true }).selectOption(resourceId);
    const name = `界面验收 ${resourceId} 历史回放 ${new Date().toISOString().replace(/[:.]/g, "-")}`;
    await dialog.getByLabel("任务名称", { exact: true }).fill(name);
    await dialog.getByRole("button", { name: "创建工作流", exact: true }).click();
    await page.getByRole("heading", { name, exact: true }).waitFor();
    const workflows = await (await page.request.get(`${output.base}/api/workflows`)).json();
    const created = workflows.find(item => item.name === name);
    assert.ok(created); assert.equal(created.mode, "replay");
    output.tasks.push({ id: created.id, name, resourceId, mode: created.mode });
    await check(`${resourceId} creates replay with catalog resource and backend dynamic nodes`, async () => { assert.equal(await page.locator(".rp-flow-node").count(), created.nodes.length); assert.ok(created.nodes.length >= 8); await screenshot(`${resourceId}-02-blueprint-draft`); });
    await page.getByRole("button", { name: "开始回放", exact: true }).click();
    await waitTask(created.id, value => value.status !== "draft" && value.nodes.some(node => node.attempt > 0));
    // Explicit acceptance of historical evidence advances the isolated QA task only.
    for (let iteration = 0; iteration < 25; iteration++) {
      const workflow = await getTask(created.id); assert.equal(workflow.mode, "replay");
      if (["completed", "failed", "aborted"].includes(workflow.status)) break;
      if (workflow.status === "attention") {
        const accept = page.getByRole("button", { name: "接受并继续", exact: true });
        if (await accept.isVisible()) { await accept.click(); await waitTask(created.id, value => value.revision > workflow.revision); }
      } else await page.waitForTimeout(250);
    }
    const finished = await getTask(created.id);
    output.tasks[output.tasks.length - 1].status = finished.status;
    output.tasks[output.tasks.length - 1].nodesWithResults = finished.nodes.filter(node => node.result).length;
    const measuredNode = finished.nodes.find(node => node.result?.artifacts?.some(artifact => artifact.kind === "image"));
    assert.ok(measuredNode, `${resourceId} requires at least one registered historical image`);
    await page.locator(".rp-flow-node").filter({ has: page.getByText(measuredNode.label, { exact: true }) }).click();
    await check(`${resourceId} actual image loads and is labelled historical`, async () => {
      const image = page.locator(".rp-selected-result .rp-images img").first(); await image.waitFor();
      await image.evaluate(img => img.complete ? undefined : new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; }));
      assert.ok(await image.evaluate(img => img.naturalWidth > 0));
      await page.locator(".rp-selected-result").getByText("历史实测数据", { exact: true }).waitFor();
      await screenshot(`${resourceId}-03-measurement-result`);
    });
    await check(`${resourceId} parameters and attempt history are actual node detail`, async () => {
      await page.locator(".rp-inspector-tabs").getByRole("button", { name: "参数", exact: true }).click();
      await page.getByText("本轮执行参数", { exact: true }).waitFor();
      await page.locator(".rp-inspector-tabs").getByRole("button", { name: "执行历史", exact: true }).click();
      await page.locator(".rp-attempt").first().waitFor(); await page.locator(".rp-attempt summary").first().click();
      await page.locator(".rp-attempt[open] pre").first().waitFor();
      await screenshot(`${resourceId}-04-attempt-history`);
    });
    await check(`${resourceId} node-specific rollback rules render`, async () => {
      await page.locator(".rp-inspector-tabs").getByRole("button", { name: "回退规则", exact: true }).click();
      await page.locator(".rp-rule").first().waitFor();
      assert.ok((await page.locator(".rp-rule").count()) > 0);
      await screenshot(`${resourceId}-05-rollback-rules`);
    });
    await check(`${resourceId} report view renders real artifacts and downloadable report`, async () => {
      await page.getByRole("button", { name: "任务对话", exact: true }).click();
      await page.getByRole("heading", { name: "对话式 Agent", exact: false }).waitFor();
      await page.locator(".rp-report-node").first().waitFor();
      const reportLink = page.locator(".rp-report-export");
      const reportResponse = await page.request.get(new URL(await reportLink.getAttribute("href"), output.base).href);
      assert.equal(reportResponse.status(), 200); assert.match(reportResponse.headers()["content-type"], /html/);
      await screenshot(`${resourceId}-06-chat-report`);
    });
  }
  await check("chat sends to backend and displays explicit model status", async () => {
    await page.getByRole("textbox", { name: "发送给智能体的消息" }).fill("界面验收：请简述当前历史回放任务状态，不启动真实设备。");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await page.locator(".rp-chat-message.assistant").last().waitFor({ timeout: 45000 });
    assert.ok((await page.locator(".rp-chat-message.assistant p").last().innerText()).length > 0);
    await screenshot("07-agent-response");
  });
  await check("page has no uncaught errors or failed artifact requests", async () => { assert.deepEqual(output.pageErrors, []); assert.deepEqual(output.networkErrors.filter(item => item.url.includes("/api/artifacts")), []); });
  output.passed = true;
})().catch(async error => { output.passed = false; output.error = error.stack || String(error); console.error(error); if (page) { await screenshot("failure").catch(() => {}); fs.writeFileSync(path.join(artifacts, "failure-dom.txt"), await page.locator("body").innerText().catch(() => "")); } process.exitCode = 1; }).finally(async () => { output.finishedAt = new Date().toISOString(); fs.writeFileSync(path.join(artifacts, "ui-acceptance.json"), JSON.stringify(output, null, 2)); await browser?.close(); });
