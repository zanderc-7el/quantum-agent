"""Versioned sampling extension contracts. All files/transport are isolated."""
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get('QCAL_RABI16_BASE_ROOT', ROOT))
sys.path.insert(0, str(BASE))
import adapters
if ROOT != BASE:
    adapters.__path__.insert(0, str(ROOT / 'adapters'))
from adapters.arclight import ArcLightAdapter, AdapterError
from adapters.arclight_rabi16_adapter import ArcLightRabi16Adapter
from adapters.arclight_rabi16_runtime import policy
from adapters.arclight_rabi16_runtime.execute_worker import validate_admission, verify_sources


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')


class SamplingContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='qcal-rabi16-test-only-')
        cls.root = Path(cls.temporary.name)
        entries = json.loads((BASE / policy.BASE_MANIFEST).read_text(encoding='utf-8'))
        for name in {r['path'] for r in entries} | {policy.BASE_MANIFEST}:
            target = cls.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BASE / name, target)
        for name in policy.NEW_FILES | {policy.MANIFEST}:
            target = cls.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        cls.profile = policy.load_profile(cls.root)
        cls.base_profile = json.loads((cls.root / policy.BASE_PROFILE).read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def request(self, blocks=16, stage='acquisition'):
        p = {'shots': 2000, 'capture_repeats': 2, 'acquisitions_per_point': blocks,
             'points': 41, 'amp_start': .02, 'amp_stop': .98, 'drive_length_s': 375e-9,
             'readout_amp': .76, 'readout_hz': 7009610000., 'drive_hz': 4859050000.}
        req = {'runId': 'TEST_ONLY-workflow', 'nodeId': 'Q11:s03_rabi_amplitude',
               'attemptId': 'TEST_ONLY-attempt', 'operationId': 'TEST_ONLY-attempt/measure',
               'resourceId': 'Q11', 'procedureId': 'sq_rabi_amplitude', 'parameters': p,
               'parameterVersions': {}, 'adapterId': policy.ADAPTER_ID,
               'adapterProfileHash': policy.fingerprint(self.root, self.profile)}
        req['executionBinding'] = {
            'schema': policy.BINDING_SCHEMA, 'stage': stage,
            'authorizationId': 'TEST_ONLY-authorization', 'authorizationSha256': 'a' * 64,
            'baseAdapterId': policy.BASE_ID, 'baseAdapterProfileHash': policy.BASE_FINGERPRINT,
            'adapterId': req['adapterId'], 'adapterProfileHash': req['adapterProfileHash'],
            'workflowId': req['runId'], 'nodeId': req['nodeId'], 'procedureId': req['procedureId'],
            'resourceId': req['resourceId'], 'attemptId': req['attemptId'], 'attemptNumber': 4,
            'selectionOperationId': req['attemptId'] + '/measure',
            'verificationOperationId': req['attemptId'] + '/verify',
            'parametersSha256': policy.parameter_context_sha256(p)}
        return req

    def test_exact_profile_delta_and_complete_source_closure(self):
        self.assertTrue(verify_sources(self.root))
        expected = deepcopy(self.base_profile)
        expected.update(id=policy.ADAPTER_ID, runner=policy.RUNNER,
                        runnerSha256=self.profile['runnerSha256'], sourceManifest=policy.MANIFEST)
        expected['parameterBounds']['acquisitions_per_point'] = [1, 16]
        self.assertEqual(expected, self.profile)
        self.assertFalse(self.profile['enabled'])
        bad = deepcopy(self.profile)
        bad['parameterBounds']['shots'][1] = 3000
        with self.assertRaises(ValueError):
            policy.verify_profile(self.root, bad)

    def test_16_real_blocks_preserve_job_and_budget_except_count(self):
        req = self.request()
        plan = ArcLightRabi16Adapter(self.root, self.profile).build_plan(req)
        old_req = deepcopy(req)
        old_req['parameters']['acquisitions_per_point'] = 8
        old = ArcLightAdapter(self.root, self.base_profile).build_plan(old_req)
        self.assertEqual(plan['totalRows'], 82)
        self.assertEqual(plan['estimated_total_shots'], 2624000)
        self.assertEqual(plan['parameters']['capture_repeats'], 2)
        new_job, old_job = deepcopy(plan['jobs'][0]), deepcopy(old['jobs'][0])
        new_job['acquisitions_per_point'] = old_job['acquisitions_per_point']
        new_job['parameters']['acquisitions_per_point'] = old_job['parameters']['acquisitions_per_point']
        self.assertEqual(new_job, old_job)
        with self.assertRaises(AdapterError) as caught:
            ArcLightAdapter(self.root, self.base_profile).build_plan(req)
        self.assertEqual(caught.exception.code, 'PARAMETER_OUT_OF_RANGE')
        self.assertEqual(ArcLightRabi16Adapter(self.root, self.profile).build_plan(self.request(8))['estimated_total_shots'],
                         old['estimated_total_shots'])

    def test_artifact_capacity_is_rejected_before_collecting(self):
        adapter = ArcLightRabi16Adapter(self.root, self.profile)
        req = self.request()
        req['parameters'].update(points=161, capture_repeats=6, shots=100)
        req['executionBinding']['parametersSha256'] = policy.parameter_context_sha256(req['parameters'])
        with self.assertRaises(AdapterError) as caught:
            adapter.build_plan(req)
        self.assertEqual(caught.exception.code, 'SAMPLING_ARTIFACT_BUDGET')
        req['parameters']['capture_repeats'] = 5
        req['executionBinding']['parametersSha256'] = policy.parameter_context_sha256(req['parameters'])
        self.assertLessEqual(adapter.build_plan(req)['maximumRegisteredArtifacts'], 16384)

    def test_identical_selection_verification_files_keep_distinct_registry_paths(self):
        from backend.controller import Controller
        adapter = ArcLightRabi16Adapter(self.root, self.profile)
        artifacts, paths = [], []
        for operation in ('TEST_ONLY-registry/measure', 'TEST_ONLY-registry/verify'):
            directory = adapter._operation_dir(operation)
            path = directory / 'results' / 'native' / 'entry_source_hashes.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'{"same":"frozen native entry hashes"}')
            artifacts.append(adapter._artifacts(directory, [str(path)])[0])
            paths.append(path.resolve())
        self.assertEqual(artifacts[0]['sha256'], artifacts[1]['sha256'])
        self.assertNotEqual(artifacts[0]['id'], artifacts[1]['id'])
        with sqlite3.connect(':memory:') as db:
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE platform_artifacts(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,payload TEXT)')
            rt = SimpleNamespace(db=db)
            controller = SimpleNamespace(root=self.root)
            for artifact in artifacts:
                Controller._artifact(controller, rt, artifact)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM platform_artifacts').fetchone()[0], 2)
            for artifact, path in zip(artifacts, paths):
                registered = db.execute('SELECT path,sha256 FROM platform_artifacts WHERE id=?',
                                        (artifact['id'],)).fetchone()
                self.assertEqual(Path(registered['path']), path)
                self.assertEqual(registered['sha256'], artifact['sha256'])

    def test_unapproved_scope_parameter_changes_and_other_methods_rejected(self):
        adapter = ArcLightRabi16Adapter(self.root, self.profile)
        for field, value in [('workflowId', 'different'), ('nodeId', 'other'),
                             ('baseAdapterProfileHash', '0' * 64), ('attemptNumber', True),
                             ('stage', 'unknown'), ('authorizationSha256', 'not-hash')]:
            with self.subTest(field=field):
                req = self.request()
                req['executionBinding'][field] = value
                with self.assertRaises(AdapterError):
                    adapter.build_plan(req)
        for blocks in (17, True, 8.5):
            with self.subTest(blocks=blocks), self.assertRaises(AdapterError):
                adapter.build_plan(self.request(blocks))
        req = self.request()
        req['parameters']['drive_length_s'] = 376e-9
        with self.assertRaises(AdapterError):
            adapter.build_plan(req)
        for method in ('sq_rabi_time', 'ro_iq01', 'sq_t1', 'sq_rb'):
            req = self.request()
            req['procedureId'] = method
            with self.subTest(method=method), self.assertRaises(AdapterError):
                adapter.build_plan(req)
        self.assertEqual(set(adapter.capabilities()['procedures']), {'sq_rabi_amplitude'})

    def test_verification_candidate_does_not_change_frozen_scan_context(self):
        adapter = ArcLightRabi16Adapter(self.root, self.profile)
        req = self.request()
        req['operationId'] = req['attemptId'] + '/verify'
        req['parameters'].update(drive_amp=.82, half_pi_amp=.82, order_seed=1234)
        self.assertEqual(adapter.build_plan(req)['estimated_total_shots'], 2624000)
        req['operationId'] = 'another-operation'
        with self.assertRaises(AdapterError):
            adapter.build_plan(req)

    def test_actual_wrapper_intent_hash_profile_and_plan_binding(self):
        adapter = ArcLightRabi16Adapter(self.root, self.profile)
        req = self.request()
        plan = adapter.build_plan(req)
        folder = self.root / 'data/runs/arclight_operations' / hashlib.sha256(req['operationId'].encode()).hexdigest()
        intent = {'operationId': req['operationId'], 'request': req,
                  'requestHash': policy.intent_digest(req),
                  'profileHash': policy.intent_digest({**self.profile, 'enabled': True})}
        put(folder / 'adapter_intent.json', intent)
        self.assertEqual(validate_admission(self.root, folder, plan), req)
        for mutate in ('plan', 'intent', 'profile', 'plan_only'):
            changed, changed_intent = deepcopy(plan), deepcopy(intent)
            if mutate == 'plan':
                changed['jobs'][0]['acquisitions_per_point'] = 8
            elif mutate == 'intent':
                changed_intent['requestHash'] = policy.digest(req)
            elif mutate == 'profile':
                changed_intent['profileHash'] = policy.intent_digest(self.profile)
            else:
                r = self.request(stage='plan_only')
                changed = adapter.build_plan(r)
                changed_intent['request'] = r
                changed_intent['requestHash'] = policy.intent_digest(r)
            put(folder / 'adapter_intent.json', changed_intent)
            with self.subTest(mutate=mutate), self.assertRaises((ValueError, AdapterError)):
                validate_admission(self.root, folder, changed)

    def test_inherited_execute_writes_real_admitted_intent_before_subprocess_boundary(self):
        req = self.request()
        req['attemptId'] = 'TEST_ONLY-real-wrapper'
        req['operationId'] = req['attemptId'] + '/measure'
        req['executionBinding'].update(attemptId=req['attemptId'],
            selectionOperationId=req['operationId'], verificationOperationId=req['attemptId'] + '/verify')
        adapter = ArcLightRabi16Adapter(self.root, {**self.profile, 'enabled': True})
        self.assertTrue(verify_sources(self.root))
        calls = []
        def block_launch(argv, **kwargs):
            self.assertEqual(argv[1:4], ['-I', '-B', '-u'])
            self.assertEqual(Path(argv[4]), self.root / policy.RUNNER)
            plan_path = Path(argv[5])
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
            self.assertEqual(validate_admission(self.root, plan_path.parent, plan), req)
            calls.append(argv)
            raise RuntimeError('TEST_ONLY stop before any actual subprocess/device')
        with patch.object(adapter, 'preflight', return_value={'ready': True}), \
                patch('adapters.arclight.subprocess.Popen', side_effect=block_launch):
            with self.assertRaises(AdapterError) as caught:
                adapter.execute(req, lambda event: None, lambda: False)
        self.assertEqual(caught.exception.code, 'FAILED_BEFORE_START')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
