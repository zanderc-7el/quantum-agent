'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {spawnSync} = require('node:child_process');
const {findRuntimes} = require('../src/services.cjs');
const root = path.resolve(__dirname, '../..');
if (!fs.existsSync(path.join(__dirname, '../node_modules/electron/dist/electron.exe'))) {
  const result = spawnSync(process.execPath, [path.join(__dirname, 'install-electron.cjs')], {stdio: 'inherit', windowsHide: true});
  if (result.status !== 0) process.exit(result.status || 1);
}
const runtimeDir = path.join(__dirname, '../resources/node');
const runtimes = findRuntimes({nodePath: process.execPath, pythonPath: process.env.QCAL_PYTHON});
fs.mkdirSync(runtimeDir, {recursive: true});
fs.copyFileSync(process.execPath, path.join(runtimeDir, 'node.exe'));
fs.copyFileSync(path.join(__dirname, '../assets/node-LICENSE.txt'), path.join(runtimeDir, 'LICENSE'));
fs.writeFileSync(path.join(runtimeDir, 'manifest.json'), JSON.stringify({nodeVersion: process.version, sha256: createHash('sha256').update(fs.readFileSync(process.execPath)).digest('hex'), source: process.execPath}, null, 2));
fs.writeFileSync(path.join(__dirname, '../resources/desktop.config.json'), JSON.stringify({projectRoot: root, pythonPath: runtimes.python}, null, 2));
console.log(`Runtime prepared: Node ${process.version}; external Python ${runtimes.python}; project ${root}`);
