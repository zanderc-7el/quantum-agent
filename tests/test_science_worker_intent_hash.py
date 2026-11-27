"""Actual ArcLight intent bytes enter both science workers; no child/device runs."""
from contextlib import ExitStack
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
from adapters.f8 import _digest as wrapper_digest
from adapters.science_runtime.policy import digest as plan_digest
from adapters.science_v3.plans import POLICY, build_rows
from adapters.science_v3_irb.half_scan import POLICY as HALF_POLICY, build_half_scan


class CapturedBeforeLaunch(BaseException):
    """Exit after the real wrapper wrote both files, before any subprocess."""


class WorkerIntentHashTests(unittest.TestCase):
    def fixture(self, variant):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='science-intent-contract-')))
        parameters = {'drive_hz': 4.86e9, 'drive_amp': .3, 'drive_length_s': 20e-9,
                      'readout_hz': 7.01e9, 'readout_amp': .4, 'readout_width_s': 2e-6,
                      'xy_delay_s': 100e-9, 'xy_filter_mhz': 4700, 'xy_attenuation_db': 3.75,
                      'anharmonicity_hz': -200e6, 'scan_axis_values': [-.5, 0., .5]}
        method = 'sq_drag_scan' if variant == 'v3' else 'sq_drag_half_scan'
        proof = {'assessment': {'executionEligible': True}, 'nodes': [], 'edges': [],
                 'fixtureScope': 'hash boundary only; no physical source attested'}
        request = {'operationId': 'intent-fixture/'+variant+'/measure', 'runId': 'fixture-run',
                   'nodeId': 'fixture-node', 'attemptId': 'fixture-attempt', 'resourceId': 'fixture-Q',
                   'procedureId': method, 'parameterVersions': {'drive_hz': 0},
                   'leaseToken': 'unit-fixture-not-a-physical-lease', 'parameters': parameters,
                   'scienceDependencies': proof, 'label': '哈希边界测试', 'outputDir': str(root/'ignored-output')}
        canonical = {k: v for k, v in request.items() if k != 'outputDir'}
        sources = {'fixtureOnly': True}
        if variant == 'v3':
            normalized, rows = build_rows(method, parameters)
            plan = {'adapter': 'science-v3', 'policy': POLICY}
        else:
            normalized, campaign = build_half_scan(parameters)
            rows = campaign['rows']
            plan = {'adapter': 'science-v3-irb', 'policy': HALF_POLICY,
                    'sampleBudget': campaign['budget']['samples'], 'rabiLineage': {'fixtureOnly': True}}
        plan.update(version=3, procedureId=method, parameters=normalized, rows=rows,
                    totalRows=len(rows), estimated_total_shots=len(rows)*normalized['shots']*normalized['acquisitions_per_point'],
                    max_seconds=10, sourceHash=plan_digest(sources), validationEvidence=proof,
                    requestHash=plan_digest(canonical),
                    requestIdentity={k: request[k] for k in ('runId','nodeId','attemptId','operationId','resourceId','procedureId','parameterVersions','leaseToken')})
        adapter = ArcLightAdapter(root, {'enabled': True, 'python': sys.executable,
                                        'runner': 'never-launched.py', 'cancelGraceSeconds': .2})
        with patch.object(adapter, 'preflight', return_value={'ready': True}), \
             patch.object(adapter, 'build_plan', return_value=plan), \
             patch('adapters.arclight.subprocess.Popen', side_effect=CapturedBeforeLaunch) as spawn:
            with self.assertRaises(CapturedBeforeLaunch):
                adapter.execute(request, lambda event: None, lambda: False)
            spawn.assert_called_once()
        folder = adapter._operation_dir(request['operationId'])
        intent_bytes = (folder/'adapter_intent.json').read_bytes()
        intent = json.loads(intent_bytes)
        self.assertIn('哈希边界测试'.encode(), intent_bytes)
        self.assertEqual(intent['request'], canonical)
        self.assertEqual(intent['requestHash'], wrapper_digest(canonical))
        self.assertNotEqual(intent['requestHash'], plan['requestHash'])
        self.assertFalse((folder/'process.json').exists())
        worker = importlib.import_module('adapters.science_v3'+('_irb' if variant == 'irb' else '')+'.execute_worker')
        return worker, folder, json.loads((folder/'plan.json').read_text(encoding='utf8')), intent, sources

    def admit(self, variant, fixture):
        worker, folder, plan, _, sources = fixture
        with ExitStack() as patches:
            if variant == 'v3':
                patches.enter_context(patch('adapters.science_v3_adapter.verify_extension', return_value=sources))
            else:
                patches.enter_context(patch('adapters.science_v3_irb.policy.verify_sources', return_value=sources))
                patches.enter_context(patch('adapters.science_v3_irb.timing.rabi_lineage', return_value={'fixtureOnly': True}))
            return worker.validate_admission(plan, folder)

    def test_real_wrapper_intent_uses_distinct_valid_hash_contracts(self):
        for variant in ('v3', 'irb'):
            with self.subTest(worker=variant):
                fixture = self.fixture(variant)
                self.assertEqual(self.admit(variant, fixture), fixture[3]['request'])

    def test_each_hash_is_independently_required(self):
        for variant in ('v3', 'irb'):
            fixture = self.fixture(variant)
            original_plan, original_intent = deepcopy(fixture[2]), deepcopy(fixture[3])
            for mutation in ('wrong_intent_digest', 'compact_intent_digest', 'wrong_plan_digest',
                             'changed_request_rehash_intent_only', 'changed_request_rehash_plan_only'):
                with self.subTest(worker=variant, mutation=mutation):
                    plan, intent = deepcopy(original_plan), deepcopy(original_intent)
                    if mutation == 'wrong_intent_digest': intent['requestHash'] = '0'*64
                    elif mutation == 'compact_intent_digest': intent['requestHash'] = plan_digest(intent['request'])
                    elif mutation == 'wrong_plan_digest': plan['requestHash'] = '0'*64
                    else:
                        intent['request']['parameters']['drive_hz'] += 1e6
                        if mutation.endswith('intent_only'): intent['requestHash'] = wrapper_digest(intent['request'])
                        else: plan['requestHash'] = plan_digest(intent['request'])
                    (fixture[1]/'adapter_intent.json').write_text(json.dumps(intent), encoding='utf8')
                    with self.assertRaisesRegex(ValueError, 'intent|lease'):
                        self.admit(variant, (fixture[0], fixture[1], plan, intent, fixture[4]))

    def test_lease_identity_preview_and_proof_guards_remain(self):
        for variant in ('v3', 'irb'):
            fixture = self.fixture(variant)
            original_plan, original_intent = deepcopy(fixture[2]), deepcopy(fixture[3])
            for mutation in ('lease', 'identity', 'preview', 'proof', 'source'):
                with self.subTest(worker=variant, mutation=mutation):
                    plan, intent = deepcopy(original_plan), deepcopy(original_intent)
                    if mutation == 'lease':
                        intent['request'].pop('leaseToken')
                        intent['requestHash'] = wrapper_digest(intent['request'])
                        plan['requestHash'] = plan_digest(intent['request'])
                    elif mutation == 'identity': plan['requestIdentity']['resourceId'] = 'different-Q'
                    elif mutation == 'preview': plan['compileOnly'] = True
                    elif mutation == 'proof': plan['validationEvidence']['assessment']['executionEligible'] = False
                    else: plan['sourceHash'] = '0'*64
                    (fixture[1]/'adapter_intent.json').write_text(json.dumps(intent), encoding='utf8')
                    with self.assertRaises(ValueError):
                        self.admit(variant, (fixture[0], fixture[1], plan, intent, fixture[4]))


if __name__ == '__main__':
    unittest.main()
