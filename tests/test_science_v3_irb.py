"""Third-batch contracts; synthetic fixtures are never physical evidence."""
import unittest
from copy import deepcopy

class CampaignContracts(unittest.TestCase):
    def test_changed_native_numeric_runtime_rejects_before_acquisition(self):
        from unittest.mock import patch
        from adapters.science_v3_irb.runtime_contract import EXPECTED,check_runtime
        with patch('adapters.science_v3_irb.runtime_contract.read_versions',return_value={**EXPECTED,'scipy':'unverified'}):
            with self.assertRaisesRegex(ValueError,'differs'):check_runtime()
    def test_reviewed_methods_keep_profile_disabled_and_do_not_import_native_analysis(self):
        import json,sys
        from pathlib import Path
        from unittest.mock import patch
        from adapters.science_v3_irb.adapter import ScienceIRBAdapter,build_irb_profile
        root=Path(__file__).resolve().parents[1];base=json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        profile=build_irb_profile(root,base);adapter=ScienceIRBAdapter(root,profile)
        self.assertFalse(profile['enabled']);self.assertTrue(all(v['available'] is True for v in adapter.capabilities()['procedures'].values()))
        self.assertEqual(adapter.parameter_schema('sq_drag_half_scan')['points']['default'],21)
        for unused in ('capture_repeats','order_seed','drive_amp','drive_length_s'):
            self.assertNotIn(unused,adapter.parameter_schema('sq_drag_irb'))
            self.assertNotIn(unused,adapter.parameter_defaults('sq_drag_irb'))
        with patch('subprocess.Popen',side_effect=AssertionError('must not spawn')):
            with self.assertRaisesRegex(Exception,'enabled'):adapter.execute({},lambda e:None,lambda:False)
    def test_half_plan_keeps_exact_server_proof_consumer_identity(self):
        import json
        from pathlib import Path
        from unittest.mock import patch
        from adapters.science_v3_irb.adapter import ScienceIRBAdapter,build_irb_profile
        from adapters.science_v3_irb.half_scan import ROLES
        from adapters.science_runtime.policy import digest
        root=Path(__file__).resolve().parents[1];profile=build_irb_profile(root,json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf-8')))
        adapter=ScienceIRBAdapter(root,profile)
        request={'runId':'fixture-consumer','nodeId':'n','attemptId':'a','operationId':'fixture/measure','resourceId':next(iter(profile['resources'])),
            'procedureId':'sq_drag_half_scan','parameters':self.parameters(),'explicitParameterKeys':[],'parameterVersions':{},'outputDir':'ignored/controller/path'}
        proof={'schema':'qcal-science-dependencies/1','policyVersion':'fixture','kind':'measurement','scope':{'workflowId':request['runId'],'resourceId':request['resourceId']},
            'inputs':{},'nodes':[{'recordId':role,'parameters':self.parameters()} for role in ROLES],
            'rootReferences':[{'role':role,'recordId':role} for role in ROLES],
            'edges':[{'fromRecordId':'consumer','toRecordId':role,'role':role} for role in ROLES]}
        proof['closureSha256']=digest(proof);consumer_hash=digest(request)
        proof['assessment']={'executionEligible':True,'consumerSha256':consumer_hash};request['scienceDependencies']=proof
        original=deepcopy(request)
        # Authenticators are explicit unit stubs; no physical qualification is made.
        def authenticate(consumer,dependency):
            self.assertEqual(consumer['procedureId'],'sq_drag_half_scan')
            return {'status':'valid','consumerSha256':digest({k:v for k,v in consumer.items() if k!='scienceDependencies'}),
                    'dependencySha256':digest(dependency),'evidenceSha256':'f'*64}
        with patch.object(adapter,'authenticate_dependency',side_effect=authenticate),patch('adapters.science_v3_irb.adapter.check_pulse_dependencies'),patch('adapters.science_v3_irb.adapter.rabi_lineage',return_value={'fixtureOnly':True}):
            plan=adapter.build_plan(request)
            self.assertEqual(plan['requestIdentity']['procedureId'],'sq_drag_half_scan')
            self.assertEqual(plan['validationEvidence'],proof);self.assertEqual(request,original)
            self.assertTrue(all(a['consumerSha256']==consumer_hash for a in plan['nativeAttestations']))
            changed=deepcopy(request);changed['scienceDependencies']['assessment']['consumerSha256']='0'*64
            with self.assertRaisesRegex(Exception,'exact consumer'):adapter.build_plan(changed)
    def parameters(self):
        return {'drive_hz':4.86e9,'drive_amp':.3,'drive_length_s':20e-9,'readout_hz':7.01e9,'readout_amp':.4,
                'readout_width_s':2e-6,'xy_delay_s':100e-9,'xy_filter_mhz':4700,'xy_attenuation_db':3.75,
                'pulse_shape':'Gaussian','pulse_length_s':20e-9,'pulse_sigma_s':4e-9,'shape_pi_amp':.3,
                'drag_lambda1':.2,'anharmonicity_hz':-200e6,'shots':100,'acquisitions_per_point':2}
    def test_default_budgets_and_independent_seed(self):
        from adapters.science_v3_irb.plans import normalize_parameters,verification_parameters
        p,budget=normalize_parameters(self.parameters())
        self.assertEqual(budget['rows'],416);self.assertLess(budget['samples'],400_000_000)
        q=verification_parameters(p)
        self.assertNotEqual(p['rb_master_seed'],q['rb_master_seed'])
        self.assertEqual(p['drag_lambda1'],q['drag_lambda1'])
        self.assertEqual(p['rb_lengths'],q['rb_lengths'])
    def test_budget_never_silently_reduces_requested_rows_or_shots(self):
        from adapters.science_v3_irb.plans import normalize_parameters
        for change in ({'rb_sequences':100,'rb_captures':4},{'rb_lengths':[1,2,3]},
                       {'reference_captures':3},{'rb_target':'Y/2'},{'rb_lengths':[1,4,16,64,64]}):
            with self.subTest(change=change),self.assertRaises(ValueError):normalize_parameters({**self.parameters(),**change})
    def test_half_drag_scan_uses_actual_half_rotations_and_frozen_verification(self):
        import math
        from adapters.science_v3_irb.half_scan import build_half_scan,target_identity
        p,bundle=build_half_scan({**self.parameters(),'scan_axis_values':[-1.,-.5,0.,.5,1.],'verify_drag_lambda1':.2})
        diagnostic=[r for r in bundle['rows'] if r['condition']=='diagnostic']
        self.assertTrue(all(g['theta']==math.pi/2 for r in diagnostic for g in r['gates']))
        self.assertTrue(all(len(r['gates'])%6==0 for r in diagnostic))
        candidate=[r for r in bundle['rows'] if r['condition']=='verification_candidate']
        self.assertEqual(len(candidate),4);self.assertTrue(all(g['pulse']['lambda1']==.2 for r in candidate for g in r['gates']))
        selected=target_identity(p,.2)['identity']
        self.assertEqual(selected['rotationRad'],math.pi/2);self.assertEqual(selected['amplitude'],.15)
    def test_half_drag_requires_actual_zero_coefficient_baseline(self):
        from adapters.science_v3_irb.half_scan import build_half_scan
        with self.assertRaisesRegex(ValueError,'zero coefficient'):
            build_half_scan({**self.parameters(),'lambda_start':.1,'lambda_stop':.5,'points':21})

if __name__=='__main__':unittest.main()
