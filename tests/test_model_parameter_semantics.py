"""Offline tests for a staged pure helper; no controller/service/device calls."""
from copy import deepcopy
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.model_parameter_semantics import build_model_parameter_contract, SUPPORTED_IDENTITY


ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / 'adapters/arclight_profile.json').read_text(encoding='utf-8-sig'))


def schema_fixture():
    return {key: {'enum': list(value['enum'])} if isinstance(value, dict) else {
        'min': value[0], 'max': value[1],
        **({'integer': True} if key in ('shots', 'capture_repeats', 'acquisitions_per_point', 'points', 'pi_pulses', 'xy_filter_mhz') else {})}
        for key, value in PROFILE['parameterBounds'].items()}


class ModelParameterSemanticsTests(unittest.TestCase):
    def build(self, procedure, parameters=None, **kwargs):
        return build_model_parameter_contract(implementation_id=SUPPORTED_IDENTITY[0],
            profile_id=PROFILE['id'], procedure_id=procedure, parameter_bounds=schema_fixture(),
            effective_parameters=parameters or {}, **kwargs)

    def test_current_profile_exact_identity_matches_and_other_adapters_are_unchanged(self):
        self.assertEqual(PROFILE['id'], 'arclight-private-f8-q11-v1')
        self.assertTrue(self.build('sq_spectroscopy')['applied'])
        original = schema_fixture()
        for implementation, profile, procedure in (
            ('adapters.f8.F8Adapter', PROFILE['id'], 'sq_spectroscopy'),
            (SUPPORTED_IDENTITY[0], 'arclight-private-f8-other-v1', 'sq_spectroscopy'),
            ('arclight-native-cy8', PROFILE['id'], 'sq_spectroscopy'),
            (SUPPORTED_IDENTITY[0], PROFILE['id'], 'sq_unsupported')):
            result = build_model_parameter_contract(implementation_id=implementation, profile_id=profile,
                procedure_id=procedure, parameter_bounds=original, effective_parameters={})
            self.assertFalse(result['applied'])
            self.assertEqual(result['parameterBounds'], original)
            result['parameterBounds']['shots']['min'] = -123
            self.assertEqual(original, schema_fixture())

    def test_method_projection_preserves_input_and_only_exposes_closed_loop_knobs(self):
        common = {'shots', 'capture_repeats', 'acquisitions_per_point'}
        expected = {'sq_spectroscopy': {'drive_hz', 'span_hz', 'points', 'spectroscopy_width_s'},
                    'sq_rabi_amplitude': {'amp_start', 'amp_stop', 'points'},
                    'sq_rabi_time': {'start_s', 'stop_s', 'points'},
                    'ro_iq01': set(), 'ro_optimize': {'amp_start', 'amp_stop', 'points'}}
        for procedure, keys in expected.items():
            with self.subTest(procedure=procedure):
                bounds = schema_fixture()
                parameters = {'capture_repeats': 1, 'drive_amp': .98}
                before = deepcopy((bounds, parameters))
                result = build_model_parameter_contract(implementation_id=SUPPORTED_IDENTITY[0],
                    profile_id=PROFILE['id'], procedure_id=procedure, parameter_bounds=bounds,
                    effective_parameters=parameters)
                self.assertEqual(set(result['parameterBounds']), common | keys)
                self.assertEqual((bounds, parameters), before)
                self.assertEqual(result['parameterBounds']['capture_repeats']['min'], 1 if procedure.startswith('ro_') else 2)
                self.assertNotIn('drive_amp', result['parameterBounds'])
                self.assertNotIn('readout_hz', result['parameterBounds'])
                self.assertFalse(result['issues'])

    def test_spectrum_explicit_frequency_axis_overrides_all_construction_knobs(self):
        result = self.build('sq_spectroscopy', {'frequencies_hz': [4.8e9, 4.81e9, 4.82e9]})
        self.assertTrue({'drive_hz', 'span_hz', 'points'}.isdisjoint(result['parameterBounds']))
        self.assertIn('spectroscopy_width_s', result['parameterBounds'])

    def test_one_explicit_spectrum_endpoint_does_not_disable_other_endpoint_controls(self):
        for parameters in ({'start_hz': 4.8e9}, {'stop_hz': 4.9e9}):
            result = self.build('sq_spectroscopy', parameters)
            self.assertTrue({'drive_hz', 'span_hz', 'points'} <= set(result['parameterBounds']))
        both = self.build('sq_spectroscopy', {'start_hz': 4.8e9, 'stop_hz': 4.9e9})
        self.assertIn('points', both['parameterBounds'])
        self.assertTrue({'drive_hz', 'span_hz'}.isdisjoint(both['parameterBounds']))

    def test_explicit_amplitude_grid_cannot_be_changed_by_ordinary_endpoint_edits(self):
        for procedure in ('sq_rabi_amplitude', 'ro_optimize'):
            result = self.build(procedure, {'amplitudes': [.1, .2, .4]})
            self.assertTrue({'amp_start', 'amp_stop', 'points'}.isdisjoint(result['parameterBounds']))
            self.assertIn('shots', result['parameterBounds'])

    def test_irrelevant_explicit_axes_do_not_remove_a_time_or_amplitude_grid(self):
        timed = self.build('sq_rabi_time', {'frequencies_hz': [4.8e9], 'amplitudes': [.4]})
        self.assertTrue({'start_s', 'stop_s', 'points'} <= set(timed['parameterBounds']))
        amplitude = self.build('sq_rabi_amplitude', {'frequencies_hz': [4.8e9]})
        self.assertTrue({'amp_start', 'amp_stop', 'points'} <= set(amplitude['parameterBounds']))
        readout = self.build('ro_optimize', {'frequencies_hz': [7e9]})
        self.assertTrue({'amp_start', 'amp_stop', 'points'} <= set(readout['parameterBounds']))

    def test_malformed_existing_axes_are_not_reinterpreted_as_defaults(self):
        for value in ([], None, '4.8e9', [float('nan')], [True], [4.8e9] * 162):
            result = self.build('sq_spectroscopy', {'frequencies_hz': value})
            self.assertEqual(result['parameterBounds'], {})
            self.assertTrue(result['requiresEffectiveParameterRepair'])
            self.assertEqual(result['issues'][0]['code'], 'INVALID_EXISTING_EXPLICIT_AXIS')

    def test_fixed_readout_keeps_only_sampling_without_explicit_eligible_failure(self):
        common = {'shots', 'capture_repeats', 'acquisitions_per_point'}
        for state, eligible in (('active', False), ('active', True), ('fixed_failed', False)):
            result = self.build('ro_optimize', {'amplitudes': [.4]},
                fixed_readout_state=state, fixed_rescan_eligible=eligible)
            self.assertEqual(set(result['parameterBounds']), common)
        unknown = self.build('ro_optimize', fixed_readout_state='stale_marker')
        self.assertEqual(set(unknown['parameterBounds']), common)
        self.assertEqual(unknown['issues'][0]['code'], 'UNKNOWN_FIXED_READOUT_STATE')

    def test_eligible_failed_fixed_candidate_retains_paired_range_and_conditional_points(self):
        actual = {'amplitudes': [.4], 'amp_start': .05, 'amp_stop': .98, 'points': 9}
        before = deepcopy(actual)
        result = self.build('ro_optimize', actual, fixed_readout_state='fixed_failed', fixed_rescan_eligible=True)
        self.assertTrue({'amp_start', 'amp_stop', 'points'} <= set(result['parameterBounds']))
        self.assertEqual(actual, before)
        self.assertIn({'parameters': ['amp_start', 'amp_stop'], 'kind': 'required_together'},
                      [{k: c[k] for k in ('parameters', 'kind')} for c in result['conditions']])
        point_condition = next(c for c in result['conditions'] if c['parameters'] == ['points'])
        self.assertEqual(point_condition['requires'], ['amp_start', 'amp_stop'])
        self.assertTrue(point_condition['requiresCallerSemanticCheck'])
        self.assertFalse(result['isExecutionApproval'])
        self.assertEqual(result['parameterBounds']['amp_stop']['max'], .98)

    def test_effective_next_request_does_not_inherit_verification_only_pin(self):
        effective = {'capture_repeats': 2, 'amp_start': .05, 'amp_stop': .98, 'points': 9}
        verification_receipt = {**effective, 'amplitudes': [.4]}
        ordinary = self.build('ro_optimize', effective)
        explicitly_pinned = self.build('ro_optimize', verification_receipt)
        self.assertIn('amp_start', ordinary['parameterBounds'])
        self.assertNotIn('amp_start', explicitly_pinned['parameterBounds'])
        self.assertNotIn('amplitudes', effective)

    def test_physical_endpoint_and_time_window_intersections_do_not_modify_operator_schema(self):
        original = schema_fixture()
        amplitude = self.build('sq_rabi_amplitude')['parameterBounds']
        readout = self.build('ro_optimize')['parameterBounds']
        timed = self.build('sq_rabi_time')['parameterBounds']
        self.assertEqual(amplitude['amp_start']['min'], .01)
        self.assertEqual(amplitude['amp_stop']['max'], .99)
        self.assertEqual(readout['amp_start']['min'], .01)
        self.assertEqual(readout['amp_stop']['max'], .98)
        self.assertEqual(timed['start_s']['max'], 79.7e-6)
        self.assertEqual(timed['stop_s']['max'], 79.7e-6)
        self.assertEqual(original['stop_s']['max'], 100e-6)
        self.assertEqual(original['amp_start']['min'], 0)

    def test_current_invalid_scalar_can_be_repaired_within_narrowed_bounds(self):
        result = self.build('sq_rabi_time', {'capture_repeats': 1, 'start_s': 90e-6, 'stop_s': 100e-6})
        self.assertFalse(result['issues'])
        self.assertEqual(result['parameterBounds']['capture_repeats']['min'], 2)
        self.assertIn('start_s', result['parameterBounds'])
        self.assertIn('stop_s', result['parameterBounds'])

    def test_narrowed_boundary_grids_match_native_static_plan_without_execution(self):
        from adapters.arclight import ArcLightAdapter
        adapter = ArcLightAdapter(ROOT, ROOT / 'adapters/arclight_profile.json')
        with patch.object(ArcLightAdapter, 'execute', side_effect=AssertionError('No hardware allowed')):
            for procedure in ('sq_rabi_amplitude', 'ro_optimize', 'sq_rabi_time'):
                bounds = self.build(procedure)['parameterBounds']
                parameters = {'shots': 100, 'capture_repeats': 2, 'acquisitions_per_point': 1, 'points': 9}
                if procedure == 'sq_rabi_time':
                    parameters.update(start_s=0, stop_s=bounds['stop_s']['max'])
                else:
                    parameters.update(amp_start=bounds['amp_start']['min'], amp_stop=bounds['amp_stop']['max'])
                plan = adapter.build_plan({'operationId': 'offline-semantics-boundary-' + procedure,
                    'resourceId': 'Q11', 'procedureId': procedure, 'parameters': parameters})
                self.assertGreater(plan['totalRows'], 0)
                self.assertLess(plan['estimated_total_shots'], PROFILE['maximumTotalShots'])


class LaterMethodSemanticsTests(unittest.TestCase):
    """Real pure plan construction; RB prerequisite mock is budget-only, never execution."""
    build = ModelParameterSemanticsTests.build

    def setUp(self):
        from adapters.arclight import ArcLightAdapter
        self.adapter = ArcLightAdapter(ROOT, ROOT / 'adapters/arclight_profile.json')
        self.no_execute = patch.object(ArcLightAdapter, 'execute', side_effect=AssertionError('No hardware allowed'))
        self.no_execute.start()
        self.addCleanup(self.no_execute.stop)

    def plan(self, procedure, **parameters):
        request = {'operationId': 'offline-later-method-semantics/' + procedure, 'resourceId': 'Q11',
            'procedureId': procedure, 'parameters': {'shots': 100, 'capture_repeats': 2,
            'acquisitions_per_point': 1, 'points': 9, 'start_s': 0, 'stop_s': 4e-6, **parameters}}
        # Does not create, read or claim accepted hardware evidence. Only the
        # existing pure RB plan geometry/budget is under test, with execute blocked.
        with patch.object(self.adapter, '_validate_snapshot', return_value={'offline_budget_only': True}):
            return self.adapter.build_plan(request)

    def test_later_methods_expose_only_effective_registered_controls(self):
        time = {'start_s', 'stop_s', 'points'}
        expected = {'sq_t1': time, 'sq_ramsey': time | {'rotation_hz'}, 'sq_echo': time | {'rotation_hz'},
            'sq_cpmg': time | {'pi_pulses'}, 'sq_allxy': set(), 'sq_rb': set()}
        original = schema_fixture()
        for procedure, knobs in expected.items():
            with self.subTest(procedure=procedure):
                contract = self.build(procedure)
                self.assertTrue(contract['applied'])
                self.assertEqual(set(contract['parameterBounds']), knobs | {'shots', 'capture_repeats', 'acquisitions_per_point'})
                self.assertEqual(contract['parameterBounds']['capture_repeats']['min'], 1 if procedure == 'sq_rb' else 2)
                self.assertFalse(contract['isExecutionApproval'])
                self.assertFalse(contract['issues'])
                self.assertTrue({'drive_amp', 'drive_hz', 'readout_amp', 'readout_hz', 'rb_lengths', 'rb_sequences'}.isdisjoint(contract['parameterBounds']))
        self.assertTrue(self.build('sq_cpmg')['parameterBounds']['pi_pulses']['integer'])
        self.assertEqual(schema_fixture(), original)

    def test_s21_survey_only_exposes_committable_frequency_and_sampling_controls(self):
        from backend.catalog import PARAMETERS
        contract = self.build('ro_resonator_survey')
        self.assertEqual(set(contract['parameterBounds']),
            {'readout_hz', 'span_hz', 'points', 'shots', 'capture_repeats', 'acquisitions_per_point'})
        self.assertEqual(contract['parameterBounds']['capture_repeats']['min'], 2)
        self.assertEqual(PARAMETERS['ro_resonator_survey'][1], ['readout_frequency_hz'])
        self.assertTrue({'readout_amp', 'drive_hz', 'drive_amp', 'amp_start', 'amp_stop'}.isdisjoint(contract['parameterBounds']))
        self.assertTrue(any('readout_amp changes the physical readout' in note for note in contract['notes']))
        before = self.plan('ro_resonator_survey', readout_amp=.8)
        after = self.plan('ro_resonator_survey', readout_amp=.4)
        self.assertNotEqual(before['jobs'][0]['config']['Q1']['para']['probCarryScale'],
            after['jobs'][0]['config']['Q1']['para']['probCarryScale'])
        # Power keeps its existing controller-only contract; auxiliary methods
        # are not accidentally included in the new survey projection.
        for procedure in ('ro_power_shift', 'ro_optimize_frequency', 'sq_rabi_frequency'):
            self.assertFalse(self.build(procedure)['applied'])

    def test_s21_survey_center_span_points_and_sampling_change_actual_scan(self):
        before = self.plan('ro_resonator_survey', readout_hz=7e9, span_hz=4e6)
        after = self.plan('ro_resonator_survey', readout_hz=7.01e9, span_hz=2e6,
            points=13, capture_repeats=3, acquisitions_per_point=2, shots=200)
        first = before['jobs'][0]['spec']['sweeps'][0]
        changed = after['jobs'][0]['spec']['sweeps'][0]
        self.assertEqual(changed['bindings'], 'cfg.Q1.para.probFreq')
        self.assertEqual((first['values'][0], first['values'][-1], len(first['values'])), (6.998e9, 7.002e9, 9))
        self.assertEqual((changed['values'][0], changed['values'][-1], len(changed['values'])), (7.009e9, 7.011e9, 13))
        self.assertEqual((before['totalRows'], after['totalRows']), (18, 39))
        self.assertEqual((before['estimated_total_shots'], after['estimated_total_shots']), (1800, 15600))

    def test_s21_explicit_frequency_axis_excludes_ineffective_grid_edits(self):
        values = [6.999e9 + index * 250000 for index in range(9)]
        contract = self.build('ro_resonator_survey', {'frequencies_hz': values})
        self.assertEqual(set(contract['parameterBounds']), {'shots', 'capture_repeats', 'acquisitions_per_point'})
        before = self.plan('ro_resonator_survey', frequencies_hz=values)
        after = self.plan('ro_resonator_survey', frequencies_hz=values, readout_hz=7.1e9,
            span_hz=8e6, points=17, amp_start=.2, amp_stop=.6, drive_hz=4.7e9)
        self.assertEqual(before['jobs'][0]['spec'], after['jobs'][0]['spec'])
        self.assertEqual(after['jobs'][0]['spec']['sweeps'][0]['values'], values)
        self.assertEqual(before['estimated_total_shots'], after['estimated_total_shots'])

    def test_explicit_unused_arrays_leave_time_controls_effective(self):
        for procedure in ('sq_t1', 'sq_ramsey', 'sq_echo', 'sq_cpmg'):
            contract = self.build(procedure, {'frequencies_hz': [4.8e9], 'amplitudes': [.4], 'powers': [.2]})
            self.assertTrue({'start_s', 'stop_s', 'points'} <= set(contract['parameterBounds']))
            plain = self.plan(procedure)
            extra = self.plan(procedure, frequencies_hz=[4.8e9], amplitudes=[.4], powers=[.2])
            self.assertEqual(plain['jobs'][0]['spec'], extra['jobs'][0]['spec'])
            self.assertEqual(plain['estimated_total_shots'], extra['estimated_total_shots'])

    def test_time_endpoints_and_points_change_actual_delay_axis_and_budget(self):
        for procedure in ('sq_t1', 'sq_ramsey', 'sq_echo', 'sq_cpmg'):
            with self.subTest(procedure=procedure):
                before = self.plan(procedure)
                after = self.plan(procedure, start_s=1e-6, stop_s=6e-6, points=13)
                job = after['jobs'][0]
                multiplier = 2 if procedure == 'sq_echo' else job.get('total_delay_multiplier', 1)
                values = job['spec']['sweeps'][0]['values']
                self.assertEqual(len(values), 13)
                self.assertAlmostEqual(values[0] * multiplier, 1e-6)
                self.assertAlmostEqual(values[-1] * multiplier, 6e-6)
                self.assertEqual((before['totalRows'], after['totalRows']), (18, 26))
                self.assertEqual((before['estimated_total_shots'], after['estimated_total_shots']), (1800, 2600))
                self.assertEqual(before['jobs'][0]['config'], after['jobs'][0]['config'])

    def test_ramsey_echo_rotation_is_actual_virtual_phase_not_drive_frequency(self):
        for procedure in ('sq_ramsey', 'sq_echo'):
            for rotation in (0, 250000, -250000):
                with self.subTest(procedure=procedure, rotation=rotation):
                    plan = self.plan(procedure, rotation_hz=rotation)
                    job = plan['jobs'][0]
                    phase = next(axis['values'] for axis in job['spec']['sweeps'] if axis['name'] == 'virtual_phase_rad')
                    self.assertAlmostEqual(phase[-1], -2 * math.pi * rotation * 4e-6)
                    self.assertEqual(job['spec']['meta']['phase_ramp_hz'], -rotation)
                    self.assertEqual(job['config'], self.plan(procedure)['jobs'][0]['config'])
                    self.assertEqual(plan['estimated_total_shots'], 1800)

    def test_cpmg_pulse_count_changes_circuit_and_delay_segments_without_new_rows(self):
        low, high = self.plan('sq_cpmg', pi_pulses=2), self.plan('sq_cpmg', pi_pulses=8)
        for plan, count in ((low, 2), (high, 8)):
            job = plan['jobs'][0]
            gates = job['spec']['circuit_variants']['circuits']['main']['sequences']
            self.assertEqual(sum(gate['gate'] == 'Y' for gate in gates), count)
            self.assertEqual(job['total_delay_multiplier'], 2 * count)
            self.assertAlmostEqual(job['spec']['sweeps'][0]['values'][-1], 4e-6 / (2 * count))
            self.assertEqual(plan['estimated_total_shots'], 1800)
        for procedure in ('sq_cpmg', 'sq_t1'):
            self.assertEqual(self.plan(procedure, rotation_hz=0)['jobs'][0]['spec'],
                self.plan(procedure, rotation_hz=1e6)['jobs'][0]['spec'])

    def test_allxy_rb_points_endpoints_and_rotation_do_not_change_native_grids(self):
        for procedure in ('sq_allxy', 'sq_rb'):
            baseline = self.plan(procedure)
            changed = self.plan(procedure, points=17, start_s=1e-6, stop_s=6e-6,
                amp_start=.2, amp_stop=.8, span_hz=2e6, rotation_hz=-1e6, pi_pulses=8)
            for before, after in zip(baseline['jobs'], changed['jobs']):
                self.assertEqual(before['spec'], after['spec'])
                self.assertEqual(before['config'], after['config'])
            self.assertEqual(baseline['expectedJobRows'], changed['expectedJobRows'])
            self.assertEqual(baseline['estimated_total_shots'], changed['estimated_total_shots'])
        self.assertEqual(self.plan('sq_allxy')['expectedJobRows'], [12, 42])
        self.assertEqual(self.plan('sq_rb')['expectedJobRows'], [12, 320, 320, 12])

    def test_sampling_changes_main_jobs_but_preserves_fixed_reference_repeats(self):
        for procedure, expected_roles in (('sq_allxy', ['independent_reference']), ('sq_rb', ['pre', 'post'])):
            contract = self.build(procedure)['samplingSemantics']
            self.assertEqual([ref['role'] for ref in contract['fixedReferences']], expected_roles)
            baseline = self.plan(procedure)
            for edit in ({'capture_repeats': 3}, {'acquisitions_per_point': 2}, {'shots': 200}):
                changed = self.plan(procedure, **edit)
                self.assertGreater(changed['estimated_total_shots'], baseline['estimated_total_shots'])
                expected_total = 0
                for job, rows in zip(changed['jobs'], changed['expectedJobRows']):
                    if job.get('rb_role') in expected_roles:
                        self.assertEqual((job['captures'], job['acquisitions_per_point'], rows), (6, 2, 12))
                    else:
                        self.assertEqual(job['captures'], edit.get('capture_repeats', 2))
                        self.assertEqual(job['acquisitions_per_point'], edit.get('acquisitions_per_point', 1))
                    self.assertEqual(job['spec']['meta']['shots'], edit.get('shots', 100))
                    expected_total += rows * job['spec']['meta']['shots'] * job['acquisitions_per_point']
                self.assertEqual(changed['estimated_total_shots'], expected_total)
                if 'shots' in edit:
                    self.assertEqual(changed['estimated_total_shots'], 2 * baseline['estimated_total_shots'])

    def test_sequence_total_window_is_final_plan_constraint_not_helper_permission(self):
        from adapters.f8 import AdapterError
        for procedure in ('sq_t1', 'sq_ramsey', 'sq_echo', 'sq_cpmg'):
            contract = self.build(procedure)
            self.assertTrue(any(item['kind'] == 'native_sequence_window' for item in contract['conditions']))
            self.assertEqual(contract['parameterBounds']['stop_s']['max'], 79.7e-6)
            with self.assertRaises(AdapterError) as failure:
                self.plan(procedure, stop_s=79.7e-6)
            self.assertEqual(failure.exception.code, 'SEQUENCE_WINDOW')
        # A joint decrease in pulse count can permit a longer CPMG window; the
        # helper must not permanently cap it using the old pulse count.
        contract = self.build('sq_cpmg', {'pi_pulses': 16, 'stop_s': 70e-6})
        self.assertGreaterEqual(contract['parameterBounds']['stop_s']['max'], 78e-6)
        self.assertGreater(self.plan('sq_cpmg', pi_pulses=1, stop_s=78e-6)['totalRows'], 0)
        with self.assertRaises(AdapterError):
            self.plan('sq_cpmg', pi_pulses=16, stop_s=78e-6)


if __name__ == '__main__':
    unittest.main()
