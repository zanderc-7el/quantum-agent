import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
test('Docker publishes only loopback frontend with bridge, while backend stays internal',()=>{
 const compose=readFileSync(new URL('../../compose.yaml',import.meta.url),'utf8');
 const workspace=compose.split('  workspace:')[1].split('\n  backend:')[0];
 const backend=compose.split('\n  backend:')[1].split('\nvolumes:')[0];
 assert.match(workspace,/127\.0\.0\.1:\$\{DEMO_PORT:-5286\}:80/);
 assert.match(workspace,/networks: \[demo, preview\]/);
 assert.match(backend,/networks: \[demo\]/);
 assert.doesNotMatch(backend,/\n    ports:/);
 assert.match(compose,/demo:\n    internal: true/);
 assert.match(compose,/preview:\n    driver: bridge/);
});
