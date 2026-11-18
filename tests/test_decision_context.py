"""Offline payload projection tests; no models, files of acquisitions, or devices."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from backend.decision_context import build_decision_context, current_attempt_evidence_ids


def legacy_collect(payload):
    found = set()
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key in ('id', 'artifactId', 'evidenceId', 'attemptId'):
                if isinstance(value.get(key), str):
                    found.add(value[key])
            if isinstance(value.get('evidenceIds'), list):
                found.update(item for item in value['evidenceIds'] if isinstance(item, str))
            for item in value.values():
                visit(item)
    visit(payload.get('observations'))
    visit(payload.get('history'))
    return found


def inspect_observation(value, evidence_id):
    if isinstance(value, list):
        for item in value:
            result = inspect_observation(item, evidence_id)
            if result is not None:
                return result
    elif isinstance(value, dict):
        if evidence_id in [value.get(key) for key in ('id', 'artifactId', 'evidenceId', 'attemptId')]:
            return value
        for item in value.values():
            result = inspect_observation(item, evidence_id)
            if result is not None:
                return result
    return None


def payload_fixture():
    return {
        'runId': 'run-current', 'nodeId': 'node-current', 'revision': 8, 'mode': 'real',
        'goal': 'OFFLINE CONTEXT TEST', 'allowedActions': ['retry', 'wait'],
        'parameterBounds': {'shots': {'min': 64, 'max': 2000}},
        'rules': [{'id': 'rule-allowed', 'action': 'retry'}],
        'observations': {
            'parameters': {'shots': 1000, 'readout_hz': 7e9}, 'attempt': 3, 'remainingAttempts': 2,
            'readoutRevalidation': {'id': 'marker-not-evidence', 'attemptId': 'old-attempt', 'state': 'active'},
            'result': {
                'id': 'result-id-not-evidence', 'attemptId': 'attempt-not-evidence',
                'dataKind': 'real', 'acquiredAt': '2026-11-18T00:00:00Z',
                'parameters': {'shots': 1000}, 'metrics': {'contrast_snr': 8.5},
                'quality': {'status': 'failed', 'reasons': ['capture disagreement']},
                'artifacts': [{'id': 'current-raw', 'name': 'raw_scan.npz', 'kind': 'raw', 'sha256': 'a'*64,
                               'metadata': {'id': 'metadata-not-evidence', 'evidenceIds': ['nested-old']}}],
                'analysisQuality': {'accepted': False, 'evidenceId': 'analysis-not-evidence',
                                    'curves': [{'id': 'curve-not-evidence', 'center': 7e9}]},
                'series': [{'name': 'actual-current-curve', 'x': [1, 2, 3], 'y': [1, .7, 1]}],
                'selectionResult': {
                    'dataKind': 'real', 'metrics': {'contrast_snr': 11.1},
                    'artifacts': [{'id': 'current-selection-raw', 'name': 'selection_raw.npz', 'sha256': 'b'*64}],
                    'selectionResult': {'artifacts': [{'id': 'nested-older-selection'}]},
                },
                'previousResult': {'artifacts': [{'id': 'old-result-artifact'}]},
            },
        },
        'history': [{'id': 'old-decision', 'attemptId': 'old-attempt', 'evidenceIds': ['old-power-raw'],
                     'action': 'retry', 'reason': 'Increase captures after earlier disagreement.',
                     'parameterChanges': {'captures': {'from': 2, 'to': 4, 'id': 'change-not-evidence'}},
                     'ruleId': 'rule-allowed', 'rule': {'id': 'nested-rule-not-evidence', 'trigger': 'repeat failure'},
                     'result': {'artifacts': [{'id': 'history-result-not-evidence'}]}}],
    }


class DecisionContextTests(unittest.TestCase):
    def test_only_current_and_direct_selection_artifacts_are_usable(self):
        original = payload_fixture()
        projected = build_decision_context(original)
        expected = {'current-raw', 'current-selection-raw'}
        self.assertEqual(set(projected['allowedEvidenceIds']), expected)
        self.assertEqual(legacy_collect(projected), expected)
        self.assertEqual(set(current_attempt_evidence_ids(original['observations']['result'])), expected)
        for evidence_id in expected:
            self.assertEqual(inspect_observation(projected['observations'], evidence_id)['id'], evidence_id)
        for illegal in ('old-power-raw', 'old-attempt', 'metadata-not-evidence', 'analysis-not-evidence',
                        'nested-older-selection', 'old-result-artifact', 'marker-not-evidence'):
            self.assertIsNone(inspect_observation(projected['observations'], illegal))

    def test_current_values_and_binding_preserved_without_mutating_archives(self):
        original = payload_fixture()
        before = deepcopy(original)
        projected = build_decision_context(original)
        self.assertEqual(original, before)
        for key in ('runId', 'nodeId', 'revision', 'allowedActions', 'parameterBounds', 'rules'):
            self.assertEqual(projected[key], original[key])
        self.assertEqual(projected['observations']['result']['series'], original['observations']['result']['series'])
        self.assertEqual(projected['observations']['result']['metrics'], {'contrast_snr': 8.5})
        self.assertEqual(projected['observations']['result']['selectionResult']['metrics'], {'contrast_snr': 11.1})
        projected['observations']['result']['series'][0]['y'][0] = -100
        self.assertEqual(original, before)

    def test_history_retains_reason_rule_and_parameter_change_but_no_evidence(self):
        projected = build_decision_context(payload_fixture())
        history = projected['history'][0]
        self.assertEqual(history, {
            'action': 'retry', 'reason': 'Increase captures after earlier disagreement.',
            'parameterChanges': {'captures': {'from': 2, 'to': 4}}, 'ruleId': 'rule-allowed',
            'rule': {'trigger': 'repeat failure'},
        })
        self.assertEqual(legacy_collect({'history': [history]}), set())

    def test_missing_and_malformed_results_never_grant_evidence(self):
        for value in (None, [], {}, {'artifacts': [None, {'id': ''}, {'id': 123}, {'artifactId': 'wrong-field'}]}):
            projected = build_decision_context({'observations': {'result': value}, 'history': []})
            self.assertEqual(projected['allowedEvidenceIds'], [])
            self.assertEqual(legacy_collect(projected), set())

    def test_untrusted_preexisting_allowlist_cannot_expand_artifacts(self):
        original = payload_fixture()
        original['allowedEvidenceIds'] = ['old-power-raw']
        original['observations']['allowedEvidenceIds'] = ['old-power-raw']
        original['observations']['evidenceIds'] = ['old-power-raw']
        original['observations']['constructor'] = {'id': 'prototype-poison'}
        projected = build_decision_context(original)
        self.assertEqual(legacy_collect(projected), set(projected['allowedEvidenceIds']))
        self.assertNotIn('constructor', projected['observations'])

    @unittest.skipUnless(shutil.which('node'), 'Node runtime unavailable for legacy validator contract')
    def test_actual_legacy_agent_validator_accepts_only_current_allowlist(self):
        projected = build_decision_context(payload_fixture())
        module = (Path(__file__).resolve().parents[1] / 'agent/src/validation.mjs').as_uri()
        script = '''import fs from 'node:fs';
const {collectEvidence,validateDecision}=await import(process.argv[1]);
const payload=JSON.parse(fs.readFileSync(0,'utf8'));
const accepted=payload.allowedEvidenceIds.map(id=>validateDecision({action:'wait',reason:'Offline test',evidenceIds:[id]},payload).evidenceIds[0]);
let oldRejected=false;try{validateDecision({action:'wait',reason:'Offline test',evidenceIds:['old-power-raw']},payload)}catch(e){oldRejected=e.message==='unknown_evidence_id'}
process.stdout.write(JSON.stringify({collected:[...collectEvidence(payload)].sort(),accepted,oldRejected}));'''
        completed = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script, module],
                                   input=json.dumps(projected), text=True, capture_output=True, timeout=20, check=True)
        result = json.loads(completed.stdout)
        self.assertEqual(result['collected'], projected['allowedEvidenceIds'])
        self.assertEqual(result['accepted'], projected['allowedEvidenceIds'])
        self.assertTrue(result['oldRejected'])


class PowerExplanationTests(unittest.TestCase):
    @staticmethod
    def trace(power, capture, index, tilted=False):
        x = [7e9 + i*(4e6/60) for i in range(61)]
        y = [1 + (.02*i if tilted else 0) for i in range(61)]
        y[index] -= .2
        return {'name': f"capture {capture} {{'Q11.para.probCarryScale': {power}}}",
                'x': x, 'y': y, 'xUnit': 'Hz', 'fit': False}

    def explain(self, series):
        payload = {'observations': {'result': {
            'series': series, 'artifacts': [{'id': 'saved-current-raw'}],
            'analysisQuality': {'accepted': False, 'repeat_centers_hz': [7e9, 7.001e9],
                                'powers_waveform_scale': [.1, .2]},
        }}, 'history': []}
        return build_decision_context(payload, procedure_id='ro_power_shift')

    def test_per_power_capture_spread_is_not_power_group_mean_difference(self):
        output = self.explain([self.trace(.1, c, idx) for c, idx in enumerate([28, 28, 31, 29], 1)] +
                              [self.trace(.2, c, idx) for c, idx in enumerate([45, 45, 45, 45], 1)])
        diagnostics = output['observations']['powerSeriesDiagnostics']
        self.assertEqual(diagnostics['status'], 'available')
        low, high = diagnostics['groups']
        self.assertEqual([r['dipIndex'] for r in low['captures']], [28, 28, 31, 29])
        self.assertAlmostEqual(low['spreadHz'], 200000, places=4)
        self.assertTrue(low['exceedsNativeRepeatThreshold'])
        self.assertEqual(high['spreadHz'], 0)
        self.assertFalse(high['exceedsNativeRepeatThreshold'])
        self.assertIn('not one center per capture', diagnostics['repeatCentersMeaning'])
        self.assertTrue(diagnostics['acceptanceUnchanged'])
        self.assertFalse(output['observations']['result']['analysisQuality']['accepted'])
        self.assertEqual(legacy_collect(output), {'saved-current-raw'})

    def test_linear_edge_baseline_matches_native_depth_not_raw_argmin(self):
        output = self.explain([self.trace(.1, 1, 25, tilted=True), self.trace(.1, 2, 25, tilted=True)])
        group = output['observations']['powerSeriesDiagnostics']['groups'][0]
        self.assertEqual([r['dipIndex'] for r in group['captures']], [25, 25])

    def test_invalid_labels_data_and_duplicate_captures_cannot_claim_complete_diagnostics(self):
        bad = self.trace(.1, 1, 25)
        bad['name'] = "capture 1 {'Q11.para.probCarryScale': __import__('os')}"
        output = self.explain([bad])['observations']['powerSeriesDiagnostics']
        self.assertEqual(output['status'], 'unavailable')
        duplicate = self.explain([self.trace(.1, 1, 25), self.trace(.1, 1, 28)])['observations']['powerSeriesDiagnostics']
        self.assertEqual(duplicate['status'], 'partial')
        self.assertIsNone(duplicate['groups'][0]['exceedsNativeRepeatThreshold'])

    def test_non_power_procedures_are_not_annotated_and_selection_is_separate(self):
        payload = payload_fixture()
        self.assertNotIn('powerSeriesDiagnostics', build_decision_context(payload, procedure_id='sq_rabi_time')['observations'])
        result = payload['observations']['result']
        result['series'] = [self.trace(.1, 1, 25), self.trace(.1, 2, 25)]
        result['selectionResult']['series'] = [self.trace(.2, 1, 35), self.trace(.2, 2, 35)]
        output = build_decision_context(payload, procedure_id='ro_power_shift')['observations']
        self.assertEqual(output['powerSeriesDiagnostics']['sourceEvidenceIds'], ['current-raw'])
        self.assertEqual(output['selectionPowerSeriesDiagnostics']['sourceEvidenceIds'], ['current-selection-raw'])
        self.assertEqual(output['selectionPowerSeriesDiagnostics']['groups'][0]['powerWaveformScale'], .2)


if __name__ == '__main__':
    unittest.main()
