import fs from 'node:fs';
import { isDeepStrictEqual } from 'node:util';

/** Read back the existing files; never repair or rewrite application data. */
function inspectFile(file, expected, counts) {
  try {
    const content = fs.readFileSync(file);
    const saved = JSON.parse(content.toString('utf8'));
    // Compare the serialized state: optional undefined fields are not persisted.
    const matchesMemory = isDeepStrictEqual(saved, JSON.parse(JSON.stringify(expected)));
    return {
      status: matchesMemory ? 'ok' : 'degraded',
      errorCode: matchesMemory ? null : 'STATE_MISMATCH',
      readable: true,
      matchesMemory,
      bytes: content.byteLength,
      lastSavedAt: fs.statSync(file).mtime.toISOString(),
      counts,
    };
  } catch (error) {
    return {
      status: 'degraded',
      errorCode: error instanceof SyntaxError ? 'INVALID_JSON' : error.code === 'ENOENT' ? 'FILE_MISSING' : 'READ_ERROR',
      readable: error instanceof SyntaxError,
      matchesMemory: false,
      bytes: null,
      lastSavedAt: null,
      counts,
    };
  }
}

export function storageStatus({ dataFile, demoDataFile, experimentState, workflows }) {
  const stores = {
    experiments: inspectFile(dataFile, experimentState, {
      plans: Object.keys(experimentState.plans).length,
      runs: Object.keys(experimentState.runs).length,
    }),
    workflows: inspectFile(demoDataFile, { version: 1, workflows }, {
      total: workflows.length,
      running: workflows.filter(workflow => workflow.status === 'running').length,
    }),
  };
  return {
    status: Object.values(stores).every(store => store.status === 'ok') ? 'ok' : 'degraded',
    checkedAt: new Date().toISOString(),
    storageType: 'json-files',
    databaseConfigured: false,
    check: 'readback-consistency',
    stores,
  };
}
