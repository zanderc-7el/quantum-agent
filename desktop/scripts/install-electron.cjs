'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {spawnSync} = require('node:child_process');
async function install() {
  const root = path.resolve(__dirname, '..');
  const electronRoot = path.join(root, 'node_modules/electron');
  const {version} = require(path.join(electronRoot, 'package.json'));
  const name = `electron-v${version}-win32-x64.zip`;
  const expected = require(path.join(electronRoot, 'checksums.json'))[name];
  if (!expected) throw new Error(`Missing pinned checksum for ${name}`);
  const archive = path.join(root, 'runtime/downloads', name);
  fs.mkdirSync(path.dirname(archive), {recursive: true});
  const valid = () => fs.existsSync(archive) && createHash('sha256').update(fs.readFileSync(archive)).digest('hex') === expected;
  if (!valid()) {
    for (const url of [`https://npmmirror.com/mirrors/electron/${version}/${name}`, `https://github.com/electron/electron/releases/download/v${version}/${name}`]) {
      console.log(`Downloading pinned Electron ${version}`);
      const result = spawnSync('curl.exe', ['--silent', '--show-error', '--fail', '--location', '--connect-timeout', '15', '--max-time', '180', '--output', archive, url], {stdio: 'inherit', windowsHide: true});
      if (result.status === 0 && valid()) break;
    }
  }
  if (!valid()) throw new Error('Electron archive did not match the pinned npm package checksum.');
  const {default: extract} = await import('@electron-internal/extract-zip');
  await extract(archive, {dir: path.join(electronRoot, 'dist')});
  fs.writeFileSync(path.join(electronRoot, 'path.txt'), 'electron.exe');
  console.log(`Electron ${version} installed; SHA256 ${expected}`);
}
install().catch(error => {console.error(error.message); process.exitCode = 1;});
