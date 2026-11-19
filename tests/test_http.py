"""Real loopback HTTP/SSE contracts; fake adapter, temporary SQLite, no hardware."""
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from backend.server import Handler, Server
try:
    from test_controller import make_fixture
except ImportError:
    from tests.test_controller import make_fixture


class HTTPContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-http-test-')
        self.controller, self.adapter = make_fixture(self.directory.name)
        self.server = Server(('127.0.0.1', 0), self.controller)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.controller.close()
        with self.controller.store.changed:
            self.controller.store.changed.notify_all()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            all_headers = {'Connection': 'close', **(headers or {})}
            if body is not None:
                all_headers['Content-Type'] = 'application/json'
                body = json.dumps(body)
            conn.request(method, path, body=body, headers=all_headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), json.loads(raw)
        finally:
            conn.close()

    def session(self):
        status, _, response = self.request('GET', '/api/session')
        self.assertEqual(status, 200, response)
        return response['token']

    def test_session_and_authenticated_workflow_creation(self):
        token = self.session()
        status, headers, wf = self.request('POST', '/api/workflows',
            {'name': 'HTTP fake workflow', 'resourceIds': ['Q11'], 'mode': 'replay'}, {'X-Qcal-Token': token})
        self.assertEqual(status, 201, wf)
        self.assertEqual(wf['mode'], 'replay')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        status, _, workflows = self.request('GET', '/api/workflows')
        self.assertEqual(status, 200)
        self.assertEqual(workflows[0]['id'], wf['id'])
        self.assertEqual(self.adapter.calls, [])

    def test_archive_routes_only_read_results_without_dispatch(self):
        calls = []
        self.controller.archive_status = lambda: {'storage': 'local', 'cloudConfigured': False}
        self.controller.archive_list = lambda **kwargs: calls.append(kwargs) or {'items': [], 'nextCursor': None}
        self.controller.archive_item = lambda key: {'id': key, 'kind': 'measurement'}
        status, _, value = self.request('GET', '/api/archive/status')
        self.assertEqual(status, 200)
        self.assertFalse(value['cloudConfigured'])
        status, _, value = self.request('GET', '/api/archive?workflowId=wf-read&limit=12&cursor=abc')
        self.assertEqual(status, 200)
        self.assertEqual(calls, [{'workflow_id': 'wf-read', 'limit': 12, 'cursor': 'abc'}])
        self.assertEqual(self.request('GET', '/api/archive/a-1')[2]['id'], 'a-1')
        self.assertEqual(self.request('GET', '/api/archive?limit=bad')[0], 400)
        self.assertEqual(self.adapter.calls, [])

    def test_report_creation_requires_session_and_does_not_dispatch_measurement(self):
        calls = []
        class Exports:
            def create(self, workflow_id, body):
                calls.append((workflow_id, body))
                return {'id': 'report-test', 'status': 'queued'}
            def get(self, key):
                return {'id': key, 'status': 'queued'}
            def list(self, workflow_id):
                return [{'workflowId': workflow_id, 'id': 'report-test', 'status': 'queued'}]
            def has_active(self):
                return True
        self.server.report_exports = Exports()
        body = {'operationId': 'export-test', 'expectedRevision': 1, 'format': 'pptx', 'appendixCount': 16}
        endpoint = '/api/workflows/wf-read/report-exports'
        self.assertEqual(self.request('POST', endpoint, body)[0], 401)
        token = self.session()
        self.assertEqual(self.request('POST', endpoint, body, {'X-Qcal-Token': token, 'Origin': 'http://untrusted.example'})[0], 403)
        self.assertEqual(calls, [])
        status, _, value = self.request('POST', endpoint, body, {'X-Qcal-Token': token})
        self.assertEqual((status, value['status']), (202, 'queued'))
        self.assertEqual(calls, [('wf-read', body)])
        self.assertEqual(self.request('GET', '/api/report-exports/report-test')[2]['id'], 'report-test')
        self.assertEqual(self.request('GET', endpoint)[2][0]['workflowId'], 'wf-read')
        status, _, value = self.request('POST', '/api/system/shutdown', {}, {'X-Qcal-Token': token})
        self.assertEqual((status, value['error']['code']), (409, 'REPORT_EXPORT_ACTIVE'))
        self.assertFalse(self.controller.stop_event.is_set())
        self.assertEqual(self.adapter.calls, [])

    def test_invalid_session_origin_and_host_are_rejected(self):
        token = self.session()
        body = {'name': 'must not create', 'resourceIds': ['Q11'], 'mode': 'real'}
        status, _, error = self.request('POST', '/api/workflows', body)
        self.assertEqual((status, error['error']['code']), (401, 'INVALID_SESSION'))
        status, _, error = self.request('POST', '/api/workflows', body,
            {'X-Qcal-Token': token, 'Origin': 'http://untrusted.example'})
        self.assertEqual((status, error['error']['code']), (403, 'CROSS_ORIGIN'))
        status, _, error = self.request('GET', '/api/session', headers={'Host': 'untrusted.example'})
        self.assertEqual((status, error['error']['code']), (403, 'INVALID_HOST'))
        self.assertEqual(self.controller.list(), [])

    def test_account_routes_require_session_and_origin_before_forwarding(self):
        calls = []
        def account(action, payload=None):
            calls.append((action, payload))
            return {'provider': 'ziyor', 'authenticated': False, 'status': 'ok'}
        self.controller.account = account
        status, _, response = self.request('GET', '/api/account/status')
        self.assertEqual(status, 200)
        self.assertFalse(response['authenticated'])
        self.assertEqual(calls, [('status', None)])
        calls.clear()
        token = self.session()
        body = {'operationId': 'account-http-1'}
        status, _, error = self.request('POST', '/api/account/test', body)
        self.assertEqual((status, error['error']['code']), (401, 'INVALID_SESSION'))
        status, _, error = self.request('POST', '/api/account/test', body,
            {'X-Qcal-Token': token, 'Origin': 'http://untrusted.example'})
        self.assertEqual((status, error['error']['code']), (403, 'CROSS_ORIGIN'))
        self.assertEqual(calls, [])
        status, _, response = self.request('POST', '/api/account/test', body, {'X-Qcal-Token': token})
        self.assertEqual(status, 200)
        self.assertEqual(calls, [('test', body)])
        self.assertEqual(self.adapter.calls, [])

    def test_write_conflict_is_409_and_duplicate_is_same_response(self):
        token = self.session()
        wf = self.controller.create({'name': 'idempotent HTTP', 'resourceIds': ['Q11'], 'mode': 'replay'})
        path = '/api/workflows/' + wf['id'] + '/actions'
        command = {'action': 'start', 'operationId': 'http-start', 'expectedRevision': wf['revision']}
        first = self.request('POST', path, command, {'X-Qcal-Token': token})
        second = self.request('POST', path, command, {'X-Qcal-Token': token})
        self.assertEqual(first[0], 200)
        self.assertEqual(first[2], second[2])
        status, _, error = self.request('POST', path, {**command, 'operationId': 'stale', 'action': 'pause'}, {'X-Qcal-Token': token})
        self.assertEqual((status, error['error']['code']), (409, 'STALE_REVISION'))

    def test_parameter_patch_auth_and_decoded_node_routing(self):
        calls = []
        def set_parameters(workflow_id, node_id, payload):
            calls.append((workflow_id, node_id, payload))
            return {'revision': 4, 'parameters': payload['parameters']}
        self.controller.set_parameters = set_parameters
        path = '/api/workflows/wf-http/nodes/Q11%3As03_rabi_amplitude%3Asq_rabi_time/parameters'
        body = {'operationId': 'parameter-http', 'expectedRevision': 3,
                'parameters': {'stop_s': 5e-6}}
        status, _, error = self.request('PATCH', path, body)
        self.assertEqual((status, error['error']['code']), (401, 'INVALID_SESSION'))
        self.assertEqual(calls, [])
        token = self.session()
        status, _, error = self.request('PATCH', path, body,
            {'X-Qcal-Token': token, 'Origin': 'http://untrusted.example'})
        self.assertEqual((status, error['error']['code']), (403, 'CROSS_ORIGIN'))
        self.assertEqual(calls, [])
        status, _, result = self.request('PATCH', path, body, {'X-Qcal-Token': token})
        self.assertEqual(status, 200, result)
        self.assertEqual(calls, [('wf-http', 'Q11:s03_rabi_amplitude:sq_rabi_time', body)])
        self.assertEqual(result['revision'], 4)

    def test_rejected_write_body_does_not_corrupt_next_request_on_same_connection(self):
        calls = []
        self.controller.set_parameters = lambda *args: calls.append(args) or {'revision': 4}
        token = self.session()
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            conn.request('PATCH', '/api/workflows/wf-http/nodes/Q11%3As01/parameters',
                body=json.dumps({'parameters': {'stop_s': 5e-6}}),
                headers={'X-Qcal-Token': token, 'Origin': 'http://untrusted.example', 'Content-Type': 'application/json'})
            socket_before = conn.sock
            rejected = conn.getresponse()
            self.assertEqual(rejected.status, 403)
            self.assertEqual(json.loads(rejected.read())['error']['code'], 'CROSS_ORIGIN')
            conn.request('GET', '/api/workflows')
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), [])
            self.assertIs(conn.sock, socket_before)
        finally:
            conn.close()
        self.assertEqual(calls, [])
        self.assertEqual(self.adapter.calls, [])

    def test_unauthorized_non_json_and_authenticated_bad_json_are_consumed_once(self):
        token = self.session()
        for authorized, expected in ((False, 401), (True, 400)):
            with self.subTest(authorized=authorized):
                conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
                try:
                    conn.request('POST', '/api/workflows', body=b'not-json',
                        headers={'X-Qcal-Token': token} if authorized else {})
                    before = conn.sock
                    response = conn.getresponse()
                    self.assertEqual(response.status, expected)
                    self.assertEqual(json.loads(response.read())['error']['code'],
                                     'INVALID_JSON' if authorized else 'INVALID_SESSION')
                    conn.request('GET', '/api/workflows')
                    response = conn.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read()), [])
                    self.assertIs(conn.sock, before)
                finally:
                    conn.close()
        self.assertEqual(self.controller.list(), [])

    def test_untrusted_framing_is_closed_without_waiting_for_body_or_mutating(self):
        token = self.session()
        cases = [('Content-Length: -1', 400), ('Content-Length: +2', 400),
                 ('Content-Length: 2, 2', 400), ('Content-Length: 2\r\nContent-Length: 2', 400),
                 ('Transfer-Encoding: chunked\r\nContent-Length: 2', 400),
                 ('Content-Length: 1048577', 413), ('Content-Length: 99999999999999999999', 413),
                 ('Content-Length: 2\r\nExpect: unsupported', 417),
                 ('Content-Length: 2\r\nExpect: 100-continue ', 417)]
        for framing, status in cases:
            with self.subTest(framing=framing):
                with socket.create_connection(('127.0.0.1', self.port), timeout=1) as sock:
                    request = ('POST /api/workflows HTTP/1.1\r\nHost: 127.0.0.1:' + str(self.port)
                        + '\r\nX-Qcal-Token: ' + token + '\r\n' + framing + '\r\n\r\n')
                    sock.sendall(request.encode('ascii'))
                    response = http.client.HTTPResponse(sock)
                    response.begin()
                    self.assertEqual(response.status, status)
                    self.assertEqual(response.getheader('Connection'), 'close')
                    self.assertIn('error', json.loads(response.read()))
                    response.close()
        self.assertEqual(self.controller.list(), [])
        self.assertEqual(self.adapter.calls, [])

    def test_incomplete_body_closes_connection_and_never_dispatches(self):
        token = self.session()
        with socket.create_connection(('127.0.0.1', self.port), timeout=1) as sock:
            request = ('POST /api/workflows HTTP/1.1\r\nHost: 127.0.0.1:' + str(self.port)
                + '\r\nX-Qcal-Token: ' + token + '\r\nContent-Length: 10\r\n\r\n{}')
            sock.sendall(request.encode('ascii'))
            sock.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(sock)
            response.begin()
            self.assertEqual(response.status, 400)
            self.assertEqual(json.loads(response.read())['error']['code'], 'INCOMPLETE_BODY')
            self.assertEqual(response.getheader('Connection'), 'close')
            response.close()
        self.assertEqual(self.controller.list(), [])

    def test_body_deadline_is_absolute_even_while_bytes_keep_arriving(self):
        token = self.session()
        for authorized, expected in ((False, 401), (True, 408)):
            with self.subTest(authorized=authorized), patch.object(Handler, 'body_read_timeout', .2):
                with socket.create_connection(('127.0.0.1', self.port), timeout=2) as sock:
                    authentication = '\r\nX-Qcal-Token: ' + token if authorized else ''
                    request = ('POST /api/workflows HTTP/1.1\r\nHost: 127.0.0.1:' + str(self.port)
                        + authentication + '\r\nContent-Length: 1000\r\n\r\n')
                    sock.sendall(request.encode('ascii'))
                    finished = threading.Event()
                    def trickle():
                        for _ in range(150):
                            if finished.wait(.02):
                                break
                            try:
                                sock.sendall(b' ')
                            except OSError:
                                break
                    sender = threading.Thread(target=trickle, daemon=True)
                    started = time.monotonic()
                    sender.start()
                    try:
                        response = http.client.HTTPResponse(sock)
                        response.begin()
                        self.assertEqual(response.status, expected)
                        self.assertEqual(json.loads(response.read())['error']['code'],
                            'REQUEST_BODY_TIMEOUT' if authorized else 'INVALID_SESSION')
                        self.assertEqual(response.getheader('Connection'), 'close')
                        response.close()
                        self.assertLess(time.monotonic() - started, 1.5)
                    finally:
                        finished.set()
                        sender.join(1)
        self.assertEqual(self.controller.list(), [])

    def test_expect_rejection_sends_final_response_before_any_continue_or_body(self):
        token = self.session()
        for authorized, length, expected in ((False, 2, 401), (True, 1048577, 413)):
            with self.subTest(authorized=authorized, length=length):
                with socket.create_connection(('127.0.0.1', self.port), timeout=1) as sock:
                    authentication = '\r\nX-Qcal-Token: ' + token if authorized else ''
                    request = ('POST /api/workflows HTTP/1.1\r\nHost: 127.0.0.1:' + str(self.port)
                        + authentication + '\r\nContent-Length: ' + str(length) + '\r\nExpect: 100-continue\r\n\r\n')
                    sock.sendall(request.encode('ascii'))
                    with sock.makefile('rb') as stream:
                        self.assertEqual(stream.readline().split()[1], str(expected).encode('ascii'))
                        headers = {}
                        for line in iter(stream.readline, b'\r\n'):
                            self.assertTrue(line)
                            key, value = line.decode('ascii').strip().split(':', 1)
                            headers[key.lower()] = value.strip()
                        self.assertEqual(headers['connection'], 'close')
                        self.assertIn('error', json.loads(stream.read(int(headers['content-length']))))
        self.assertEqual(self.controller.list(), [])

    def test_authenticated_expect_continue_dispatches_only_after_complete_body(self):
        token = self.session()
        calls = []
        self.controller.create = lambda body: calls.append(body) or {'accepted': True}
        with socket.create_connection(('127.0.0.1', self.port), timeout=1) as sock:
            request = ('POST /api/workflows HTTP/1.1\r\nHost: 127.0.0.1:' + str(self.port)
                + '\r\nX-Qcal-Token: ' + token + '\r\nContent-Length: 2\r\nExpect: 100-continue\r\nConnection: close\r\n\r\n')
            sock.sendall(request.encode('ascii'))
            with sock.makefile('rb') as stream:
                self.assertEqual(stream.readline().split()[1], b'100')
                self.assertEqual(stream.readline(), b'\r\n')
                self.assertEqual(calls, [])
                sock.sendall(b'{}')
                self.assertEqual(stream.readline().split()[1], b'201')
                headers = {}
                for line in iter(stream.readline, b'\r\n'):
                    self.assertTrue(line)
                    key, value = line.decode('ascii').strip().split(':', 1)
                    headers[key.lower()] = value.strip()
                self.assertEqual(headers['connection'], 'close')
                self.assertEqual(json.loads(stream.read(int(headers['content-length']))), {'accepted': True})
        self.assertEqual(calls, [{}])

    def test_live_series_derives_operation_from_effect_and_resets_stale_cursor(self):
        self.controller.get = lambda identity: {'id': 'wf-live', 'mode': 'real',
            'adapterId': 'arclight-test', 'nodes': [{'id': 'Q11:s01', 'latestAttemptId': 'a-live'}]}
        with self.controller.store.transaction() as rt:
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)',
                          ('a-live/measure', 'wf-live', 'a-live', 'pending', '{}'))
        calls=[]
        class Reader:
            def read(self, operation, **kwargs):
                calls.append((operation,kwargs))
                return {'operationId': operation, 'cursor': 7, 'points': [], 'quality': 'unverified'}
        self.server.live_series=Reader()
        path='/api/workflows/wf-live/nodes/Q11%3As01/live-series'
        status,_,body=self.request('GET',path+'?after=500&operationId=unrelated')
        self.assertEqual(status,200,body)
        self.assertEqual(calls[-1],('a-live/measure',{'after':0,'limit':256}))
        status,_,_=self.request('GET',path+'?after=7&operationId=a-live%2Fmeasure')
        self.assertEqual(status,200)
        self.assertEqual(calls[-1][1]['after'],7)
        status,_,_=self.request('GET',path+'?after=-1')
        self.assertEqual(status,400)
        self.assertEqual(self.adapter.calls,[])

    @staticmethod
    def read_sse(response):
        fields = {}
        for _ in range(10):
            line = response.readline().decode('utf-8').rstrip('\r\n')
            if not line and fields:
                return fields
            if ': ' in line:
                key, value = line.split(': ', 1)
                fields[key] = value
        raise AssertionError('No complete SSE frame received')

    def test_sse_delivers_committed_snapshot_and_reconnects_by_cursor(self):
        self.session()  # Also validates dynamic bind Host handling.
        wf = self.controller.create({'name': 'SSE fake', 'resourceIds': ['Q11'], 'mode': 'replay'})
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            conn.request('GET', '/api/events?after=0')
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn('text/event-stream', response.getheader('Content-Type'))
            first = self.read_sse(response)
            snapshot = json.loads(first['data'])
            self.assertEqual(first['event'], 'snapshot')
            self.assertEqual(snapshot['workflows'][0]['id'], wf['id'])
            current = self.controller.action(wf['id'], {'action': 'start', 'operationId': 'sse-start', 'expectedRevision': wf['revision']})
            second = self.read_sse(response)
            self.assertGreater(int(second['id']), int(first['id']))
            self.assertEqual(json.loads(second['data'])['workflows'][0]['revision'], current['revision'])
        finally:
            conn.close()
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            conn.request('GET', '/api/events?after=0', headers={'Last-Event-ID': second['id']})
            response = conn.getresponse()
            frame = self.read_sse(response)
            self.assertGreaterEqual(int(frame['id']), int(second['id']))
            self.assertEqual(json.loads(frame['data'])['seq'], int(frame['id']))
        finally:
            conn.close()

    def test_bad_sse_cursor_is_structured_error(self):
        self.session()
        status, _, error = self.request('GET', '/api/events?after=-1')
        self.assertEqual((status, error['error']['code']), (400, 'INVALID_CURSOR'))

    def test_chat_offline_is_waiting_model_and_never_runs_adapter(self):
        token = self.session()
        status, _, reply = self.request('POST', '/api/chat', {'message': 'measure now'}, {'X-Qcal-Token': token})
        self.assertEqual(status, 200)
        self.assertEqual(reply['status'], 'waiting_model')
        self.assertEqual(self.adapter.calls, [])

    def test_shutdown_refuses_queued_task_without_stopping_worker(self):
        token = self.session()
        wf = self.controller.create({'name': 'shutdown checkpoint', 'resourceIds': ['Q11'], 'mode': 'real'})
        self.controller.action(wf['id'], {'action': 'start', 'operationId': 'shutdown-queued', 'expectedRevision': wf['revision']})
        status, _, reply = self.request('POST', '/api/system/shutdown', {}, {'X-Qcal-Token': token})
        self.assertEqual(status, 409)
        self.assertEqual(reply['error']['code'], 'SHUTDOWN_CHECKPOINT_REQUIRED')
        self.assertFalse(self.controller.stop_event.is_set())
        self.assertEqual(self.adapter.calls, [])

    def test_shutdown_refuses_unresolved_receipt_and_allows_empty_checkpoint(self):
        token = self.session()
        with self.controller.store.transaction() as rt:
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)', ('unfinished', 'wf', 'attempt', 'unknown', '{}'))
        status, _, reply = self.request('POST', '/api/system/shutdown', {}, {'X-Qcal-Token': token})
        self.assertEqual(status, 409)
        self.assertFalse(self.controller.stop_event.is_set())
        with self.controller.store.transaction() as rt:
            rt.db.execute("UPDATE platform_effects SET state='not_started' WHERE operation_id='unfinished'")
        status, _, reply = self.request('POST', '/api/system/shutdown', {}, {'X-Qcal-Token': token})
        self.assertEqual(status, 202)
        self.assertTrue(self.controller.stop_event.is_set())


if __name__ == '__main__':
    unittest.main()
