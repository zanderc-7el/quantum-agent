"""Unsupported next step must not overwrite the previous verified acquisition."""
from pathlib import Path
import tempfile
import unittest
from tests.test_controller import make_fixture


class ControllerProgression(unittest.TestCase):
    def test_unsupported_next_step_keeps_previous_step_verified_and_its_result(self):
        with tempfile.TemporaryDirectory(prefix='qcal-progress-fake-') as directory:
            controller, adapter = make_fixture(Path(directory))
            try:
                wf = controller.create({'name': 'FAKE unsupported next step', 'resourceIds': ['Q11'],
                    'mode': 'real', 'policy': 'threshold', 'coverage': 'full'})
                controller.action(wf['id'], {'operationId': 'start', 'expectedRevision': wf['revision'], 'action': 'start'})
                controller.run_workflow(wf['id'])
                result = controller.get(wf['id'])
                first = result['nodes'][0]
                unsupported = next(n for n in result['nodes'] if n['capability'] != 'available')
                self.assertEqual(result['engineState'], 'unsupported_method')
                self.assertEqual(result['currentNodeId'], unsupported['id'])
                self.assertEqual(unsupported['engineState'], 'unsupported_method')
                self.assertEqual(first['engineState'], 'verified')
                self.assertEqual(first['result']['quality']['status'], 'passed')
                self.assertEqual(first['attempt'], 1)
                self.assertEqual(len(adapter.calls), 2)  # synthetic selection + verification only
            finally:
                controller.close()


if __name__ == '__main__':
    unittest.main()
