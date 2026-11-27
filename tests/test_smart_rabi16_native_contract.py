"""TEST_ONLY versioned smart sampling; plans and injected transport, no hardware."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter, AdapterError
from adapters.arclight_smart_rabi16_adapter import ArcLightSmartRabi16Adapter
from adapters.arclight_smart_rabi16_runtime import policy
from adapters.arclight_smart_rabi16_runtime.execute_worker import verify_sources, validate_admission

ROOT = Path(__file__).resolve().parents[1]


class SmartSamplingContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='qcal-smart-rabi16-TEST_ONLY-')
        cls.root = Path(cls.temp.name)
        files = json.loads((ROOT / policy.MANIFEST).read_text(encoding='utf-8'))
        for name in {r['path'] for r in files} | {policy.MANIFEST}:
            target = cls.root / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        cls.profile = policy.load_profile(cls.root)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def request(self, ordinal=11):
        p = {'shots': 2000, 'capture_repeats': 2, 'acquisitions_per_point': 16,
             'points': 41, 'amp_start': .02, 'amp_stop': .98, 'drive_length_s': 375e-9}
        req = {'runId': 'TEST_ONLY-smart', 'nodeId': 'Q11:s03_rabi_amplitude',
               'attemptId': 'TEST_ONLY-attempt', 'operationId': 'TEST_ONLY-attempt/measure',
               'resourceId': 'Q11', 'procedureId': 'sq_rabi_amplitude', 'parameters': p,
               'parameterVersions': {}, 'adapterId': policy.ADAPTER_ID,
               'adapterProfileHash': policy.fingerprint(self.root, self.profile)}
        req['executionBinding'] = {'schema': policy.BINDING_SCHEMA, 'stage': 'acquisition',
            'authorizationId': 'TEST_ONLY-auth', 'authorizationSha256': 'a'*64,
            'baseAdapterId': policy.BASE_ID, 'baseAdapterProfileHash': policy.BASE_FINGERPRINT,
            'adapterId': req['adapterId'], 'adapterProfileHash': req['adapterProfileHash'],
            'workflowId': req['runId'], 'nodeId': req['nodeId'], 'procedureId': req['procedureId'],
            'resourceId': 'Q11', 'attemptId': req['attemptId'], 'attemptNumber': ordinal,
            'selectionOperationId': req['attemptId']+'/measure', 'verificationOperationId': req['attemptId']+'/verify',
            'parametersSha256': policy.parameter_context_sha256(p), 'smartRunSha256': 'b'*64,
            'sourceAttemptId': 'TEST_ONLY-completed-eight-block-source', 'sourceEvidenceSha256': 'c'*64}
        return req

    def test_source_closure_and_no_attempt_ten_ceiling(self):
        self.assertTrue(verify_sources(self.root))
        for ordinal in (11, 101):
            plan = ArcLightSmartRabi16Adapter(self.root, self.profile).build_plan(self.request(ordinal))
            self.assertEqual(plan['executionBinding']['attemptNumber'], ordinal)
            self.assertEqual(plan['estimated_total_shots'], 2624000)
            self.assertEqual(plan['totalRows'], 82)
            self.assertEqual(plan['executionPolicy'], policy.EXECUTION_POLICY)
        self.assertFalse(self.profile['enabled'])

    def test_physical_limits_and_exact_scope_remain(self):
        adapter = ArcLightSmartRabi16Adapter(self.root, self.profile)
        for changes in ({'acquisitions_per_point':20}, {'shots':2048}):
            req = self.request(); req['parameters'].update(changes)
            req['executionBinding']['parametersSha256'] = policy.parameter_context_sha256(req['parameters'])
            with self.assertRaises(AdapterError): adapter.build_plan(req)
        for key,value in [('attemptNumber',0), ('attemptNumber',True), ('sourceEvidenceSha256',''),
                           ('smartRunSha256',''), ('baseAdapterId','foreign')]:
            req = self.request(); req['executionBinding'][key] = value
            with self.assertRaises(AdapterError): adapter.build_plan(req)
        req = self.request(); req['procedureId'] = 'sq_t1'
        with self.assertRaises(AdapterError): adapter.build_plan(req)

    def test_complete_intent_plan_binding_and_old_profile_rejection(self):
        adapter = ArcLightSmartRabi16Adapter(self.root, self.profile); req = self.request()
        plan = adapter.build_plan(req); folder = adapter._operation_dir(req['operationId']);folder.mkdir(parents=True)
        intent = {'operationId':req['operationId'],'request':req,'requestHash':policy.intent_digest(req),
                  'profileHash':policy.intent_digest({**self.profile,'enabled':True})}
        (folder/'adapter_intent.json').write_text(json.dumps(intent),encoding='utf-8')
        self.assertEqual(validate_admission(self.root,folder,plan),req)
        bad = deepcopy(plan);bad['parameters']['shots']=1000
        with self.assertRaises(ValueError): validate_admission(self.root,folder,bad)
        old = policy.read(self.root/policy.BASE_PROFILE)
        with self.assertRaises(AdapterError): ArcLightAdapter(self.root,old).build_plan(req)

    def test_actual_native_jobs_are_identical_to_frozen_r31_16_block_jobs(self):
        from adapters.arclight_rabi16_adapter import ArcLightRabi16Adapter
        from adapters.arclight_rabi16_runtime import policy as old
        request=self.request(11);new_plan=ArcLightSmartRabi16Adapter(self.root,self.profile).build_plan(request)
        prior=deepcopy(request);profile=old.load_profile(self.root)
        prior.update(adapterId=old.ADAPTER_ID,adapterProfileHash=old.fingerprint(self.root,profile))
        b={k:v for k,v in prior['executionBinding'].items() if k in old.BINDING_FIELDS}
        b.update(schema=old.BINDING_SCHEMA,adapterId=old.ADAPTER_ID,adapterProfileHash=prior['adapterProfileHash'],attemptNumber=4)
        prior['executionBinding']=b
        old_plan=ArcLightRabi16Adapter(self.root,profile).build_plan(prior)
        self.assertEqual(new_plan['jobs'],old_plan['jobs'])
        self.assertEqual(new_plan['expectedJobRows'],old_plan['expectedJobRows'])
        self.assertEqual(new_plan['estimated_total_shots'],old_plan['estimated_total_shots'])
        prior['executionBinding']['attemptNumber']=11
        with self.assertRaises(AdapterError):ArcLightRabi16Adapter(self.root,profile).build_plan(prior)

    def test_real_wrapper_writes_admissible_intent_for_both_independent_operations(self):
        adapter=ArcLightSmartRabi16Adapter(self.root,self.profile).with_execution_enabled()
        request=self.request();request['attemptId']='TEST_ONLY-wrapper-ordinal11'
        binding=request['executionBinding'];binding.update(attemptId=request['attemptId'],
            selectionOperationId=request['attemptId']+'/measure',verificationOperationId=request['attemptId']+'/verify')
        checked=[]
        def boundary(*args,**kwargs):
            folder=adapter._operation_dir(request['operationId'])
            plan=json.loads((folder/'plan.json').read_text(encoding='utf-8'))
            self.assertEqual(validate_admission(self.root,folder,plan),request)
            self.assertEqual(plan['totalRows'],82);self.assertEqual(plan['estimated_total_shots'],2624000)
            checked.append(plan['executionBinding']);raise OSError('TEST_ONLY no process/connection')
        with patch('adapters.arclight.subprocess.Popen',side_effect=boundary):
            for stage in ('measure','verify'):
                request['operationId']=request['attemptId']+'/'+stage
                if stage=='verify':request['parameters'].update(drive_amp=.8,half_pi_amp=.8,order_seed=123)
                with self.assertRaises(AdapterError) as caught:adapter.execute(request,lambda e:None,lambda:False)
                self.assertEqual(caught.exception.code,'FAILED_BEFORE_START')
        self.assertEqual(checked,[binding,binding])


if __name__ == '__main__': unittest.main()
