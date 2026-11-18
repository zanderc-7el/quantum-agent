"""Synthetic in-memory contract fixtures only; no measurements or file/device I/O."""
from copy import deepcopy
import math
import unittest

from backend.supplemental_quality import (
    POLICY_VERSION, SUPPORTED_PROCEDURES, evaluate_supplemental_quality, hash_payload, policy_snapshot,
)


GATES = {
    'sq_allxy': {'allxy_rms': {'min': 0, 'max': .2}},
    **{p: {'r_squared': {'min': .8}, 'relative_stderr': {'max': .3}}
       for p in ('sq_ramsey', 'sq_echo', 'sq_cpmg')},
}


def fit(decay, model='damped_sinusoid', error_fraction=.01):
    sigma = decay*error_fraction
    if model == 'exponential':
        value, stderr, parameter = decay, sigma, 'decay_time_in_x_units'
    else:
        value, stderr, parameter = 8e5, 1e3, 'frequency_per_x_unit'
    return {'model': model, 'parameter': parameter, 'value': value,
            'standard_error_model': stderr, 'ci95_model': [value-1.96*stderr, value+1.96*stderr],
            'decay_time': decay, 'decay_time_ci95_model': [decay-1.96*sigma, decay+1.96*sigma]}


def t2_report(procedure, values, raw, model='damped_sinusoid'):
    return {'metrics': {'r_squared': .95, 'relative_stderr': .01, 't2_s': sum(values)/len(values)},
            'analysis': {'procedureId': procedure, 'accepted': True, 'raw_sha256': raw,
                         'source_sha256': 'a'*64, 'x_axis': {'unit': 's'},
                         'groups': [{'group_axes': {}, 'repeat_validated': True,
                                     'repeats': [{'repeat': index+1, 'fit': fit(value, model),
                                                  'diagnostics': {'rejection_reasons': []}}
                                                 for index, value in enumerate(values)]}]}}


def allxy_report(offsets, raw):
    points, rms = [], {}
    for capture, offset in enumerate(offsets):
        for index in range(21):
            ideal = [0., .5, 1.][index % 3]
            points.append({'capture': capture, 'native_index': index, 'ideal_p1': ideal,
                           'prepared_reference_iq_mean_unclipped': ideal+offset})
        rms[str(capture)] = math.sqrt(sum((p['prepared_reference_iq_mean_unclipped']-p['ideal_p1'])**2
                                        for p in points if p['capture'] == capture)/21)
    merged = abs(sum(offsets)/len(offsets))
    return {'metrics': {'allxy_rms': merged},
            'analysis': {'procedureId': 'sq_allxy', 'accepted': True, 'raw_sha256': raw,
                         'source_sha256': 'a'*64, 'captures': list(range(len(offsets))), 'points': points,
                         'rms_by_capture': rms, 'rms_deviation_from_ideal': merged,
                         'independent_reference': {'independent_reference': True, 'source': 'fixture-only'},
                         'normalization': {'allxy_used_to_train_axis_or_scale': False}}}


def arguments(procedure='sq_ramsey'):
    policy = policy_snapshot()
    reports = {role: allxy_report([.05, -.04], raw) if procedure == 'sq_allxy'
               else t2_report(procedure, [1e-6, 1.02e-6], raw,
                              'exponential' if procedure == 'sq_cpmg' else 'damped_sinusoid')
               for role, raw in [('selection', '1'*64), ('verification', '2'*64)]}
    evidence = {role: {'operationId': 'offline-' + role, 'rawSha256s': [raw],
                       'analysisArtifactSha256': digest, 'calibrationContextSha256': 'c'*64}
                for role, raw, digest in [('selection', '1'*64, '3'*64), ('verification', '2'*64, '4'*64)]}
    return {'reports': reports, 'policy': policy, 'base_gates': deepcopy(GATES), 'evidence': evidence,
            'context': {'runId': 'offline-run', 'resourceId': 'Q11', 'baseGatesVersion': 'diagnostic-gates/1',
                        'baseGatesSha256': hash_payload(GATES), 'sourceManifestSha256': '5'*64,
                        'moduleSha256': '6'*64, 'policySha256': hash_payload(policy)}}


class SupplementalQualityTests(unittest.TestCase):
    def evaluate(self, procedure='sq_ramsey', stage='cross', args=None):
        return evaluate_supplemental_quality(procedure, stage, **(args or arguments(procedure)))

    def test_snapshot_is_fresh_versioned_and_limited_to_requested_scope(self):
        self.assertEqual(POLICY_VERSION, 'capture-consistency/1')
        self.assertEqual(set(SUPPORTED_PROCEDURES), {'sq_allxy', 'sq_ramsey', 'sq_echo', 'sq_cpmg'})
        changed = policy_snapshot(); changed['t2']['nativeRepeatSigmaMultiplier'] = 100
        self.assertEqual(policy_snapshot()['t2']['nativeRepeatSigmaMultiplier'], 3)

    def test_all_stages_pass_good_capture_data_without_mutating_inputs(self):
        for procedure in SUPPORTED_PROCEDURES:
            for stage in ('selection', 'verification', 'cross'):
                with self.subTest(procedure=procedure, stage=stage):
                    args = arguments(procedure); before = deepcopy(args)
                    result = self.evaluate(procedure, stage, args)
                    self.assertEqual(result['status'], 'passed', result['reasons'])
                    self.assertTrue(result['accepted']); self.assertEqual(args, before)
                    self.assertEqual(result['acquisitionClaim'], 'derived_validation_only')

    def test_selection_does_not_require_a_future_verification(self):
        args = arguments(); args['reports'].pop('verification'); args['evidence'].pop('verification')
        result = self.evaluate(stage='selection', args=args)
        self.assertTrue(result['accepted'], result['reasons'])
        self.assertTrue(result['requiresIndependentAcquisition'])
        self.assertEqual(self.evaluate(stage='cross', args=args)['status'], 'unverified')

    def test_verification_stage_does_not_require_selection_until_cross_stage(self):
        args = arguments(); args['reports'].pop('selection'); args['evidence'].pop('selection')
        self.assertTrue(self.evaluate(stage='verification', args=args)['accepted'])
        self.assertFalse(self.evaluate(stage='cross', args=args)['accepted'])

    def test_allxy_opposite_bad_captures_cannot_hide_in_a_good_mean(self):
        args = arguments('sq_allxy')
        args['reports']['selection'] = allxy_report([.3, -.3], '1'*64)
        self.assertEqual(args['reports']['selection']['metrics']['allxy_rms'], 0)
        result = self.evaluate('sq_allxy', 'selection', args)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len([c for c in result['checks'] if c['id'].endswith('.rms') and c['status'] == 'failed']), 2)

    def test_allxy_each_capture_uses_unchanged_original_threshold(self):
        args = arguments('sq_allxy'); args['reports']['selection'] = allxy_report([.2, -.2], '1'*64)
        self.assertTrue(self.evaluate('sq_allxy', 'selection', args)['accepted'])
        args['reports']['selection'] = allxy_report([.2001, -.2001], '1'*64)
        self.assertFalse(self.evaluate('sq_allxy', 'selection', args)['accepted'])

    def test_missing_duplicate_or_falsely_reported_allxy_capture_is_not_accepted(self):
        for damage in ('missing_rms', 'missing_point', 'duplicate_point', 'wrong_rms', 'refit_reference'):
            args = arguments('sq_allxy'); analysis = args['reports']['selection']['analysis']
            if damage == 'missing_rms': analysis.pop('rms_by_capture')
            if damage == 'missing_point': analysis['points'].pop()
            if damage == 'duplicate_point': analysis['points'][0] = deepcopy(analysis['points'][1])
            if damage == 'wrong_rms': analysis['rms_by_capture']['0'] = 0
            if damage == 'refit_reference': analysis['normalization']['allxy_used_to_train_axis_or_scale'] = True
            with self.subTest(damage=damage): self.assertFalse(self.evaluate('sq_allxy', 'selection', args)['accepted'])

    def test_t2_same_oscillation_frequency_does_not_validate_different_decays(self):
        args = arguments(); args['reports']['selection'] = t2_report('sq_ramsey', [1e-6, 8e-6], '1'*64)
        group = args['reports']['selection']['analysis']['groups'][0]
        self.assertTrue(group['repeat_validated'])
        self.assertEqual({r['fit']['value'] for r in group['repeats']}, {8e5})
        result = self.evaluate(stage='selection', args=args)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(any('一致性' in r for r in result['reasons']))

    def test_t2_individually_stable_operations_are_compared_across_operations(self):
        args = arguments(); args['reports']['verification'] = t2_report('sq_ramsey', [2e-6, 2.02e-6], '2'*64)
        self.assertTrue(self.evaluate(stage='selection', args=args)['accepted'])
        self.assertTrue(self.evaluate(stage='verification', args=args)['accepted'])
        result = self.evaluate(stage='cross', args=args)
        self.assertFalse(result['accepted'])
        self.assertTrue(any(c['id'].startswith('t2.cross.') and c['status'] == 'failed' for c in result['checks']))

    def test_unresolved_missing_or_overwide_t2_ci_is_not_accepted(self):
        for damage in ('missing_decay_ci', 'unresolved_model', 'nonfinite', 'wide_decay_ci', 'wrong_units', 'missing_native_fit'):
            args = arguments(); analysis = args['reports']['selection']['analysis']; repeat = analysis['groups'][0]['repeats'][0]
            if damage == 'missing_decay_ci': repeat['fit'].pop('decay_time_ci95_model')
            if damage == 'unresolved_model': repeat['fit']['model'] = 'sinusoid'
            if damage == 'nonfinite': repeat['fit']['decay_time'] = float('nan')
            if damage == 'wide_decay_ci': repeat['fit'] = fit(1e-6, error_fraction=.28)
            if damage == 'wrong_units': analysis['x_axis']['unit'] = 'Hz'
            if damage == 'missing_native_fit': repeat['diagnostics'] = {}
            with self.subTest(damage=damage): self.assertFalse(self.evaluate(stage='selection', args=args)['accepted'])

    def test_original_base_gate_failure_is_not_overridden(self):
        args = arguments(); args['reports']['selection']['metrics']['r_squared'] = .7
        self.assertEqual(self.evaluate(stage='selection', args=args)['status'], 'failed')

    def test_missing_evidence_and_frozen_binding_mismatch_are_not_accepted(self):
        for damage in ('missing_raws', 'missing_artifact', 'wrong_raw', 'wrong_policy', 'wrong_gates', 'missing_manifest'):
            args = arguments()
            if damage == 'missing_raws': args['evidence']['selection'].pop('rawSha256s')
            if damage == 'missing_artifact': args['evidence']['selection'].pop('analysisArtifactSha256')
            if damage == 'wrong_raw': args['reports']['selection']['analysis']['raw_sha256'] = 'f'*64
            if damage == 'wrong_policy': args['policy']['t2']['nativeRepeatRelativeFloor'] = 10
            if damage == 'wrong_gates': args['base_gates']['sq_ramsey']['r_squared']['min'] = 0
            if damage == 'missing_manifest': args['context'].pop('sourceManifestSha256')
            with self.subTest(damage=damage): self.assertFalse(self.evaluate(stage='selection', args=args)['accepted'])

    def test_same_operation_reused_raw_or_changed_context_cannot_be_independent(self):
        for damage in ('operation', 'raw', 'context'):
            args = arguments()
            if damage == 'operation': args['evidence']['verification']['operationId'] = args['evidence']['selection']['operationId']
            if damage == 'raw': args['evidence']['verification']['rawSha256s'].append('1'*64)
            if damage == 'context': args['evidence']['verification']['calibrationContextSha256'] = 'd'*64
            with self.subTest(damage=damage): self.assertEqual(self.evaluate(args=args)['status'], 'failed')

    def test_evidence_hash_changes_when_input_analysis_changes(self):
        args = arguments(); before = self.evaluate(stage='selection', args=args)
        args['reports']['selection']['analysis']['review_fixture'] = 'changed'
        after = self.evaluate(stage='selection', args=args)
        self.assertNotEqual(before['evidenceHash'], after['evidenceHash'])
        self.assertEqual(before['policyHash'], after['policyHash'])


if __name__ == '__main__':
    unittest.main()
