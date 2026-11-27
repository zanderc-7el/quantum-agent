"""Actual wrapper request bytes, no process spawn or instrument connection."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.test_science_flux_commanded import route,confirmation
from tests.test_science_flux_plans import parameters
ROOT=Path(__file__).resolve().parents[1]

class AdapterContracts(unittest.TestCase):
    def test_profile_admission_and_actual_wrapper_bind_authorized_start_end(self):
        from adapters.science_flux_adapter import ScienceFluxAdapter,build_flux_profile,validate_admission
        from adapters.science_flux.contracts import command_authorization,COMMAND_POLICY
        from adapters.science_runtime.policy import digest
        p=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf8'));p['resources']={'Qfixture':p['resources']['Q11']}
        r=route();r['deviceKey']='device:'+p['instrument']['host']+':'+str(p['instrument']['port']);p['biasRoutes']={'Qfixture':r}
        p=build_flux_profile(ROOT,p);self.assertFalse(p['enabled']);p['enabled']=True
        adapter=ScienceFluxAdapter(ROOT,p)
        self.assertTrue(adapter.capabilities()['procedures']['ro_flux_scan']['available'])
        request={'operationId':'TEST_ONLY-wrapper/measure','runId':'TEST_ONLY-run','nodeId':'TEST_ONLY-node','attemptId':'TEST_ONLY-attempt',
            'resourceId':'Qfixture','procedureId':'ro_flux_scan','leaseToken':'TEST_ONLY-not-physical','configurationBinding':{},
            'parameters':parameters(),'parameterVersions':{},'explicitParameterKeys':[]}
        request['biasCommandAuthorization']=command_authorization(r,request['operationId'],start_confirmation=confirmation(request,r))
        proof={'schema':'qcal-science-dependencies/1','policyVersion':'TEST_ONLY','kind':'flux',
            'scope':{'workflowId':request['runId'],'resourceId':'Qfixture'},'inputs':{},
            'rootReferences':[{'role':'cavity_seed','recordId':'TEST_ONLY-source'}],
            'nodes':[{'recordId':'TEST_ONLY-source'}],'edges':[{'role':'cavity_seed','toRecordId':'TEST_ONLY-source'}]}
        proof['closureSha256']=digest(proof);proof['assessment']={'executionEligible':True,'consumerSha256':digest(request)}
        request['scienceDependencies']=proof
        class BeforeSpawn(BaseException):pass
        with tempfile.TemporaryDirectory() as temporary:
            adapter.operations=Path(temporary)
            with patch('adapters.science_flux_adapter.verify_sources',return_value={'TEST_ONLY-source':True}), \
                 patch.object(ScienceFluxAdapter,'authenticate_dependency',return_value={'fixtureOnly':True}), \
                 patch.object(adapter,'preflight',return_value={'ready':True}), \
                 patch('adapters.arclight.subprocess.Popen',side_effect=BeforeSpawn):
                with self.assertRaises(BeforeSpawn):adapter.execute(request,lambda e:None,lambda:False)
                folder=adapter._operation_dir(request['operationId']);plan=json.loads((folder/'plan.json').read_text(encoding='utf8'))
                self.assertEqual(plan['policy'],COMMAND_POLICY);self.assertEqual(plan['maximumReadbackSnapshots'],0)
                self.assertEqual(plan['biasCommandAuthorization'],request['biasCommandAuthorization'])
                from adapters.f8 import _digest
                intent=json.loads((folder/'adapter_intent.json').read_text(encoding='utf8'))
                self.assertEqual(digest(plan['profileSnapshot']),plan['profileHash'])
                self.assertEqual(_digest(plan['profileSnapshot']),intent['profileHash'])
                self.assertIs(plan['profileSnapshot']['enabled'],True)
                self.assertEqual(validate_admission(ROOT,plan,folder),request)
                for path,value in [('endSettingV',0),('startSettingV',0),('operationId','different')]:
                    bad=deepcopy(plan);bad['biasCommandAuthorization'][path]=value
                    with self.subTest(path=path),self.assertRaises(ValueError):validate_admission(ROOT,bad,folder)
                bad=deepcopy(request);bad.pop('biasCommandAuthorization')
                with self.assertRaises(Exception):adapter.build_plan(bad)

if __name__=='__main__':unittest.main()
