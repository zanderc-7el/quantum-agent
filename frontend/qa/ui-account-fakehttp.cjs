const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const http = require("node:http");
const { chromium } = require(process.env.QCAL_PLAYWRIGHT_ROOT || "C:/Program Files/WindowsApps/OpenAI.CodexPrimaryRuntime.v26-1112-611-0_26.1112.611.0_x64__3k8sg7r9htsxt/dependencies/node/node_modules/playwright");
const directory = path.resolve(__dirname, "../qa-artifacts");
const output = { mode: "Standalone built frontend with all API routes intercepted by fakeHTTP fixtures; no real SMS, login, membership inference, task creation or hardware actions.", tests: [], pageErrors: [], consoleErrors: [], networkErrors: [], mutations: [], screenshots: [] };
let browser, page, staticServer;
let status = { provider: "ziyor", configured: false, authenticated: false, modelConnection: { provider: "codex_cli", model: "codex-default", active: true, verified: true, selection: "existing", note: "验收连接说明：会员登录后使用会员额度。" }, capabilities: { sendCode: true, login: true, connect: true, test: true } };
const models = [{ id: "fixture-secondary", label: "其他验收模型", provider: "ziyor" }, { id: "fixture-member-model", label: "验收模型（模拟目录）", provider: "ziyor", default: true }, { id: "__existing_provider__", label: "原有独立模型连接", provider: "existing" }];
const writes = [];
async function capture(name) { await page.screenshot({ path: path.join(directory, name + ".png"), fullPage: true, animations: "disabled" }); output.screenshots.push(name + ".png"); }
(async () => {
  const dist = path.resolve(__dirname, "../dist");
  staticServer = http.createServer((request, response) => {
    const pathname = new URL(request.url, "http://127.0.0.1").pathname;
    const file = path.resolve(dist, "." + (pathname === "/" ? "/index.html" : decodeURIComponent(pathname)));
    if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404); return response.end(); }
    response.setHeader("Content-Type", ({ ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" })[path.extname(file)] || "application/octet-stream");
    fs.createReadStream(file).pipe(response);
  });
  await new Promise(resolve => staticServer.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ executablePath: process.env.QCAL_BROWSER_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, locale: "zh-CN" });
  page = await context.newPage();
  page.on("pageerror", error => output.pageErrors.push(String(error)));
  page.on("console", message => { if (message.type() === "error") output.consoleErrors.push(message.text()); });
  page.on("response", response => { if (response.status() >= 400) output.networkErrors.push({ status: response.status(), url: response.url() }); });
  await page.route("**/api/**", async route => {
    const request = route.request(), url = new URL(request.url());
    if (!url.pathname.startsWith("/api/account/")) {
      if (request.method() !== "GET") throw new Error(`Unexpected non-account mutation ${request.method()} ${url.pathname}`);
      if (url.pathname === "/api/events") return route.fulfill({ status: 200, contentType: "text/event-stream", body: 'id: 1\nevent: snapshot\ndata: {"version":1,"seq":1,"workflows":[]}\n\n' });
      const fixtures = {
        "/api/session": { token: "fakeHTTP-local-session", actor: { id: "fixture", displayName: "界面验收", role: "leader" } },
        "/api/health": { status: "ok", agent: { provider: "codex_cli", configured: true, model: "codex-default" }, hardware: { enabled: false }, version: "fakeHTTP" },
        "/api/workflows": [],
        "/api/catalog": { blueprint: { version: "fakeHTTP", nodes: [], edges: [] }, resources: [], rules: [], capabilities: {} },
      };
      assert.ok(Object.hasOwn(fixtures, url.pathname), "Unmocked API request: " + url.pathname);
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fixtures[url.pathname]) });
    }
    let result;
    if (request.method() === "GET") result = url.pathname.endsWith("/models") ? { models, source: "fakeHTTP 验收目录", defaultModelId: "fixture-member-model" } : status;
    else {
      const body = request.postDataJSON(); writes.push({ path: url.pathname, body });
      assert.ok(body.operationId?.length > 20); assert.ok(request.headers()["x-qcal-token"]);
      output.mutations.push({ path: url.pathname, operationId: body.operationId });
      if (url.pathname.endsWith("/send-code")) result = { status: "sent", retryAfter: 60 };
      else if (url.pathname.endsWith("/login")) { assert.equal(body.useForAgent, true); status = { ...status, authenticated: true, user: { name: "界面验收账户", phoneMasked: "139****0000" }, balance: { label: "积分", remainingCalls: 0 }, modelConnection: { provider: "ziyor", model: "fixture-member-model", active: true, verified: false } }; result = { status: "ok" }; }
      else if (url.pathname.endsWith("/connect")) { status = { ...status, modelConnection: body.model === "__existing_provider__" ? { provider: "codex_cli", model: "codex-default", active: true, verified: false } : { provider: "ziyor", model: body.model, active: true, verified: false } }; result = { status: "ok" }; }
      else if (url.pathname.endsWith("/test")) { status = { ...status, modelConnection: { ...status.modelConnection, verified: true } }; result = { status: "ok" }; }
      else if (url.pathname.endsWith("/logout")) { status = { ...status, authenticated: false, user: undefined, balance: undefined, modelConnection: { ...status.modelConnection, active: false, verified: false } }; result = { status: "ok" }; }
      else throw new Error("Unexpected account mutation " + url.pathname);
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(result) });
  });
  await page.goto(`http://127.0.0.1:${staticServer.address().port}`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "打开 Ziyor 会员账户" }).click();
  const dialog = page.getByRole("dialog", { name: "Ziyor 会员账户" });
  await dialog.getByText("Codex CLI", { exact: true }).waitFor();
  assert.equal(writes.length, 0); assert.equal(await dialog.locator(".rp-member-identity").count(), 0);
  assert.ok(await dialog.getByLabel("会员手机号", { exact: true }).isVisible());
  assert.ok(!(await dialog.innerText()).includes("会员服务暂未配置"));
  await dialog.getByText("验收连接说明：会员登录后使用会员额度。", { exact: true }).waitFor();
  await dialog.locator("summary").filter({ hasText: "模型与高级连接设置" }).click();
  assert.equal(await dialog.getByLabel("会员模型目录", { exact: true }).inputValue(), "fixture-member-model");
  await dialog.getByText("目录来源：fakeHTTP 验收目录", { exact: true }).waitFor();
  await dialog.locator("summary").filter({ hasText: "模型与高级连接设置" }).click();
  output.tests.push("Account opening makes reads only; active Codex connection does not imply membership login");
  output.tests.push("No credentials yet does not disable available login capabilities; backend model default and connection note are honored");
  await capture("13-membership-login-dark-fakehttp");
  await page.getByLabel("会员手机号", { exact: true }).fill("13900000000");
  await dialog.getByRole("button", { name: "获取验证码", exact: true }).click();
  await dialog.getByRole("button", { name: /s 后重发/ }).waitFor();
  assert.equal(await dialog.getByRole("button", { name: /s 后重发/ }).isDisabled(), true);
  assert.equal(writes.length, 1); assert.equal(writes[0].body.phone, "13900000000");
  output.tests.push("SMS sends only on explicit click and honors backend countdown");
  await page.getByLabel("短信验证码", { exact: true }).fill("123456");
  await dialog.getByRole("button", { name: "登录并使用 Ziyor 额度", exact: true }).click();
  await dialog.getByText("139****0000", { exact: true }).waitFor();
  await dialog.getByTestId("membership-points").filter({ hasText: /^0$/ }).waitFor();
  assert.equal(writes.length, 2); assert.equal(writes[1].body.useForAgent, true);
  assert.ok(!(await dialog.innerText()).includes("13900000000")); assert.ok(!(await dialog.innerText()).includes("123456"));
  await page.locator(".sandbox-card strong").filter({ hasText: "Ziyor 会员 · 已连接" }).waitFor();
  output.tests.push("Login defaults to Ziyor allowance; actual zero credits and masked account display; no automatic inference");
  await capture("14-membership-connected-dark-fakehttp");
  status = { ...status, balance: undefined };
  await dialog.getByRole("button", { name: "刷新状态", exact: true }).click();
  await dialog.getByTestId("membership-points").filter({ hasText: "暂未获取" }).waitFor();
  assert.equal(writes.length, 2); output.tests.push("Unavailable credit balance is not fabricated as zero");
  await dialog.getByRole("button", { name: "验证连接", exact: true }).click();
  await dialog.getByText("模型连接验证通过。", { exact: true }).waitFor();
  assert.equal(writes.filter(item => item.path.endsWith("/test")).length, 1);
  output.tests.push("Model test is explicit and sends exactly one short-request action");
  await dialog.getByRole("button", { name: "退出会员", exact: true }).click();
  await dialog.getByText("等待模型连接", { exact: true }).waitFor();
  assert.equal(status.modelConnection.provider, "ziyor"); assert.equal(status.authenticated, false);
  assert.equal(writes.filter(item => item.path.endsWith("/connect")).length, 0);
  await page.locator(".sandbox-card strong").filter({ hasText: "智能体等待模型连接" }).waitFor();
  output.tests.push("Membership logout stays waiting_model; never silently switches to independent connection");
  await dialog.locator("summary").filter({ hasText: "模型与高级连接设置" }).click();
  await dialog.getByLabel("会员模型目录", { exact: true }).selectOption("__existing_provider__");
  await dialog.getByRole("button", { name: "切换到原有独立连接", exact: true }).click();
  await dialog.getByText("Codex CLI", { exact: true }).waitFor();
  assert.equal(writes.at(-1).body.model, "__existing_provider__");
  output.tests.push("Independent provider can be selected explicitly from advanced model settings");
  await page.getByRole("button", { name: "关闭会员账户", exact: true }).click();
  await page.getByRole("button", { name: "切换主题", exact: true }).click();
  await page.getByRole("button", { name: "打开 Ziyor 会员账户" }).click();
  await dialog.getByText("Codex CLI", { exact: true }).waitFor();
  await capture("15-membership-login-light-fakehttp");
  assert.deepEqual(output.pageErrors, []); assert.deepEqual(output.consoleErrors, []); assert.deepEqual(output.networkErrors, []);
  output.passed = true;
})().catch(error => { output.passed = false; output.error = error.stack || String(error); console.error(error); process.exitCode = 1; }).finally(async () => { output.finishedAt = new Date().toISOString(); fs.writeFileSync(path.join(directory, "ui-account-fakehttp-acceptance.json"), JSON.stringify(output, null, 2)); await browser?.close(); await new Promise(resolve => staticServer ? staticServer.close(resolve) : resolve()); console.log(JSON.stringify(output, null, 2)); });
