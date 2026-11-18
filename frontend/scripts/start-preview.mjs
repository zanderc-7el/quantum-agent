import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const entry = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url));
const build = fileURLToPath(new URL('../dist/index.html', import.meta.url));

if (!existsSync(entry) || !existsSync(build)) {
  console.error('Preview is not ready. Run npm ci and npm run build first.');
  process.exitCode = 1;
} else {
  const child = spawn(process.execPath, [entry, 'preview', '--host', '127.0.0.1', '--port', '5286', '--strictPort'], { cwd: root, stdio: 'inherit' });
  child.once('spawn', () => console.log(`超导量子自进化测控平台 preview PID=${child.pid} · http://127.0.0.1:5286 · Ctrl+C to stop`));
  child.once('error', (error) => { console.error(`Preview could not start: ${error.message}`); process.exitCode = 1; });
  const interrupt = () => child.kill('SIGINT');
  const terminate = () => child.kill('SIGTERM');
  process.on('SIGINT', interrupt);
  process.on('SIGTERM', terminate);
  child.once('close', (code, signal) => {
    process.off('SIGINT', interrupt);
    process.off('SIGTERM', terminate);
    process.exitCode = code ?? (signal === 'SIGINT' ? 130 : 143);
  });
}
