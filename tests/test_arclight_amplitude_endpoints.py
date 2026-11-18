"""Offline regression for a real, valid 161-point proposal rejected at one ULP."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
from adapters.arclight_runtime.platform_specs import build_job, linspace
from adapters.f8 import AdapterError

ROOT = Path(__file__).resolve().parents[1]


class AmplitudeEndpointTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ArcLightAdapter(ROOT, ROOT / 'adapters/arclight_profile_v2.json')

    def plan(self, changes=None):
        request = {'operationId': 'amplitude-endpoint-default', 'resourceId': 'Q11',
                   'procedureId': 'sq_rabi_amplitude', 'parameters': changes or {}}
        with patch.object(self.adapter, 'execute', side_effect=AssertionError('No hardware execution')):
            return self.adapter.build_plan(request)

    def test_actual_rejected_model_proposal_has_exact_endpoint_and_unchanged_interior(self):
        changes = {'amp_start': .01, 'amp_stop': .99, 'points': 161,
                   'capture_repeats': 4, 'acquisitions_per_point': 2, 'shots': 2000}
        before = deepcopy(changes)
        generated = linspace(.01, .99, 161)
        self.assertEqual(generated[-1], math.nextafter(.99, math.inf))
        plan = self.plan(changes)
        axis = plan['jobs'][0]['spec']['sweeps'][0]
        self.assertEqual(axis['values'][0], .01)
        self.assertEqual(axis['values'][-1], .99)
        self.assertEqual(axis['values'][1:-1], generated[1:-1])
        self.assertTrue(all(.01 <= x <= .99 for x in axis['values']))
        self.assertEqual(plan['totalRows'], 644)
        self.assertEqual(plan['estimated_total_shots'], 2576000)
        self.assertEqual(plan['jobs'][0]['gate_repeats'], 5)
        self.assertEqual(plan['parameters']['drive_length_s'], 320e-9)
        self.assertEqual(plan['parameters']['readout_hz'], 7009510000.)
        self.assertEqual(changes, before)
        self.assertEqual(plan['derivedAxisEndpointCorrections'], [{
            'binding': 'cfg.Q1.gate.U_gates.pi_amp', 'pointIndex': 160,
            'sourceParameter': 'amp_stop', 'computedValue': generated[-1], 'actualValue': .99,
            'reason': 'generated_uniform_axis_endpoint_roundoff'}])

    def test_truly_out_of_range_request_endpoints_remain_rejected(self):
        for changes in ({'amp_stop': math.nextafter(.99, math.inf)}, {'amp_stop': 1.0},
                        {'amp_start': 0.0, 'amp_stop': .99, 'points': 161}):
            with self.subTest(changes=changes), self.assertRaises(AdapterError) as failure:
                self.plan(changes)
            self.assertEqual(failure.exception.code, 'PARAMETER_OUT_OF_RANGE')

    def test_explicit_axis_is_never_corrected_even_by_one_ulp(self):
        axis = linspace(.01, .99, 161)
        before = deepcopy(axis)
        with self.assertRaises(AdapterError) as failure:
            self.plan({'amplitudes': axis})
        self.assertEqual(failure.exception.code, 'PARAMETER_OUT_OF_RANGE')
        self.assertEqual(axis, before)
        axis[-1] = .99
        plan = self.plan({'amplitudes': axis})
        self.assertEqual(plan['jobs'][0]['spec']['sweeps'][0]['values'], axis)
        self.assertNotIn('derivedAxisEndpointCorrections', plan)

    def test_invalid_generated_interior_is_not_clipped(self):
        def invalid_job(*args):
            job = build_job(*args)
            job['spec']['sweeps'][0]['values'][80] = math.nextafter(.99, math.inf)
            return job
        with patch('adapters.arclight.build_job', side_effect=invalid_job):
            with self.assertRaises(AdapterError) as failure:
                self.plan({'amp_start': .01, 'amp_stop': .99, 'points': 161})
        self.assertEqual(failure.exception.details['pointIndex'], 80)

    def test_large_generated_endpoint_error_is_not_clipped(self):
        def invalid_job(*args):
            job = build_job(*args)
            job['spec']['sweeps'][0]['values'][-1] = 1.0
            return job
        with patch('adapters.arclight.build_job', side_effect=invalid_job):
            with self.assertRaises(AdapterError) as failure:
                self.plan({'amp_start': .01, 'amp_stop': .99, 'points': 161})
        self.assertEqual(failure.exception.details['pointIndex'], 160)

    def test_existing_default_plan_is_byte_identical(self):
        plan = self.plan()
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(digest, 'edcc5132a405e73060a57130f43cda82d0f8a1ded38a9d9d93552a11f0889f49')


if __name__ == '__main__':
    unittest.main()
