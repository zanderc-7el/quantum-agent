"""Local proxy tests. Never contacts Ziyor, sends SMS, or spends model quota."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest

from backend.account import account_request, public_account
from backend.common import APIError

class ProxyContracts(unittest.TestCase):
    def test_response_is_a_whitelist_not_a_credential_passthrough(self):
        value = public_account({'authenticated': True, 'app_key': 'NOT-A-REAL-KEY',
            'user': {'name': 'test', 'phoneMasked': '138****0000', 'mobile': 'never-return', 'api_key': 'never-return'},
            'modelConnection': {'provider': 'ziyor', 'active': True, 'apiKey': 'never-return'},
            'balance': {'remainingCalls': 15, 'label': '积分'}, 'models': [{'id': 'test-model', 'label': 'Test', 'secret': 'never-return'}]})
        text = json.dumps(value)
        self.assertNotIn('never-return', text)
        self.assertNotIn('NOT-A-REAL-KEY', text)
        self.assertEqual(value['balance']['remainingCalls'], 15)

    def test_only_known_account_operations_are_allowed(self):
        for action in ('purchase', 'arbitrary-url', '../login'):
            with self.assertRaises(APIError):
                account_request('http://127.0.0.1:1', action, {})
        with self.assertRaises(APIError):
            account_request('http://127.0.0.1:1', 'login', {'phone': 'test', 'code': 'test', 'operationId': 'test', 'api_key': 'forbidden'})

    def test_proxy_sends_explicit_operation_and_returns_safe_response(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path, body))
                result = json.dumps({'status': 'sent', 'retryAfter': 60, 'app_key': 'must-strip'}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(result)))
                self.end_headers()
                self.wfile.write(result)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            result = account_request(f'http://127.0.0.1:{server.server_port}', 'send-code', {'phone': 'test-fixture', 'operationId': 'test-send'})
            self.assertEqual(result, {'status': 'sent', 'retryAfter': 60})
            self.assertEqual(calls, [('/account/send-code', {'phone': 'test-fixture', 'operationId': 'test-send'})])
        finally:
            server.shutdown()
            server.server_close()

    def test_write_requires_operation_id_before_network(self):
        for action in ('login', 'logout', 'connect', 'test', 'send-code'):
            with self.assertRaises(APIError) as raised:
                account_request('http://127.0.0.1:1', action, {})
            self.assertEqual(raised.exception.code, 'OPERATION_ID_REQUIRED')

if __name__ == '__main__':
    unittest.main()
