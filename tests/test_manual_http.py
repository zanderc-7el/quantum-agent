"""Real HTTP requests, isolated SQLite and explicitly fake acquisitions only."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from backend.server import Server
from tests.test_controller import make_fixture


class ManualHTTPContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-manual-http-')
        self.controller, self.adapter = make_fixture(Path(self.directory.name))
        self.server = Server(('127.0.0.1', 0), self.controller)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.token = self.request('/api/session')[1]['token']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(2)
        self.controller.close()
        self.directory.cleanup()

    def request(self, path, body=None):
        headers = {'Origin': self.base}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'X-Qcal-Token': self.token})
        request = urllib.request.Request(self.base + path, headers=headers,
                                        data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            return response.code, json.load(response)

    def test_selected_step_http_roundtrip_and_reload_never_dispatches_next(self):
        status, wf = self.request('/api/workflows', {'name': 'Manual HTTP test',
            'mode': 'real', 'policy': 'strict', 'coverage': 'main', 'resourceIds': ['Q11']})
        self.assertLess(status, 300)
        node_id = wf['nodes'][0]['id']
        endpoint = '/api/workflows/' + wf['id']
        payload = {'action': 'run_step', 'nodeId': node_id, 'expectedRevision': wf['revision'],
                   'operationId': 'single-click'}
        first_status, first = self.request(endpoint + '/actions', payload)
        self.assertEqual(first_status, 200, first)
        self.assertEqual(self.request(endpoint + '/actions', payload), (first_status, first))
        self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 2)  # acquisition and independent repeat
        _, finished = self.request(endpoint)
        self.assertEqual(finished['engineState'], 'awaiting_acceptance')
        for _ in range(3):
            self.request(endpoint)
            self.request('/api/workflows')
            self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 2)
        stale_status, _ = self.request(endpoint + '/actions', {
            **payload, 'operationId': 'new-stale-click'})
        self.assertEqual(stale_status, 409)
        status, accepted = self.request(endpoint + '/actions', {
            'action': 'approve', 'nodeId': node_id, 'operationId': 'accept-click',
            'expectedRevision': finished['revision']})
        self.assertEqual(status, 200, accepted)
        self.assertEqual(accepted['status'], 'paused')
        self.assertEqual(accepted['engineState'], 'manual_ready')
        self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 2)


if __name__ == '__main__':
    unittest.main()
