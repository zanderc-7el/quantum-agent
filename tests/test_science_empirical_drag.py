"""R19 mode/identity admission contracts. No database or instrument execution."""
from copy import deepcopy
from types import SimpleNamespace
import math
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from backend.common import APIError, digest
from backend.science_dependencies import ScienceDependencyResolver, _pulse_identities


class EmpiricalDragDependencies(unittest.TestCase):
    def proof(self, procedure, mode='empirical_beta'):
        roles=['pi01','half01','readout']+(['rabi_amplitude','rabi_time'] if procedure=='sq_drag_half_scan' else ['drag'])
        consumer={'runId':'fixture-run','resourceId':'Q11','procedureId':procedure,'parameters':{'drag_mode':mode}}
        proof={'scope':{'workflowId':'fixture-run','resourceId':'Q11'},
            'rootReferences':[{'role':role,'recordId':role} for role in roles],
            'nodes':[{'recordId':role,'reference':{'workflowId':'fixture-run','attemptId':'fixture-'+role}} for role in roles],
            'edges':[{'toRecordId':role,'role':role} for role in roles],
            'assessment':{'integrityIntact':True,'evidenceCurrent':True,'nodes':{role:{'controllerVerified':True} for role in roles}}}
        def authenticate(request, dependency):
            return {'status':'valid','consumerSha256':digest(request),'dependencySha256':digest(dependency),'evidenceSha256':'a'*64}
        bound=SimpleNamespace(_physical_adapter=True,adapter=SimpleNamespace(authenticate_dependency=authenticate))
        resolver=ScienceDependencyResolver(SimpleNamespace(for_workflow=lambda *_:bound))
        return resolver,proof,consumer

    def test_empirical_mode_removes_only_anharmonicity_requirement(self):
        for procedure in ('sq_drag_half_scan','sq_drag_irb'):
            resolver,proof,consumer=self.proof(procedure)
            resolver._execution(None,proof,consumer)
            self.assertTrue(proof['assessment']['executionEligible'])
            for removed in ('pi01','half01','readout','rabi_time' if procedure=='sq_drag_half_scan' else 'drag'):
                broken=deepcopy(proof);broken['rootReferences']=[r for r in broken['rootReferences'] if r['role']!=removed]
                with self.subTest(procedure=procedure,removed=removed),self.assertRaises(APIError):
                    resolver._execution(None,broken,consumer)

    def test_default_and_explicit_normalized_still_require_real_anharmonicity(self):
        for mode in ('normalized_alpha',None,'not-a-mode'):
            resolver,proof,consumer=self.proof('sq_drag_half_scan',mode)
            if mode is None:consumer['parameters'].pop('drag_mode')
            with self.subTest(mode=mode),self.assertRaises(APIError):resolver._execution(None,proof,consumer)

    def identity(self):
        value={'policy':'native_science_v3_empirical_beta_fixed_duration_v1','transition':'01','shape':'gaussian',
            'durationS':20e-9,'sigmaS':5e-9,'amplitude':.2,'frequencyHz':4.8e9,'phaseRad':0.,
            'rotationRad':math.pi/2,'lambda1':None,'anharmonicityHz':None,'bufferS':2e-9,
            'xySampleRateHz':4e9,'readoutContextHash':'b'*64,'dragMode':'empirical_beta','betaSeconds':-1e-9}
        return {'identity':value,'sha256':digest(value)}

    def test_signed_seconds_identity_does_not_invent_alpha_or_lambda(self):
        _pulse_identities({'targetPulseIdentity':self.identity()})
        for change in ({'betaSeconds':True},{'betaSeconds':float('nan')},{'betaSeconds':1.},
                       {'lambda1':0.},{'anharmonicityHz':-200e6},{'dragMode':'normalized_alpha'},
                       {'policy':'native_science_v3_fixed_duration_drag_segmented_carrier_v1'}):
            with self.subTest(change=change),self.assertRaises((APIError,ValueError)):
                binding=self.identity();binding['identity'].update(change);binding['sha256']=digest(binding['identity'])
                _pulse_identities({'targetPulseIdentity':binding})

    def test_legacy_identity_remains_accepted(self):
        binding=self.identity();identity=binding['identity']
        identity.update(policy='native_science_v3_fixed_duration_drag_segmented_carrier_v1',lambda1=.2,anharmonicityHz=-200e6)
        identity.pop('dragMode');identity.pop('betaSeconds');binding['sha256']=digest(identity)
        _pulse_identities({'targetPulseIdentity':binding})

    def test_other_science_methods_keep_their_original_roles(self):
        from backend.science_dependencies import EXECUTION_ROLES,execution_roles
        for procedure in ('sq_drag_scan','sq_shelve_spectrum12','sq_shelve_rabi12','sq_shelve_readout_compare'):
            with self.subTest(procedure=procedure):
                self.assertEqual(execution_roles(procedure,{'drag_mode':'empirical_beta'}),set(EXECUTION_ROLES[procedure]))


class EmpiricalDragController(unittest.TestCase):
    def setUp(self):
        from tests.test_controller import make_fixture
        from adapters.science_v3_irb.adapter import ScienceIRBAdapter,build_irb_profile
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.c,self.fake=make_fixture(Path(self.temp.name));self.addCleanup(self.c.close)
        root=Path(__file__).resolve().parents[1]
        base=json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf8'))
        profile=build_irb_profile(root,base);self.native=ScienceIRBAdapter(root,profile)
        self.fake.profile.update(sciencePolicy=profile['sciencePolicy'],scienceIRBPolicy=profile['scienceIRBPolicy'],
            parameterBounds=deepcopy(profile['parameterBounds']),resources=deepcopy(profile['resources']))
        self.fake.capabilities=lambda:{'procedures':{m:{'available':True} for m in ('sq_drag_half_scan','sq_drag_irb')}}
        for method in ('parameter_schema','parameter_keys_by_mode','validation_parameter_keys','normalize_candidate'):
            setattr(self.fake,method,getattr(self.native,method))
        self.c._physical_adapter=True;self.c.adapter_profile_hash=self.c._adapter_fingerprint()

    def data(self):
        return {'name':'TEST_ONLY signed beta','mode':'real','policy':'strict','resourceIds':['Q11'],
            'executionProfile':'science-v3-irb','manualProcedureId':'sq_drag_half_scan',
            'manualParameters':{'drag_mode':'empirical_beta','beta_start_s':-1e-9,'beta_stop_s':1e-9},
            'manualScienceReferences':[{'role':r,'recordId':'fixture-'+r} for r in ('pi01','half01','rabi_amplitude','rabi_time','readout')]}

    def test_real_creation_keeps_beta_mode_and_roles_without_fake_alpha(self):
        wf=self.c.create(self.data());node=wf['nodes'][0]
        self.assertEqual(node['parameters']['drag_mode'],'empirical_beta')
        self.assertNotIn('anharmonicity',node['scienceSourceRoles'])
        self.assertEqual(len(node['scienceReferences']),5)
        self.assertNotIn('anharmonicity_hz',node['parameterOverrides'])
        self.assertEqual(self.fake.calls,[])
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],0)

    def test_creation_rejects_explicit_cross_mode_fields_before_persisting(self):
        for fields in ({'drag_lambda1':.3},{'anharmonicity_hz':-200e6},{'lambda_start':-1.}):
            data=self.data();data['manualParameters'].update(fields)
            with self.subTest(fields=fields),self.assertRaises(APIError):self.c.create(data)
        self.assertEqual(self.c.list(),[])

    def test_signed_beta_is_validated_and_committed_as_beta_only(self):
        wf=self.c.create(self.data());node=wf['nodes'][0]
        result={'parameters':{'drag_mode':'empirical_beta'},'candidateParameters':{'drag_beta_s':-2e-10},
            'quality':{'status':'passed','reasons':[]},'artifacts':[]}
        candidate=self.c._validated_candidate('sq_drag_half_scan',result,'verification')
        self.assertEqual(candidate,{'drag_beta_s':-2e-10},result)
        node['result']=result
        with self.c.store.transaction() as rt:
            self.c._commit_candidate(rt,wf,node,{**candidate,'drag_lambda1':.3},{})
            rows=dict(rt.db.execute('SELECT name,value FROM parameters'))
        self.assertEqual(json.loads(rows[wf['id']+':Q11:drag_beta_s']),-2e-10)
        self.assertNotIn(wf['id']+':Q11:drag_lambda1',rows)

    def test_catalog_exposes_mode_specific_roles_and_keys_without_mutation(self):
        binding={'profile':{'id':'test-only'},'resources':[{'id':'Q11'}]}
        self.c._workspace_service=SimpleNamespace(chips=SimpleNamespace(resolve_binding=lambda *args:deepcopy(binding)))
        with patch('backend.controller.detached_science_profile',return_value=self.c.profile), \
                patch.object(self.c,'with_binding',return_value=self.c), \
                patch.object(self.fake,'preflight',return_value={'staticReady':True}):
            catalog=self.c.science_catalog(chip_version_id='fixture-chip',deployment_version_id='fixture-deploy',
                resource_id='Q11',execution_profile='science-v3-irb')
        methods={m['procedureId']:m for m in catalog['manualMethods']}
        for name in ('sq_drag_half_scan','sq_drag_irb'):
            method=methods[name]
            self.assertIn('anharmonicity',method['scienceSourceRolesByMode']['normalized_alpha'])
            self.assertNotIn('anharmonicity',method['scienceSourceRolesByMode']['empirical_beta'])
            self.assertIn('drag_beta_s',method['parameterKeysByMode']['empirical_beta'])
            self.assertNotIn('drag_lambda1',method['parameterKeysByMode']['empirical_beta'])
            self.assertNotIn('drag_beta_s',method['parameterKeysByMode']['normalized_alpha'])
        self.assertEqual(self.c.list(),[]);self.assertEqual(self.fake.calls,[])
