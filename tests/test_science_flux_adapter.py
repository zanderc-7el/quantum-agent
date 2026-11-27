from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile
from tests.test_science_flux import route,Provider
from tests.test_science_flux_plans import parameters
ROOT=Path(__file__).resolve().parents[1]

class AdapterContracts(unittest.TestCase):
    def base(self):
        p=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'));p['resources']={'Qfixture':p['resources']['Q11']}
        return p
    def test_disabled_profile_precise_missing_provider_and_no_bias_defaults(self):
        from adapters.science_flux_adapter import ScienceFluxAdapter,build_flux_profile
        p=build_flux_profile(ROOT,self.base());adapter=ScienceFluxAdapter(ROOT,p)
        self.assertFalse(p['enabled']);self.assertFalse(adapter.capabilities()['procedures']['ro_flux_scan']['available'])
        self.assertIn('biasRoutes.Qfixture',str(adapter.capabilities()))
        p['biasRoutes']={'Qfixture':route()};adapter=ScienceFluxAdapter(ROOT,p)
        self.assertIn('provider',str(adapter.capabilities()))
        self.assertNotIn('bias_values_v',adapter.parameter_defaults('ro_flux_scan'))
        self.assertNotIn('drive_amp',adapter.parameter_schema('ro_flux_scan'))
        self.assertEqual(adapter.normalize_candidate('sq_flux_point_recheck',{'readout_hz':7e9}),{})
    def test_no_client_proof_can_dispatch_without_registered_provider(self):
        from adapters.science_flux_adapter import ScienceFluxAdapter,build_flux_profile
        p=build_flux_profile(ROOT,self.base());p['biasRoutes']={'Qfixture':route()};a=ScienceFluxAdapter(ROOT,p)
        with self.assertRaisesRegex(Exception,'provider'):
            a.build_plan({'operationId':'test','procedureId':'ro_flux_scan','resourceId':'Qfixture',
                'parameters':parameters(),'scienceDependencies':{'assessment':{'executionEligible':True}}})

    def test_actual_wrapper_bytes_exact_admission_and_plan_tamper(self):
        from adapters.science_flux_adapter import ScienceFluxAdapter,build_flux_profile,validate_admission
        from adapters.science_runtime.policy import digest
        from adapters.f8 import _digest
        class BeforeSpawn(BaseException):pass
        p=self.base();r=route();r['deviceKey']='device:'+p['instrument']['host']+':'+str(p['instrument']['port']);p['biasRoutes']={'Qfixture':r}
        p=build_flux_profile(ROOT,p);p['enabled']=True;a=ScienceFluxAdapter(ROOT,p)
        request={'operationId':'fixture-wrapper/measure','runId':'fixture-run','nodeId':'fixture-node','attemptId':'fixture-attempt',
            'resourceId':'Qfixture','procedureId':'ro_flux_scan','leaseToken':'fixture-no-real-lease','configurationBinding':{},
            'parameters':parameters(),'parameterVersions':{},'explicitParameterKeys':[]}
        proof={'schema':'qcal-science-dependencies/1','policyVersion':'fixture','kind':'flux',
            'scope':{'workflowId':request['runId'],'resourceId':'Qfixture'},'inputs':{},
            'rootReferences':[{'role':'cavity_seed','recordId':'fixture-source'}],
            'nodes':[{'recordId':'fixture-source'}],'edges':[{'role':'cavity_seed','toRecordId':'fixture-source'}]}
        proof['closureSha256']=digest(proof);proof['assessment']={'executionEligible':True,'consumerSha256':digest(request)}
        request['scienceDependencies']=proof
        with tempfile.TemporaryDirectory() as temporary:
            a.operations=Path(temporary)
            with patch('adapters.science_flux_adapter.provider_for',return_value=Provider), \
                    patch('adapters.science_flux_adapter.verify_sources',return_value={'testOnlySourceContract':True}), \
                    patch.object(ScienceFluxAdapter,'authenticate_dependency',return_value={'fixtureOnly':True}), \
                    patch.object(a,'preflight',return_value={'ready':True}), \
                    patch('adapters.arclight.subprocess.Popen',side_effect=BeforeSpawn):
                with self.assertRaises(BeforeSpawn):a.execute(request,lambda e:None,lambda:False)
                folder=a._operation_dir(request['operationId']);plan=json.loads((folder/'plan.json').read_text(encoding='utf-8'))
                intent=json.loads((folder/'adapter_intent.json').read_text(encoding='utf-8'))
                self.assertEqual(intent['requestHash'],_digest(request));self.assertNotEqual(intent['requestHash'],plan['requestHash'])
                self.assertEqual(validate_admission(ROOT,plan,folder),request)
                changed=deepcopy(plan);changed['rows'][1]['frequencyHz']+=1
                with self.assertRaises(ValueError):validate_admission(ROOT,changed,folder)
                changed=deepcopy(plan);changed['hardwareLocks']=changed['hardwareLocks'][:2]
                with self.assertRaises(ValueError):validate_admission(ROOT,changed,folder)
                changed=deepcopy(plan);changed['profileSnapshot']['enabled']=False
                with self.assertRaises(ValueError):validate_admission(ROOT,changed,folder)
            # Removing test-only provider injection rejects the same request;
            # no caller-supplied plan or proof can register a measurement getter.
            with self.assertRaisesRegex(Exception,'provider'):validate_admission(ROOT,plan,folder)

if __name__=='__main__':unittest.main()
