"""Worker supervision survives telemetry faults; all processes here are local fakes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
from adapters.f8 import AdapterError


ROOT = Path(__file__).resolve().parents[1]


class ProgressFaultTests(unittest.TestCase):
    def run_worker(self, payload, *, running=True, shutdown=True, cooperative=True,
                   completed=False, read_error=None):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        profile = json.loads((ROOT / 'adapters/arclight_profile.json').read_text(encoding='utf8'))
        adapter = ArcLightAdapter(folder, {**profile, 'enabled': True, 'cancelGraceSeconds': .2})
        operation = adapter._operation_dir('progress-fault')
        calls = {'cancel': 0, 'kill': 0, 'read': 0}

        def completion():
            raw = operation / 'partial_raw.npz'
            raw.write_bytes(b'temporary test fixture, never measured')
            (operation / 'completion.json').write_text(json.dumps({
                'state': 'completed' if completed else 'failed_after_shutdown',
                'shutdown_ok': shutdown, 'files': [str(raw)], 'pointsCompleted': 3,
                'error': 'fixture shutdown result',
            }), encoding='utf8')

        class Process:
            pid = 12345
            returncode = None if running else (0 if completed else 1)

            def poll(self):
                if self.returncode is None and cooperative and (operation / 'STOP_AFTER_POINT').exists():
                    completion()
                    self.returncode = 0 if completed else 1
                return self.returncode

            def kill(self):
                calls['kill'] += 1
                self.returncode = 1

            def wait(self, timeout):
                return self.returncode

        def launch(*args, **kwargs):
            (operation / 'events.jsonl').write_bytes(payload)
            if not running:
                completion()
            return Process()

        def cancel():
            calls['cancel'] += 1
            return False

        tick = iter(n * .2 for n in range(100))
        self.enterContext(patch.object(adapter, 'preflight', return_value={'ready': True}))
        self.enterContext(patch.object(adapter, 'build_plan', return_value={'max_seconds': 10}))
        self.enterContext(patch('adapters.arclight.subprocess.Popen', side_effect=launch))
        self.enterContext(patch('adapters.arclight.time.sleep', return_value=None))
        self.enterContext(patch('adapters.arclight.time.monotonic', side_effect=lambda: next(tick)))
        if read_error:
            def failed_read(*args):
                calls['read'] += 1
                raise read_error
            self.enterContext(patch('adapters.arclight.read_event_batch', side_effect=failed_read))
        self.enterContext(patch.object(adapter, '_result', return_value={'dataKind': 'real', 'fixtureOnly': True}))
        try:
            result = adapter.execute({'operationId': 'progress-fault', 'procedureId': 'sq_t1'}, lambda event: None, cancel)
            error = None
        except Exception as exc:
            result, error = None, exc
        path = operation / 'adapter_receipt.json'
        receipt = json.loads(path.read_text(encoding='utf8')) if path.exists() else None
        return operation, calls, receipt, error, result

    def test_malformed_or_oversized_journal_requests_stop_and_preserves_partial_receipt(self):
        for payload in (b'not-json\n', b'\xff\n', b'x' * (1024 * 1024 + 1)):
            with self.subTest(payload_length=len(payload)):
                operation, calls, receipt, error, _ = self.run_worker(payload)
                self.assertIsInstance(error, AdapterError)
                self.assertEqual(error.code, 'FAILED_AFTER_SHUTDOWN')
                self.assertTrue((operation / 'STOP_AFTER_POINT').is_file())
                self.assertGreaterEqual(calls['cancel'], 2)
                self.assertEqual(receipt['status'], 'failed_after_shutdown')
                self.assertTrue(receipt['shutdownConfirmed'])
                self.assertEqual(receipt['pointsCompleted'], 3)
                self.assertTrue(receipt['telemetryError'])
                self.assertNotIn('result', receipt)

    def test_unreadable_journal_is_disabled_and_force_termination_still_quarantines(self):
        operation, calls, receipt, error, _ = self.run_worker(b'', cooperative=False, read_error=OSError('read failed'))
        self.assertIsInstance(error, AdapterError)
        self.assertEqual(error.code, 'NEEDS_RECONCILIATION')
        self.assertTrue((operation / 'STOP_AFTER_POINT').is_file())
        self.assertEqual(calls['kill'], 1)
        self.assertEqual(calls['read'], 1)
        self.assertEqual(receipt['status'], 'unknown')
        self.assertIn('OSError', receipt['telemetryError'])

    def test_tail_journal_fault_does_not_replace_failed_shutdown_with_success(self):
        _, _, receipt, error, _ = self.run_worker(b'broken\n', running=False, shutdown=False)
        self.assertIsInstance(error, AdapterError)
        self.assertEqual(error.code, 'NEEDS_RECONCILIATION')
        self.assertEqual(receipt['status'], 'unknown')
        self.assertFalse(receipt['completion']['shutdown_ok'])
        self.assertTrue(receipt['telemetryError'])

    def test_tail_journal_fault_does_not_discard_confirmed_completion(self):
        _, _, receipt, error, result = self.run_worker(b'broken\n', running=False, completed=True)
        self.assertIsNone(error)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(result['dataKind'], 'real')
        self.assertTrue(receipt['telemetryError'])


if __name__ == '__main__':
    unittest.main()
