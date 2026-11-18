"""Offline bounded model-view tests; no model/provider/device requests."""
from copy import deepcopy
import io
import json
import math
import subprocess
import unittest
import urllib.error
from unittest.mock import patch

from backend.common import ROOT, dumps, digest
from backend.controller import Controller
from backend.model_input_projection import project_model_input, MAX_INPUT_UNITS
from backend.prior_failure_context import javascript_json_units_upper_bound


def payload(large=True):
    count = 1932 if large else 8
    stats = {k: [math.sqrt(2) * (i + 1) / (count + n + 1) * 1e-5 for i in range(count)]
             for n, k in enumerate(('effective_sem', 'shot_iid_sem', 'acquisition_block_sem'))}
    stats.update(point_order='Original randomized acquisition order, not sorted by frequency.', acquisitions_per_point=2)
    size = 161 if large else 8
    traces = [{'name': 'capture ' + str(j), 'x': [4.86e9 + i * 25000 for i in range(size)],
               'y': [math.sin(i / 7 + j) * 1e-4 for i in range(size)], 'xUnit': 'Hz'} for j in range(12 if large else 1)]
    result = {'dataKind': 'real', 'metrics': {'peak_snr': 2.75}, 'quality': {'status': 'failed', 'criteria': {'peak_snr': {'min': 5}}},
        'analysisQuality': {'accepted': False, 'reasons': ['native repeat failure'], 'raw_sha256': 'a' * 64,
                            'point_statistics': stats}, 'artifacts': [{'id': 'raw-current', 'sha256': 'a' * 64}],
        'provenance': {'freshAcquisition': True}, 'series': traces}
    return {'runId': 'offline', 'nodeId': 'Q11:s02', 'revision': 1, 'mode': 'real', 'goal': 'OFFLINE test',
        'observations': {'result': result}, 'allowedActions': ['retry', 'wait'], 'parameterBounds': {'shots': {'min': 100, 'max': 2047}},
        'allowedEvidenceIds': ['raw-current'], 'rules': [{'ruleVersion': 'frozen'}], 'history': []}


def js_validation(value):
    script = "import fs from 'node:fs';import {validateDecisionInput} from './agent/src/validation.mjs';const x=JSON.parse(fs.readFileSync(0,'utf8'));let error=null;try{validateDecisionInput(x)}catch(e){error=e.message};console.log(JSON.stringify({error,units:JSON.stringify(x).length}));"
    return json.loads(subprocess.run(['node', '--input-type=module', '-e', script], input=dumps(value),
        encoding='utf8', capture_output=True, check=True, timeout=15, cwd=ROOT).stdout)


class ModelInputProjectionTests(unittest.TestCase):
    def test_small_inputs_preserved_exactly_without_projection_metadata(self):
        original = payload(False); output = project_model_input(original)
        self.assertEqual(original, output); self.assertIsNot(output, original)
        self.assertNotIn('modelInputProjection', output)
        self.assertIsNone(js_validation(output)['error'])

    def test_large_arrays_are_partial_original_values_without_evidence_or_gate_change(self):
        original = payload(); original['observations']['result']['series'] *= 3
        original_hash = digest(original); output = project_model_input(original)
        self.assertEqual(digest(original), original_hash)
        self.assertLessEqual(javascript_json_units_upper_bound(output), MAX_INPUT_UNITS)
        self.assertIsNone(js_validation(output)['error'])
        for key in ('runId', 'nodeId', 'revision', 'allowedEvidenceIds', 'rules', 'parameterBounds', 'history'):
            self.assertEqual(original[key], output[key])
        src, dst = original['observations']['result'], output['observations']['result']
        for key in ('dataKind', 'metrics', 'quality', 'artifacts', 'provenance'):
            self.assertEqual(src[key], dst[key])
        for key in ('accepted', 'reasons', 'raw_sha256'):
            self.assertEqual(src['analysisQuality'][key], dst['analysisQuality'][key])
        self.assertNotIn('partial', dst)
        for index, trace in enumerate(dst['series']):
            info = trace['modelArrayProjection']; ids = info['originalIndices']; source = src['series'][index]
            self.assertEqual(info['originalPointCount'], 161); self.assertEqual((ids[0], ids[-1]), (0, 160))
            self.assertEqual(trace['x'], [source['x'][i] for i in ids]); self.assertEqual(trace['y'], [source['y'][i] for i in ids])
            minimum = min(range(161), key=source['y'].__getitem__)
            self.assertEqual(info['globalMinimum'], {'index': minimum, 'x': source['x'][minimum], 'y': source['y'][minimum]})
        stats = dst['analysisQuality']['point_statistics']; old = src['analysisQuality']['point_statistics']
        self.assertIn('NOT the sorted series order', stats['modelArrayProjection']['order'])
        for key, info in stats['modelArrayProjection']['arrays'].items():
            self.assertEqual(info['originalPointCount'], 1932)
            self.assertEqual(stats[key], [old[key][i] for i in info['originalIndices']])
            self.assertEqual(info['originalIndices'][-1], 1931)

    def test_selection_result_and_uncertainty_use_their_own_indices(self):
        original = payload(); selection = deepcopy(payload(False)['observations']['result'])
        original['observations']['result']['series'] *= 3
        selection['series'][0] = {'x': list(range(100)), 'y': [100 - i for i in range(100)], 'sem': [i / 100 for i in range(100)]}
        original['observations']['result']['selectionResult'] = selection
        output = project_model_input(original)
        sampled = output['observations']['result']['selectionResult']['series'][0]
        ids = sampled['modelArrayProjection']['originalIndices']
        self.assertEqual(sampled['x'], ids); self.assertEqual(sampled['sem'], [i / 100 for i in ids])
        self.assertEqual(sampled['modelArrayProjection']['originalPointCount'], 100)
        self.assertEqual(selection['series'][0]['x'], list(range(100)))

    def test_unknown_arrays_and_misaligned_series_are_not_silently_changed(self):
        original = payload(); original['observations']['result']['analysisQuality']['unknown'] = [1] * 100
        original['observations']['result']['series'][0]['sem'] = [1]
        output = project_model_input(original)
        self.assertEqual(output['observations']['result']['analysisQuality']['unknown'], [1] * 100)
        self.assertEqual(output['observations']['result']['series'][0], original['observations']['result']['series'][0])

    def test_protected_oversize_is_rejected_before_http_and_does_not_claim_account_failure(self):
        original = payload(False); original['rules'] = [{'unchanged': 'x' * 210000}]
        output = project_model_input(original); self.assertEqual(output['rules'], original['rules'])
        c = Controller.__new__(Controller)
        with patch('backend.controller.urllib.request.build_opener', side_effect=AssertionError('No request allowed')):
            response = c._brain('/decide', output)
        self.assertEqual(response['error']['code'], 'context_budget_exceeded'); self.assertNotIn('configured', response)

    def test_http_known_validation_error_is_structured_and_unknown_body_not_exposed(self):
        c = Controller.__new__(Controller); c.brain_url = 'http://127.0.0.1:1'
        for message, expected in [('input_too_large', 'input_too_large'), ('input_too_deep', 'input_too_deep'),
                                  ('SECRET_TEST_TOKEN_DO_NOT_ECHO', None)]:
            error = urllib.error.HTTPError('http://127.0.0.1:1/decide', 400, 'Bad Request', {},
                io.BytesIO(dumps({'error': {'code': 'invalid_request', 'message': message}}).encode()))
            with patch('backend.controller.urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = error
                response = c._brain('/decide', payload(False))
            if expected:
                self.assertEqual(response['error']['code'], expected); self.assertNotIn('configured', response)
            self.assertNotIn('SECRET_TEST_TOKEN', dumps(response))

    def test_input_sampling_cannot_claim_full_curves_in_navigation(self):
        original = payload(); original['observations']['curveNavigation'] = {'notice': '原曲线仍在 result.series 中，原生质量与控制器验收不变。'}
        original['observations']['result']['series'] *= 3
        result = project_model_input(original)
        self.assertNotIn('原曲线仍在 result.series 中', result['observations']['curveNavigation']['notice'])
        self.assertIn('原曲线仍在 result.series 中', original['observations']['curveNavigation']['notice'])

    def test_statistics_only_is_preferred_and_full_curves_remain_available(self):
        original = payload(); output = project_model_input(original)
        self.assertEqual(output['modelInputProjection']['projectionStage'], 'statistics_only')
        self.assertEqual(output['observations']['result']['series'], original['observations']['result']['series'])
        self.assertIsNone(js_validation(output)['error'])


if __name__ == '__main__':
    unittest.main()
