'use strict';
const {app, BrowserWindow, Menu, dialog, ipcMain, shell, session} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const {ServiceManager, resolveConfiguration, isProjectRoot, API_ORIGIN} = require('./services.cjs');
const localPage = path.join(__dirname, '../assets/startup.html');
let config = resolveConfiguration({appPath: app.getAppPath(), resourcesPath: process.resourcesPath, executable: process.execPath});
if (config.projectRoot) app.setPath('userData', path.join(config.projectRoot, 'desktop/runtime/electron-profile'));
app.setName('量子测控智能体');
app.setAppUserModelId('cn.quantum.qcal.desktop');
let win, manager, starting = false, status = {phase: 'starting', message: '正在启动本地桌面…'};
const isAllowed = url => {try {return new URL(url).origin === API_ORIGIN;} catch {return false;}};
const isStartup = url => url.startsWith('file:') && decodeURI(new URL(url).pathname).replace(/^\//, '').toLowerCase() === localPage.replace(/\\/g, '/').toLowerCase();
function trusted(event) {const url = event.senderFrame?.url || ''; if (!isAllowed(url) && !isStartup(url)) throw new Error('Untrusted desktop request');}
function update(value) {status = {...status, ...value}; if (win && !win.isDestroyed()) win.webContents.send('desktop:status', status);}
async function openUserLink(url) {
  let parsed;
  try {parsed = new URL(url);} catch {return;}
  if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password || url.length > 4096) return;
  await shell.openExternal(url);
}
function secureWindow(window) {
  window.webContents.on('will-navigate', (event, url) => {if (!isAllowed(url)) event.preventDefault();});
  window.webContents.setWindowOpenHandler(({url}) => {
    if (!isAllowed(url)) return {action: 'deny'};
    return {action: 'allow', overrideBrowserWindowOptions: {autoHideMenuBar: true, width: 1100, height: 800,
      webPreferences: {preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true}}};
  });
  window.webContents.on('did-create-window', child => secureWindow(child));
}
function createWindow() {
  win = new BrowserWindow({title: '量子测控智能体', width: 1500, height: 980, minWidth: 1000, minHeight: 700,
    backgroundColor: '#f7f8fb', show: false, icon: path.join(__dirname, '../assets/icon.ico'),
    webPreferences: {preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true}});
  secureWindow(win);
  win.once('ready-to-show', () => win.show());
  win.on('closed', () => {win = null;});
  win.loadFile(localPage);
  win.webContents.on('did-finish-load', () => update(status));
  win.webContents.on('did-fail-load', (_event, code, description, url, mainFrame) => {
    if (mainFrame && isAllowed(url) && code !== -3) {update({phase: 'error', message: `界面加载失败：${description}`}); win.loadFile(localPage);}
  });
}
async function boot() {
  if (starting) return status;
  starting = true;
  try {
    manager = new ServiceManager(config, {onStatus: update});
    const result = await manager.start();
    update({phase: 'ready', message: '本地服务已就绪', ...result});
    if (win && !win.isDestroyed()) await win.loadURL(API_ORIGIN);
  } catch (error) {update({phase: 'error', message: error.message});}
  finally {starting = false;}
  return status;
}
async function chooseProject() {
  if (starting || status.phase === 'ready') return status;
  const selected = await dialog.showOpenDialog(win, {title: '选择量子测控平台目录', properties: ['openDirectory']});
  if (!selected.canceled) {
    const root = selected.filePaths[0];
    if (!isProjectRoot(root)) {update({phase: 'error', message: '所选目录需要包含 backend、agent、frontend/dist。'}); return status;}
    config.projectRoot = root;
    await boot();
  }
  return status;
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {if (win) {if (win.isMinimized()) win.restore(); win.focus();}});
  app.whenReady().then(async () => {
    session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    session.defaultSession.setPermissionCheckHandler(() => false);
    session.defaultSession.on('will-download', (event, item) => {
      if (!isAllowed(item.getURL())) {event.preventDefault(); return;}
      // Electron's native save dialog preserves HTML report/raw download workflows.
      item.setSaveDialogOptions({title: '保存测试结果', defaultPath: path.join(app.getPath('downloads'), path.basename(item.getFilename()))});
    });
    ipcMain.handle('desktop:status', event => {trusted(event); return status;});
    ipcMain.handle('desktop:retry', event => {trusted(event); return boot();});
    ipcMain.handle('desktop:choose-project', event => {trusted(event); return chooseProject();});
    ipcMain.on('desktop:external-link', (event, url) => {try {trusted(event);} catch {return;} if (typeof url === 'string') void openUserLink(url).catch(() => {});});
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      {label: '平台', submenu: [
        {label: '服务状态', click: () => dialog.showMessageBox(win, {type: 'info', title: '本地服务状态', message: '桌面窗口与实验服务独立运行', detail: JSON.stringify(manager?.snapshot() || status, null, 2) + '\n\n关闭桌面后，后台任务保持运行。暂停或终止实验请在平台任务中操作。'})},
        {label: '打开桌面日志', click: () => {if (config.projectRoot) {const dir = path.join(config.projectRoot, 'desktop/runtime/logs'); fs.mkdirSync(dir, {recursive: true}); shell.openPath(dir);}}},
        {type: 'separator'}, {label: '退出桌面（后台任务继续）', click: () => app.quit()},
      ]},
      {label: '视图', submenu: [{role: 'reload', label: '刷新界面'}, {role: 'resetZoom', label: '实际大小'}, {role: 'zoomIn', label: '放大'}, {role: 'zoomOut', label: '缩小'}, {role: 'togglefullscreen', label: '全屏'}]},
      {label: '帮助', submenu: [{label: '关于', click: () => dialog.showMessageBox(win, {title: '量子测控智能体', message: `量子测控智能体 ${app.getVersion()}`, detail: 'Windows 本地桌面\n前端、实验状态服务、智能体服务均运行于本机。\n启动窗口不会创建或启动测量。'})}]},
    ]));
    createWindow(); await boot();
  }).catch(error => {dialog.showErrorBox('桌面启动失败', error.message); app.quit();});
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', () => manager?.detach());
}
