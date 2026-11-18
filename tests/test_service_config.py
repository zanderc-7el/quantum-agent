import json
from pathlib import Path
import tempfile
import unittest

from backend.server import configured_agent_port


class ServiceConfigTests(unittest.TestCase):
    def test_default_and_shared_local_port(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(configured_agent_port(root), 8766)
            (root / 'config').mkdir()
            config = root / 'config/runtime.local.json'
            config.write_text(json.dumps({'agentPort': 8767, 'adapter': 'arclight-native-cy8'}))
            self.assertEqual(configured_agent_port(root), 8767)
            self.assertEqual(configured_agent_port(root, 8766), 8766)

    def test_unknown_ports_and_types_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config').mkdir()
            config = root / 'config/runtime.local.json'
            for value in (True, 8767.0, '8767', 0, 8765, 65536, 'https://example.org'):
                with self.subTest(value=value):
                    config.write_text(json.dumps({'agentPort': value}))
                    with self.assertRaises(ValueError):
                        configured_agent_port(root)

    def test_malformed_configuration_never_silently_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config').mkdir()
            config = root / 'config/runtime.local.json'
            for text in ('[8767]', '{broken'):
                config.write_text(text)
                with self.assertRaises(ValueError):
                    configured_agent_port(root)
