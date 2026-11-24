"""Local ephemeral HTTP listener; temporary DB/FakeAdapter and offline brain."""
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from backend import server as server_module
from backend.server import Server
from tests.test_controller import make_fixture


class ServerExclusiveBinding(unittest.TestCase):
    def test_main_rejects_duplicate_port_before_constructing_database_controller(self):
        with tempfile.TemporaryDirectory(prefix='qcal-main-bind-fake-') as directory:
            first = Server(('127.0.0.1', 0), None)
            try:
                with patch('sys.argv', ['qcal', '--root', directory, '--port', str(first.server_address[1])]), \
                     patch.object(server_module, 'Controller') as constructor:
                    with self.assertRaises(OSError):
                        server_module.main()
                    constructor.assert_not_called()
                self.assertFalse((Path(directory) / 'data/platform.sqlite').exists())
            finally:
                first.server_close()

    def test_main_releases_bound_socket_if_controller_initialization_fails(self):
        made = []
        def create_server(*args):
            result = Server(*args)
            made.append(result)
            return result
        with tempfile.TemporaryDirectory(prefix='qcal-main-init-fake-') as directory:
            with patch('sys.argv', ['qcal', '--root', directory, '--port', '0']), \
                 patch.object(server_module, 'Server', side_effect=create_server), \
                 patch.object(server_module, 'Controller', side_effect=RuntimeError('synthetic init failure')):
                with self.assertRaisesRegex(RuntimeError, 'synthetic init failure'):
                    server_module.main()
            self.assertEqual(len(made), 1)
            self.assertEqual(made[0].socket.fileno(), -1)

    def test_second_controller_on_same_endpoint_is_rejected_and_first_remains_healthy(self):
        with tempfile.TemporaryDirectory(prefix='qcal-bind-fake-') as directory:
            controller, adapter = make_fixture(Path(directory))
            first = Server(('127.0.0.1', 0), controller)
            thread = threading.Thread(target=first.serve_forever, daemon=True)
            thread.start()
            second = None
            try:
                with self.assertRaises(OSError):
                    second = Server(first.server_address, controller)
                client = http.client.HTTPConnection(*first.server_address, timeout=2)
                try:
                    client.request('GET', '/api/health')
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read())['projectRoot'], str(Path(directory).resolve()))
                finally:
                    client.close()
                self.assertEqual(adapter.calls, [])
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    self.assertFalse(first.allow_reuse_address)
                    self.assertEqual(first.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
            finally:
                if second:
                    second.server_close()
                first.shutdown(); first.server_close(); thread.join(timeout=2)
                controller.close()


if __name__ == '__main__':
    unittest.main()
