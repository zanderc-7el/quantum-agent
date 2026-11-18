"""Offline bounded curve navigation; keeps actual evidence and gates untouched."""
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil
import subprocess
import unittest

from backend.curve_context import (attach_curve_summary, curve_summary, MAX_SAMPLES,
                                   MAX_TRACES, MAX_SUMMARY_BYTES, MAX_INPUT_UNITS)
from backend.decision_context import build_decision_context
from backend.prior_failure_context import javascript_json_units_upper_bound


def trace(count=161, phase=0):
    x = [.02 + .96 * i / (count - 1) for i in range(count)]
    y = [.0007 + .0002 * math.cos(5 * math.pi * i / (count - 1) + phase) for i in range(count)]
    return {'name': 'raw trace', 'x': x, 'y': y, 'xUnit': 'a.u.', 'yUnit': 'IQ a.u.', 'fit': False}


def payload():
    return {'runId': 'offline-run', 'nodeId': 'offline-node', 'revision': 3, 'mode': 'real',
        'goal': 'Offline projection', 'allowedActions': ['wait'], 'parameterBounds': {}, 'rules': [],
        'observations': {'result': {'series': [trace(), trace(phase=.03)],
            'analysisQuality': {'accepted': False, 'reasons': ['Fitted oscillation contains fewer than 1.5 cycles.']},
            'metrics': {}, 'quality': {'status': 'failed'},
            'artifacts': [{'id': 'real-current', 'kind': 'raw', 'name': 'raw_scan.npz'}]}}, 'history': []}


class CurveContextTests(unittest.TestCase):
    def test_full_range_samples_are_original_aligned_pairs_and_extrema_use_all_points(self):
        item = trace()
        item['y'][3], item['y'][157] = -2., 3.  # Deliberately absent from uniform samples.
        result = curve_summary({'series': [item]})
        summary = result['traces'][0]
        self.assertEqual(summary['pointCount'], 161)
        self.assertEqual(summary['sampleCount'], MAX_SAMPLES)
        self.assertEqual([p['index'] for p in summary['samples']], list(range(0, 161, 10)))
        self.assertEqual(summary['xRange'], [item['x'][0], item['x'][-1]])
        for p in summary['samples']:
            self.assertEqual((p['x'], p['y']), (item['x'][p['index']], item['y'][p['index']]))
        self.assertEqual(summary['globalMinimum'], {'index': 3, 'x': item['x'][3], 'y': -2.})
        self.assertEqual(summary['globalMaximum'], {'index': 157, 'x': item['x'][157], 'y': 3.})
        self.assertTrue(summary['coverage']['fullTraceRangeCovered'])
        self.assertTrue(summary['coverage']['samplesAreSubset'])
        self.assertNotIn('cycles', summary)

    def test_uneven_axis_uses_paired_indices_and_short_traces_keep_all_points(self):
        item = {'x': [0., .1, .25, .9, 1.2], 'y': [4, 3, 1, 2, 6]}
        summary = curve_summary({'series': [item]})['traces'][0]
        self.assertEqual(summary['samples'], [{'index': i, 'x': x, 'y': item['y'][i]} for i, x in enumerate(item['x'])])
        self.assertFalse(summary['coverage']['samplesAreSubset'])

    def test_bad_pairs_are_explicitly_unavailable_without_invented_coverage(self):
        cases = [(None, 'trace_not_object'), ({'x': [1], 'y': []}, 'paired_array_length_mismatch'),
                 ({'x': [1, 2], 'y': [1, math.nan]}, 'nonfinite_or_nonnumeric_pair'),
                 ({'x': [1, math.inf], 'y': [1, 2]}, 'nonfinite_or_nonnumeric_pair'),
                 ({'x': [1, 1], 'y': [1, 2]}, 'axis_not_strictly_increasing'),
                 ({'x': [2, 1], 'y': [1, 2]}, 'axis_not_strictly_increasing'),
                 ({'x': [], 'y': []}, 'empty_or_point_limit_exceeded')]
        for item, reason in cases:
            with self.subTest(reason=reason):
                summary = curve_summary({'series': [item]})['traces'][0]
                self.assertEqual(summary['status'], 'unavailable')
                self.assertEqual(summary['reason'], reason)
                self.assertNotIn('samples', summary)
                self.assertNotIn('globalMinimum', summary)
                self.assertNotIn('coverage', summary)

    def test_multiple_traces_stages_and_fit_exclusion_do_not_add_evidence_or_change_quality(self):
        original = build_decision_context(payload(), procedure_id='sq_rabi_amplitude')
        original['observations']['result']['series'].append({**trace(), 'fit': True})
        original['observations']['result']['selectionResult'] = {'series': [trace(phase=.5)]}
        before = deepcopy(original)
        output = attach_curve_summary(original, procedure_id='sq_rabi_amplitude')
        summary = output['observations'].pop('curveNavigation')
        self.assertEqual(output, before)
        self.assertEqual(original, before)
        self.assertEqual(summary['fitTracesExcluded'], 1)
        self.assertEqual([(s['resultRole'], s['seriesIndex']) for s in summary['traces']],
                         [('result', 0), ('result', 1), ('selectionResult', 0)])
        self.assertIn('缺失 metrics.cycles 不等于零周期', summary['notice'])
        self.assertIn('不是实测波形没有周期或单调的定论', summary['notice'])
        self.assertNotIn('accepted', summary)

    def test_many_traces_are_bounded_and_omitted_count_is_explicit(self):
        result = curve_summary({'series': [trace() for _ in range(100)]})
        self.assertLessEqual(result['providedTraceCount'], MAX_TRACES)
        self.assertEqual(result['providedTraceCount'] + result['omittedTraceCount'], 100)
        self.assertEqual(result['status'], 'partial')
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True, separators=(',', ':')).encode('ascii')), MAX_SUMMARY_BYTES)
        for item in result['traces']:
            self.assertEqual(item['samples'][-1]['index'], 160)

    def test_input_budget_discards_only_new_summary_and_other_procedures_unchanged(self):
        original = build_decision_context(payload())
        baseline = javascript_json_units_upper_bound(original)
        original['padding'] = 'x' * (MAX_INPUT_UNITS - baseline - 20)
        before = deepcopy(original)
        output = attach_curve_summary(original, procedure_id='sq_rabi_amplitude')
        self.assertEqual(output, before)
        self.assertEqual(original, before)
        self.assertLessEqual(javascript_json_units_upper_bound(output), MAX_INPUT_UNITS)
        self.assertEqual(attach_curve_summary(original, procedure_id='sq_rb'), original)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_actual_agent_validator_retains_current_evidence_and_safe_input_length(self):
        output = attach_curve_summary(build_decision_context(payload()), procedure_id='sq_rabi_amplitude')
        module = (Path(__file__).resolve().parents[1] / 'agent/src/validation.mjs').as_uri()
        script = """import fs from 'node:fs';
const {validateDecisionInput,collectEvidence}=await import(process.argv[1]);
const p=JSON.parse(fs.readFileSync(0,'utf8'));validateDecisionInput(p);
process.stdout.write(JSON.stringify({length:JSON.stringify(p).length,evidence:[...collectEvidence(p)]}));"""
        run = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script, module],
                             input=json.dumps(output), text=True, capture_output=True, timeout=20, check=True)
        received = json.loads(run.stdout)
        self.assertEqual(received['evidence'], ['real-current'])
        self.assertLess(received['length'], 200000)
        self.assertLessEqual(received['length'], javascript_json_units_upper_bound(output))


if __name__ == '__main__':
    unittest.main()
