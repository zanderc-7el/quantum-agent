"""Offline ArcLight/controller contract: real plans, fake acquisition only.

No static preflight contacts hardware; none of these tests calls adapter.execute
unless it is replaced by the explicit local fake below. RB evidence validation
is patched solely for static budget counting, never for an executed request.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
from backend.common import ROOT, APIError, now
from backend.controller import Controller


class ArcControllerIntegration(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-arc-offline-')
        self.root = Path(self.directory.name)
        imports = self.root / 'data/imports'
        imports.mkdir(parents=True)
        (imports / 'history.json').write_text(json.dumps({'version': 1, 'resources': [{'id': 'Q11', 'label': 'Q11'}], 'records': []}))
        self.adapter = ArcLightAdapter(ROOT, ROOT / 'adapters/arclight_profile.json')
        self.controller = Controller(self.root, adapter=self.adapter, start_worker=False)
        self.controller._brain = lambda *args, **kwargs: {'status': 'waiting_model', 'message': 'OFFLINE TEST'}

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def create(self):
        return self.controller.create({'name': 'OFFLINE Q11 CONTRACT', 'mode': 'real', 'policy': 'autopilot', 'resourceIds': ['Q11']})

    def test_full_coverage_budget_includes_two_independent_captures_and_rb_pre_post(self):
        wf = self.create()
        self.assertEqual(len(wf['nodes']), 13)
        self.assertTrue(all(n['capability'] == 'available' for n in wf['nodes']))
        plans = {}
        with patch.object(self.adapter, '_validate_snapshot', return_value={'offline_budget_only': True}):
            for node in wf['nodes']:
                request = {'runId': wf['id'], 'resourceId': 'Q11', 'procedureId': node['procedureId'],
                           'operationId': 'offline-plan/' + node['procedureId'], 'parameters': {}, 'parameterVersions': {}}
                plans[node['procedureId']] = self.adapter.build_plan(request)
        planned_shots = 2 * sum(plan['estimated_total_shots'] for plan in plans.values())
        self.assertEqual(planned_shots, 20696000)
        self.assertEqual([j['rb_role'] for j in plans['sq_rb']['jobs']], ['pre', 'reference', 'interleaved', 'post'])
        self.assertLess(planned_shots, wf['maxTotalShots'])
        self.assertEqual(wf['maxTotalShots'], 30000000)
        self.assertEqual(wf['maxAttemptsPerNode'], 3)
        self.assertEqual(wf['maxWorkflowAttempts'], 40)
        self.assertEqual(wf['budget']['maxTotalShots'], wf['maxTotalShots'])

    def test_actual_plan_applies_parameter_edit_and_records_normalization(self):
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        with patch.object(ArcLightAdapter, 'execute', side_effect=AssertionError('No hardware allowed')):
            saved = self.controller.set_parameters(wf['id'], node['id'], {'operationId': 'offline-edit', 'expectedRevision': wf['revision'],
                'parameters': {'drive_amp': .6, 'drive_length_s': 320.3e-9}})
        detail = self.controller.detail(wf['id'], node['id'])
        self.assertEqual(saved['status'], 'draft')
        self.assertEqual(detail['parameters']['drive_amp'], .6)
        self.assertEqual(detail['parameters']['half_pi_amp'], .6)
        self.assertAlmostEqual(detail['parameters']['drive_length_s'], 320.5e-9, places=15)
        self.assertEqual(detail['decisions'][-1]['requestedParameterChanges']['drive_length_s'], 320.3e-9)
        self.assertTrue(detail['parameterBounds']['acquisitions_per_point']['integer'])
        stale = {'operationId': 'stale-edit', 'expectedRevision': wf['revision'], 'parameters': {'drive_amp': .5}}
        with self.assertRaises(APIError) as error:
            self.controller.set_parameters(wf['id'], node['id'], stale)
        self.assertEqual(error.exception.code, 'STALE_REVISION')

    def test_native_analysis_rejection_stops_independent_capture_and_cannot_pass(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current['hardwareConfirmed'] = True
            self.controller.store.save(rt, current, 'offline_fixture_confirmation')
        calls = []
        def fake_execute(_adapter, request, emit, cancel):
            calls.append(request['operationId'])
            raw = self.root / 'data/raw-test.json'
            raw.write_text(json.dumps({'test_only': True, 'operationId': request['operationId']}))
            return {'dataKind': 'real', 'acquiredAt': now(), 'metrics': {'contrast_snr': 100},
                    'analysisQuality': {'accepted': False, 'reasons': ['Native offline test rejection']},
                    'candidateParameters': {}, 'parameters': request['parameters'],
                    'artifacts': [{'id': 'offline-raw', 'name': raw.name, 'kind': 'raw', 'localPath': str(raw),
                                   'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}]}
        with patch.object(self.controller, 'hardware_ownership', return_value={'verified': True, 'owners': []}), \
             patch.object(ArcLightAdapter, 'execute', fake_execute):
            passed = self.controller._execute_node(wf['id'], wf['nodes'][0]['id'])
        self.assertFalse(passed)
        self.assertEqual(len(calls), 1)
        detail = self.controller.detail(wf['id'], wf['nodes'][0]['id'])
        self.assertEqual(detail['node']['result']['quality']['status'], 'failed')
        self.assertIn('Native offline test rejection', detail['node']['result']['quality']['reasons'])
        self.assertIsNone(detail['attempts'][0]['verificationOperationId'])

    def test_model_retry_updates_the_actual_spectrum_axis_without_acquiring(self):
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_spectroscopy')
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.update(status='attention', engineState='quality_attention', currentNodeId=node['id'])
            current_node = next(n for n in current['nodes'] if n['id'] == node['id'])
            current_node.update(engineState='quality_attention', result={'artifacts': [], 'quality': {'status': 'failed'}})
            self.controller.store.save(rt, current, 'offline_failed_spectrum')
        self.controller._brain = lambda path, payload: {'status': 'ok', 'binding': {k: payload[k] for k in ('runId', 'nodeId', 'revision')},
            'decision': {'action': 'retry', 'reason': 'OFFLINE bounded scan test', 'evidenceIds': [],
                         'parameterChanges': {'drive_hz': 4.9e9, 'span_hz': 8e6, 'points': 9}}}
        with patch.object(ArcLightAdapter, 'execute', side_effect=AssertionError('No hardware allowed')):
            self.assertTrue(self.controller._decide(wf['id'], node['id']))
        detail = self.controller.detail(wf['id'], node['id'])
        plan = self.adapter.build_plan({'operationId': 'offline-actual-axis', 'resourceId': 'Q11',
            'procedureId': 'sq_spectroscopy', 'parameters': detail['parameters']})
        frequency_axis = plan['jobs'][0]['spec']['sweeps'][0]['values']
        self.assertEqual(len(frequency_axis), 9)
        self.assertEqual(frequency_axis[0], 4.896e9)
        self.assertEqual(frequency_axis[-1], 4.904e9)
        self.assertEqual(detail['decisions'][-1]['source'], 'model')

    def test_rb_snapshot_uses_current_time_rabi_without_old_amplitude_evidence(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            rb = next(n for n in current['nodes'] if n['procedureId'] == 'sq_rb')
            parameters, versions = self.controller._parameters(rt, current, rb)
            parameters['drive_length_s'] = 327.5e-9
            for procedure in ('sq_rabi_amplitude', 'sq_rabi_time', 'ro_iq01'):
                node = next(n for n in current['nodes'] if n['procedureId'] == procedure)
                actual = deepcopy(parameters)
                if procedure == 'sq_rabi_amplitude':
                    actual['drive_length_s'] = 320e-9
                attempt_id = 'offline-evidence-' + procedure
                node.update(engineState='verified', latestAttemptId=attempt_id)
                self.controller.store.attempt(rt, current['id'], {
                    'id': attempt_id, 'nodeId': node['id'], 'number': 1,
                    'operationId': attempt_id + '/measure',
                    'verificationOperationId': attempt_id + '/verify',
                    'result': {'quality': {'status': 'passed'}, 'parameters': actual}})
            snapshot = self.controller._validation_snapshot(rt, current, rb, parameters, versions)
        self.assertEqual({e['procedureId'] for e in snapshot['acceptedEvidence']}, {'sq_rabi_time', 'ro_iq01'})
        self.assertEqual(snapshot['parameters']['drive_length_s'], 327.5e-9)

    def _offline_failed_power_context(self):
        """Native static grid plus labelled synthetic series; never acquisition."""
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'ro_power_shift')
        plan = self.adapter.build_plan({'operationId': 'offline-power-context', 'resourceId': 'Q11',
            'procedureId': 'ro_power_shift', 'parameters': {'capture_repeats': 4, 'acquisitions_per_point': 2}})
        axes = plan['jobs'][0]['spec']['sweeps']
        frequencies, powers = axes[0]['values'], axes[1]['values']
        result = {'dataKind': 'real', 'parameters': deepcopy(plan['parameters']), 'artifacts': [],
            'quality': {'status': 'failed', 'reasons': ['EXPLICIT OFFLINE POWER FIXTURE']},
            'analysisQuality': {'powers_waveform_scale': deepcopy(powers)},
            'series': [{'name': 'capture ' + str(capture) + " {'cfg.Q1.para.probCarryScale': " + str(power) + '}',
                'x': list(frequencies), 'y': [1. if i != 30 else .5 for i in range(len(frequencies))], 'xUnit': 'Hz'}
                for power in powers for capture in range(1, 5)]}
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current.update(status='attention', engineState='quality_attention', currentNodeId=node['id'],
                           maxTotalShots=60000000, reservedShots=8052000)
            target = next(n for n in current['nodes'] if n['id'] == node['id'])
            target.update(attempt=1, parameters=deepcopy(plan['parameters']), result=result,
                parameterOverrides={k: plan['parameters'][k] for k in ('points', 'shots', 'span_hz', 'capture_repeats', 'acquisitions_per_point')})
            self.controller.store.save(rt, current, 'offline_power_context_fixture')
        return self.controller.get(wf['id']), node, result

    def test_power_decision_exposes_per_operation_and_workflow_budget_without_planning(self):
        wf, node, original_result = self._offline_failed_power_context()
        calls = []
        self.controller._brain = lambda path, payload: calls.append(deepcopy(payload)) or {'status': 'ok',
            'decision': {'action': 'wait', 'reason': 'OFFLINE inspect limits', 'evidenceIds': []}}
        profile_before = deepcopy(self.adapter.profile)
        schema_before = self.controller._parameter_schema('ro_power_shift')
        with patch.object(ArcLightAdapter, 'execute', side_effect=AssertionError('No hardware allowed')), \
             patch.object(self.adapter, 'build_plan', side_effect=AssertionError('Context must not replan')):
            self.assertFalse(self.controller._decide(wf['id'], node['id']))
        payload = calls[0]
        self.assertEqual(set(payload['parameterBounds']), {'shots', 'capture_repeats', 'acquisitions_per_point', 'points', 'span_hz'})
        limits = payload['executionConstraints']
        self.assertEqual(limits['maximumTotalShots'], 12000000)
        self.assertEqual(limits['maximumPoints'], 15000)
        self.assertEqual(limits['remainingWorkflowShots'], 51948000)
        self.assertTrue(limits['selectionAndVerificationBudgetedSeparately'])
        scan = limits['currentScan']
        self.assertEqual(scan['status'], 'confirmed')
        self.assertEqual((scan['frequencyPoints'], scan['powerPoints'], scan['pointsPerCapture']), (61, 5, 305))
        self.assertEqual((scan['shotsPerOperation'], scan['selectionPlusVerificationShots']), (4880000, 9760000))
        self.assertEqual(scan['maximumCaptureAcquisitionProductAtCurrentGridAndShots'], 19)
        costs = {(v['capture_repeats'], v['acquisitions_per_point']): v for v in limits['illustrativeCostsAtCurrentGridAndShots']}
        self.assertEqual(costs[(4, 4)]['shotsPerOperation'], 9760000)
        self.assertEqual(costs[(6, 4)]['shotsPerOperation'], 14640000)
        self.assertFalse(costs[(6, 4)]['withinPerOperationShotLimit'])
        self.assertEqual(self.adapter.profile, profile_before)
        self.assertEqual(self.controller._parameter_schema('ro_power_shift'), schema_before)
        self.assertEqual(self.controller.detail(wf['id'], node['id'])['node']['result'], original_result)

    def test_power_model_cannot_edit_ineffective_axis_controls_or_upstream_readout_center(self):
        for key, value in (('amp_start', .2), ('amp_stop', .9), ('readout_amp', .4), ('readout_hz', 7e9)):
            with self.subTest(parameter=key):
                wf, node, original_result = self._offline_failed_power_context()
                self.controller._brain = lambda *args: {'status': 'ok', 'decision': {'action': 'retry',
                    'reason': 'OFFLINE ineffective change must be rejected', 'evidenceIds': [], 'parameterChanges': {key: value}}}
                with patch.object(ArcLightAdapter, 'execute', side_effect=AssertionError('No hardware allowed')):
                    self.assertFalse(self.controller._decide(wf['id'], node['id']))
                detail = self.controller.detail(wf['id'], node['id'])
                self.assertEqual(detail['node']['engineState'], 'waiting_model')
                self.assertEqual(detail['node']['lastDecisionError']['code'], 'PARAMETER_OUT_OF_RANGE')
                self.assertEqual(detail['node']['result'], original_result)
                self.assertNotIn(key, detail['node']['parameterOverrides'])

    def test_power_model_4_by_4_is_valid_but_6_by_4_remains_over_native_budget(self):
        for captures, expected in ((4, True), (6, False)):
            with self.subTest(captures=captures):
                wf, node, original_result = self._offline_failed_power_context()
                self.controller._brain = lambda *args: {'status': 'ok', 'decision': {'action': 'retry',
                    'reason': 'OFFLINE native limit test', 'evidenceIds': [],
                    'parameterChanges': {'capture_repeats': captures, 'acquisitions_per_point': 4}}}
                with patch.object(ArcLightAdapter, 'execute', side_effect=AssertionError('No hardware allowed')):
                    self.assertEqual(self.controller._decide(wf['id'], node['id']), expected)
                current = self.controller.get(wf['id'])
                actual = next(n for n in current['nodes'] if n['id'] == node['id'])
                self.assertEqual(current['reservedShots'], 8052000)
                self.assertEqual(actual['attempt'], 1)
                self.assertEqual(actual['result'], original_result)
                if not expected:
                    self.assertEqual(actual['lastDecisionError']['code'], 'BUDGET_EXCEEDED')
                    self.assertEqual(actual['parameterOverrides']['acquisitions_per_point'], 2)

    def test_saved_power_geometry_mismatch_does_not_publish_confirmed_cost(self):
        from backend.decision_context import build_decision_context
        wf, node, result = self._offline_failed_power_context()
        result['parameters']['points'] = 31
        node = {**node, 'result': result}
        context = build_decision_context({'observations': {'result': result}}, procedure_id='ro_power_shift')
        constraints = self.controller._decision_execution_constraints(wf, node, context)
        self.assertEqual(constraints['currentScan']['status'], 'unconfirmed')
        self.assertNotIn('shotsPerOperation', constraints['currentScan'])
        self.assertEqual(constraints['maximumTotalShots'], 12000000)

    def test_rb_snapshot_requires_all_adapter_working_point_fields(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            rb = next(n for n in current['nodes'] if n['procedureId'] == 'sq_rb')
            parameters, versions = self.controller._parameters(rt, current, rb)
            node = next(n for n in current['nodes'] if n['procedureId'] == 'ro_iq01')
            node.update(engineState='verified', latestAttemptId='offline-missing-policy')
            actual = deepcopy(parameters)
            actual.pop('pulse_compatibility')
            self.controller.store.attempt(rt, current['id'], {
                'id': 'offline-missing-policy', 'nodeId': node['id'], 'number': 1,
                'operationId': 'offline-missing-policy/measure',
                'verificationOperationId': 'offline-missing-policy/verify',
                'result': {'quality': {'status': 'passed'}, 'parameters': actual}})
            snapshot = self.controller._validation_snapshot(rt, current, rb, parameters, versions)
        self.assertEqual(snapshot['acceptedEvidence'], [])

    def test_arc_readout_verification_plan_measures_only_selected_setting(self):
        for procedure, key, axis, value in (
                ('ro_optimize', 'readout_amp', '.para.probCarryScale', .86375),
                ('ro_optimize_frequency', 'readout_hz', '.para.probFreq', 7.00951e9)):
            with self.subTest(procedure=procedure):
                original = {key: value, 'points': 9, 'amplitudes': [.1, .4, .98],
                            'frequencies_hz': [7.008e9, 7.009e9, 7.01e9]}
                frozen = self.adapter.build_verification_parameters(procedure, original, {key: value})
                plan = self.adapter.build_plan({'operationId': 'offline-frozen-' + procedure,
                    'resourceId': 'Q11', 'procedureId': procedure, 'parameters': frozen})
                sweep = next(s for s in plan['jobs'][0]['spec']['sweeps'] if s['bindings'].endswith(axis))
                self.assertEqual(sweep['values'], [value])
                self.assertEqual(plan['expectedJobRows'], [4])  # two labels x two captures
                self.assertEqual(len(original['amplitudes']), 3)
                self.assertEqual(len(original['frequencies_hz']), 3)

    def test_controller_fixed_readout_rebuild_uses_native_single_point_grid(self):
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'ro_optimize')
        with self.controller.store.connect() as rt:
            original, _ = self.controller._parameters(rt, wf, node)
        original['readout_amp'] = .4
        marker = {'optimizerNodeId': node['id'], 'values': {'readout_amp': .4, 'readout_hz': original['readout_hz']}}
        fixed = self.controller._readout_revalidation_parameters(node, original, marker)
        self.assertEqual(fixed['amplitudes'], [.4])
        with patch.object(ArcLightAdapter, 'execute', side_effect=AssertionError('No hardware allowed')):
            plan = self.adapter.build_plan({'operationId': 'offline-controller-revalidation',
                'resourceId': 'Q11', 'procedureId': 'ro_optimize', 'parameters': fixed})
            scan = self.adapter.build_plan({'operationId': 'offline-controller-rescan',
                'resourceId': 'Q11', 'procedureId': 'ro_optimize',
                'parameters': {**original, 'amp_start': .1, 'amp_stop': .8}})
        self.assertEqual(plan['expectedJobRows'], [4])
        self.assertEqual(scan['expectedJobRows'], [36])
        axis = next(s for s in plan['jobs'][0]['spec']['sweeps'] if s['bindings'].endswith('.para.probCarryScale'))
        self.assertEqual(axis['values'], [.4])
        scanned_axis = next(s for s in scan['jobs'][0]['spec']['sweeps'] if s['bindings'].endswith('.para.probCarryScale'))
        self.assertEqual(len(scanned_axis['values']), 9)
        self.assertAlmostEqual(scanned_axis['values'][0], .1)
        self.assertAlmostEqual(scanned_axis['values'][-1], .8)

    def test_independent_success_on_uncommittable_context_does_not_advance(self):
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_time')
        self.controller.set_parameters(wf['id'], node['id'], {
            'operationId': 'offline-context-edit', 'expectedRevision': wf['revision'],
            'parameters': {'drive_amp': .7}})
        self._run_successful_fake_at_node(wf['id'], node['id'])
        detail = self.controller.detail(wf['id'], node['id'])
        self.assertEqual(len(self.fake_context_calls), 2)
        self.assertFalse(detail['node']['mainProcedureComplete'])
        self.assertEqual(self.controller.get(wf['id'])['engineState'], 'waiting_model')
        self.assertEqual(detail['node']['result']['quality']['status'], 'failed')
        self.assertTrue(any('drive_amp' in reason and '上游' in reason for reason in detail['node']['result']['quality']['reasons']))
        with self.controller.store.connect() as rt:
            self.assertIsNone(rt.db.execute('SELECT value FROM parameters WHERE name=?',
                (wf['id'] + ':Q11:drive_length_s',)).fetchone())

    def test_incomplete_actual_context_is_not_accepted(self):
        wf = self.create()
        node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_time')
        self._run_successful_fake_at_node(wf['id'], node['id'], missing_key='xy_delay_s')
        detail = self.controller.detail(wf['id'], node['id'])
        self.assertEqual(detail['node']['result']['quality']['status'], 'failed')
        self.assertTrue(any('缺少完整校准上下文' in r and 'xy_delay_s' in r for r in detail['node']['result']['quality']['reasons']))

    def _run_successful_fake_at_node(self, workflow_id, node_id, missing_key=None):
        with self.controller.store.transaction() as rt:
            current = self.controller.get(workflow_id, rt)
            current['hardwareConfirmed'] = True
            self.controller.store.save(rt, current, 'offline_fixture_confirmation')
        self.fake_context_calls = []
        def fake_execute(_adapter, request, emit, cancel):
            self.fake_context_calls.append(deepcopy(request))
            parameters = self.adapter.build_plan(request)['parameters']
            if missing_key:
                parameters.pop(missing_key)
            raw = self.root / ('offline-context-' + str(len(self.fake_context_calls)) + '.json')
            raw.write_text(json.dumps({'test_only': True, 'operationId': request['operationId']}))
            return {'dataKind': 'real', 'acquiredAt': now(), 'parameters': parameters,
                    'metrics': {'r_squared': .99, 'contrast_snr': 10, 'cycles': 3},
                    'analysisQuality': {'accepted': True, 'reasons': []},
                    'candidateParameters': {'drive_length_s': 320e-9},
                    'artifacts': [{'id': 'offline-context-' + str(len(self.fake_context_calls)),
                        'name': raw.name, 'kind': 'raw', 'localPath': str(raw),
                        'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}]}
        with patch.object(self.controller, 'hardware_ownership', return_value={'verified': True, 'owners': []}), \
             patch.object(ArcLightAdapter, 'execute', fake_execute):
            self.assertFalse(self.controller._execute_node(workflow_id, node_id))


if __name__ == '__main__':
    unittest.main()
