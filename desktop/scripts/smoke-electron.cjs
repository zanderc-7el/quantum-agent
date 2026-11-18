'use strict';
// Run explicitly after restarting the API and agent. Never sends write requests.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {requestHealth, validateIdentity, configuredAgentPort, serviceDefinitions, API_ORIGIN} = require('../src/services.cjs');
const desktopRoot = path.resolve(__dirname, '..');
const projectRoot = path.dirname(desktopRoot);
const agentPort = configuredAgentPort(projectRoot);
const definitions = serviceDefinitions(agentPort);
const bundledModules = 'C:/Program Files/WindowsApps/OpenAI.CodexPrimaryRuntime.v26-1112-611-0_26.1112.611.0_x64__3k8sg7r9htsxt/dependencies/node/node_modules';
const executablePath = process.env.QCAL_SMOKE_EXE ? path.resolve(process.env.QCAL_SMOKE_EXE) : path.join(desktopRoot, 'dist/win-unpacked/QuantumControl.exe');
const out = path.join(desktopRoot, 'runtime/smoke', new Date().toISOString().replace(/[:.]/g, '-'));
const report = {startedAt: new Date().toISOString(), executablePath, projectRoot, agentPort, passed: false, requests: [], blockedRequests: [], screenshots: [], policy: 'read-only; no SMS/login/model test/workflow creation; close desktop only'};
const normalized = p => path.resolve(p).replace(/\\/g, '/').toLowerCase();
let electronApp, page;
async function readApi(endpoint) {
  const response = await fetch(API_ORIGIN + endpoint, {signal: AbortSignal.timeout(15000)});
  if (!response.ok) throw new Error(`Read-only API ${endpoint}: HTTP ${response.status}`);
  return response.json();
}
async function main() {
  fs.mkdirSync(out, {recursive: true});
  assert.ok(fs.existsSync(executablePath), 'Build the directory EXE first');
  // Requiring both services ready ensures the smoke launch only reuses services.
  report.servicesBefore = [];
  for (const def of definitions) {
    const health = await requestHealth(def, 6000);
    assert.ok(health, `${def.kind} is not ready: restart it before running the smoke test`);
    report.servicesBefore.push({kind: def.kind, port: def.port, identity: validateIdentity(def, health, projectRoot, agentPort)});
  }
  const initialAccount = await readApi('/api/account/status');
  assert.equal(initialAccount.authenticated, false, 'Membership is already logged in; this test will not log out or change the account');
  const workflowsBefore = await readApi('/api/workflows');
  const idsBefore = workflowsBefore.map(item => item.id).sort();
  const playwrightPath = require.resolve('playwright', {paths: [process.env.QCAL_PLAYWRIGHT_MODULES || bundledModules, desktopRoot]});
  const {_electron} = require(playwrightPath);
  const env = {...process.env, QCAL_PROJECT_ROOT: projectRoot}; delete env.ELECTRON_RUN_AS_NODE;
  electronApp = await _electron.launch({executablePath, cwd: desktopRoot, args: [], env, timeout: 45000});
  await electronApp.context().route('**/*', async route => {
    const request = route.request(); const url = new URL(request.url()); const method = request.method();
    if (url.protocol === 'file:' || url.protocol === 'data:') return route.continue();
    if (url.origin !== API_ORIGIN || !['GET', 'HEAD'].includes(method)) {
      report.blockedRequests.push({method, origin: url.origin, path: url.pathname});
      return route.abort('blockedbyclient');
    }
    report.requests.push({method, path: url.pathname});
    return route.continue();
  });
  page = await electronApp.firstWindow({timeout: 30000});
  page.setDefaultTimeout(20000);
  await page.waitForURL(url => url.origin === API_ORIGIN, {timeout: 45000});
  const entry = page.getByRole('button', {name: '打开 Ziyor 会员账户', exact: true});
  await entry.waitFor({state: 'visible'});
  report.nativeWindow = await electronApp.evaluate(({app, BrowserWindow}) => {
    const windows = BrowserWindow.getAllWindows(); const main = windows[0]; const preferences = main.webContents.getLastWebPreferences();
    return {isPackaged: app.isPackaged, executable: process.execPath, pid: process.pid, electronVersion: process.versions.electron,
      windowCount: windows.length, title: main.getTitle(), visible: main.isVisible(), url: main.webContents.getURL(),
      bounds: main.getBounds(), userData: app.getPath('userData'), nodeIntegration: preferences.nodeIntegration,
      contextIsolation: preferences.contextIsolation, sandbox: preferences.sandbox};
  });
  assert.equal(report.nativeWindow.isPackaged, true); assert.equal(normalized(report.nativeWindow.executable), normalized(executablePath));
  assert.equal(report.nativeWindow.visible, true); assert.equal(report.nativeWindow.nodeIntegration, false);
  assert.equal(report.nativeWindow.contextIsolation, true); assert.equal(report.nativeWindow.sandbox, true);
  report.desktop = await page.evaluate(() => window.qcalDesktop.status());
  assert.equal(report.desktop.phase, 'ready'); assert.ok(report.desktop.services.every(service => service.ownership === 'shared'));
  const viewport = page.viewportSize() || await page.evaluate(() => ({width: innerWidth, height: innerHeight}));
  const entryBox = await entry.boundingBox(); assert.ok(entryBox && entryBox.x < viewport.width * .35 && entryBox.y > viewport.height * .4, 'Membership entry should be in the lower left sidebar');
  const dashboardImage = path.join(out, '01-desktop-dashboard.png'); await page.screenshot({path: dashboardImage, fullPage: true}); report.screenshots.push(dashboardImage);
  await entry.click();
  const modal = page.getByRole('dialog', {name: 'Ziyor 会员账户', exact: true}); await modal.waitFor({state: 'visible'});
  await modal.getByText('尚未登录会员。当前独立模型连接与会员账户分别管理。', {exact: true}).waitFor({state: 'visible'});
  await page.getByText('正在读取账户状态…', {exact: true}).waitFor({state: 'hidden', timeout: 20000});
  assert.equal(await modal.getByRole('textbox', {name: '会员手机号', exact: true}).inputValue(), '');
  const finalAccount = await readApi('/api/account/status'); assert.equal(finalAccount.authenticated, false);
  report.account = {authenticated: false, configured: finalAccount.configured, phoneFieldEmpty: true};
  const memberImage = path.join(out, '02-membership-signed-out.png'); await page.screenshot({path: memberImage, fullPage: true}); report.screenshots.push(memberImage);
  assert.deepEqual((await readApi('/api/workflows')).map(item => item.id).sort(), idsBefore, 'Workflow identity set changed during smoke');
  assert.equal(report.blockedRequests.length, 0, 'The page attempted an unexpected non-read-only or external request');
  report.passed = true;
}
(async () => {
  try {await main();}
  catch (error) {
    report.error = error.message;
    if (page && !page.isClosed()) {try {const screenshot = path.join(out, 'error.png'); await page.screenshot({path: screenshot}); report.screenshots.push(screenshot);} catch {}}
    process.exitCode = 1;
  }
  finally {
    if (electronApp) {try {await electronApp.close(); report.desktopClosed = true;} catch (error) {report.closeError = error.message; report.passed = false; process.exitCode = 1;}}
    report.servicesAfter = [];
    for (const def of definitions) {
      try {const health = await requestHealth(def, 6000); report.servicesAfter.push({kind: def.kind, port: def.port, healthy: Boolean(health), identity: health ? validateIdentity(def, health, projectRoot, agentPort) : null});}
      catch (error) {report.servicesAfter.push({kind: def.kind, healthy: false, error: error.message});}
    }
    if (report.desktopClosed && report.servicesAfter.some(service => !service.healthy)) {report.passed = false; process.exitCode = 1;}
    report.finishedAt = new Date().toISOString(); fs.mkdirSync(out, {recursive: true});
    fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({passed: report.passed, report: path.join(out, 'result.json'), screenshots: report.screenshots, error: report.error, servicesAfter: report.servicesAfter}, null, 2));
  }
})();
