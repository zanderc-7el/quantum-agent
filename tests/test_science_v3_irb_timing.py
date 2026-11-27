"""Explicit temporary receipt fixtures for the narrow DC timing migration."""
from copy import deepcopy
import hashlib
import json
import unittest
from adapters.science_runtime.policy import digest


class TimingContracts(unittest.TestCase):
    def setUp(self):
        from tests.test_science_v3_evidence import EvidenceContracts
        from tests.test_science_v3_irb import CampaignContracts
        from adapters.arclight_configurable_runtime.native_single_qubit_specs import rabi_time_spec
        EvidenceContracts.setUp(self)
        self.consumer['parameters']=CampaignContracts().parameters()
        self.dependency.update(procedureId='sq_rabi_time',sourceRoles=['rabi_time'],
            parameters={**self.consumer['parameters'],'drive_amp':.2},
            resolvedOutputs={'candidateParameters':{'drive_length_s':20e-9}})
        for index,op in enumerate(self.dependency['operations']):
            folder=self.folder(op);intent=self.read(folder/'adapter_intent.json');receipt=self.read(folder/'adapter_receipt.json');plan=self.read(folder/'plan.json')
            p=deepcopy(self.dependency['parameters']);duration=(20.1+.02*index)*1e-9;frequency=1/(2*duration)
            intent['request'].update(procedureId='sq_rabi_time',parameters=p)
            receipt['result'].update(parameters=p,candidateParameters={'drive_length_s':duration},
                metrics={'rabi_frequency_hz':frequency},analysisQuality={'accepted':True,'kind':'rabi_time','procedureId':'sq_rabi_time',
                    'groups':[{'repeats':[{'fit':{'value':frequency,'standard_error_model':1e4}},{'fit':{'value':frequency,'standard_error_model':1.1e4}}]}]})
            spec=rabi_time_spec([n*1e-9 for n in range(101)],drive_amplitude=.2,shots=100)
            spec['static_config']['cfg.Q1.gate.U_gates.buff']=2e-9
            spec['meta']['pulse_compatibility']='f8_validated_short_half_dc_buff2ns_v1'
            plan.update(procedureId='sq_rabi_time',parameters=p,jobs=[{'kind':'rabi_time','target':'Q11','spec':spec,
                'config':{'Q1':{'gate':{'sRate':{'XY':4e9}}}}}])
            self.write(folder/'adapter_intent.json',intent);self.write(folder/'adapter_receipt.json',receipt);self.write(folder/'plan.json',plan)
            self.refresh(op)
    def folder(self,op):return self.root/'data/runs/arclight_operations'/hashlib.sha256(op['operationId'].encode()).hexdigest()
    @staticmethod
    def read(path):return json.loads(path.read_text(encoding='utf-8'))
    @staticmethod
    def write(path,value):path.write_text(json.dumps(value),encoding='utf-8')
    def refresh(self,op):
        from adapters.science_v3.evidence import result_content
        folder=self.folder(op);op['adapterRequestSha256']=digest(self.read(folder/'adapter_intent.json')['request'])
        op['resultContentSha256']=digest(result_content(self.read(folder/'adapter_receipt.json')['result']))
    def authenticate(self):
        from adapters.science_v3_irb.timing import authenticate_dependency
        return authenticate_dependency(self.root,self.consumer,self.dependency,expected_route=self.route,
            expected_binding=self.binding,expected_locks=['fixture-lock1','fixture-lock2'],expected_instrument=self.instrument)
    def test_authenticates_continuous_fit_and_two_explicit_quantization_policies(self):
        result=self.authenticate();migration=result['timingMigration']
        self.assertEqual(result['dependencySha256'],digest(self.dependency))
        self.assertAlmostEqual(migration['sourceContinuousDurationS'],20.1e-9)
        self.assertEqual(migration['sourceCommittedDurationS'],20e-9)
        self.assertEqual(migration['targetDurationS'],20e-9)
        self.assertAlmostEqual(migration['quantizationErrorS'],-.1e-9)
        self.assertEqual(len(migration['fitOperations']),2)
        self.assertFalse(migration['calibratesShapedRotation'])
    def test_changed_drive_readout_or_target_duration_rejects(self):
        original=deepcopy(self.consumer['parameters'])
        for key,value in [('drive_hz',4.85e9),('readout_amp',.41),('pulse_length_s',20.25e-9)]:
            with self.subTest(key=key):
                self.consumer['parameters']={**original,key:value}
                with self.assertRaises(ValueError):self.authenticate()
        self.consumer['parameters']=original
    def test_fake_candidate_or_non_dc_protocol_rejects_even_after_effect_rehash(self):
        op=self.dependency['operations'][0];folder=self.folder(op);original=self.read(folder/'adapter_receipt.json')
        changed=deepcopy(original);changed['result']['candidateParameters']['drive_length_s']=21e-9
        self.write(folder/'adapter_receipt.json',changed);self.refresh(op)
        with self.assertRaisesRegex(ValueError,'fit'):self.authenticate()
        self.write(folder/'adapter_receipt.json',original);self.refresh(op)
        plan=self.read(folder/'plan.json');plan['jobs'][0]['spec']['static_config']['cfg.Q1.gate.U_gates.shape']='Gaussian';self.write(folder/'plan.json',plan)
        with self.assertRaisesRegex(ValueError,'DC'):self.authenticate()
    def test_missing_uncertainty_uncommitted_duration_and_wrong_role_reject(self):
        op=self.dependency['operations'][0];folder=self.folder(op);receipt=self.read(folder/'adapter_receipt.json')
        receipt['result']['analysisQuality']['groups'][0]['repeats'][0]['fit'].pop('standard_error_model')
        self.write(folder/'adapter_receipt.json',receipt);self.refresh(op)
        with self.assertRaises(ValueError):self.authenticate()
        self.setUp()
        self.dependency['resolvedOutputs']['candidateParameters']['drive_length_s']=20.5e-9
        with self.assertRaisesRegex(ValueError,'committed'):self.authenticate()
        self.dependency['sourceRoles']=['rabi_time','half01']
        with self.assertRaises(ValueError):self.authenticate()
    def test_changed_raw_route_and_reused_operation_still_reject(self):
        self.route['xyPhysical']=[1,0]
        with self.assertRaises(ValueError):self.authenticate()
        self.route['xyPhysical']=[0,0]
        original=deepcopy(self.dependency['operations'])
        self.dependency['operations'][1]['operationId']=self.dependency['operations'][0]['operationId']
        with self.assertRaises(ValueError):self.authenticate()
        self.dependency['operations']=original
        folder=self.folder(self.dependency['operations'][0]);(folder/'raw_scan.npz').write_bytes(b'CHANGED EXPLICIT FIXTURE')
        with self.assertRaises(ValueError):self.authenticate()

    def test_new_window_quantization_is_distinct_from_old_committed_half_duration_grid(self):
        op=self.dependency['operations'][0];folder=self.folder(op);receipt=self.read(folder/'adapter_receipt.json')
        duration=20.14e-9;frequency=1/(2*duration);result=receipt['result']
        result['candidateParameters']['drive_length_s']=duration;result['metrics']['rabi_frequency_hz']=frequency
        for repeat in result['analysisQuality']['groups'][0]['repeats']:repeat['fit']['value']=frequency
        self.write(folder/'adapter_receipt.json',receipt);self.refresh(op)
        self.consumer['parameters']['pulse_length_s']=20.25e-9
        migration=self.authenticate()['timingMigration']
        self.assertEqual(migration['sourceCommittedDurationS'],20e-9)
        self.assertEqual(migration['targetDurationS'],20.25e-9)
        self.assertAlmostEqual(migration['quantizationErrorS'],.11e-9)

    def lineage_fixture(self):
        from adapters.science_v3_irb.timing import rabi_lineage
        attestation=self.authenticate();time_id=self.dependency['recordId']
        proof={'nodes':[{'recordId':'pi','procedureId':'sq_shape_rabi_amplitude'},{'recordId':'half'},self.dependency],
            'rootReferences':[{'role':role,'recordId':record} for role,record in [('pi01','pi'),('rabi_amplitude','pi'),('half01','half'),('rabi_time',time_id)]], 'edges':[]}
        lineage=rabi_lineage(proof,[attestation],self.consumer['parameters'])
        return proof,attestation,lineage

    def test_lineage_requires_same_amplitude_record_and_authenticated_time(self):
        from adapters.science_v3_irb.timing import rabi_lineage
        proof,attestation,lineage=self.lineage_fixture()
        self.assertEqual(lineage['pi01RecordId'],lineage['amplitudeRecordId'])
        self.assertEqual(lineage['sha256'],digest({k:v for k,v in lineage.items() if k!='sha256'}))
        with self.assertRaises(ValueError):rabi_lineage(proof,[],self.consumer['parameters'])
        proof['rootReferences'][1]['recordId']='another-pi'
        with self.assertRaises(ValueError):rabi_lineage(proof,[attestation],self.consumer['parameters'])

    def test_irb_inherits_both_native_half_receipts_and_exact_ancestor_edges(self):
        from adapters.science_v3_irb.timing import inherited_rabi_lineage
        proof,attestation,lineage=self.lineage_fixture();refs=deepcopy(proof['rootReferences'])
        drag={'recordId':'drag','procedureId':'sq_drag_half_scan','operations':[]}
        for stage in ('selection','verification'):
            op={'operationId':'half-fixture/'+stage,'stage':stage};folder=self.folder(op);folder.mkdir(parents=True)
            self.write(folder/'adapter_receipt.json',{'result':{'provenance':{'scienceHalfDrag':{'rabiLineage':lineage}}}})
            drag['operations'].append(op)
        proof['nodes'].append(drag);proof['rootReferences']=[refs[0],refs[2],{'role':'drag','recordId':'drag'}]
        proof['edges']=[{'fromRecordId':'drag','toRecordId':r['recordId'],'role':r['role']} for r in refs]
        self.assertEqual(inherited_rabi_lineage(self.root,proof,[attestation],self.consumer['parameters']),lineage)
        proof['edges'].pop()
        with self.assertRaisesRegex(ValueError,'ancestry'):inherited_rabi_lineage(self.root,proof,[attestation],self.consumer['parameters'])

    def test_half_plan_and_worker_reauthenticate_real_timing_receipt_fixture(self):
        from pathlib import Path
        from unittest.mock import patch
        from adapters.science_v3_irb.adapter import ScienceIRBAdapter,build_irb_profile
        from adapters.science_v3_irb.timing import authenticate_dependency as authenticate
        from adapters.science_v3_irb.execute_worker import validate_admission
        root=Path(__file__).resolve().parents[1]
        profile=build_irb_profile(root,self.read(root/'adapters/arclight_profile.json'))
        profile.update(instrument=self.instrument,hardwareLocks=['fixture-lock1','fixture-lock2'],configurationBinding=self.binding,
                       routingByResource={'Q11':self.route})
        adapter=ScienceIRBAdapter(self.root,profile)
        p={**deepcopy(self.consumer['parameters']),'pulse_compatibility':profile['resources']['Q11']['pulse_compatibility']};time_id=self.dependency['recordId']
        roles={'pi01':'pi','rabi_amplitude':'pi','half01':'half','anharmonicity':'delta','readout':'ro','rabi_time':time_id}
        proof={'schema':'qcal-science-dependencies/1','policyVersion':'explicit-test-fixture','kind':'measurement','scope':{'workflowId':'target','resourceId':'Q11'},'inputs':{},
               'rootReferences':[{'role':role,'recordId':record} for role,record in roles.items()],
               'nodes':[{'recordId':'pi','procedureId':'sq_shape_rabi_amplitude'},{'recordId':'half'},{'recordId':'delta'},{'recordId':'ro'},self.dependency],
               'edges':[{'fromRecordId':None,'role':role,'toRecordId':record} for role,record in roles.items()]}
        proof['closureSha256']=digest(proof)
        request={**self.consumer,'procedureId':'sq_drag_half_scan','operationId':'fixture/half/measure','nodeId':'half','attemptId':'fixture/half',
                 'parameters':p,'leaseToken':'explicit-unit-fixture','explicitParameterKeys':[]}
        proof['assessment']={'executionEligible':True,'consumerSha256':digest(request)};request['scienceDependencies']=proof
        def auth(root,request,dependency,**expected):
            if 'rabi_time' in dependency['sourceRoles']:return authenticate(root,request,dependency,**expected)
            return {'recordId':dependency['recordId'],'fixtureOnly':True}
        sources={'fixture':{'sha256':'f'*64}}
        with patch('adapters.science_v3_irb.adapter.authenticate_dependency',side_effect=auth),patch('adapters.science_v3_irb.adapter.check_pulse_dependencies'),patch('adapters.science_v3_irb.adapter.verify_sources',return_value=sources):
            plan=adapter.build_plan(request)
        self.assertEqual(plan['rabiLineage']['time']['targetDurationS'],20e-9)
        from adapters.f8 import _digest as intent_digest
        folder=self.root/'operation';folder.mkdir();self.write(folder/'adapter_intent.json',{'request':request,'requestHash':intent_digest(request)})
        with patch('adapters.science_v3_irb.execute_worker.PROJECT',self.root),patch('adapters.science_v3_irb.policy.verify_sources',return_value=sources),patch('adapters.science_v3_irb.timing.authenticate_dependency',side_effect=auth):
            self.assertEqual(validate_admission(plan,folder),request)
            plan['rabiLineage']['time']['targetDurationS']=20.25e-9
            with self.assertRaisesRegex(ValueError,'lineage changed'):validate_admission(plan,folder)


if __name__=='__main__':unittest.main()
