/** Bounded, read-only access to a server-selected source tree. Never executes source. */
import fs from 'node:fs/promises';
import {constants} from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';

const ROOTS = ['README.md', 'INTEGRATION_CONTRACT.md', 'agent/README.md', 'backend', 'adapters', 'agent/src', 'frontend/src', 'knowledge', 'profiles'];
const EXTENSIONS = new Set(['.py', '.mjs', '.ts', '.tsx', '.md', '.yml', '.yaml']);
// Configuration JSON is excluded by default, including every profiles JSON file.
const STATIC_JSON = new Set(['knowledge/rules.json', 'adapters/rb_contract.json']);
const EXCLUDED = new Set(['node_modules', 'vendor', 'vendors', 'data', 'runtime', 'log', 'logs',
  'reports', 'credentials', 'results', 'result', 'outputs', 'output', 'artifacts', 'offline_validation',
  '__pycache__', 'dist', 'build', 'cache', 'tmp', 'temp', 'storage', 'uploads', 'sessions', 'snapshots',
  'captures', 'recordings', 'downloads', 'site-packages', 'venv', 'environment', 'env', 'account',
  'accounts', 'secrets', 'tokens', 'vault']);
const SENSITIVE_NAME = /(?:^|[_.-])(?:env|accounts?|tokens?|secrets?|credentials?|passwords?|vault|private[_-]?key|api[_-]?key|logs?|data)(?:[_.-]|$)/i;
const MAX_FILE_BYTES = 512 * 1024;
const MAX_SCAN_BYTES = 8 * 1024 * 1024;
const MAX_ENTRIES = 5000;
const MAX_RESULT_CHARS = 12000;
const MAX_LINES = 160;
const error = code => Object.assign(new Error(code), {code});
const fail = code => {throw error(code);};
const samePath = (a, b) => process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
const protectedPath = value => value.replaceAll('\\', '/').split('/').some(part => part.toLowerCase() === 'qoskit-workspace');

function relativePath(value, {empty = false} = {}) {
  if (empty && (value === undefined || value === '')) return '';
  if (typeof value !== 'string' || !value || value.length > 500 || /^[\\/]/.test(value)
    || /[:%\x00-\x1f\x7f]/.test(value)) fail('source_path_not_allowed');
  const relative = value.replaceAll('\\', '/');
  const parts = relative.split('/');
  if (parts.some(part => !part || part === '.' || part === '..' || part.startsWith('.') || /[. ]$/.test(part))
    || protectedPath(relative)) fail('source_path_not_allowed');
  if (!ROOTS.some(scope => relative === scope || relative.startsWith(scope + '/'))) fail('source_path_not_allowed');
  if (parts.some(part => EXCLUDED.has(part.toLowerCase()) || SENSITIVE_NAME.test(part))) fail('source_path_not_allowed');
  return relative;
}

function allowedFile(relative) {
  try {relativePath(relative);} catch {return false;}
  return EXTENSIONS.has(path.posix.extname(relative).toLowerCase()) || STATIC_JSON.has(relative);
}

function sensitiveContent(text) {
  // Reject the file rather than silently showing a redacted line as exact source.
  return /-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----/.test(text)
    || /["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|secret|token)["']?\s*[:=]\s*(["'`])[^"'`\r\n]+\1/i.test(text)
    || /\bBearer\s+[A-Za-z0-9._-]{12,}/i.test(text)
    || /\b(?:sk|ghp|github_pat)[_-][A-Za-z0-9_-]{20,}\b/.test(text)
    || /https?:\/\/[^\s/:]+:[^\s/@]+@/.test(text);
}

function argumentsFor(name, args) {
  const keys = {search_source: ['query', 'pathPrefix', 'limit'], read_source: ['path', 'startLine', 'endLine'],
    list_source_files: ['pathPrefix', 'limit']}[name];
  if (!keys) fail('source_tool_not_allowed');
  if (!args || typeof args !== 'object' || Array.isArray(args) || Object.keys(args).some(key => !keys.includes(key))) fail('source_invalid_arguments');
  if (name === 'search_source' && (typeof args.query !== 'string' || !args.query.trim() || args.query.length > 200 || /[\r\n\x00]/.test(args.query))) fail('source_invalid_arguments');
  for (const field of ['limit', 'startLine', 'endLine']) {
    if (args[field] !== undefined && (!Number.isSafeInteger(args[field]) || args[field] < 1 || args[field] > (field === 'limit' ? 100 : 1000000))) fail('source_invalid_arguments');
  }
  if (args.endLine !== undefined && args.endLine < (args.startLine ?? 1)) fail('source_invalid_arguments');
}

/** The caller must supply the trusted project root, never a model-supplied context root. */
export function createSourceTools({root} = {}) {
  if (typeof root !== 'string' || !path.isAbsolute(root) || protectedPath(root)) fail('source_root_not_allowed');
  const projectRoot = path.resolve(root);

  async function verifyRoot() {
    let current = path.parse(projectRoot).root;
    for (const part of projectRoot.slice(current.length).split(path.sep).filter(Boolean)) {
      current = path.join(current, part);
      let stat;
      try {stat = await fs.lstat(current);} catch {fail('source_root_not_allowed');}
      if (stat.isSymbolicLink() || !stat.isDirectory()) fail('source_root_not_allowed');
    }
    if (!samePath(await fs.realpath(projectRoot), projectRoot)) fail('source_root_not_allowed');
  }

  async function verifyPath(relative, allowMissing = false) {
    await verifyRoot();
    let current = projectRoot;
    let stat;
    const parts = relative.split('/');
    for (let index = 0; index < parts.length; index++) {
      current = path.join(current, parts[index]);
      try {stat = await fs.lstat(current);} catch (caught) {
        if (allowMissing && caught.code === 'ENOENT') return null;
        fail(caught.code === 'ENOENT' ? 'source_not_found' : 'source_path_not_allowed');
      }
      if (stat.isSymbolicLink() || (index < parts.length - 1 && !stat.isDirectory())) fail('source_path_not_allowed');
    }
    if (!samePath(await fs.realpath(current), current)) fail('source_path_not_allowed');
    return stat;
  }

  async function loadSource(relative, state) {
    if (!allowedFile(relative)) fail('source_path_not_allowed');
    const before = await verifyPath(relative);
    if (!before.isFile() || before.nlink > 1) fail('source_path_not_allowed');
    if (before.size > MAX_FILE_BYTES) fail('source_file_too_large');
    if (state.scannedBytes + before.size + 1 > MAX_SCAN_BYTES) fail('source_scan_limit');
    let handle;
    try {
      handle = await fs.open(path.join(projectRoot, relative), constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
      const opened = await handle.stat();
      if (opened.ino !== before.ino || opened.dev !== before.dev || opened.nlink > 1 || !opened.isFile()) fail('source_changed');
      await verifyPath(relative);
      const buffer = Buffer.alloc(before.size + 1);
      let used = 0;
      while (used < buffer.length) {
        const {bytesRead} = await handle.read(buffer, used, buffer.length - used, used);
        if (!bytesRead) break;
        used += bytesRead;
      }
      state.scannedBytes += used;
      state.scannedFiles++;
      const after = await handle.stat();
      const current = await verifyPath(relative);
      if (used !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs
        || current.ino !== before.ino || current.dev !== before.dev || current.nlink > 1) fail('source_changed');
      const raw = buffer.subarray(0, used);
      let text;
      try {text = new TextDecoder('utf-8', {fatal: true}).decode(raw);} catch {fail('source_not_text');}
      if (text.includes('\0')) fail('source_not_text');
      if (sensitiveContent(text)) fail('source_sensitive_content');
      const lines = text.split(/\r\n|\n|\r/);
      if (lines.at(-1) === '') lines.pop();
      return {path: relative, sha256: createHash('sha256').update(raw).digest('hex'), bytes: used, lines};
    } catch (caught) {
      if (String(caught.code).startsWith('source_')) throw caught;
      fail('source_read_failed');
    } finally {await handle?.close();}
  }

  async function* files(prefix, state) {
    const relevant = relative => !prefix || relative.startsWith(prefix) || prefix.startsWith(relative + '/');
    async function* walk(relative, depth) {
      if (!relevant(relative)) return;
      if (depth > 16 || state.entries >= MAX_ENTRIES) {state.truncated = true; return;}
      let stat;
      try {relativePath(relative); stat = await verifyPath(relative, true);} catch {return;}
      if (!stat) return;
      if (stat.isFile()) {
        if ((!prefix || relative.startsWith(prefix)) && allowedFile(relative)) yield relative;
        return;
      }
      if (!stat.isDirectory()) return;
      let directory;
      try {directory = await fs.opendir(path.join(projectRoot, relative));} catch {state.truncated = true; return;}
      for await (const entry of directory) {
        if (++state.entries > MAX_ENTRIES) {state.truncated = true; break;}
        if (!entry.isSymbolicLink()) yield* walk(relative + '/' + entry.name, depth + 1);
      }
    }
    for (const scope of ROOTS) yield* walk(scope, 0);
  }

  // Include metadata and JSON escaping in the cap. Reserve room for counters.
  function append(result, key, item) {
    result[key].push(item);
    if (JSON.stringify(result).length <= MAX_RESULT_CHARS - 200) return true;
    result.truncated = true;
    if (typeof item.content === 'string') {
      const original = item.content;
      let low = 0, high = original.length;
      item.truncated = true;
      while (low < high) {
        const middle = Math.ceil((low + high) / 2);
        item.content = original.slice(0, middle);
        if (JSON.stringify(result).length <= MAX_RESULT_CHARS - 200) low = middle;
        else high = middle - 1;
      }
      item.content = original.slice(0, low);
      if (low > 0) return false;
    }
    result[key].pop();
    return false;
  }

  async function execute(name, args = {}) {
    argumentsFor(name, args);
    const state = {scannedBytes: 0, scannedFiles: 0, searchedFiles: 0, skippedFiles: 0, entries: 0, truncated: false};
    const result = {tool: name, truncated: false, scannedBytes: 0, scannedFiles: 0};
    if (name === 'read_source') {
      const relative = relativePath(args.path);
      const file = await loadSource(relative, state);
      const start = args.startLine ?? 1;
      const requestedEnd = args.endLine ?? start + 79;
      const end = Math.min(requestedEnd, start + MAX_LINES - 1, file.lines.length);
      Object.assign(result, {path: relative, sha256: file.sha256, startLine: start, endLine: end,
        totalLines: file.lines.length, lines: [], truncated: requestedEnd > start + MAX_LINES - 1 && file.lines.length >= start + MAX_LINES});
      for (let line = start; line <= end; line++) {
        if (!append(result, 'lines', {line, content: file.lines[line - 1]})) break;
      }
      result.endLine = result.lines.at(-1)?.line ?? null;
    } else {
      const prefix = relativePath(args.pathPrefix, {empty: true});
      await verifyRoot();
      if (prefix) await verifyPath(prefix, true);
      const key = name === 'search_source' ? 'matches' : 'files';
      const limit = args.limit ?? 30;
      if (name === 'search_source') {
        const terms = [...new Set(args.query.trim().split(/\s+/u))];
        // Explain the existing matcher; never silently broaden a query or its
        // authorized tree. Suggested terms are options for a later model call.
        Object.assign(result, {
          query: {text: args.query, mode: 'literal_substring', caseSensitive: true},
          coverage: {scope: 'allowlisted_project_source_only', pathPrefix: prefix || null,
            uploadedDocumentsIncluded: false, searchedFiles: 0, skippedFiles: 0, boundedScanFinished: false},
          interpretation: 'no_literal_matches_in_searched_source_not_proof_of_absence',
          followup: {keywordCandidates: terms.length > 1 ? terms.filter(term => term.length > 1).slice(0, 6) : [],
            source: 'Search a shorter identifier or keyword within the same allowed pathPrefix; use list_source_files then read_source to inspect context and applicability.',
            documents: 'Uploaded documents are not searched here. Use list_documents and read_document within the already authorized document scope.'},
        });
      }
      result[key] = [];
      scan: for await (const relative of files(prefix, state)) {
        let file;
        try {file = await loadSource(relative, state);} catch (caught) {
          if (caught.code === 'source_scan_limit') {state.truncated = true; break;}
          state.skippedFiles++;
          if (['source_sensitive_content', 'source_not_text', 'source_path_not_allowed'].includes(caught.code)) continue;
          state.truncated = true;
          continue;
        }
        if (key === 'files') {
          if (result.files.length >= limit) {result.truncated = true; break;}
          if (!append(result, key, {path: relative, line: 1, sha256: file.sha256, bytes: file.bytes, lineCount: file.lines.length})) break;
        } else {
          state.searchedFiles++;
          for (let index = 0; index < file.lines.length; index++) {
            if (!file.lines[index].includes(args.query)) continue;
            if (result.matches.length >= limit) {result.truncated = true; break scan;}
            if (!append(result, key, {path: relative, line: index + 1, content: file.lines[index], sha256: file.sha256})) break scan;
          }
        }
      }
    }
    result.scannedBytes = state.scannedBytes;
    result.scannedFiles = state.scannedFiles;
    result.truncated ||= state.truncated;
    if (name === 'search_source') {
      Object.assign(result.coverage, {searchedFiles: state.searchedFiles, skippedFiles: state.skippedFiles,
        boundedScanFinished: !result.truncated && state.skippedFiles === 0});
      if (result.matches.length) result.interpretation = 'literal_matches_require_context_and_applicability_check';
    }
    return result;
  }
  return Object.freeze({execute});
}
