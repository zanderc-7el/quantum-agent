"""Offline deadline regression: fake acquisition plus F8's pure plan builder.

No native experiment, subprocess, hardware discovery or model request is run.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adapters.f8 import AdapterError, F8Adapter
from backend.quality import evaluate

try:
    from test_controller import make_fixture
except ImportError:
    from tests.test_controller import make_fixture


class VerificationDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-deadline-offline-')
        self.controller, self.adapter = make_fixture(self.directory.name)
        self.clock = 1000.0
        self.limit = self.adapter.profile['maximumRunSeconds']
        profile_path = Path(__file__).resolve().parents[1] / 'adapters' / 'f8_profile.json'
        profile = json.loads(profile_path.read_text(encoding='utf-8'))
        profile['maximumRunSeconds'] = self.limit
        self.planner = F8Adapter(self.directory.name, profile)
        self.workflow = self.controller.create({'name': 'OFFLINE per-operation deadline',
            'mode': 'real', 'policy': 'threshold', 'resourceIds': ['Q11']})
        self.node_id = self.workflow['nodes'][0]['id']

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def test_long_selection_does_not_expire_verification_or_change_its_budget(self):
        original_execute = self.adapter.execute
        self.adapter.candidate = {'readout_hz': 7.0001e9}
        self.workflow = self.controller.set_parameters(self.workflow['id'], self.node_id, {
            'operationId': 'offline-readout-center', 'expectedRevision': self.workflow['revision'],
            'parameters': {'readout_hz': 7e9}})
        observed = []

        def execute(request, emit, cancel):
            # This is the real F8 static timeout/budget calculation, not execution.
            plan = self.planner.build_plan(request)
            observed.append((deepcopy(request), deepcopy(plan), self.clock))
            result = original_execute(request, emit, cancel)
            self.clock += self.limit - .25  # Each fake acquisition is within its cap.
            return result

        def analyze(*args, **kwargs):
            result = evaluate(*args, **kwargs)
            if not kwargs.get('independent', False):
                self.clock += .5  # Registration/analysis can outlast the old deadline.
            return result

        self.adapter.execute = execute
        with patch('backend.controller.time.time', side_effect=lambda: self.clock), \
                patch('backend.controller.evaluate', side_effect=analyze):
            completed = self.controller._execute_node(self.workflow['id'], self.node_id)
            self.assertTrue(completed, self.controller.detail(self.workflow['id'], self.node_id)['node']['detail'])
            self.assertEqual(len(observed), 2)
            selected, repeated = observed
            self.assertGreater(repeated[2], selected[0]['deadline'])
            for request, plan, started_at in observed:
                self.assertEqual(request['deadline'] - started_at, self.limit)
                self.assertEqual(plan['max_seconds'], self.limit)
                # F8 still rejects this operation's own deadline once it expires.
                saved_clock, self.clock = self.clock, request['deadline'] + .01
                with self.assertRaises(AdapterError) as error:
                    self.planner.build_plan(request)
                self.assertEqual(error.exception.code, 'DEADLINE_EXPIRED')
                self.clock = saved_clock

        first, second = selected[0], repeated[0]
        self.assertEqual(first['operationId'], first['attemptId'] + '/measure')
        self.assertEqual(second['operationId'], first['attemptId'] + '/verify')
        for key in ('runId', 'nodeId', 'attemptId', 'resourceId', 'procedureId',
                    'parameterVersions', 'adapterId', 'adapterProfileHash', 'outputDir', 'leaseToken'):
            self.assertEqual(first[key], second[key], key)
        self.assertEqual(second['parameters'], {**first['parameters'], **self.adapter.candidate})
        self.assertEqual(selected[1]['estimated_total_shots'], repeated[1]['estimated_total_shots'])
        with self.controller.store.connect() as rt:
            effects = {row['operation_id']: row for row in rt.db.execute(
                'SELECT * FROM platform_effects WHERE workflow_id=?', (self.workflow['id'],))}
        self.assertEqual(len(effects), 2)
        for request, _, _ in observed:
            effect = effects[request['operationId']]
            self.assertEqual(effect['state'], 'completed')
            self.assertEqual(json.loads(effect['request']), request)
        detail = self.controller.detail(self.workflow['id'], self.node_id)
        self.assertEqual(detail['node']['engineState'], 'verified')
        self.assertEqual(detail['attempts'][0]['verificationOperationId'], second['operationId'])
        self.assertEqual(detail['node']['result']['candidateParameters'], self.adapter.candidate)
        self.assertEqual(self.controller.get(self.workflow['id'])['budget'], self.workflow['budget'])

    def test_f8_operation_limit_still_caps_a_more_distant_deadline(self):
        request = {'runId': self.workflow['id'], 'nodeId': self.node_id, 'attemptId': 'offline',
            'operationId': 'offline/verify', 'resourceId': 'Q11', 'procedureId': 'ro_resonator_survey',
            'parameters': {}, 'parameterVersions': {}, 'deadline': self.clock + 10 * self.limit}
        with patch('backend.controller.time.time', side_effect=lambda: self.clock):
            self.assertEqual(self.planner.build_plan(request)['max_seconds'], self.limit)

    def test_pause_after_long_selection_does_not_start_independent_verification(self):
        def pause(request, emit, cancel):
            self.clock += self.limit - .25
            current = self.controller.get(self.workflow['id'])
            self.controller.action(current['id'], {'action': 'pause', 'operationId': 'offline-pause',
                'expectedRevision': current['revision']})

        self.adapter.before_return = pause
        with patch('backend.controller.time.time', side_effect=lambda: self.clock):
            self.assertFalse(self.controller._execute_node(self.workflow['id'], self.node_id))
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertTrue(self.adapter.cancel_observed)
        self.assertEqual(self.controller.get(self.workflow['id'])['status'], 'paused')
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0], 1)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
