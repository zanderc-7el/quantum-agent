from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from backend.common import APIError
from unittest.mock import Mock
from adapters.f8 import AdapterError
from tests.test_controller import make_fixture


class ManualExperimentSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c, self.adapter = make_fixture(Path(self.tmp.name))
        self.c.profile.setdefault('parameterBounds', {}).update(start_s=[0, 80e-6], stop_s=[0, 80e-6])
        self.request = {'name': 'Rabi setup', 'resourceIds': ['Q11'], 'mode': 'real',
                        'policy': 'strict', 'manualProcedureId': 'sq_rabi_amplitude'}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_selection_sets_target_without_accepting_dependencies_or_dispatch(self):
        wf = self.c.create({**self.request, 'manualParameters': {'drive_amp': .2}})
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        self.assertEqual(wf['currentNodeId'], node['id'])
        self.assertEqual(wf['manualExperiment']['procedureId'], 'sq_rabi_amplitude')
        self.assertEqual(node['parameters']['drive_amp'], .2)
        with self.c.store.connect() as rt:
            actual, _ = self.c._parameters(rt, wf, node)
        self.assertEqual(actual['drive_amp'], .2)
        self.assertTrue(node['dependsOn'])
        self.assertTrue(all(n['engineState'] == 'pending' for n in wf['nodes']))
        self.assertEqual(self.adapter.calls, [])

    def test_invalid_parameters_and_wrong_mode_do_not_create_workflow(self):
        cases = [({'manualParameters': {'drive_amp': float('nan')}}, 'PARAMETER_OUT_OF_RANGE'),
                 ({'manualParameters': {'step': 0}}, 'UNKNOWN_PARAMETER'),
                 ({'manualParameters': {'stop_s': 1e-6, 'start_s': 2e-6}}, 'INVALID_SCAN'),
                 ({'policy': 'autopilot'}, 'MANUAL_POLICY_REQUIRED'),
                 ({'manualProcedureId': 'does_not_exist'}, 'UNSUPPORTED_METHOD')]
        for values, code in cases:
            with self.subTest(values=values):
                with self.assertRaises(APIError) as caught:
                    self.c.create({**self.request, **values})
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(self.c.list(), [])

    def test_create_operation_is_idempotent_and_rejects_changed_body(self):
        request = {**self.request, 'operationId': 'create-once'}
        first = self.c.create(request)
        second = self.c.create(deepcopy(request))
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(self.c.list()), 1)
        with self.assertRaises(APIError) as caught:
            self.c.create({**request, 'name': 'changed'})
        self.assertEqual(caught.exception.code, 'IDEMPOTENCY_CONFLICT')

    def test_native_scan_grid_failure_does_not_leave_draft(self):
        self.c._physical_adapter = True
        self.adapter.build_plan = Mock(side_effect=AdapterError('PULSE_GRID', '样点不在原生网格'))
        with self.assertRaises(APIError) as caught:
            self.c.create({**self.request, 'manualParameters': {'drive_amp': .2}})
        self.assertEqual(caught.exception.code, 'PULSE_GRID')
        self.assertEqual(self.c.list(), [])
        self.adapter.build_plan.assert_called_once()
