"""Offline controller integration: static native plans, no execution or services."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from adapters.arclight import ArcLightAdapter
from backend.common import ROOT, APIError, dumps, now
from backend.controller import Controller
try:
    from test_controller import make_fixture
except ImportError:
    from tests.test_controller import make_fixture


class ModelSemanticsControllerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-model-semantics-offline-')
        self.root = Path(self.directory.name)
        imports = self.root / 'data/imports'
        imports.mkdir(parents=True)
        (imports / 'history.json').write_text(json.dumps({'version': 1,
            'resources': [{'id': 'Q11', 'label': 'Q11'}], 'records': []}), encoding='utf-8')
        self.adapter = ArcLightAdapter(ROOT, ROOT / 'adapters/arclight_profile.json')
        self.adapter.execute = Mock(side_effect=AssertionError('OFFLINE ONLY: no hardware execution'))
        self.controller = Controller(self.root, adapter=self.adapter, start_worker=False)
        self.payloads = []

    def tearDown(self):
        self.adapter.execute.assert_not_called()
        self.controller.close()
        self.directory.cleanup()

    def create_at(self, procedure, overrides=None, observed=None):
        wf = self.controller.create({'name': 'OFFLINE model semantics', 'mode': 'real',
            'policy': 'autopilot', 'resourceIds': ['Q11']})
        node_id = next(n['id'] for n in wf['nodes'] if n['procedureId'] == procedure)
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.update(status='attention', engineState='quality_attention', currentNodeId=node_id)
            node = self.controller._node(current, node_id)
            node.update(parameterOverrides=deepcopy(overrides or {}), parameters=deepcopy(observed or {}),
                result={'dataKind': 'real', 'parameters': deepcopy(observed or {}), 'artifacts': [],
                        'quality': {'status': 'failed', 'reasons': ['Explicit offline context fixture']}})
            self.controller.store.save(rt, current, 'offline_model_semantics')
        return wf['id'], node_id

    def decide(self, workflow_id, node_id, changes=None, mutate_payload=None):
        def brain(path, payload):
            self.payloads.append(deepcopy(payload))
            if mutate_payload:
                mutate_payload(payload)
            return {'status': 'ok', 'decision': {'action': 'wait' if changes is None else 'retry',
                'reason': 'Explicit offline model proposal', 'evidenceIds': [],
                **({'parameterChanges': deepcopy(changes)} if changes is not None else {})}}
        self.controller._brain = brain
        return self.controller._decide(workflow_id, node_id)

    def fixed_failure(self, *, verification=False):
        workflow_id, node_id = self.create_at('ro_optimize', {'amplitudes': [.4], 'shots': 1000})
        with self.controller.store.transaction() as rt:
            wf = self.controller.get(workflow_id, rt)
            node = self.controller._node(wf, node_id)
            prefix = workflow_id + ':Q11:'
            for key, value in {'readout_hz': self.adapter.profile['resources']['Q11']['readout_hz'], 'readout_amp': .4}.items():
                rt.db.execute('INSERT OR REPLACE INTO parameters VALUES(?,?,?)', (prefix + key, dumps(value), 1))
            parameters, versions = self.controller._parameters(rt, wf, node)
            actual = self.adapter.build_plan({'operationId': 'offline-fixed-plan', 'resourceId': 'Q11',
                'procedureId': 'ro_optimize', 'parameters': parameters})['parameters']
            attempt_id = 'offline-fixed-' + workflow_id
            values = {key: actual[key] for key in ('readout_hz', 'readout_amp')}
            marker = {'id': 'offline-pin-' + workflow_id, 'state': 'fixed_failed', 'optimizerNodeId': node_id,
                'lockedNodeIds': [node_id], 'values': values,
                'parameterVersions': {key: versions[key] for key in values}, 'lastFixedAttemptId': attempt_id}
            wf['readoutRevalidations'] = {'Q11': marker}
            # A discrepant candidate can be the reason the actual fixed point
            # failed; it must not be confused with a wrong acquired working point.
            result = {'dataKind': 'real', 'parameters': actual, 'candidateParameters': {'readout_amp': .41},
                'quality': {'status': 'failed', 'reasons': ['OFFLINE fixed-candidate rejection']}, 'artifacts': []}
            node.update(latestAttemptId=attempt_id, attempt=2, result=deepcopy(result), parameters=deepcopy(actual))
            recorded_marker = {**deepcopy(marker), 'state': 'active'}
            attempt = {'id': attempt_id, 'nodeId': node_id, 'number': 2, 'mode': 'real', 'procedureId': 'ro_optimize',
                'state': 'attention', 'endedAt': now(), 'parameters': actual, 'parameterVersions': versions,
                'readoutRevalidation': recorded_marker, 'result': deepcopy(result), 'operationId': attempt_id + '/measure',
                'verificationOperationId': attempt_id + '/verify' if verification else None}
            self.controller.store.attempt(rt, workflow_id, attempt)
            for operation in filter(None, (attempt['operationId'], attempt['verificationOperationId'])):
                request = {'runId': workflow_id, 'nodeId': node_id, 'resourceId': 'Q11', 'procedureId': 'ro_optimize',
                    'attemptId': attempt_id, 'operationId': operation, 'parameters': actual,
                    'parameterVersions': versions, 'readoutRevalidation': recorded_marker}
                receipt = {'dataKind': 'real', 'parameters': actual, 'artifacts': []}
                rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',
                    (operation, workflow_id, attempt_id, 'completed', dumps(request), dumps(receipt)))
            self.controller.store.save(rt, wf, 'offline_fixed_failure')
        return workflow_id, node_id

    def test_next_committed_plus_overrides_replace_observed_snapshot_for_model_context(self):
        wf, node = self.create_at('sq_spectroscopy', {'drive_hz': 4.95e9, 'span_hz': 6e6},
            {'drive_hz': 4.1e9, 'frequencies_hz': [4.1e9, 4.11e9]})
        with self.controller.store.transaction() as rt:
            rt.db.execute('INSERT OR REPLACE INTO parameters VALUES(?,?,?)', (wf + ':Q11:drive_hz', dumps(4.9e9), 2))
        self.assertFalse(self.decide(wf, node))
        payload = self.payloads[-1]
        self.assertEqual(payload['observations']['parameters']['drive_hz'], 4.95e9)
        self.assertNotIn('frequencies_hz', payload['observations']['parameters'])
        self.assertTrue({'drive_hz', 'points', 'span_hz'} <= set(payload['parameterBounds']))
        self.assertTrue(payload['modelParameterSemantics']['applied'])
        self.assertEqual(payload['modelParameterSemantics']['profileId'], 'arclight-private-f8-q11-v1')

    def test_real_effective_explicit_axis_blocks_ineffective_model_changes(self):
        wf, node = self.create_at('sq_spectroscopy', {'frequencies_hz': [4.8e9, 4.81e9, 4.82e9]})
        self.assertFalse(self.decide(wf, node, {'points': 41}))
        result = self.controller.detail(wf, node)['node']
        self.assertNotIn('points', self.payloads[-1]['parameterBounds'])
        self.assertEqual(result['lastDecisionError']['code'], 'PARAMETER_OUT_OF_RANGE')
        self.assertEqual(result['parameterOverrides']['frequencies_hz'], [4.8e9, 4.81e9, 4.82e9])

    def test_verification_only_amplitude_pin_is_not_inherited_by_ordinary_optimizer_retry(self):
        wf, node = self.create_at('ro_optimize', observed={'amplitudes': [.4], 'readout_amp': .4})
        self.assertTrue(self.decide(wf, node, {'amp_start': .1, 'amp_stop': .8, 'points': 21}))
        self.assertTrue({'amp_start', 'amp_stop', 'points'} <= set(self.payloads[-1]['parameterBounds']))
        self.assertNotIn('amplitudes', self.payloads[-1]['observations']['parameters'])
        self.assertNotIn('amplitudes', self.controller.detail(wf, node)['node']['parameterOverrides'])

    def test_narrowed_bounds_are_frozen_before_model_call_and_preserve_quality_gates(self):
        wf, node = self.create_at('sq_spectroscopy')
        before = self.controller.get(wf)
        def widen(payload):
            payload['parameterBounds']['drive_amp'] = {'min': .01, 'max': .99}
        self.assertFalse(self.decide(wf, node, {'drive_amp': .5}, mutate_payload=widen))
        after = self.controller.get(wf)
        self.assertEqual(self.controller._node(after, node)['lastDecisionError']['code'], 'PARAMETER_OUT_OF_RANGE')
        for field in ('gates', 'gatesVersion', 'adapterProfileHash', 'reservedShots', 'iterations'):
            self.assertEqual(after[field], before[field])
        self.assertEqual(self.controller._node(after, node)['result'], self.controller._node(before, node)['result'])
        self.assertIn('drive_amp', self.controller._parameter_schema('sq_spectroscopy'))

    def test_minimum_captures_and_window_limits_reject_before_any_dispatch(self):
        for procedure, change in (('sq_spectroscopy', {'capture_repeats': 1}),
                                  ('sq_rabi_amplitude', {'capture_repeats': 1}),
                                  ('sq_rabi_time', {'stop_s': 90e-6}),
                                  ('ro_optimize', {'amp_stop': .99})):
            wf, node = self.create_at(procedure)
            self.assertFalse(self.decide(wf, node, change))
            self.assertEqual(self.controller.detail(wf, node)['node']['lastDecisionError']['code'], 'PARAMETER_OUT_OF_RANGE')

    def test_matching_profile_name_does_not_apply_rules_to_fake_adapter_class(self):
        with tempfile.TemporaryDirectory(prefix='qcal-other-adapter-offline-') as directory:
            controller, adapter = make_fixture(directory)
            try:
                controller.close()
                adapter.profile['id'] = 'arclight-private-f8-q11-v1'
                controller = Controller(directory, adapter=adapter, start_worker=False)
                wf = controller.create({'name': 'OFFLINE different implementation', 'mode': 'real',
                    'policy': 'autopilot', 'resourceIds': ['Q11']})
                node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_spectroscopy')
                payloads = []
                controller._brain = lambda path, payload: payloads.append(deepcopy(payload)) or {
                    'status': 'ok', 'decision': {'action': 'wait', 'reason': 'OFFLINE no change', 'evidenceIds': []}}
                controller._decide(wf['id'], node['id'])
                self.assertNotIn('modelParameterSemantics', payloads[0])
                self.assertIn('drive_amp', payloads[0]['parameterBounds'])
                self.assertEqual(adapter.calls, [])
            finally:
                controller.close()

    def test_decision_rechecks_binding_before_model_and_retains_prior_measurement(self):
        wf, node = self.create_at('sq_spectroscopy')
        before = self.controller.detail(wf, node)
        brain = Mock(side_effect=AssertionError('Changed binding must not reach model'))
        self.controller._brain = brain
        with patch.object(self.controller, '_assert_adapter_binding',
                          side_effect=APIError('ADAPTER_CHANGED', 'OFFLINE binding mismatch')):
            self.assertFalse(self.controller._decide(wf, node))
        after = self.controller.detail(wf, node)
        self.assertEqual(after['node']['engineState'], 'adapter_changed')
        self.assertEqual(after['node']['result'], before['node']['result'])
        self.assertEqual(after['attempts'], before['attempts'])
        self.assertEqual(self.controller.get(wf)['iterations'], 0)
        brain.assert_not_called()

    def test_eligible_current_fixed_failure_allows_paired_range_but_preserves_history(self):
        for verification in (False, True):
            wf, node = self.fixed_failure(verification=verification)
            attempts_before = self.controller.store.detail(wf, node)[0]
            self.assertTrue(self.decide(wf, node, {'amp_start': .1, 'amp_stop': .8, 'points': 21}))
            payload = self.payloads[-1]
            self.assertTrue(payload['modelParameterSemantics']['fixedReadoutContext']['eligible'])
            current = self.controller.get(wf)
            self.assertEqual(current['readoutRevalidations']['Q11']['state'], 'released')
            self.assertNotIn('amplitudes', self.controller._node(current, node)['parameterOverrides'])
            self.assertEqual(self.controller.store.detail(wf, node)[0], attempts_before)
            self.assertEqual(current['reservedShots'], 0)

    def test_points_only_cannot_release_pin_even_if_model_mutates_semantics(self):
        wf, node = self.fixed_failure()
        self.assertFalse(self.decide(wf, node, {'points': 21},
            mutate_payload=lambda payload: payload['modelParameterSemantics']['conditions'].clear()))
        current = self.controller.get(wf)
        self.assertEqual(current['readoutRevalidations']['Q11']['state'], 'fixed_failed')
        self.assertEqual(self.controller._node(current, node)['lastDecisionError']['code'], 'INEFFECTIVE_PARAMETER_CHANGE')
        self.assertEqual(self.controller._node(current, node)['parameterOverrides']['amplitudes'], [.4])

    def test_sampling_retry_keeps_current_fixed_candidate(self):
        wf, node = self.fixed_failure()
        self.assertTrue(self.decide(wf, node, {'shots': 1500}))
        current = self.controller.get(wf)
        self.assertEqual(current['readoutRevalidations']['Q11']['state'], 'fixed_failed')
        self.assertEqual(self.controller._node(current, node)['parameterOverrides']['amplitudes'], [.4])
        self.assertEqual(self.controller._node(current, node)['parameterOverrides']['shots'], 1500)

    def test_incomplete_or_mismatched_fixed_failure_evidence_never_opens_rescan_bounds(self):
        cases = ('active', 'optimizer', 'latest_attempt', 'stale', 'partial', 'version', 'value', 'effective_override',
                 'attempt_result', 'wrong_receipt', 'wrong_request', 'missing_measure', 'missing_verify', 'unknown', 'lease')
        for damage in cases:
            with self.subTest(damage=damage):
                wf, node_id = self.fixed_failure(verification=damage == 'missing_verify')
                with self.controller.store.transaction() as rt:
                    current = self.controller.get(wf, rt)
                    node = self.controller._node(current, node_id)
                    marker = current['readoutRevalidations']['Q11']
                    operation = node['latestAttemptId'] + '/measure'
                    if damage == 'active': marker['state'] = 'active'
                    if damage == 'optimizer': marker['optimizerNodeId'] = 'another-node'
                    if damage == 'latest_attempt': marker['lastFixedAttemptId'] = 'previous-attempt'
                    if damage in ('stale', 'partial'): node['result'][damage] = True
                    if damage == 'version':
                        rt.db.execute('UPDATE parameters SET revision=2 WHERE name=?', (wf + ':Q11:readout_amp',))
                    if damage == 'value':
                        rt.db.execute('UPDATE parameters SET value=? WHERE name=?', (dumps(.5), wf + ':Q11:readout_amp'))
                    if damage == 'effective_override': node['parameterOverrides']['readout_amp'] = .5
                    if damage == 'attempt_result': node['result']['metrics'] = {'offline_different': 1}
                    if damage in ('wrong_receipt', 'wrong_request'):
                        column = 'result' if damage == 'wrong_receipt' else 'request'
                        record = json.loads(rt.db.execute('SELECT ' + column + ' FROM platform_effects WHERE operation_id=?', (operation,)).fetchone()[0])
                        record['parameters']['readout_amp'] = .5
                        rt.db.execute('UPDATE platform_effects SET ' + column + '=? WHERE operation_id=?', (dumps(record), operation))
                    if damage == 'missing_measure':
                        rt.db.execute('DELETE FROM platform_effects WHERE operation_id=?', (operation,))
                    if damage == 'missing_verify':
                        rt.db.execute('DELETE FROM platform_effects WHERE operation_id=?', (node['latestAttemptId'] + '/verify',))
                    if damage == 'unknown':
                        rt.db.execute("UPDATE platform_effects SET state='unknown' WHERE operation_id=?", (operation,))
                    if damage == 'lease':
                        rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',
                            ('offline-lease', wf, node['latestAttemptId'], 'active', 'offline-token'))
                    self.controller.store.save(rt, current, 'offline_fixed_evidence_damage')
                self.assertFalse(self.decide(wf, node_id, {'amp_start': .1, 'amp_stop': .8}))
                context = self.payloads[-1]['modelParameterSemantics']['fixedReadoutContext']
                self.assertFalse(context['eligible'])
                self.assertTrue({'amp_start', 'amp_stop', 'points'}.isdisjoint(self.payloads[-1]['parameterBounds']))
                self.assertNotEqual(self.controller.get(wf)['readoutRevalidations']['Q11']['state'], 'released')


if __name__ == '__main__':
    unittest.main()
