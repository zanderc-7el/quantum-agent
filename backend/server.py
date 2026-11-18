"""Loopback HTTP API, durable SSE snapshots, and the original frontend build."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
import signal
import sys
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from .common import APIError, ROOT, checked_path, dumps
from .controller import Controller

def configured_agent_port(root, override=None):
    local = checked_path(checked_path(root) / 'config' / 'runtime.local.json', parent=checked_path(root))
    settings = json.loads(local.read_text(encoding='utf-8-sig')) if local.is_file() else {}
    if not isinstance(settings, dict):
        raise ValueError('runtime.local.json must contain an object')
    value = override if override is not None else settings.get('agentPort', 8766)
    if type(value) is not int or value not in (8766, 8767):
        raise ValueError('agentPort must be 8766 or 8767 on 127.0.0.1')
    return value

def html_report(controller, report):
    escape = lambda v: html.escape(str(v))
    wf = report['workflow']
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
             '<title>' + escape(wf['name']) + '</title><style>body{font:15px system-ui;margin:32px auto;max-width:1400px;color:#182331}h1{border-bottom:3px solid #b42624;padding-bottom:18px}section{break-inside:avoid;margin:28px 0}figure{display:inline-block;width:46%;vertical-align:top;margin:1%}img{width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f8;padding:16px}small{color:#556}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px}@media print{body{margin:10mm}figure{width:46%}}</style>',
             '<h1>' + escape(wf['name']) + '</h1>',
             '<p>来源：' + ('历史实测回放' if wf['mode'] == 'replay' else '本轮真机测量 · ' + escape(wf.get('adapterId', '测量执行器'))) + ' · ' + escape(wf['target']) + '</p>',
             '<p>流程完整验收：' + ('通过' if report['workflowComplete'] else '尚未全部通过') + '</p>']
    if wf.get('coverage') == 'working_point':
        parts.append('<p>任务范围：复刻已有工作点，所选10项脉冲测量。' + escape(wf.get('completionScope', {}).get('meaning', '')) + '</p>')
        parts.append('<p>历史来源仅作初值（seed_only）：' + escape(wf.get('seedReference', {}).get('sourceId', '')) + '；未复制历史通过状态或 IQ 参考。</p>')
    for item in report['nodes']:
        node = item['node']
        parts.append('<section><h2>' + escape(node['qubits'] + ' · ' + node['label']) + '</h2>')
        if node.get('engineState') == 'stale':
            parts.append('<p>当前节点需复测：前置参数已变化。以下保留记录不代表当前参数已通过。</p>')
        if not item['attempts']:
            parts.append('<p>尚无该方法的采集记录。</p>')
        for attempt in item['attempts']:
            result = attempt.get('result') or {}
            parts.append('<h3>尝试 ' + str(attempt['number']) + ' · ' + escape(result.get('methodLabel', attempt.get('procedureId', ''))) + '</h3>')
            quality = result.get('quality', {})
            parts.append('<p>' + escape(quality.get('status', attempt['state'])) + '：' + escape('；'.join(quality.get('reasons', []))) + '</p>')
            stages = [('本次采集', result)]
            if result.get('selectionResult'):
                stages = [('候选测量', result['selectionResult']), ('独立复测', result)]
            for label, stage in stages:
                parts.append('<h4>' + label + '</h4>')
                if stage.get('supplementalQuality'):
                    parts.append('<details><summary>重复性补充验收</summary><pre>' + escape(json.dumps(stage['supplementalQuality'], ensure_ascii=False, indent=2)) + '</pre></details>')
                if stage.get('metrics'):
                    parts.append('<pre>' + escape(json.dumps(stage['metrics'], ensure_ascii=False, indent=2)) + '</pre>')
                for artifact in stage.get('artifacts', []):
                    if artifact['kind'] != 'image':
                        continue
                    try:
                        path, _ = controller.artifact(artifact['id'])
                        mime = mimetypes.guess_type(path.name)[0] or 'image/png'
                        encoded = base64.b64encode(path.read_bytes()).decode('ascii')
                        parts.append('<figure><img src="data:' + mime + ';base64,' + encoded + '"><figcaption>' + escape(artifact['name']) + '</figcaption><small>SHA256 ' + escape(artifact['sha256']) + '</small></figure>')
                    except (APIError, OSError) as exc:
                        parts.append('<p>产物校验失败：' + escape(str(exc)) + '</p>')
                parts.append('<details><summary>' + label + '参数和来源</summary><pre>' + escape(json.dumps({'parameters': stage.get('parameters', attempt.get('parameters')), 'provenance': stage.get('provenance'), 'quality': stage.get('quality'), 'artifacts': stage.get('artifacts')}, ensure_ascii=False, indent=2)) + '</pre></details>')
        if item['decisions']:
            parts.append('<h3>决策与回退</h3><pre>' + escape(json.dumps(item['decisions'], ensure_ascii=False, indent=2)) + '</pre>')
        parts.append('</section>')
    parts.append('</html>')
    return ''.join(parts).encode('utf-8')

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, controller):
        self.controller = controller
        self.session_token = secrets.token_urlsafe(32)
        self.live_series = None
        self.live_series_lock = threading.Lock()
        super().__init__(address, Handler)
        self.allowed_hosts = {f'127.0.0.1:{self.server_address[1]}', f'localhost:{self.server_address[1]}'}

class Handler(BaseHTTPRequestHandler):
    server_version = 'QcalLocal/1'
    protocol_version = 'HTTP/1.1'
    maximum_body_bytes = 1024 * 1024
    body_read_timeout = 2.0

    @property
    def controller(self):
        return self.server.controller

    def log_message(self, fmt, *args):
        # Never print bodies, model prompts, credentials or the local session token.
        sys.stderr.write('%s %s\n' % (self.log_date_time_string(), fmt % args))

    def _host(self):
        if self.headers.get('Host') not in self.server.allowed_hosts:
            raise APIError('INVALID_HOST', '仅允许本机地址访问', 403)

    def _auth(self):
        self._host()
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in self.server.allowed_hosts} | {'http://127.0.0.1:5173', 'http://localhost:5173'}:
            raise APIError('CROSS_ORIGIN', '拒绝跨来源写操作', 403)
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise APIError('CROSS_ORIGIN', '拒绝跨站写操作', 403)
        if not secrets.compare_digest(self.headers.get('X-Qcal-Token', ''), self.server.session_token):
            raise APIError('INVALID_SESSION', '本地会话已变更，请刷新页面', 401)

    def _body_length(self, allow_empty=False):
        """Only unambiguous, bounded request framing can remain on keep-alive."""
        def reject(code, message, status=400):
            self.close_connection = True
            raise APIError(code, message, status)
        if self.headers.get_all('Transfer-Encoding'):
            reject('UNSUPPORTED_TRANSFER_ENCODING', '不支持分块或其它传输编码')
        expect = self.headers.get('Expect')
        if expect is not None and expect.lower() != '100-continue':
            reject('UNSUPPORTED_EXPECTATION', '不支持该请求 Expect 设置', 417)
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) > 1:
            reject('INVALID_BODY', '请求长度不明确')
        raw = lengths[0].strip() if lengths else '0'
        if not re.fullmatch(r'[0-9]+', raw):
            reject('INVALID_BODY', '请求长度无效')
        canonical = raw.lstrip('0') or '0'
        if len(canonical) > 7 or int(canonical) > self.maximum_body_bytes:
            reject('INVALID_BODY', '请求正文最多为 1048576 字节', 413)
        length = int(canonical)
        if not allow_empty and length == 0:
            reject('INVALID_BODY', '请求正文必须为 1–1048576 字节', 413)
        return length

    def _read_body_bytes(self, *, retain=True, allow_empty=False):
        length = self._body_length(allow_empty=allow_empty)
        remaining, chunks = length, []
        deadline = time.monotonic() + self.body_read_timeout
        previous_timeout = self.connection.gettimeout()
        try:
            while remaining:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    raise TimeoutError()
                self.connection.settimeout(timeout)
                # read1 makes at most one raw read. A per-read timeout alone
                # would otherwise allow a slow sender to reset the wait forever.
                chunk = self.rfile.read1(min(remaining, 64 * 1024))
                if not chunk:
                    self.close_connection = True
                    raise APIError('INCOMPLETE_BODY', '请求正文未完整接收')
                remaining -= len(chunk)
                if retain:
                    chunks.append(chunk)
        except TimeoutError:
            self.close_connection = True
            raise APIError('REQUEST_BODY_TIMEOUT', '请求正文接收超时', 408) from None
        except OSError:
            self.close_connection = True
            raise
        finally:
            try:
                self.connection.settimeout(previous_timeout)
            except OSError:
                pass
        return b''.join(chunks)

    def handle_expect_100(self):
        # BaseHTTPRequestHandler calls this before _write(). Do not wait for a
        # body whose sender is waiting for our 100-continue acknowledgement.
        try:
            self._auth()
            self._body_length()
        except APIError as exc:
            self.close_connection = True
            self._error(exc)
            return False
        self.send_response_only(100)
        self.end_headers()
        return True

    def _body(self):
        raw = self._read_body_bytes()
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise APIError('INVALID_JSON', '请求不是有效 JSON')
        if not isinstance(body, dict):
            raise APIError('INVALID_BODY', '请求必须为对象')
        return body

    def _send(self, body, status=200, content_type='application/json; charset=utf-8', headers=None):
        if not isinstance(body, bytes):
            body = dumps(body).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        if self.close_connection:
            self.send_header('Connection', 'close')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _error(self, exc):
        try:
            if isinstance(exc, APIError):
                self._send({'error': {'code': exc.code, 'message': exc.message}}, exc.status)
            else:
                self.close_connection = True
                self._send({'error': {'code': 'INTERNAL_ERROR', 'message': str(exc)}}, 500)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def do_GET(self):
        try:
            self._host()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if path == '/api/session':
                return self._send({'token': self.server.session_token, 'actor': {'id': 'local-operator', 'displayName': '现场操作员', 'role': 'leader'}})
            if path == '/api/health':
                return self._send(self.controller.health())
            match = re.fullmatch(r'/api/account/(status|models)', path)
            if match:
                return self._send(self.controller.account(match[1]))
            if path == '/api/catalog':
                return self._send(self.controller.catalog.describe(self.controller.adapter.capabilities(), self.controller._bounds()))
            if path == '/api/knowledge':
                return self._send(self.controller.knowledge())
            if path == '/api/workflows':
                return self._send(self.controller.list())
            if path == '/api/events':
                return self._events(query)
            match = re.fullmatch(r'/api/workflows/([^/]+)', path)
            if match:
                return self._send(self.controller.get(match[1]))
            match = re.fullmatch(r'/api/workflows/([^/]+)/nodes/([^/]+)', path)
            if match:
                return self._send(self.controller.detail(match[1], match[2]))
            match = re.fullmatch(r'/api/workflows/([^/]+)/nodes/([^/]+)/live-series', path)
            if match:
                workflow = self.controller.get(match[1])
                node = next((n for n in workflow['nodes'] if n['id'] == match[2]), None)
                if node is None:
                    raise APIError('NOT_FOUND', '节点不存在', 404)
                try:
                    cursor = int(query.get('after', ['0'])[0])
                except ValueError:
                    raise APIError('INVALID_CURSOR', '采集点游标无效')
                if cursor < 0:
                    raise APIError('INVALID_CURSOR', '采集点游标无效')
                with self.controller.store.connect() as rt:
                    row = rt.db.execute('SELECT operation_id FROM platform_effects WHERE workflow_id=? AND attempt_id=? ORDER BY rowid DESC LIMIT 1',
                                        (workflow['id'], node.get('latestAttemptId'))).fetchone()
                if workflow['mode'] != 'real' or not row or not str(workflow.get('adapterId', '')).startswith('arclight'):
                    return self._send({'operationId': None, 'cursor': 0, 'points': [], 'pending': True,
                                       'source': 'live_acquired_points', 'quality': 'unverified'})
                operation = row[0]
                if query.get('operationId', [''])[0] != operation:
                    cursor = 0
                with self.server.live_series_lock:
                    if self.server.live_series is None:
                        from .live_series import LiveSeries
                        self.server.live_series = LiveSeries(self.controller.root, self.controller.profile['python'])
                    reader = self.server.live_series
                return self._send(reader.read(operation, after=cursor, limit=256))
            match = re.fullmatch(r'/api/workflows/([^/]+)/report', path)
            if match:
                report = self.controller.report(match[1])
                if query.get('format') == ['html']:
                    return self._send(html_report(self.controller, report), content_type='text/html; charset=utf-8')
                return self._send(report, headers={'Content-Disposition': 'attachment; filename="qcal-report.json"'})
            match = re.fullmatch(r'/api/artifacts/([^/]+)', path)
            if match:
                artifact, metadata = self.controller.artifact(match[1])
                kind = metadata.get('kind')
                mime = (mimetypes.guess_type(artifact.name)[0] or 'image/png') if kind == 'image' else 'application/octet-stream'
                return self._send(artifact.read_bytes(), content_type=mime,
                                  headers={} if kind == 'image' else {'Content-Disposition': 'attachment; filename="artifact' + artifact.suffix + '"'})
            if path.startswith('/api/'):
                raise APIError('NOT_FOUND', '接口不存在', 404)
            static_root = self.controller.root / 'frontend' / 'dist'
            static = checked_path(static_root / (path.lstrip('/') or 'index.html'), parent=static_root)
            if not static.is_file() and '.' not in Path(path).name:
                static = static_root / 'index.html'
            if not static.is_file():
                raise APIError('NOT_FOUND', '前端尚未构建或文件不存在', 404)
            mime = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html'}.get(static.suffix, mimetypes.guess_type(static.name)[0] or 'application/octet-stream')
            return self._send(static.read_bytes(), content_type=mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            self._error(exc)

    def _events(self, query):
        try:
            after = int(self.headers.get('Last-Event-ID') or query.get('after', ['0'])[0])
            if after < 0:
                raise ValueError()
        except ValueError:
            raise APIError('INVALID_CURSOR', '事件游标必须为非负整数')
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-transform')
        self.send_header('Connection', 'close')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        first = True
        while not self.controller.stop_event.is_set():
            snapshot = self.controller.snapshot()
            if first or snapshot['seq'] > after:
                self.wfile.write(f"id: {snapshot['seq']}\nevent: snapshot\ndata: {dumps(snapshot)}\n\n".encode('utf-8'))
                after, first = snapshot['seq'], False
            else:
                self.wfile.write(b': heartbeat\n\n')
            self.wfile.flush()
            with self.controller.store.changed:
                # Recheck under the condition lock so a commit between flush()
                # and wait() cannot be lost until the next heartbeat.
                latest = self.controller.snapshot()['seq']
                if latest <= after and not self.controller.stop_event.is_set():
                    self.controller.store.changed.wait(timeout=10)
        self.close_connection = True

    def do_POST(self):
        self._write('POST')

    def do_PATCH(self):
        self._write('PATCH')

    def _write(self, method):
        try:
            try:
                self._auth()
            except APIError as exc:
                # Authentication still precedes every dispatch. Consume only a
                # trusted bounded body, without parsing it, before replying so
                # neither keep-alive nor a normal close leaves unread JSON.
                try:
                    self._read_body_bytes(retain=False, allow_empty=True)
                except (APIError, OSError):
                    self.close_connection = True
                return self._error(exc)
            path = unquote(urlsplit(self.path).path)
            body = self._body()
            match = re.fullmatch(r'/api/account/(send-code|login|connect|logout|test)', path)
            if match and method == 'POST':
                return self._send(self.controller.account(match[1], body))
            if path == '/api/workflows' and method == 'POST':
                return self._send(self.controller.create(body), 201)
            if path == '/api/chat' and method == 'POST':
                return self._send(self.controller.chat(body))
            match = re.fullmatch(r'/api/knowledge/candidates/([^/]+)/publish', path)
            if match and method == 'POST':
                return self._send(self.controller.publish_experience(match[1], body.get('operationId')))
            if path == '/api/system/shutdown' and method == 'POST':
                with self.controller.store.transaction() as rt:
                    active = any(w['status'] in ('queued', 'running') or w.get('engineState') in ('deciding', 'decision_queued')
                                 for w in self.controller.store.workflows(rt))
                    unresolved = rt.db.execute("SELECT 1 FROM platform_leases LIMIT 1").fetchone() or rt.db.execute(
                        "SELECT 1 FROM platform_effects WHERE state IN ('pending','unknown') LIMIT 1").fetchone()
                    if active or unresolved:
                        raise APIError('SHUTDOWN_CHECKPOINT_REQUIRED', '任务仍在执行或回执待核查；到无活动采集的检查点后才能关闭服务', 409)
                    self.controller.stop_event.set()
                self._send({'status': 'stopping_at_checkpoint'}, 202)
                self.controller.wake.set()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            match = re.fullmatch(r'/api/workflows/([^/]+)/actions', path)
            if match and method == 'POST':
                return self._send(self.controller.action(match[1], body))
            match = re.fullmatch(r'/api/workflows/([^/]+)/policy', path)
            if match and method == 'PATCH':
                return self._send(self.controller.set_policy(match[1], body))
            match = re.fullmatch(r'/api/workflows/([^/]+)/budget', path)
            if match and method == 'PATCH':
                return self._send(self.controller.set_budget(match[1], body))
            match = re.fullmatch(r'/api/workflows/([^/]+)/readout-withdrawal', path)
            if match and method == 'POST':
                return self._send(self.controller.withdraw_readout_candidate(match[1], body))
            match = re.fullmatch(r'/api/workflows/([^/]+)/supplemental-acceptance', path)
            if match and method == 'POST':
                return self._send(self.controller.install_supplemental_acceptance(match[1], body))
            match = re.fullmatch(r'/api/workflows/([^/]+)/nodes/([^/]+)/parameters', path)
            if match and method == 'PATCH':
                return self._send(self.controller.set_parameters(match[1], match[2], body))
            raise APIError('NOT_FOUND', '接口不存在', 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:
            self._error(exc)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--agent-port', type=int, choices=(8766, 8767))
    args = parser.parse_args()
    try:
        agent_port = configured_agent_port(args.root, args.agent_port)
    except (ValueError, APIError) as exc:
        parser.error(str(exc))
    controller = Controller(args.root, brain_url=f'http://127.0.0.1:{agent_port}')
    server = Server(('127.0.0.1', args.port), controller)
    print(f'Quantum control platform: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
        server.server_close()

if __name__ == '__main__':
    main()
