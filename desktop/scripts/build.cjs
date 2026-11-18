'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
// Pinned hashes are the same as electron-builder 26.18.0 toolset declarations.
const tools = [
  {release: '7zip@1.0.0', name: '7zip-win-x64.tar.gz', sha256: 'be071f15bd6da2f78fe81c6ddef2009b0c4d8a51f36b780cb806c7e6df95e1b3', dir: '7zip'},
  {release: 'nsis@1.2.1', name: 'nsis-bundle-3.12.tar.gz', sha256: '56997fdefe25e7928a1a68b4583d08b240b66cf660234053b20131a74cc082f4', dir: 'nsis'},
  {release: 'win-codesign@1.1.0', name: 'rcedit-windows-2_0_0.zip', sha256: 'c66591ebe0919c60231f0bf79ff223e6504bfa69bc13edc1fa8bfc6177b73402', dir: 'rcedit'},
];
async function prepare(tool) {
  const archive = path.join(root, 'runtime/downloads', tool.name);
  const out = path.join(root, 'runtime/build-tools', tool.dir);
  fs.mkdirSync(path.dirname(archive), {recursive: true}); fs.mkdirSync(out, {recursive: true});
  const valid = () => fs.existsSync(archive) && createHash('sha256').update(fs.readFileSync(archive)).digest('hex') === tool.sha256;
  if (!valid()) {
    for (const base of ['https://npmmirror.com/mirrors/electron-builder-binaries', 'https://github.com/electron-userland/electron-builder-binaries/releases/download']) {
      const result = spawnSync('curl.exe', ['--silent', '--show-error', '--fail', '--location', '--connect-timeout', '15', '--max-time', '180', '--output', archive, `${base}/${tool.release}/${tool.name}`], {stdio: 'inherit', windowsHide: true});
      if (result.status === 0 && valid()) break;
    }
  }
  if (!valid()) throw new Error(`Toolset checksum mismatch: ${tool.name}`);
  if (tool.name.endsWith('.tar.gz')) await require('tar').extract({file: archive, cwd: out, strip: 1});
  else {const {default: extract} = await import('@electron-internal/extract-zip'); await extract(archive, {dir: out});}
  return out;
}
(async () => {
  const [zipDir, nsisDir, rceditDir] = await Promise.all(tools.map(prepare));
  const env = {...process.env, ELECTRON_BUILDER_CACHE: path.join(root, 'runtime/builder-cache'),
    ELECTRON_BUILDER_7ZIP_PATH: path.join(zipDir, 'bin/7za.exe'), ELECTRON_BUILDER_NSIS_DIR: nsisDir,
    ELECTRON_BUILDER_NSIS_RESOURCES_DIR: nsisDir, ELECTRON_BUILDER_RCEDIT_PATH: rceditDir};
  const target = process.argv[2] || 'portable';
  if (!['dir', 'portable', 'nsis'].includes(target)) throw new Error('Unknown Windows build target');
  const args = [require.resolve('electron-builder/cli.js'), '--win', ...(target === 'dir' ? ['--dir'] : [target]), '--x64'];
  const result = spawnSync(process.execPath, args, {cwd: root, env, stdio: 'inherit', windowsHide: true});
  process.exitCode = result.status || (result.error ? 1 : 0);
})().catch(error => {console.error(error.message); process.exitCode = 1;});
