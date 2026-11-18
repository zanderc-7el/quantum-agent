'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {ServiceManager, validateIdentity, DEFINITIONS, resolveConfiguration, isProjectRoot, persistentNodeRuntime, configuredAgentPort, serviceDefinitions, serviceLaunchConfiguration} = require('../src/services.cjs');
const {execFileSync} = require('node:child_process');
const tempRoot = path.join(__dirname, '../runtime/tests');
fs.mkdirSync(tempRoot, {recursive: true});
function fixture(t) {
  const root = fs.mkdtempSync(path.join(tempRoot, 'project-'));
  for (const filename of ['backend/server.py', 'agent/src/server.mjs', 'frontend/dist/index.html']) {
    fs.mkdirSync(path.dirname(path.join(root, filename)), {recursive: true}); fs.writeFileSync(path.join(root, filename), 'fixture');
  }
  t.after(() => {assert.ok(path.resolve(root).startsWith(path.resolve(tempRoot) + path.sep)); fs.rmSync(root, {recursive: true, force: true});});
  return root;
}
test('both existing services are reused without launching or requiring Python', async t => {
  const root = fixture(t); let launches = 0;
  const manager = new ServiceManager({projectRoot: root}, {health: async d => ({[d.identityKey]: d.identity, projectRoot: root}), launch: () => launches++, runtimes: () => {throw new Error('must not inspect runtime');}});
  const result = await manager.start();
  assert.equal(launches, 0); assert.deepEqual(result.services.map(x => x.ownership), ['shared', 'shared']);
  assert.ok(result.services.every(x => x.identity === 'project-root-verified'));
  assert.equal(manager.detach().exitPolicy, 'leave-services-running');
});
test('unknown listener fails closed and never launches a replacement', async t => {
  const root = fixture(t); let launches = 0;
  const manager = new ServiceManager({projectRoot: root}, {health: async () => null, occupied: async () => true, launch: () => launches++});
  await assert.rejects(manager.start(), /健康检查未通过/); assert.equal(launches, 0);
});
test('service identity and project identity are both checked', () => {
  assert.throws(() => validateIdentity(DEFINITIONS[0], {serviceId: 'other'}, 'D:/a'), /其他服务/);
  assert.throws(() => validateIdentity(DEFINITIONS[1], {projectId: 'qcal-local-f8-platform', projectRoot: 'D:/wrong'}, 'D:/right'), /另一份项目/);
  assert.equal(validateIdentity(DEFINITIONS[0], {serviceId: 'qcal-brain', agentRoot: 'D:/right/agent'}, 'D:/right'), 'project-root-verified');
  assert.throws(() => validateIdentity(DEFINITIONS[0], {serviceId: 'qcal-brain'}, 'D:/right'), /缺少工程路径标识/);
});
test('portable extraction keeps a persistent hash-verified Node outside the temporary package', t => {
  const root = fixture(t); const resources = path.join(root, 'temporary-extraction/resources/node');
  fs.mkdirSync(resources, {recursive: true});
  const source = path.join(resources, 'node.exe'); const contents = Buffer.from('offline fake runtime, never executed');
  const sha256 = require('node:crypto').createHash('sha256').update(contents).digest('hex');
  fs.writeFileSync(source, contents); fs.writeFileSync(path.join(resources, 'manifest.json'), JSON.stringify({sha256}));
  const config = {projectRoot: root, bundledNode: true}; const staged = persistentNodeRuntime(config, source);
  assert.equal(staged, path.join(root, 'desktop/runtime/node', sha256, 'node.exe'));
  assert.ok(fs.readFileSync(staged).equals(contents)); assert.equal(persistentNodeRuntime(config, source), staged);
  fs.unlinkSync(source); assert.ok(fs.readFileSync(staged).equals(contents));
});
test('tampered bundled runtime is rejected before staging or execution', t => {
  const root = fixture(t); const source = path.join(root, 'node.exe'); fs.writeFileSync(source, 'modified');
  fs.writeFileSync(path.join(root, 'manifest.json'), JSON.stringify({sha256: '0'.repeat(64)}));
  assert.throws(() => persistentNodeRuntime({projectRoot: root, bundledNode: true}, source), /校验失败/);
  assert.equal(fs.existsSync(path.join(root, 'desktop/runtime/node')), false);
});
test('portable location uses embedded stable project root and its own bundled node', t => {
  const root = fixture(t); const extracted = path.join(root, 'temporary-extraction/resources');
  fs.mkdirSync(path.join(extracted, 'node'), {recursive: true}); fs.writeFileSync(path.join(extracted, 'node/node.exe'), 'fixture');
  fs.writeFileSync(path.join(extracted, 'desktop.config.json'), JSON.stringify({projectRoot: root}));
  const result = resolveConfiguration({appPath: path.join(extracted, 'app.asar'), resourcesPath: extracted, executable: path.join(extracted, '../QuantumControl.exe'), env: {PORTABLE_EXECUTABLE_DIR: path.join(root, 'desktop/dist')}});
  assert.equal(result.projectRoot, root); assert.equal(result.nodePath, path.join(extracted, 'node/node.exe')); assert.equal(result.bundledNode, true);
});
test('absent services start once in dependency order and retain launch provenance', async t => {
  const root = fixture(t); const seen = new Set(); const launched = [];
  const manager = new ServiceManager({projectRoot: root}, {
    health: async d => seen.has(d.kind) ? {[d.identityKey]: d.identity, projectRoot: root} : null,
    occupied: async () => false, runtimes: () => ({node: 'test-node', python: 'test-python'}),
    launch: async d => {launched.push(d.kind); seen.add(d.kind); return {pid: 100 + launched.length, launchId: d.kind};},
  });
  const result = await manager.start(); assert.deepEqual(launched, ['agent', 'api']);
  assert.equal(result.services.length, 2); assert.ok(result.services.every(x => x.ready && x.ownership === 'started-by-desktop'));
  manager.detach(); assert.equal(result.services[1].pid, 102);
});
test('startup timeout preserves started service instead of killing it', async t => {
  const root = fixture(t); let clock = 0; let launches = 0;
  const manager = new ServiceManager({projectRoot: root, startupTimeoutMs: 1000}, {
    health: async () => null, occupied: async () => false, runtimes: () => ({}),
    launch: async () => {launches++; return {pid: 812};}, now: () => clock, sleep: async ms => {clock += ms;},
  });
  await assert.rejects(manager.start(), /后台进程保持运行/);
  assert.equal(launches, 1); assert.equal(manager.detach().services[0].pid, 812);
});
test('packaged config and portable override resolve without reading project secrets', t => {
  const root = fixture(t); const resources = path.join(root, 'desktop/resources'); fs.mkdirSync(resources, {recursive: true});
  fs.writeFileSync(path.join(resources, 'desktop.config.json'), JSON.stringify({projectRoot: root, pythonPath: 'D:/Python313/python.exe'}));
  const result = resolveConfiguration({appPath: 'X:/irrelevant/app.asar', resourcesPath: resources, executable: 'X:/irrelevant/app.exe', env: {}});
  assert.equal(result.projectRoot, root); assert.equal(result.apiOrigin, 'http://127.0.0.1:8765');
});
test('protected production workspace is rejected before inspecting it', () => {
  assert.equal(isProjectRoot('D:/qoskit-workspace'), false);
  assert.equal(isProjectRoot('D:/qoskit-workspace/child'), false);
});
test('project agentPort accepts only the two local numeric ports and preserves default compatibility', t => {
  const root = fixture(t), file = path.join(root, 'config/runtime.local.json'); fs.mkdirSync(path.dirname(file));
  assert.equal(configuredAgentPort(root), 8766);
  for (const agentPort of [8766,8767]) {fs.writeFileSync(file, JSON.stringify({adapter: 'arclight-native-cy8', agentPort})); assert.equal(configuredAgentPort(root), agentPort);}
  for (const agentPort of [null,true,'8767',8767.5,8765,0,65535]) {fs.writeFileSync(file, JSON.stringify({agentPort})); assert.throws(() => configuredAgentPort(root), /8766.*8767/);}
  for (const invalid of ['{broken','[]','null']) {fs.writeFileSync(file, invalid); assert.throws(() => configuredAgentPort(root));}
});
test('API identity must advertise configured 8767 while legacy absence is accepted only for 8766', () => {
  const health = {projectId:'qcal-local-f8-platform',projectRoot:'D:/project'};
  assert.equal(validateIdentity(DEFINITIONS[1], health, 'D:/project', 8766), 'project-root-verified');
  assert.throws(() => validateIdentity(DEFINITIONS[1], health, 'D:/project', 8767), /不一致/);
  for (const value of [8766,'8767',null]) assert.throws(() => validateIdentity(DEFINITIONS[1], {...health,agentPort:value}, 'D:/project', 8767), /不一致/);
  assert.throws(() => validateIdentity(DEFINITIONS[1], {...health,agentPort:8767}, 'D:/project', 8766), /不一致/);
  assert.equal(validateIdentity(DEFINITIONS[1], {...health,agentPort:8767}, 'D:/project', 8767), 'project-root-verified');
});
test('manager uses only configured 8767 and rejects an API still connected to 8766 without launching', async t => {
  const root = fixture(t); fs.mkdirSync(path.join(root,'config')); fs.writeFileSync(path.join(root,'config/runtime.local.json'), JSON.stringify({agentPort:8767}));
  const checked = []; let launches = 0;
  const manager = new ServiceManager({projectRoot:root}, {health:async def => {checked.push(def.port); return {[def.identityKey]:def.identity,projectRoot:root,...(def.kind==='api'?{agentPort:8766}:{})};}, launch:async()=>{launches++;}});
  await assert.rejects(manager.start(), /不一致/); assert.deepEqual(checked,[8767,8765]); assert.equal(launches,0);
  const correct = new ServiceManager({projectRoot:root}, {health:async def=>({[def.identityKey]:def.identity,projectRoot:root,agentPort:8767}),launch:async()=>{launches++;}});
  const result=await correct.start();assert.equal(result.agentPort,8767);assert.equal(launches,0);
});
test('launch configuration forwards one selected port to Python arguments and Node environment', () => {
  const config={projectRoot:'D:/private project',agentPort:8767};
  const defs=serviceDefinitions(config.agentPort);assert.deepEqual(defs.map(def=>def.port),[8767,8765]);
  const agent=serviceLaunchConfiguration(defs[0],config,'test-launch'), api=serviceLaunchConfiguration(defs[1],config,'test-launch');
  assert.equal(agent.env.QCAL_AGENT_PORT,'8767');assert.equal(api.env.QCAL_AGENT_PORT,'8767');
  assert.deepEqual(api.args.slice(-2),['--agent-port','8767']); assert.equal(agent.env.ELECTRON_RUN_AS_NODE,undefined);
  assert.throws(()=>serviceLaunchConfiguration(defs[0],{...config,agentPort:'8767'},'test-launch'),/8766.*8767/);
});
test('PowerShell startup parses and its isolated config/identity helpers match desktop rules without launching services', t => {
  const root=fixture(t), script=path.resolve(__dirname,'../../scripts/start.ps1'); fs.mkdirSync(path.join(root,'config'));
  const source=String.raw`
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($env:QCAL_TEST_START_SCRIPT,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
$functions=$ast.FindAll({param($item) $item -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -in @('Get-ConfiguredAgentPort','Assert-ApiAgentPort')},$false)
if($functions.Count -ne 2){throw 'Expected two pure functions'}
foreach($fn in $functions){Invoke-Expression $fn.Extent.Text}
$config=Join-Path $env:QCAL_TEST_PROJECT 'config/runtime.local.json'
foreach($port in @(8766,8767)){
  @{agentPort=$port}|ConvertTo-Json|Set-Content -LiteralPath $config -Encoding UTF8
  if((Get-ConfiguredAgentPort $env:QCAL_TEST_PROJECT) -ne $port){throw 'valid port mismatch'}
}
foreach($port in @($null,$true,'8767',8767.5,8765,0,65535)){
  @{agentPort=$port}|ConvertTo-Json|Set-Content -LiteralPath $config -Encoding UTF8
  $rejected=$false;try{Get-ConfiguredAgentPort $env:QCAL_TEST_PROJECT|Out-Null}catch{$rejected=$true}
  if(-not $rejected){throw 'invalid port accepted'}
}
Assert-ApiAgentPort ([pscustomobject]@{}) 8766
Assert-ApiAgentPort ([pscustomobject]@{agentPort=8767}) 8767
foreach($health in @([pscustomobject]@{},[pscustomobject]@{agentPort=8766},[pscustomobject]@{agentPort='8767'},[pscustomobject]@{agentPort=$null})){
  $rejected=$false;try{Assert-ApiAgentPort $health 8767}catch{$rejected=$true}
  if(-not $rejected){throw 'mismatched API accepted'}
}
Write-Output 'isolated-helpers-ok'
`;
  const output=execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(source,'utf16le').toString('base64')],{encoding:'utf8',windowsHide:true,timeout:15000,env:{...process.env,QCAL_TEST_PROJECT:root,QCAL_TEST_START_SCRIPT:script}});
  assert.match(output,/isolated-helpers-ok/);
});
