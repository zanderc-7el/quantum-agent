"""Offline native plans + temporary controller DB; no instrument/model access."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from adapters.arclight import ArcLightAdapter
from adapters.f8 import F8Adapter
from backend.acquisition_budget import (verification_probe, selection_lookahead,
    verify_admission, sampling_context, VERSION)
from backend.common import ROOT, APIError, now
from backend.controller import Controller


class BudgetPureTests(unittest.TestCase):
    def test_pair_check_is_not_reservation_and_verification_is_not_double_charged(self):
        wf = {'maxTotalShots': 300, 'reservedShots': 100}
        before = deepcopy(wf)
        check = selection_lookahead(wf, {'estimated_total_shots': 100}, {'estimated_total_shots': 100}, basis='test')
        self.assertEqual(wf, before)
        self.assertFalse(check['reservation'])
        self.assertEqual(check['jointShots'], 200)
        wf['reservedShots'] += 100  # Only the actual selection reservation.
        verified = verify_admission(wf, {'estimated_total_shots': 100}, check)
        self.assertFalse(verified['selectionChargedAgain'])
        self.assertEqual(wf['reservedShots'], 200)
        with self.assertRaises(APIError) as error:
            selection_lookahead(wf, {'estimated_total_shots': 100}, {'estimated_total_shots': 100}, basis='test')
        self.assertEqual(error.exception.code, 'JOINT_SHOTS_BUDGET')

    def test_unknown_malformed_and_bool_budgets_fail_closed(self):
        for key, bad in [('maxTotalShots', None), ('reservedShots', None), ('reservedShots', -1),
                         ('reservedShots', 101), ('reservedShots', False), ('maxTotalShots', 100.0)]:
            with self.subTest(key=key, bad=bad), self.assertRaises(APIError):
                selection_lookahead({**{'maxTotalShots': 100, 'reservedShots': 0}, key: bad},
                    {'estimated_total_shots': 1}, {'estimated_total_shots': 1}, basis='test')
        for bad in (None, 0, -1, True, 1.0, float('nan')):
            with self.subTest(bad=bad), self.assertRaises(APIError):
                selection_lookahead({'maxTotalShots': 100, 'reservedShots': 0},
                    {'estimated_total_shots': bad}, {'estimated_total_shots': 1}, basis='test')
        with self.assertRaises(APIError) as error:
            verification_probe(implementation_id='unverified.Adapter', profile_id='arclight-private-f8-q11-v2',
                procedure_id='sq_ramsey', request_parameters={}, selection_plan={})
        self.assertEqual(error.exception.code, 'VERIFICATION_BUDGET_UNKNOWN')

    def test_changed_verification_or_depleted_current_budget_rejected(self):
        check = {'version': VERSION, 'verificationShotsUpperBound': 20}
        for shots, reserved, expected in ((21, 0, 'VERIFICATION_BUDGET_CHANGED'), (20, 81, 'TOTAL_SHOTS_BUDGET')):
            with self.subTest(expected=expected), self.assertRaises(APIError) as error:
                verify_admission({'maxTotalShots': 100, 'reservedShots': reserved}, {'estimated_total_shots': shots}, check)
            self.assertEqual(error.exception.code, expected)

    def test_science_context_is_specific_to_audited_capture_fit_methods(self):
        kwargs = {'implementation_id': 'adapters.arclight.ArcLightAdapter', 'profile_id': 'arclight-private-f8-q11-v2'}
        context = sampling_context(**kwargs, procedure_id='sq_ramsey')
        self.assertEqual(context['rSquaredAggregation'], 'minimum_across_capture_fits')
        self.assertIn('不会降低单个 capture', context['captureRepeats'])
        self.assertIn('实际采集耗时', context['wallClock'])
        self.assertIsNone(sampling_context(**kwargs, procedure_id='sq_rb'))
        self.assertIsNone(sampling_context(**{**kwargs, 'profile_id': 'unknown'}, procedure_id='sq_ramsey'))


class NativeBudgetControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='qcal-joint-budget-offline-')
        self.root = Path(self.tmp.name)
        imports = self.root / 'data/imports'
        imports.mkdir(parents=True)
        (imports / 'history.json').write_text(json.dumps({'version': 1,
            'resources': [{'id': 'Q11', 'label': 'Q11'}], 'records': []}), encoding='utf-8')
        self.adapter = ArcLightAdapter(ROOT, ROOT / 'adapters/arclight_profile_v2.json')
        self.controller = Controller(self.root, adapter=self.adapter, start_worker=False)
        self.controller._brain = Mock(return_value={'status': 'waiting_model', 'message': 'OFFLINE'})
        self.calls = []

    def tearDown(self):
        self.controller.close()
        self.tmp.cleanup()

    def create_at(self, *, procedure='sq_ramsey', parameters=None, maximum=60_000_000, reserved=0):
        wf = self.controller.create({'name': 'OFFLINE joint budget', 'mode': 'real', 'policy': 'autopilot', 'resourceIds': ['Q11']})
        node = next(n for n in wf['nodes'] if n['procedureId'] == procedure)
        with self.controller.store.transaction() as rt:
            wf = self.controller.get(wf['id'], rt)
            wf.update(status='queued', engineState='queued', currentNodeId=node['id'], hardwareConfirmed=True,
                      maxTotalShots=maximum, reservedShots=reserved)
            node = self.controller._node(wf)
            node.update(parameterOverrides=deepcopy(parameters or {}), result={'dataKind': 'real',
                'quality': {'status': 'failed', 'reasons': ['OFFLINE prior failure']}, 'artifacts': []})
            self.controller.store.save(rt, wf, 'offline_fixture')
        return self.controller.get(wf['id'])

    def plan_pair(self, procedure, parameters):
        wf = self.create_at(procedure=procedure, parameters=parameters)
        node = self.controller._node(wf)
        with self.controller.store.connect() as rt:
            actual, versions = self.controller._parameters(rt, wf, node)
        return self.controller._validate_plan(wf, node, actual, versions, 'offline-plan', joint_budget=True)

    def test_actual_sixth_ramsey_configuration_requires_23m232_not_11m616(self):
        pair = self.plan_pair('sq_ramsey', {'points': 121, 'capture_repeats': 6, 'acquisitions_per_point': 8, 'shots': 2000})
        self.assertEqual(pair['selectionShots'], 11_616_000)
        self.assertEqual(pair['verificationShotsUpperBound'], 11_616_000)
        self.assertEqual(pair['jointShots'], 23_232_000)
        self.assertEqual(pair['verificationBasis'], 'same_sampling_counts_after_calibration_only')

    def test_readout_plan_counts_fixed_candidate_and_preserves_scan_parameters(self):
        parameters = {'points': 9, 'capture_repeats': 2, 'acquisitions_per_point': 4, 'shots': 2000}
        before = deepcopy(parameters)
        pair = self.plan_pair('ro_optimize', parameters)
        self.assertEqual(pair['selectionShots'], 9 * 2 * 2 * 4 * 2000)
        self.assertEqual(pair['verificationShotsUpperBound'], 2 * 2 * 4 * 2000)
        self.assertEqual(parameters, before)
        fixed = self.plan_pair('ro_optimize', {**parameters, 'amplitudes': [.8]})
        self.assertEqual(fixed['selectionShots'], pair['verificationShotsUpperBound'])

    def test_allxy_and_rb_joint_budgets_include_references_in_both_operations(self):
        allxy = self.plan_pair('sq_allxy', {'shots': 2000, 'capture_repeats': 2, 'acquisitions_per_point': 4})
        self.assertEqual(allxy['selectionShots'], 21 * 2 * 4 * 2000 + 2 * 6 * 2 * 2000)
        self.assertEqual(allxy['jointShots'], 2 * allxy['selectionShots'])
        # Evidence validity is a separate existing contract; replace it ONLY
        # for offline counting, without creating receipts or executing the plan.
        with patch.object(self.adapter, '_validate_snapshot', return_value={'offline_budget_only': True}):
            rb = self.plan_pair('sq_rb', {})
        self.assertEqual(rb['selectionShots'], 2 * 8 * 20 * 4 * 2000 + 2 * 2 * 6 * 2 * 2000)
        self.assertEqual(rb['jointShots'], 2 * rb['selectionShots'])

    def test_f8_native_plans_preserve_own_verification_semantics_and_rb_reference_costs(self):
        adapter = F8Adapter(ROOT, ROOT / 'adapters/f8_profile.json')
        wf = {'maxTotalShots': 60_000_000, 'reservedShots': 0}
        for procedure in ('sq_ramsey', 'ro_optimize', 'sq_allxy', 'sq_rb'):
            with self.subTest(procedure=procedure), \
                 patch.object(adapter, 'execute', side_effect=AssertionError('No F8 execution')), \
                 patch.object(adapter, '_calibration', return_value=None), \
                 patch('adapters.rb_suite.validate_snapshot', return_value={'offline_budget_only': True}):
                request = {'runId': 'offline-f8', 'resourceId': 'Q11', 'procedureId': procedure,
                    'operationId': 'offline-f8-measure', 'parameters': {}, 'parameterVersions': {}}
                plan = adapter.build_plan(request)
                params, basis = verification_probe(implementation_id='adapters.f8.F8Adapter',
                    profile_id=adapter.profile['id'], procedure_id=procedure,
                    request_parameters=request['parameters'], selection_plan=plan)
                verification = adapter.build_plan({**request, 'operationId': 'offline-f8-verify', 'parameters': params})
                pair = selection_lookahead(wf, plan, verification, basis=basis)
                self.assertEqual(pair['jointShots'], 2 * plan['estimated_total_shots'])
                self.assertEqual(params, {})
                if procedure == 'ro_optimize':
                    self.assertEqual(len(verification['jobs']), 1)
                    self.assertEqual(verification['jobs'][0]['points'], 15)  # F8 still rescans, unlike ArcLight.
                if procedure == 'sq_rb':
                    self.assertEqual(plan['estimated_total_shots'], 30 * 8 * 2 * 2 * 1000 + 2 * 2 * 4 * 1000)

    def test_insufficient_pair_rejected_before_attempt_lease_effect_or_result_changes(self):
        wf = self.create_at(parameters={'points': 121, 'capture_repeats': 6, 'acquisitions_per_point': 8, 'shots': 2000},
                            reserved=44_008_000)
        node = self.controller._node(wf)
        with patch.object(ArcLightAdapter, 'execute', side_effect=AssertionError('No acquisition')) as execute, \
             patch.object(self.controller, 'hardware_ownership', side_effect=AssertionError('No ownership probe')):
            self.assertFalse(self.controller._execute_node(wf['id'], node['id']))
        execute.assert_not_called()
        current = self.controller.get(wf['id'])
        after = self.controller._node(current)
        self.assertEqual(after['lastAdmissionError']['code'], 'JOINT_SHOTS_BUDGET')
        for key in ('attempt', 'result', 'latestAttemptId'):
            self.assertEqual(after.get(key), node.get(key))
        self.assertEqual(current['reservedShots'], 44_008_000)
        self.assertEqual(current['iterations'], wf['iterations'])
        with self.controller.store.connect() as rt:
            for table in ('platform_attempts', 'platform_effects', 'platform_leases'):
                self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def fake_execute(self, adapter, request, emit, cancel):
        self.calls.append(deepcopy(request))
        path = self.root / ('offline-raw-' + str(len(self.calls)) + '.json')
        path.write_text(json.dumps({'operation': request['operationId'], 'offline': True}))
        return {'dataKind': 'real', 'acquiredAt': now(), 'parameters': adapter.build_plan(request)['parameters'],
            'analysisQuality': {'accepted': True, 'reasons': []},
            'metrics': {'r_squared': .95, 'relative_stderr': .1}, 'candidateParameters': {},
            'artifacts': [{'id': 'offline-raw-' + str(len(self.calls)), 'name': path.name, 'kind': 'raw',
                'localPath': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}

    def test_two_real_plans_fake_acquisitions_reserve_once_each_at_exact_boundary(self):
        wf = self.create_at(parameters={'points': 41, 'capture_repeats': 2, 'acquisitions_per_point': 8, 'shots': 2000},
                            maximum=2_624_000)
        with patch.object(ArcLightAdapter, 'execute', autospec=True, side_effect=self.fake_execute), \
             patch.object(self.controller, 'hardware_ownership', return_value={'verified': True, 'owners': []}):
            self.assertTrue(self.controller._execute_node(wf['id'], wf['currentNodeId']))
        self.assertEqual(len(self.calls), 2)
        current = self.controller.get(wf['id'])
        self.assertEqual(current['reservedShots'], 2_624_000)
        self.assertEqual(self.controller._node(current)['engineState'], 'verified')
        self.assertEqual(self.calls[1]['verificationBudgetCheck']['remainingShotsAtCheck'], 1_312_000)
        self.assertEqual(self.calls[0]['parameters'], self.calls[1]['parameters'])
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM platform_leases').fetchone()[0], 0)

    def test_model_retry_rejects_unaffordable_pair_preserving_old_scientific_failure(self):
        wf = self.create_at(reserved=44_008_000)
        before = deepcopy(self.controller._node(wf)['result'])
        self.controller._brain = Mock(return_value={'status': 'ok', 'decision': {'action': 'retry',
            'reason': 'OFFLINE proposal', 'evidenceIds': [], 'parameterChanges':
            {'points': 121, 'capture_repeats': 6, 'acquisitions_per_point': 8, 'shots': 2000}}})
        self.assertFalse(self.controller._decide(wf['id'], wf['currentNodeId']))
        current = self.controller.get(wf['id'])
        node = self.controller._node(current)
        self.assertEqual(node['lastDecisionError']['code'], 'JOINT_SHOTS_BUDGET')
        self.assertEqual(node['result'], before)
        self.assertEqual(current['reservedShots'], 44_008_000)
        context = self.controller._brain.call_args.args[1]['executionConstraints']
        self.assertTrue(context['selectionAdmissionRequiresVerificationCapacity'])
        self.assertEqual(context['samplingSemantics']['fitUnit'], 'one_capture')

    def test_model_backtrack_checks_capacity_before_invalidating_target(self):
        wf = self.create_at(reserved=44_008_000)
        target_id = next(n['id'] for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            target = self.controller._node(current, target_id)
            target['parameterOverrides'] = {'points': 121, 'capture_repeats': 6, 'acquisitions_per_point': 8, 'shots': 2000}
            target.update(status='success', engineState='verified', mainProcedureComplete=True)
            self.controller.store.save(rt, current, 'offline_backtrack_fixture')
        self.controller._brain = Mock(return_value={'status': 'ok', 'decision': {'action': 'backtrack',
            'targetNodeId': target_id, 'reason': 'OFFLINE backtrack', 'evidenceIds': []}})
        self.assertFalse(self.controller._decide(wf['id'], wf['currentNodeId']))
        current = self.controller.get(wf['id'])
        self.assertEqual(self.controller._node(current)['lastDecisionError']['code'], 'JOINT_SHOTS_BUDGET')
        self.assertEqual(self.controller._node(current, target_id)['engineState'], 'verified')
        self.assertEqual(current['reservedShots'], 44_008_000)

    def test_pause_after_selection_keeps_reservation_and_does_not_start_verify(self):
        wf = self.create_at(parameters={'points': 41, 'capture_repeats': 2, 'acquisitions_per_point': 4, 'shots': 2000})
        def stopped(adapter, request, emit, cancel):
            result = self.fake_execute(adapter, request, emit, cancel)
            with self.controller.store.transaction() as rt:
                current = self.controller.get(wf['id'], rt)
                current['cancelRequested'] = 'pause'
                self.controller.store.save(rt, current, 'offline_cooperative_pause')
            return result
        with patch.object(ArcLightAdapter, 'execute', autospec=True, side_effect=stopped), \
             patch.object(self.controller, 'hardware_ownership', return_value={'verified': True, 'owners': []}):
            self.assertFalse(self.controller._execute_node(wf['id'], wf['currentNodeId']))
        self.assertEqual(len(self.calls), 1)
        current = self.controller.get(wf['id'])
        self.assertEqual(current['reservedShots'], 656_000)
        self.assertEqual(current['status'], 'paused')
        self.assertNotEqual(self.controller._node(current)['result']['quality']['status'], 'passed')

    def test_candidate_changed_verify_cost_blocks_without_losing_first_receipt(self):
        wf = self.create_at(parameters={'points': 41, 'capture_repeats': 2, 'acquisitions_per_point': 4, 'shots': 2000})
        def changed(adapter, request, emit, cancel):
            result = self.fake_execute(adapter, request, emit, cancel)
            result['candidateParameters'] = {'points': 81}  # Explicit contract violation fixture.
            return result
        with patch.object(ArcLightAdapter, 'execute', autospec=True, side_effect=changed), \
             patch.object(self.controller, 'hardware_ownership', return_value={'verified': True, 'owners': []}):
            self.assertFalse(self.controller._execute_node(wf['id'], wf['currentNodeId']))
        self.assertEqual(len(self.calls), 1)
        current = self.controller.get(wf['id'])
        self.assertEqual(current['reservedShots'], 656_000)
        node = self.controller._node(current)
        self.assertEqual(node['engineState'], 'budget_exhausted')
        self.assertEqual(node['result']['quality']['status'], 'unverified')
        self.assertTrue(node['result']['selectionResult']['artifacts'])
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM platform_effects').fetchone()[0], 1)
            self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM platform_leases').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
