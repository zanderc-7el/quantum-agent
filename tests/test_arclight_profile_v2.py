"""Offline planning contracts for a separate, inactive v2 profile. No device I/O."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from adapters.arclight import ArcLightAdapter, AdapterError
from backend.model_parameter_semantics import build_model_parameter_contract, METHOD_KNOBS, SUPPORTED_IDENTITIES

ROOT = Path(__file__).resolve().parents[1]


def profile(version):
    name = 'arclight_profile.json' if version == 1 else 'arclight_profile_v2.json'
    return json.loads((ROOT / 'adapters' / name).read_text(encoding='utf8'))


def changed_paths(left, right, prefix=''):
    if isinstance(left, dict) and isinstance(right, dict):
        return [p for key in set(left) | set(right) for p in changed_paths(left.get(key), right.get(key), prefix + '/' + key)]
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [p for index, (a, b) in enumerate(zip(left, right)) for p in changed_paths(a, b, prefix + '/' + str(index))]
    return [] if left == right else [prefix]


class ArcLightV2ProfileTests(unittest.TestCase):
    def setUp(self):
        self.v1, self.v2 = profile(1), profile(2)
        self.a1, self.a2 = ArcLightAdapter(ROOT, self.v1), ArcLightAdapter(ROOT, self.v2)

    def plan(self, adapter, parameters):
        return adapter.build_plan({'operationId': 'OFFLINE-v2-planning-only', 'procedureId': 'ro_power_shift',
                                   'resourceId': 'Q11', 'parameters': parameters})

    def test_profile_diff_exactly_id_and_block_upper_bound(self):
        self.assertEqual(set(changed_paths(self.v1, self.v2)), {'/id', '/parameterBounds/acquisitions_per_point/1'})
        self.assertEqual(self.v2['id'], 'arclight-private-f8-q11-v2')
        self.assertEqual(self.v2['parameterBounds']['acquisitions_per_point'], [1, 8])
        self.assertEqual(self.v1['parameterBounds']['acquisitions_per_point'], [1, 4])
        self.assertFalse(self.v2['enabled'])

    def test_3_by_6_preserves_original_frequency_window_and_physical_sequence(self):
        shared = {'capture_repeats': 3, 'points': 61, 'span_hz': 4e6, 'shots': 2000}
        baseline = self.plan(self.a1, {**shared, 'acquisitions_per_point': 4})
        proposed = self.plan(self.a2, {**shared, 'acquisitions_per_point': 6})
        self.assertEqual(proposed['estimated_total_shots'], 10_980_000)
        self.assertEqual(proposed['totalRows'], 915)
        self.assertEqual(proposed['max_seconds'], 7200)
        old, new = baseline['jobs'][0], proposed['jobs'][0]
        self.assertEqual(set(changed_paths(old, new)), {'/acquisitions_per_point', '/parameters/acquisitions_per_point'})
        self.assertEqual(new['spec']['meta']['shots'], 2000)
        self.assertEqual(new['captures'], 3)
        self.assertEqual(new['acquisitions_per_point'], 6)
        self.assertEqual(new['spec']['sweeps'][1]['values'], [.1, .2, .4, .6, .8])
        frequencies = new['spec']['sweeps'][0]['values']
        self.assertEqual(len(frequencies), 61)
        self.assertAlmostEqual(frequencies[-1] - frequencies[0], 4e6)
        self.assertAlmostEqual(frequencies[1] - frequencies[0], 4e6 / 60, places=5)

    def test_4_by_6_narrow_window_keeps_frequency_step_and_budget(self):
        plan = self.plan(self.a2, {'capture_repeats': 4, 'acquisitions_per_point': 6,
                                   'points': 49, 'span_hz': 3.2e6, 'shots': 2000})
        self.assertEqual(plan['estimated_total_shots'], 11_760_000)
        self.assertEqual(plan['totalRows'], 980)
        job = plan['jobs'][0]
        self.assertEqual(job['spec']['meta']['shots'], 2000)
        self.assertEqual(job['captures'], 4)
        self.assertEqual(job['config'], self.plan(self.a1, {})['jobs'][0]['config'])
        self.assertEqual(job['spec']['sweeps'][1]['values'], [.1, .2, .4, .6, .8])
        frequencies = job['spec']['sweeps'][0]['values']
        self.assertEqual(len(frequencies), 49)
        self.assertAlmostEqual(frequencies[-1] - frequencies[0], 3.2e6)
        self.assertAlmostEqual(frequencies[1] - frequencies[0], 4e6 / 60, places=5)

    def test_existing_default_sampling_and_runtime_binding_unchanged(self):
        self.assertEqual(self.plan(self.a1, {}), self.plan(self.a2, {}))
        plan = self.plan(self.a2, {})
        self.assertEqual(plan['parameters']['capture_repeats'], 2)
        self.assertEqual(plan['parameters']['acquisitions_per_point'], 1)
        self.assertEqual(plan['parameters']['shots'], 2000)
        for key in ('runtimeRoot', 'runner', 'runnerSha256', 'sourceManifest', 'python', 'visaLibrary',
                    'instrument', 'hardwareLocks', 'resources', 'maximumRunSeconds', 'maximumTotalShots', 'maximumPoints'):
            self.assertEqual(self.v1[key], self.v2[key], key)
        self.assertEqual(hashlib.sha256((ROOT / self.v2['runner']).read_bytes()).hexdigest(), self.v2['runnerSha256'])

    def test_caps_integer_block_limit_and_budget_still_reject(self):
        cases = [(self.a1, {'acquisitions_per_point': 6}, 'PARAMETER_OUT_OF_RANGE'),
                 (self.a2, {'acquisitions_per_point': 9}, 'PARAMETER_OUT_OF_RANGE'),
                 (self.a2, {'acquisitions_per_point': 6.5}, 'INVALID_PARAMETER'),
                 (self.a2, {'shots': 2048}, 'PARAMETER_OUT_OF_RANGE'),
                 (self.a2, {'capture_repeats': 4, 'acquisitions_per_point': 8}, 'BUDGET_EXCEEDED')]
        for adapter, parameters, code in cases:
            with self.subTest(parameters=parameters, profile=adapter.profile['id']):
                with self.assertRaises(AdapterError) as error:
                    self.plan(adapter, parameters)
                self.assertEqual(error.exception.code, code)

    def test_explicit_v2_identity_has_same_method_constraints_as_v1(self):
        self.assertEqual(SUPPORTED_IDENTITIES, frozenset(
            ('adapters.arclight.ArcLightAdapter', 'arclight-private-f8-q11-v' + str(version))
            for version in (1, 2, 3)))
        for method in METHOD_KNOBS:
            with self.subTest(method=method):
                common = {'implementation_id': 'adapters.arclight.ArcLightAdapter', 'procedure_id': method,
                          'effective_parameters': {}}
                a = build_model_parameter_contract(**common, profile_id=self.v1['id'], parameter_bounds=self.a1.parameter_schema(method))
                b = build_model_parameter_contract(**common, profile_id=self.v2['id'], parameter_bounds=self.a2.parameter_schema(method))
                self.assertTrue(a['applied']); self.assertTrue(b['applied'])
                self.assertEqual(b['parameterBounds']['acquisitions_per_point']['max'], 8)
                b['profileId'] = a['profileId']
                b['parameterBounds']['acquisitions_per_point']['max'] = 4
                self.assertEqual(a, b)
        for implementation, identity in [('adapters.f8.F8Adapter', self.v2['id']),
                                         ('adapters.arclight.ArcLightAdapter', self.v2['id'] + '-foreign')]:
            result = build_model_parameter_contract(implementation_id=implementation, profile_id=identity,
                procedure_id='sq_spectroscopy', parameter_bounds=self.a2.parameter_schema('sq_spectroscopy'), effective_parameters={})
            self.assertFalse(result['applied'])


if __name__ == '__main__':
    unittest.main()
