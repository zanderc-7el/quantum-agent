'use strict';
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const {spawn, spawnSync} = require('node:child_process');

const API_ORIGIN = 'http://127.0.0.1:8765';
const DEFINITIONS = [
  {kind: 'agent', port: 8766, health: '/health', identityKey: 'serviceId', identity: 'qcal-brain'},
  {kind: 'api', port: 8765, health: '/api/health', identityKey: 'projectId', identity: 'qcal-local-f8-platform'},
];
const normalized = value => path.resolve(value).replace(/\\/g, '/').replace(/\/$/, '').toLowerCase();
function isProjectRoot(root) {
  if (!root || typeof root !== 'string') return false;
  // Never inspect the protected production workspace, even through a selected path.
  const resolved = normalized(root);
  if (resolved === 'd:/qoskit-workspace' || resolved.startsWith('d:/qoskit-workspace/')) return false;
  try {const real = normalized(fs.realpathSync(root)); if (real === 'd:/qoskit-workspace' || real.startsWith('d:/qoskit-workspace/')) return false;} catch {return false;}
  return ['backend/server.py', 'agent/src/server.mjs', 'frontend/dist/index.html'].every(file => fs.existsSync(path.join(root, file)));
}
function readJson(file) { try {return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));} catch {return {};} }
function configuredAgentPort(projectRoot) {
  const file = path.join(projectRoot, 'config/runtime.local.json');
  if (!fs.existsSync(file)) return 8766;
  let value;
  try {value = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));} catch {throw new Error('config/runtime.local.json 无法解析，未启动服务。');}
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('runtime.local.json 必须是配置对象。');
  const port = Object.hasOwn(value, 'agentPort') ? value.agentPort : 8766;
  if (!Number.isInteger(port) || ![8766, 8767].includes(port)) throw new Error('agentPort 仅允许整数 8766 或 8767。');
  return port;
}
function serviceDefinitions(agentPort) {
  if (!Number.isInteger(agentPort) || ![8766, 8767].includes(agentPort)) throw new Error('agentPort 仅允许整数 8766 或 8767。');
  return DEFINITIONS.map(def => ({...def, ...(def.kind === 'agent' ? {port: agentPort} : {})}));
}
function resolveConfiguration({appPath, resourcesPath, executable, env = process.env}) {
  const portableDir = env.PORTABLE_EXECUTABLE_DIR || path.dirname(executable);
  const files = [path.join(resourcesPath, 'desktop.config.json'), path.join(portableDir, 'desktop.config.json')];
  const settings = Object.assign({}, ...files.map(readJson));
  const candidates = [env.QCAL_PROJECT_ROOT, settings.projectRoot];
  for (const base of [appPath, portableDir]) {
    let cursor = base;
    for (let i = 0; i < 7; i++) {candidates.push(cursor); cursor = path.dirname(cursor);}
  }
  const projectRoot = candidates.find(isProjectRoot);
  const bundledNodePath = path.join(resourcesPath, 'node/node.exe');
  const bundledNode = !env.QCAL_NODE && fs.existsSync(bundledNodePath);
  return {projectRoot: projectRoot && path.resolve(projectRoot), pythonPath: env.QCAL_PYTHON || settings.pythonPath,
    nodePath: env.QCAL_NODE || (bundledNode ? bundledNodePath : settings.nodePath), bundledNode,
    startupTimeoutMs: 45000, apiOrigin: API_ORIGIN, agentPort: projectRoot ? configuredAgentPort(projectRoot) : 8766};
}
function requestHealth(def, timeout = 5000) {
  return new Promise(resolve => {
    const req = http.get({hostname: '127.0.0.1', port: def.port, path: def.health, timeout,
      headers: {'Accept': 'application/json'}}, res => {
      let data = '';
      res.on('data', chunk => {data += chunk; if (data.length > 65536) req.destroy();});
      res.on('end', () => {try {resolve(res.statusCode === 200 ? JSON.parse(data) : null);} catch {resolve(null);}});
    });
    req.on('error', () => resolve(null)); req.on('timeout', () => req.destroy());
  });
}
function portOccupied(port) {
  return new Promise(resolve => {
    const socket = net.connect({host: '127.0.0.1', port});
    socket.setTimeout(1500);
    socket.once('connect', () => {socket.destroy(); resolve(true);});
    socket.once('error', () => resolve(false));
    socket.once('timeout', () => {socket.destroy(); resolve(true);});
  });
}
function validateIdentity(def, health, projectRoot, agentPort = 8766) {
  if (health?.[def.identityKey] !== def.identity) throw new Error(`端口 ${def.port} 已由其他服务使用，未停止任何进程。`);
  const reportedRoot = health.projectRoot || (health.agentRoot ? path.dirname(health.agentRoot) : null);
  if (!reportedRoot) throw new Error(`端口 ${def.port} 的旧版服务缺少工程路径标识，请通过原启动方式停止并重启新版服务。桌面未结束任何进程。`);
  if (reportedRoot && normalized(reportedRoot) !== normalized(projectRoot)) throw new Error(`端口 ${def.port} 对应另一份项目，请先处理该服务；未终止任何进程。`);
  if (def.kind === 'api' && health.agentPort !== agentPort && !(agentPort === 8766 && health.agentPort === undefined)) {
    throw new Error(`实验服务实际智能体端口 ${health.agentPort ?? '未声明'} 与项目配置 ${agentPort} 不一致，未复用或停止该服务。`);
  }
  return 'project-root-verified';
}
function persistentNodeRuntime(config, source) {
  if (!config.bundledNode) return source;
  const {createHash, randomUUID} = require('node:crypto');
  const bytes = fs.readFileSync(source);
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  const manifest = readJson(path.join(path.dirname(source), 'manifest.json'));
  if (manifest.sha256 !== sha256) throw new Error('随包 Node 运行时校验失败，尚未启动智能体服务。');
  const dir = path.join(config.projectRoot, 'desktop/runtime/node', sha256);
  const destination = path.join(dir, 'node.exe');
  fs.mkdirSync(dir, {recursive: true});
  const validExisting = () => fs.existsSync(destination) && createHash('sha256').update(fs.readFileSync(destination)).digest('hex') === sha256;
  if (!validExisting()) {
    if (fs.existsSync(destination)) throw new Error('已固化的 Node 运行时哈希不一致，请检查 desktop/runtime/node。');
    const temporary = path.join(dir, `node-${randomUUID()}.tmp`);
    fs.writeFileSync(temporary, bytes, {flag: 'wx'});
    try {fs.renameSync(temporary, destination);} catch (error) {if (!validExisting()) throw error;}
    finally {if (fs.existsSync(temporary)) fs.unlinkSync(temporary);}
  }
  for (const file of ['LICENSE', 'manifest.json']) {
    const from = path.join(path.dirname(source), file);
    if (fs.existsSync(from)) fs.copyFileSync(from, path.join(dir, file));
  }
  return destination;
}
function executableVersion(executable, args, name) {
  const result = spawnSync(executable, args, {encoding: 'utf8', windowsHide: true, timeout: 10000});
  if (result.status !== 0) throw new Error(`${name} 运行时不可用：${executable}`);
  return (result.stdout || '').trim();
}
function findRuntimes(config) {
  const pythonCandidates = [config.pythonPath, 'D:/Python313/python.exe', 'D:/Python312/python.exe', 'python.exe'].filter(Boolean);
  let python;
  for (const candidate of pythonCandidates) {
    try {
      // Pure standard library import; never imports platform or instrument code.
      const version = executableVersion(candidate, ['-I', '-c', 'import sys,sqlite3; print(sys.executable); print("%s.%s" % sys.version_info[:2])'], 'Python');
      const lines = version.split(/\r?\n/); const parts = lines.at(-1).split('.').map(Number);
      if (parts[0] === 3 && parts[1] >= 11) {python = lines[0]; break;}
    } catch {}
  }
  if (!python) throw new Error('未找到 Python 3.11+（需 sqlite3）。在 desktop.config.json 的 pythonPath 设置本地解释器。');
  const node = config.nodePath || 'node.exe';
  const version = executableVersion(node, ['--version'], 'Node');
  if (Number(version.replace(/^v/, '').split('.')[0]) < 24) throw new Error('智能体服务需要 Node 24+。');
  return {python, node, nodeVersion: version};
}
class ServiceManager {
  constructor(config, deps = {}) {
    this.config = config; this.records = []; this.health = deps.health || requestHealth;
    this.occupied = deps.occupied || portOccupied; this.launch = deps.launch || this.spawnDetached.bind(this);
    this.runtimes = deps.runtimes || findRuntimes; this.onStatus = deps.onStatus || (() => {});
    this.sleep = deps.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
    this.now = deps.now || Date.now;
  }
  snapshot() {return {apiOrigin: API_ORIGIN, projectRoot: this.config.projectRoot, agentPort: this.config.agentPort, services: this.records, exitPolicy: 'leave-services-running'};}
  async start() {
    if (!isProjectRoot(this.config.projectRoot)) throw new Error('请选择包含 backend、agent 和 frontend/dist 的量子测控平台目录。');
    this.config = {...this.config, agentPort: configuredAgentPort(this.config.projectRoot)};
    let runtimes;
    for (const def of serviceDefinitions(this.config.agentPort)) {
      this.onStatus({phase: 'checking', message: `正在检查${def.kind === 'api' ? '实验服务' : '智能体服务'}…`});
      let health = await this.health(def);
      if (health) {
        const identity = validateIdentity(def, health, this.config.projectRoot, this.config.agentPort);
        this.records.push({kind: def.kind, port: def.port, ownership: 'shared', identity, ready: true});
        continue;
      }
      if (await this.occupied(def.port)) throw new Error(`端口 ${def.port} 有服务响应但健康检查未通过。没有结束或替换该进程；请稍后重试或查看原服务。`);
      runtimes ||= this.runtimes(this.config);
      this.onStatus({phase: 'starting', message: `正在启动${def.kind === 'api' ? '实验服务' : '智能体服务'}…`});
      const processRecord = await this.launch(def, runtimes);
      this.records.push({...processRecord, kind: def.kind, port: def.port, ownership: 'started-by-desktop', ready: false});
      const deadline = this.now() + (this.config.startupTimeoutMs || 45000);
      while (this.now() < deadline) {
        health = await this.health(def);
        if (health) break;
        await this.sleep(500);
      }
      if (!health) throw new Error(`${def.kind} 启动未就绪，请查看 desktop/runtime/logs。后台进程保持运行，未强制结束。`);
      Object.assign(this.records.at(-1), {ready: true, identity: validateIdentity(def, health, this.config.projectRoot, this.config.agentPort)});
    }
    this.onStatus({phase: 'ready', message: '本地服务已就绪', ...this.snapshot()});
    return this.snapshot();
  }
  async spawnDetached(def, runtimes) {
    const runtimeDir = path.join(this.config.projectRoot, 'desktop/runtime');
    const logDir = path.join(runtimeDir, 'logs'); fs.mkdirSync(logDir, {recursive: true});
    const launchId = require('node:crypto').randomUUID();
    const out = fs.openSync(path.join(logDir, `${def.kind}.stdout.log`), 'a');
    const err = fs.openSync(path.join(logDir, `${def.kind}.stderr.log`), 'a');
    const executable = def.kind === 'api' ? runtimes.python : persistentNodeRuntime(this.config, runtimes.node);
    const {args, env} = serviceLaunchConfiguration(def, this.config, launchId);
    let child;
    try {
      child = spawn(executable, args, {cwd: this.config.projectRoot, detached: true, windowsHide: true, shell: false, stdio: ['ignore', out, err], env});
      await new Promise((resolve, reject) => {child.once('spawn', resolve); child.once('error', reject);});
    } finally {fs.closeSync(out); fs.closeSync(err);}
    child.unref();
    const record = {launchId, pid: child.pid, launchedAt: new Date().toISOString(), executable, args, projectRoot: this.config.projectRoot, port: def.port, agentPort: this.config.agentPort};
    // Launch provenance only. A saved PID is never authority to stop a process.
    fs.appendFileSync(path.join(runtimeDir, 'service-launches.jsonl'), JSON.stringify({kind: def.kind, ...record}) + '\n');
    return record;
  }
  detach() {return this.snapshot();} // Deliberately no process.kill, taskkill, shutdown API or process-tree teardown.
}
function serviceLaunchConfiguration(def, config, launchId) {
  serviceDefinitions(config.agentPort);
  const args = def.kind === 'api' ? ['-B', '-X', 'utf8', '-m', 'backend.server', '--root', config.projectRoot, '--port', '8765', '--agent-port', String(config.agentPort)] : [path.join(config.projectRoot, 'agent/src/server.mjs')];
  const env = {...process.env, QCAL_AGENT_PORT: String(config.agentPort), PYTHONUTF8: '1', QCAL_DESKTOP_LAUNCH_ID: launchId};
  delete env.ELECTRON_RUN_AS_NODE;
  return {args, env};
}
module.exports = {API_ORIGIN, DEFINITIONS, ServiceManager, resolveConfiguration, isProjectRoot, validateIdentity, requestHealth, portOccupied, findRuntimes, persistentNodeRuntime, configuredAgentPort, serviceDefinitions, serviceLaunchConfiguration};
