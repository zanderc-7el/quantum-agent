"""Explicit third-batch controller contracts; temporary DB, never instruments."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack

from backend.common import APIError,digest,now
from tests.test_controller import make_fixture

ROOT=Path(__file__).resolve().parents[1]
METHODS=('sq_drag_half_scan','sq_drag_irb')


class ScienceIRBControllerContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.c,self.fake=make_fixture(Path(self.temp.name));self.addCleanup(self.c.close)

    def fixture(self):
        from adapters.science_v3_irb.adapter import ScienceIRBAdapter,build_irb_profile
        base=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf8'))
        profile=build_irb_profile(ROOT,base);native=ScienceIRBAdapter(ROOT,profile)
        self.fake.profile.update(sciencePolicy=profile['sciencePolicy'],scienceIRBPolicy=profile['scienceIRBPolicy'],
                                 parameterBounds=deepcopy(profile['parameterBounds']))
        self.fake.capabilities=lambda:{'procedures':{key:{'available':True} for key in METHODS}}
        self.fake.parameter_schema=native.parameter_schema
        self.fake.validation_parameter_keys=native.validation_parameter_keys
        self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        return native,{'name':'explicit half IRB','mode':'real','policy':'strict','resourceIds':['Q11'],
            'executionProfile':'science-v3-irb','manualProcedureId':'sq_drag_irb',
            'manualParameters':{'rb_lengths':[1,4,16,64,128],'rb_master_seed':42},
            'manualScienceReferences':[{'role':role,'recordId':'source-'+role} for role in ('pi01','half01','drag','anharmonicity','readout')]}

    def test_third_profile_is_explicit_single_method_and_retains_array_parameters(self):
        _,data=self.fixture();wf=self.c.create(data)
        self.assertEqual(wf['executionProfile'],'science-v3-irb')
        self.assertEqual([node['procedureId'] for node in wf['nodes']],['sq_drag_irb'])
        self.assertEqual(wf['gatesVersion'],'science-drag-xhalf-irb-gates/1')
        with self.c.store.connect() as rt:parameters,_=self.c._parameters(rt,wf,wf['nodes'][0])
        self.assertEqual(parameters['rb_lengths'],[1,4,16,64,128])
        self.assertEqual(parameters['rb_master_seed'],42)
        self.assertNotIn('sq_drag_scan',wf['gates'])
        self.assertEqual(self.fake.calls,[])

    def test_array_type_bounds_and_explicit_profile_cannot_be_bypassed(self):
        _,data=self.fixture()
        for lengths in ([1,4,16,64], [1,4,16,16,128], [1,4,16,64,513], [True,4,16,64,128], '[1,4,16,64,128]'):
            with self.subTest(lengths=lengths),self.assertRaises(APIError):
                self.c.create({**data,'manualParameters':{'rb_lengths':lengths}})
        with self.assertRaises(APIError):self.c.create({**data,'executionProfile':'science-v3'})
        self.assertEqual(self.c.list(),[])

    def test_irb_budget_requires_exact_adapter_and_two_complete_fresh_seed_campaigns(self):
        native,_=self.fixture();self.c.adapter=native;self.c.profile=native.profile
        from adapters.science_v3_irb.plans import normalize_parameters,POLICY
        parameters,_=normalize_parameters({**{k:v for k,v in native.profile['resources']['Q11'].items()
            if k in native.validation_parameter_keys()},'pulse_compatibility':'native_science_v3_fixed_duration_drag_segmented_carrier_v1',
            'anharmonicity_hz':-200e6,'rb_master_seed':42})
        request={'operationId':'measure','nodeId':'node','procedureId':'sq_drag_irb','parameters':parameters}
        plan={'adapter':'science-v3-irb','policy':POLICY,'parameters':parameters,'estimated_total_shots':416000}
        calls=[]
        def build(probe):calls.append(deepcopy(probe));return {**plan,'parameters':probe['parameters']}
        with patch.object(self.c,'_science_request_dependencies'),patch.object(native,'build_plan',side_effect=build):
            budget=self.c._selection_budget({'maxTotalShots':1000000,'reservedShots':0,'nodes':[{'id':'node'}]},request,plan)
        self.assertEqual(budget['jointShots'],832000)
        self.assertNotEqual(calls[0]['parameters']['rb_master_seed'],42)
        self.assertEqual({k:v for k,v in calls[0]['parameters'].items() if k!='rb_master_seed'},
                         {k:v for k,v in parameters.items() if k!='rb_master_seed'})
        class Unreviewed(type(native)):pass
        self.c.adapter=Unreviewed(ROOT,native.profile)
        with self.assertRaises(APIError) as error:self.c._selection_budget({'maxTotalShots':1000000},request,plan)
        self.assertEqual(error.exception.code,'VERIFICATION_BUDGET_UNKNOWN')

    def test_exact_adapter_two_effects_one_lease_fresh_seed_and_human_acceptance(self):
        """Real bounded adapter planning, injected source auth/campaign/transport.

        The numerical receipt is an explicit synthetic fixture. This test cannot
        create a production source or establish physical gate fidelity.
        """
        from adapters.science_v3_irb.adapter import ScienceIRBAdapter,build_irb_profile
        from adapters.science_v3_irb.plans import normalize_parameters,identity
        from tests.science_irb_quality_fixture import result_fixture
        template=result_fixture(self)
        base=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf8'))
        base['resources']['Q11'].update(template['parameters'])
        profile=build_irb_profile(ROOT,base);profile['connectionConfirmedAt']=now()
        manifest=self.c.root/profile['sourceManifest'];manifest.parent.mkdir(parents=True,exist_ok=True);manifest.write_text('[]',encoding='utf8')
        self.c.adapter=ScienceIRBAdapter(self.c.root,profile);self.c.profile=self.c.adapter.profile;self.c._physical_adapter=True
        self.c.adapter_id=profile['id'];self.c.adapter_profile_hash=self.c._adapter_fingerprint();observed=[]
        def campaign(root,python,p):
            _,budget=normalize_parameters(p)
            return {'budget':budget,'rows':[{} for _ in range(budget['rows'])],
                'targetPulseIdentity':identity(p),'sha256':digest({'testOnly':True,'parameters':p})}
        def resolve(rt,**kwargs):
            request=kwargs['consumer'];proof=deepcopy(template['provenance']['scienceDependencies'])
            proof['scope']=kwargs['scope']
            for role in ('readout','anharmonicity'):
                proof['rootReferences'].append({'role':role,'recordId':role});proof['nodes'].append({'recordId':role})
            drag_id=next(ref['recordId'] for ref in proof['rootReferences'] if ref['role']=='drag')
            next(node for node in proof['nodes'] if node['recordId']==drag_id)['targetPulseIdentity']=identity(request['parameters'])
            proof.pop('closureSha256',None);proof['closureSha256']=digest(proof)
            proof['assessment']={'executionEligible':True,'consumerSha256':digest({k:v for k,v in request.items() if k!='scienceDependencies'})}
            return proof
        def execute(adapter,request,emit,cancel):
            import hashlib
            plan=adapter.build_plan(request)
            with self.c.store.connect() as rt:
                observed.append((deepcopy(request),plan,rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0]))
            out=deepcopy(template);out.update(dataKind='real',acquiredAt=now(),sourceRunId=request['operationId'],parameters=plan['parameters'])
            out['provenance'].update(scienceDependencies=deepcopy(plan['validationEvidence']),operationId=request['operationId'],sequenceManifestSha256=plan['campaign']['sha256'])
            irb=out['provenance']['scienceIRB'];irb['irb']['bootstrap']['rng_seed']=plan['parameters']['rb_master_seed']
            frozen=irb['reference']['pre'];frozen['rawSha256']=digest({'test-only-independent-PRE':request['operationId']})
            frozen['projection']['reference_raw_sha256']=frozen['rawSha256'];irb['irb']['projection']=deepcopy(frozen['projection'])
            frozen['sha256']=digest({k:v for k,v in frozen.items() if k!='sha256'})
            out['artifacts']=[]
            for name in ('raw_pre.npz','raw_rb.npz','raw_irb.npz','raw_post.npz','raw_scan.npz'):
                path=self.c.root/'data'/'fake-raw'/str(len(observed))/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('synthetic-controller-fixture '+request['operationId']+name,encoding='utf8')
                out['artifacts'].append({'id':digest(str(path)),'name':name,'kind':'raw','localPath':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            return out
        with ExitStack() as stack:
            stack.enter_context(patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=resolve))
            stack.enter_context(patch('adapters.science_v3_irb.adapter.verify_sources',return_value={}))
            stack.enter_context(patch('adapters.science_v3_irb.adapter.plan_native',side_effect=campaign))
            stack.enter_context(patch('adapters.science_v3_irb.adapter.inherited_rabi_lineage',return_value=template['provenance']['scienceIRB']['rabiLineage']))
            stack.enter_context(patch('adapters.science_v3_irb.adapter.check_pulse_dependencies'))
            stack.enter_context(patch.object(ScienceIRBAdapter,'authenticate_dependency',return_value={'fixtureOnly':True}))
            stack.enter_context(patch.object(ScienceIRBAdapter,'preflight',return_value={'ready':True,'staticReady':True,'hardwareConnected':False}))
            stack.enter_context(patch.object(ScienceIRBAdapter,'capabilities',return_value={'procedures':{m:{'available':True} for m in METHODS}}))
            stack.enter_context(patch.object(ScienceIRBAdapter,'execute',execute))
            stack.enter_context(patch.object(self.c,'hardware_ownership',return_value={'verified':True,'owners':[]}))
            wf=self.c.create({'name':'temporary synthetic controller IRB','mode':'real','policy':'strict','resourceIds':['Q11'],
                'executionProfile':'science-v3-irb','manualProcedureId':'sq_drag_irb'})
            node=wf['nodes'][0]['id']
            wf=self.c.action(wf['id'],{'action':'run_step','nodeId':node,'operationId':'first-request','expectedRevision':wf['revision']})
            self.c.run_workflow(wf['id']);wf=self.c.get(wf['id']);self.assertEqual(wf['engineState'],'waiting_hardware')
            wf=self.c.action(wf['id'],{'action':'confirm_hardware','operationId':'fixture-handover','expectedRevision':wf['revision']})
            wf=self.c.action(wf['id'],{'action':'run_step','nodeId':node,'operationId':'single-permit','expectedRevision':wf['revision']})
            self.c.run_workflow(wf['id']);wf=self.c.get(wf['id'])
            self.assertEqual(wf['engineState'],'awaiting_acceptance',wf.get('events'))
            with patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=APIError('STALE','changed source')):
                with self.assertRaises(APIError):self.c.action(wf['id'],{'action':'approve','nodeId':node,'operationId':'stale-accept','expectedRevision':wf['revision']})
            wf=self.c.action(wf['id'],{'action':'approve','nodeId':node,'operationId':'fresh-accept','expectedRevision':wf['revision']})
        self.assertEqual(wf['status'],'completed');self.assertEqual(len(observed),2)
        self.assertTrue(all(leases==1 for _,_,leases in observed))
        self.assertEqual(observed[0][1]['campaign']['targetPulseIdentity'],observed[1][1]['campaign']['targetPulseIdentity'])
        self.assertNotEqual(observed[0][0]['parameters']['rb_master_seed'],observed[1][0]['parameters']['rb_master_seed'])
        self.assertEqual(wf['nodes'][0]['result']['candidateParameters'],{})
        self.assertEqual(wf['reservedShots'],sum(plan['estimated_total_shots'] for _,plan,_ in observed))
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],2)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_attempts').fetchone()[0],1)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)
