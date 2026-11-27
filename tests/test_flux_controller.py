"""Flux must remain an explicit bounded manual task, with no bias ledger commit."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack
import json
import hashlib
from backend.common import APIError
from tests.test_controller import make_fixture

class FluxControllerContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.c,self.fake=make_fixture(Path(self.tmp.name));self.addCleanup(self.c.close)
    def fixture(self):
        from adapters.science_flux.contracts import POLICY
        self.fake.profile['scienceFluxPolicy']=POLICY
        self.fake.profile['parameterBounds']['bias_values_v']={'type':'numberArray','minItems':1,'maxItems':21,'min':-.1,'max':.1,'unique':True}
        self.fake.capabilities=lambda:{'procedures':{'ro_flux_scan':{'available':True}}}
        self.fake.validation_parameter_keys=lambda:{'readout_hz','bias_values_v'}
        self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        return {'name':'fixture explicit flux','executionProfile':'science-flux','mode':'real','policy':'strict',
                'resourceIds':['Q11'],'manualProcedureId':'ro_flux_scan','manualParameters':{'bias_values_v':[-.1,0,.1]},
                'manualScienceReferences':[{'role':'cavity_seed','recordId':'source-readout'}]}
    def test_optin_creates_single_manual_node_with_frozen_scientific_gates(self):
        data=self.fixture();wf=self.c.create(data)
        self.assertEqual(wf['executionProfile'],'science-flux')
        self.assertEqual([n['procedureId'] for n in wf['nodes']],['ro_flux_scan'])
        self.assertEqual(wf['gatesVersion'],'science-flux-measured-restore-gates/1')
        with self.c.store.connect() as rt:p,_=self.c._parameters(rt,wf,wf['nodes'][0])
        self.assertEqual(p['bias_values_v'],[-.1,0,.1]);self.assertEqual(self.fake.calls,[])
        from backend.catalog import PARAMETERS
        self.assertEqual(PARAMETERS['ro_flux_scan'][1],[])
        self.assertEqual(PARAMETERS['sq_flux_point_recheck'][1],[])
    def test_omitted_profile_and_autopilot_cannot_open_flux_tasks(self):
        data=self.fixture()
        for change in ({'executionProfile':None},{'executionProfile':'science-v3'},{'policy':'autopilot'}):
            with self.subTest(change=change),self.assertRaises(APIError):self.c.create({**data,**change})
        self.assertEqual(self.c.list(),[]);self.assertEqual(self.fake.calls,[])
    def test_number_arrays_reject_boolean_nonfinite_duplicate_and_outside(self):
        data=self.fixture()
        for axis in ([True],[float('nan')],[.2],[0,0],[],[0]*22,'[0]'):
            with self.subTest(axis=axis),self.assertRaises(APIError):self.c.create({**data,'manualParameters':{'bias_values_v':axis}})
        self.assertEqual(self.c.list(),[])
    def test_client_point_proof_is_never_accepted(self):
        data=self.fixture()
        with self.assertRaises(APIError) as error:self.c.create({**data,'fluxPointEvidence':{'accepted':True}})
        self.assertEqual(error.exception.code,'SCIENCE_CLIENT_PROOF_FORBIDDEN')
        self.assertEqual(self.c.list(),[])

    def test_detached_catalog_reports_missing_route_without_touching_legacy_profile(self):
        import json
        root=Path(__file__).resolve().parents[1]
        base=json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf8'))
        manifest=self.c.root/'adapters/science_flux/source_manifest.json';manifest.parent.mkdir(parents=True);manifest.write_text('[]')
        # All runtime source checks stay read-only; the temp fixture has no runner.
        binding={'profile':base,'resources':[{'id':'Q11','label':'Q11'}],'source':{},'digest':'fixture'}
        before=deepcopy(base)
        with patch.object(self.c.workspace.chips,'resolve_binding',return_value=binding),patch('adapters.science_flux_adapter.sha',return_value='fixture-runner'):
            catalog=self.c.science_catalog(chip_version_id='chip',deployment_version_id='dep',resource_id='Q11',execution_profile='science-flux')
        self.assertEqual(catalog['executionProfile'],'science-flux')
        self.assertEqual({m['procedureId'] for m in catalog['manualMethods']},{'ro_flux_scan','sq_flux_point_recheck'})
        self.assertTrue(all(m['capability']=='unavailable' for m in catalog['manualMethods']))
        self.assertIn('biasRoutes.Q11',catalog['preflight']['missingFields']['Q11'])
        self.assertEqual(base,before);self.assertEqual(self.fake.calls,[])

    def test_exact_flux_adapter_uses_one_lease_two_effects_and_never_commits_bias_frequencies(self):
        """Actual planner + injected transports; synthetic ADC, no source eligibility."""
        from backend.common import digest,now,dumps
        from backend.workspace import device_key
        from adapters.science_flux_adapter import ScienceFluxAdapter,build_flux_profile
        root=Path(__file__).resolve().parents[1]
        fixture=json.loads((root/'tests/fixtures/science_flux_numeric_result.json').read_text(encoding='utf8'))
        self.assertTrue(fixture['neverAcquiredFromHardware'])
        base=json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf8'))
        route=deepcopy(fixture['selectionResult']['provenance']['scienceFlux']['route'])
        route.update(resourceId='Q11',deviceKey=device_key(base))
        base['biasRoutes']={'Q11':route};base['resources']['Q11'].update(fixture['plan']['parameters'])
        profile=build_flux_profile(root,base);profile['connectionConfirmedAt']=now()
        manifest=self.c.root/profile['sourceManifest'];manifest.parent.mkdir(parents=True);manifest.write_text('[]')
        native=ScienceFluxAdapter(self.c.root,profile)
        self.c.adapter=native;self.c.profile=native.profile;self.c._physical_adapter=True
        self.c.adapter_id=profile['id'];self.c.adapter_profile_hash=self.c._adapter_fingerprint();observed=[]
        def resolve(rt,**kwargs):
            request=kwargs['consumer'];proof={'schema':'qcal-science-dependencies/1','policyVersion':'science-dependency-policy/1',
                'kind':'measurement','scope':kwargs['scope'],'inputs':{},'rootReferences':[{'role':'cavity_seed','recordId':'fixture-source'}],
                'nodes':[{'recordId':'fixture-source','procedureId':'ro_resonator_survey'}],
                'edges':[{'role':'cavity_seed','toRecordId':'fixture-source','fromRecordId':None}]}
            proof['closureSha256']=digest(proof)
            proof['assessment']={'executionEligible':True,'consumerSha256':digest({k:v for k,v in request.items() if k!='scienceDependencies'})}
            return proof
        def execute(adapter,request,emit,cancel):
            plan=adapter.build_plan(request)
            with self.c.store.connect() as rt:observed.append((deepcopy(request),plan,rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0]))
            result=deepcopy(fixture['selectionResult' if len(observed)==1 else 'verificationResult'])
            result.update(sourceRunId=request['operationId'],acquiredAt=now(),parameters=plan['parameters'])
            flux=result['provenance']['scienceFlux'];flux.update(route=route,routeSha256=digest(route),sourceClosure=plan['validationEvidence']['closureSha256'])
            context=flux['biasContext'];context.update(resourceId='Q11',routeSha256=digest(route),operationId=request['operationId'])
            context['sha256']=digest({k:v for k,v in context.items() if k!='sha256'})
            result['provenance'].update(scienceDependencies=plan['validationEvidence'],operationId=request['operationId'])
            artifacts=[]
            for index,point in enumerate(flux['points']):
                path=self.c.root/'data/fixture-flux'/str(len(observed))/f'cut-{index}.npz';path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(('explicit synthetic ADC '+request['operationId']+str(index)).encode())
                sha=hashlib.sha256(path.read_bytes()).hexdigest();point.update(rawSha256=sha,rawPath=str(path))
                point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
                artifacts.append({'id':digest(str(path)),'name':path.name,'kind':'raw','localPath':str(path),'sha256':sha})
            result['artifacts']=artifacts;return result
        with ExitStack() as stack:
            stack.enter_context(patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=resolve))
            stack.enter_context(patch('adapters.science_flux_adapter.verify_sources',return_value={}))
            stack.enter_context(patch('adapters.science_flux_adapter.provider_for',return_value=type('FixtureProvider',(),{'lock_names':('fixture-meter',)})))
            stack.enter_context(patch.object(ScienceFluxAdapter,'authenticate_dependency',return_value={'fixtureOnly':True}))
            stack.enter_context(patch.object(ScienceFluxAdapter,'preflight',return_value={'ready':True,'staticReady':True,'hardwareConnected':False}))
            stack.enter_context(patch.object(ScienceFluxAdapter,'execute',execute))
            stack.enter_context(patch.object(self.c,'hardware_ownership',return_value={'verified':True,'owners':[]}))
            wf=self.c.create({'name':'synthetic flux lifecycle','mode':'real','policy':'strict','resourceIds':['Q11'],
                'executionProfile':'science-flux','manualProcedureId':'ro_flux_scan','manualParameters':{
                    key:fixture['plan']['parameters'][key] for key in ('bias_values_v','readout_frequencies_hz')},
                'manualScienceReferences':[{'role':'cavity_seed','recordId':'fixture-source'}]})
            node=wf['nodes'][0]['id']
            wf=self.c.action(wf['id'],{'action':'run_step','nodeId':node,'operationId':'first','expectedRevision':wf['revision']})
            self.c.run_workflow(wf['id']);wf=self.c.get(wf['id']);self.assertEqual(wf['engineState'],'waiting_hardware')
            wf=self.c.action(wf['id'],{'action':'confirm_hardware','operationId':'fixture-confirm','expectedRevision':wf['revision']})
            wf=self.c.action(wf['id'],{'action':'run_step','nodeId':node,'operationId':'fixture-permit','expectedRevision':wf['revision']})
            self.c.run_workflow(wf['id']);wf=self.c.get(wf['id'])
            self.assertEqual(wf['engineState'],'awaiting_acceptance',wf.get('events'))
            wf=self.c.action(wf['id'],{'action':'approve','nodeId':node,'operationId':'accept','expectedRevision':wf['revision']})
        self.assertEqual(wf['status'],'completed');self.assertEqual(len(observed),2)
        self.assertTrue(all(lease==1 for _,_,lease in observed));self.assertNotEqual(observed[0][0]['operationId'],observed[1][0]['operationId'])
        self.assertEqual(observed[0][1]['parameters'],observed[1][1]['parameters'])
        self.assertEqual(wf['reservedShots'],sum(p['estimated_total_shots'] for _,p,_ in observed))
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],2)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM parameters').fetchone()[0],0)

if __name__=='__main__':unittest.main()
