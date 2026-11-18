import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from quantum_agents.runtime import ContractError
from quantum_agents.twin import QuantumTwinBackend

@unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('scipy'),
                     'Physical adapter identity requires optional numpy/scipy')
class TwinIdentityTests(unittest.TestCase):
    def test_same_identity_can_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            first=QuantumTwinBackend(d,seed=7)
            before=(Path(d)/'frozen_manifest.json').read_bytes()
            QuantumTwinBackend(d,seed=7)
            self.assertEqual(before,(Path(d)/'frozen_manifest.json').read_bytes())

    def test_seed_change_cannot_reuse_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            QuantumTwinBackend(d,seed=7)
            with self.assertRaisesRegex(ContractError,'BACKEND_VERSION_CONFLICT'):
                QuantumTwinBackend(d,seed=8)

    def test_source_or_environment_identity_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            QuantumTwinBackend(d)
            path=Path(d)/'frozen_manifest.json';value=json.loads(path.read_text())
            value['config_sha256']='0'*64;path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ContractError,'BACKEND_VERSION_CONFLICT'):
                QuantumTwinBackend(d)

    def test_unversioned_directory_is_not_silently_adopted(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'old-receipt.json').write_text('{}')
            with self.assertRaisesRegex(ContractError,'缺冻结身份'):
                QuantumTwinBackend(d)
