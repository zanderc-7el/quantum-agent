"""R23 normal controller entry; temporary DB and no hardware executor."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from backend.common import APIError,digest,now
from contextlib import ExitStack
from tests.test_controller import make_fixture
from tests.test_science_repeat5 import parameters

ROOT=Path(__file__).resolve().parents[1]
METHOD='sq_shape_rabi_amplitude_repeat5'

class ControllerContracts(unittest.TestCase):
    def setUp(self):
        from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter,build_repeat5_profile
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.c,self.fake=make_fixture(Path(self.temp.name));self.addCleanup(self.c.close)
        base=json.loads((ROOT/'adapters/arclight_profile.json').read_text('utf8'))
        profile=build_repeat5_profile(ROOT,base);self.native=ScienceRepeat5Adapter(ROOT,profile)
        self.fake.profile.update({k:deepcopy(profile[k]) for k in ('sciencePolicy','scienceIRBPolicy','scienceRepeat5Policy','parameterBounds')})
        self.fake.capabilities=lambda:self.native.capabilities()
        self.fake.parameter_schema=self.native.parameter_schema
        self.fake.validation_parameter_keys=self.native.validation_parameter_keys
        self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        self.data={'name':'TEST ONLY repeat5','mode':'real','policy':'strict','resourceIds':['Q11'],
            'executionProfile':'science-shape-repeat5','manualProcedureId':METHOD,
            'manualParameters':{k:v for k,v in parameters().items() if k in self.native.parameter_schema(METHOD)},
            'manualScienceReferences':[{'role':r,'recordId':r} for r in ('f01','readout')]}

    def test_normal_explicit_create_freezes_new_gate_version_and_finite_parameters(self):
        wf=self.c.create(self.data)
        self.assertEqual(wf['executionProfile'],'science-shape-repeat5')
        self.assertEqual(wf['gatesVersion'],'science-shape-repeat5-chain-gates/1')
        self.assertEqual([n['procedureId'] for n in wf['nodes']],[METHOD])
        self.assertEqual(wf['nodes'][0]['parameterOverrides']['pulse_length_s'],660e-9)
        self.assertFalse(self.fake.calls)
        self.assertFalse(self.native.profile['enabled'])

    def test_old_profile_and_user_repetition_are_not_implicitly_upgraded(self):
        for changes in ({'executionProfile':'science-v3'}, {'manualParameters':{'gate_repeats':5}},
                        {'manualParameters':{'amp_stop':1.1}}, {'manualParameters':{'pulse_length_s':float('nan')}}):
            with self.subTest(changes=changes),self.assertRaises(APIError):self.c.create({**self.data,**changes})
        self.assertEqual(self.c.list(),[])

    def test_joint_budget_includes_independently_fixed_five_gate_rows_and_exact_type(self):
        from adapters.science_shape_repeat5.plans import build_repeat5,POLICY
        from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter
        self.c.adapter=self.native;self.c.profile=self.native.profile
        p,rows=build_repeat5(parameters());plan={'adapter':'science-repeat5','policy':POLICY,'parameters':p,
            'estimated_total_shots':len(rows)*p['shots']}
        request={'operationId':'measure','nodeId':'node','procedureId':METHOD,'parameters':p}
        def build(probe):
            vp,vr=build_repeat5(probe['parameters']);self.assertEqual(sum(r['condition']=='verification_candidate' for r in vr),2)
            return {**plan,'parameters':vp,'estimated_total_shots':len(vr)*vp['shots']}
        with patch.object(self.c,'_science_request_dependencies'),patch.object(self.native,'build_plan',side_effect=build):
            budget=self.c._selection_budget({'maxTotalShots':100000,'reservedShots':0,'nodes':[{'id':'node'}]},request,plan)
        self.assertEqual(budget['jointShots'],16600)
        class Foreign(ScienceRepeat5Adapter):pass
        self.c.adapter=Foreign(ROOT,self.native.profile)
        with self.assertRaises(APIError):self.c._selection_budget({'maxTotalShots':100000},request,plan)

    def test_two_effects_one_lease_and_current_source_required_for_acceptance(self):
        """Native-generated numeric receipts with explicitly injected transport.

        This checks controller orchestration, not physical source authenticity;
        the separate native test exercises full raw/file/source authentication.
        """
        from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter,build_repeat5_profile
        envelope=json.loads((ROOT/'tests/fixtures/r23_repeat5_native_results.json').read_text('utf8'))
        self.assertTrue(envelope['fixtureOnly']);self.assertFalse(envelope['physicalAccepted'])
        templates={v['stage']:v['result'] for v in envelope['results']}
        base=json.loads((ROOT/'adapters/arclight_profile.json').read_text('utf8'));base['resources']['Q11'].update(parameters())
        profile=build_repeat5_profile(ROOT,base);profile['connectionConfirmedAt']=now()
        manifest=self.c.root/profile['sourceManifest'];manifest.parent.mkdir(parents=True,exist_ok=True);manifest.write_text('[]')
        self.c.adapter=ScienceRepeat5Adapter(self.c.root,profile);self.c.profile=self.c.adapter.profile
        self.c._physical_adapter=True;self.c.adapter_id=profile['id'];self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        observations=[]
        def resolve(rt,**kwargs):
            request=kwargs['consumer'];p=parameters()
            proof={'schema':'qcal-science-dependencies/1','policyVersion':'science-dependency-policy/1','kind':'measurement',
                'scope':kwargs['scope'],'inputs':{},'rootReferences':[{'role':r,'recordId':r} for r in ('f01','readout')],
                'nodes':[{'recordId':r,'parameters':p,'resolvedOutputs':{'candidateParameters':{'drive_hz':p['drive_hz']}}} for r in ('f01','readout')],
                'edges':[{'role':r,'toRecordId':r} for r in ('f01','readout')]}
            proof['closureSha256']=digest(proof);proof['assessment']={'executionEligible':True,'consumerSha256':digest({k:v for k,v in request.items() if k!='scienceDependencies'})}
            return proof
        def execute(adapter,request,emit,cancel):
            import hashlib
            plan=adapter.build_plan(request)
            with self.c.store.connect() as rt:observations.append((deepcopy(request),plan,rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0]))
            result=deepcopy(templates['verification' if len(observations)==2 else 'selection'])
            result.update(parameters=plan['parameters'],sourceRunId=request['operationId'],acquiredAt=now())
            result['provenance'].update(operationId=request['operationId'],scienceDependencies=plan['validationEvidence'])
            path=self.c.root/'data'/(str(len(observations))+'-fixture-only-raw.npz');path.write_text('synthetic transport '+request['operationId'])
            result['artifacts']=[{'id':digest(str(path)),'name':'raw_scan.npz','kind':'raw','localPath':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]
            return result
        with ExitStack() as stack:
            stack.enter_context(patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=resolve))
            stack.enter_context(patch('adapters.science_shape_repeat5.adapter.verify_sources',return_value={}))
            stack.enter_context(patch.object(ScienceRepeat5Adapter,'authenticate_dependency',return_value={'fixtureOnly':True}))
            stack.enter_context(patch.object(ScienceRepeat5Adapter,'preflight',return_value={'ready':True,'staticReady':True,'hardwareConnected':False}))
            stack.enter_context(patch.object(ScienceRepeat5Adapter,'execute',execute))
            stack.enter_context(patch.object(self.c,'hardware_ownership',return_value={'verified':True,'owners':[]}))
            wf=self.c.create(self.data);node=wf['nodes'][0]['id']
            wf=self.c.action(wf['id'],{'action':'run_step','nodeId':node,'operationId':'prepare','expectedRevision':wf['revision']})
            self.c.run_workflow(wf['id']);wf=self.c.get(wf['id']);self.assertEqual(wf['engineState'],'waiting_hardware')
            wf=self.c.action(wf['id'],{'action':'confirm_hardware','operationId':'TEST_ONLY-confirm','expectedRevision':wf['revision']})
            wf=self.c.action(wf['id'],{'action':'run_step','nodeId':node,'operationId':'TEST_ONLY-start','expectedRevision':wf['revision']})
            self.c.run_workflow(wf['id']);wf=self.c.get(wf['id'])
            self.assertEqual(wf['engineState'],'awaiting_acceptance',wf['nodes'][0].get('result'))
            with patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=APIError('STALE','source changed')):
                with self.assertRaises(APIError):self.c.action(wf['id'],{'action':'approve','nodeId':node,'operationId':'stale','expectedRevision':wf['revision']})
            wf=self.c.action(wf['id'],{'action':'approve','nodeId':node,'operationId':'accept','expectedRevision':wf['revision']})
        self.assertEqual(len(observations),2);self.assertTrue(all(o[2]==1 for o in observations))
        self.assertNotIn('verify_shape_pi_amp',observations[0][1]['parameters'])
        self.assertEqual(observations[1][1]['parameters']['verify_shape_pi_amp'],templates['selection']['candidateParameters']['shape_pi_amp'])
        self.assertTrue(all(len(r['gates'])==5 for _,p,_ in observations for r in p['rows']))
        self.assertEqual(wf['nodes'][0]['result']['candidateParameters'],templates['selection']['candidateParameters'])
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],2)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)
