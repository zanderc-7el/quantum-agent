"""V3 controller admission contracts; no hardware or native driver import."""
from copy import deepcopy
from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from backend.common import APIError, digest
from backend.quality import evaluate
from tests.test_controller import make_fixture


METHODS = ('sq_shape_rabi_amplitude', 'sq_shape_half_verify', 'sq_drag_scan',
           'sq_shelve_spectrum12', 'sq_shelve_rabi12', 'sq_shelve_readout_compare')


class ScienceControllerContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c, self.adapter = make_fixture(Path(self.temp.name))

    def tearDown(self):
        self.c.close()
        self.temp.cleanup()

    def science_fixture(self):
        from adapters.science_v3.plans import POLICY, BOUNDS
        self.adapter.profile['sciencePolicy'] = POLICY
        self.adapter.profile['parameterBounds'].update(deepcopy(BOUNDS))
        self.adapter.capabilities = lambda: {'procedures': {key: {'available': True} for key in METHODS}}
        self.c.adapter_profile_hash = self.c._adapter_fingerprint()
        return {'name': 'detached science', 'mode': 'real', 'policy': 'strict',
                'resourceIds': ['Q11'], 'manualProcedureId': METHODS[0],
                'manualScienceReferences': [{'role': 'f01', 'recordId': 'f01-record'},
                                            {'role': 'readout', 'recordId': 'readout-record'}]}

    def test_new_methods_require_capability_and_only_selected_science_node_is_created(self):
        original = self.c.catalog.describe(self.adapter.capabilities())
        self.assertFalse(set(METHODS) & {x['procedureId'] for x in original['manualMethods']})
        data = self.science_fixture()
        wf = self.c.create(data)
        self.assertEqual([n['procedureId'] for n in wf['nodes']], [METHODS[0]])
        self.assertEqual(wf['nodes'][0]['scienceReferences'], data['manualScienceReferences'])
        self.assertEqual(wf['gatesVersion'], 'science-diagnostic-gates/3')
        self.assertEqual(wf['gatesSha256'], digest(wf['gates']))
        self.assertEqual(self.adapter.calls, [])

    def test_client_cannot_supply_execution_proof_or_verification_parameters(self):
        data = self.science_fixture()
        for field in ({'scienceDependencies': {'assessment': {'executionEligible': True}}},
                      {'manualParameters': {'verify_shape_pi_amp': .2}},
                      {'manualScienceReferences': [{'role': 'f01', 'recordId': 'x', 'accepted': True}]}):
            with self.subTest(field=field), self.assertRaises(APIError):
                self.c.create({**data, **field})
        self.assertEqual(self.c.list(), [])

    def test_science_draft_rejects_automatic_policy(self):
        data = self.science_fixture()
        with self.assertRaises(APIError):
            self.c.create({**data, 'policy': 'autopilot', 'manualProcedureId': None})

    def test_quality_records_frozen_version_and_hash_and_independent_candidate_gates(self):
        from backend.quality import SCIENCE_GATES
        raw = {'dataKind': 'real', 'acquiredAt': '2026-11-26', 'artifacts': [{'id': 'raw'}],
               'analysisQuality': {'accepted': True},
               'metrics': {'r_squared': .95, 'contrast_snr': 10, 'cycles': 2}}
        first = evaluate(raw, METHODS[0], 'real', SCIENCE_GATES, independent=False,
                         rule_version='science-diagnostic-gates/3')
        self.assertEqual(first['quality']['reasons'], ['需要独立采集复测'])
        missing = evaluate(raw, METHODS[0], 'real', SCIENCE_GATES, independent=True,
                           rule_version='science-diagnostic-gates/3')
        self.assertEqual(missing['quality']['status'], 'unverified')
        raw['metrics'].update(candidate_agreement_fraction=.2, candidate_signal_agreement=.3, candidate_capture_agreement=.3, candidate_points_acquired=4)
        result = evaluate(raw, METHODS[0], 'real', SCIENCE_GATES, independent=True,
                          rule_version='science-diagnostic-gates/3')
        self.assertEqual(result['quality']['status'], 'passed')
        self.assertEqual(result['quality']['ruleVersion'], 'science-diagnostic-gates/3')
        self.assertEqual(result['quality']['rulesSha256'], digest(SCIENCE_GATES))
        raw['metrics']['candidate_signal_agreement'] = 2
        rejected = evaluate(raw, METHODS[0], 'real', SCIENCE_GATES, independent=True,
                            rule_version='science-diagnostic-gates/3')
        self.assertEqual(rejected['quality']['status'], 'failed')

    def test_fresh_dependencies_resolved_before_attempt_lease_or_effect(self):
        data = self.science_fixture()
        wf = self.c.create(data)
        node = wf['nodes'][0]
        self.c._physical_adapter = True
        self.adapter.build_plan = lambda request: {'estimated_total_shots':1}
        with patch('backend.science_dependencies.ScienceDependencyResolver.resolve',
                   side_effect=APIError('SCIENCE_DEPENDENCY_NOT_EXECUTABLE', 'stale source')):
            with self.assertRaises(APIError) as error:
                self.c.action(wf['id'], {'action': 'run_step', 'nodeId': node['id'],
                    'operationId': 'blocked', 'expectedRevision': wf['revision']})
        self.assertEqual(error.exception.code,'SCIENCE_DEPENDENCY_NOT_EXECUTABLE')
        with self.c.store.connect() as rt:
            for table in ('platform_leases', 'platform_effects', 'platform_attempts'):
                self.assertEqual(rt.db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0)

    def test_oversize_resolved_science_request_is_rejected_before_queue_or_hardware(self):
        data = self.science_fixture()
        wf = self.c.create(data)
        self.c._physical_adapter = True
        self.adapter.build_plan = lambda request: {'estimated_total_shots':1}
        with patch('backend.science_dependencies.MAX_REQUEST_BYTES',1024,create=True), \
                patch('backend.science_dependencies.ScienceDependencyResolver.resolve',
                      return_value={'closureSha256':'frozen','padding':'x'*2048}):
            with self.assertRaises(APIError) as error:
                self.c.action(wf['id'], {'action':'run_step','nodeId':wf['nodes'][0]['id'],
                    'operationId':'oversize','expectedRevision':wf['revision']})
        self.assertEqual(error.exception.code,'SCIENCE_REQUEST_LIMIT')
        current=self.c.get(wf['id'])
        self.assertEqual((current['status'],current['revision']),(wf['status'],wf['revision']))
        self.assertEqual(self.adapter.calls,[])
        with self.c.store.connect() as rt:
            for table in ('platform_leases','platform_effects','platform_attempts'):
                self.assertEqual(rt.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_measure_verify_use_one_lease_two_effects_then_require_human_acceptance(self):
        data = self.science_fixture()
        self.adapter.candidate = {'shape_pi_amp': .3}
        original_execute = self.adapter.execute
        observed = []
        def execute(request, emit, cancel):
            with self.c.store.connect() as rt:
                observed.append((request['operationId'], rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],
                                 rt.db.execute('SELECT count(*) FROM platform_attempts').fetchone()[0]))
            result = original_execute(request, emit, cancel)
            result['analysisQuality'] = {'accepted': True}
            result['metrics'].update(candidate_agreement_fraction=.1, candidate_signal_agreement=.3, candidate_capture_agreement=.3, candidate_points_acquired=4)
            return result
        self.adapter.execute = execute
        def proof(rt, **kwargs):
            return {'closureSha256': 'frozen-source-closure', 'assessment': {'executionEligible': True}}
        wf = self.c.create(data)
        node_id = wf['nodes'][0]['id']
        with patch('backend.science_dependencies.ScienceDependencyResolver.resolve', side_effect=proof) as resolve:
            wf = self.c.action(wf['id'], {'action': 'run_step', 'nodeId': node_id,
                'operationId': 'science-run', 'expectedRevision': wf['revision']})
            self.c.run_workflow(wf['id'])
        wf = self.c.get(wf['id'])
        self.assertEqual(wf['engineState'], 'awaiting_acceptance')
        self.assertEqual(len(observed), 2)
        self.assertTrue(observed[0][0].endswith('/measure'))
        self.assertTrue(observed[1][0].endswith('/verify'))
        self.assertTrue(all(leases == attempts == 1 for _, leases, attempts in observed))
        self.assertGreaterEqual(resolve.call_count, 3)
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0], 0)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0], 2)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_attempts').fetchone()[0], 1)
        wf = self.c.action(wf['id'], {'action': 'approve', 'nodeId': node_id,
            'operationId': 'science-accept', 'expectedRevision': wf['revision']})
        self.assertEqual(wf['status'], 'completed')
        self.assertEqual(len(self.adapter.calls), 2)

    def test_existing_closure_cannot_be_replaced_by_newer_source_before_dispatch(self):
        data = self.science_fixture()
        wf = self.c.create(data)
        request = {'scienceDependencies': {'closureSha256': 'old'}}
        with self.c.store.connect() as rt, patch('backend.science_dependencies.ScienceDependencyResolver.resolve',
                return_value={'closureSha256': 'new'}):
            with self.assertRaises(APIError) as caught:
                self.c._science_request_dependencies(rt, wf, wf['nodes'][0], request)
        self.assertEqual(caught.exception.code, 'SCIENCE_DEPENDENCY_CHANGED')

    def test_bound_standard_prerequisite_has_same_inherited_verification_budget(self):
        from adapters.arclight_dynamic import DynamicArcLightAdapter, build_dynamic_profile
        root = Path(__file__).resolve().parents[1]
        base = json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf-8-sig'))
        chip = {'id': 'chip-test', 'resources': {'Q11': {'parameters': base['resources']['Q11']}}}
        deployment = {'id': 'deployment-test', 'instrument': base['instrument'],
                      'routes': {'Q11': {key: base['instrument'][key] for key in ('xyPhysical','readoutPhysical','adcPhysical')}}}
        profile = build_dynamic_profile(root, base, chip, deployment, ['Q11'])
        self.c.adapter = DynamicArcLightAdapter(root, profile)
        self.c.profile = profile
        wf = {'maxTotalShots': 10000000, 'reservedShots': 0}
        request = {'operationId': 'preview', 'nodeId': 'Q11:readout', 'resourceId': 'Q11',
                   'procedureId': 'ro_optimize', 'parameters': {'points': 9, 'capture_repeats': 2}}
        plan = self.c.adapter.build_plan(request)
        result = self.c._selection_budget(wf, request, plan)
        self.assertLess(result['verificationShotsUpperBound'], result['selectionShots'])
        self.assertIn('single_setting', result['verificationBasis'])
        self.assertFalse(result['reservation'])
        plan['configurationBinding']['chipHash'] = 'tampered'
        with self.assertRaises(APIError) as caught:
            self.c._selection_budget(wf, request, plan)
        self.assertEqual(caught.exception.code, 'VERIFICATION_BUDGET_UNKNOWN')

    def test_explicit_catalog_uses_bound_preflight_and_never_dispatches_or_changes_default(self):
        self.science_fixture()
        catalog = self.c.catalog
        before = deepcopy(self.adapter.profile)
        binding = {'profile': {'id': 'old'}, 'resources': [{'id':'Q11','label':'Q11'}]}
        selected = SimpleNamespace(adapter=SimpleNamespace(preflight=lambda: {'staticReady': False, 'reason':'source mismatch'},
                    capabilities=self.adapter.capabilities), catalog=catalog, adapter_profile_hash='selected-hash',
                    _bounds=self.c._bounds, _parameter_schema=self.c._parameter_schema)
        self.c._workspace_service = SimpleNamespace(chips=SimpleNamespace(resolve_binding=lambda *args: deepcopy(binding)))
        with patch('adapters.science_v3_adapter.build_science_profile', return_value={'id':'detached','enabled':False}),\
                patch.object(self.c, 'with_binding', return_value=selected):
            result = self.c.science_catalog(chip_version_id='chip-v1', deployment_version_id='dep-v1', resource_id='Q11')
        self.assertFalse(result['preflight']['staticReady'])
        self.assertTrue(all(method['capability'] == 'unavailable' for method in result['manualMethods']))
        self.assertEqual(result['configurationBinding']['adapterProfileHash'], 'selected-hash')
        self.assertEqual(self.adapter.profile, before)
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.c.list(), [])

    def test_detail_exposes_actual_plan_defaults_without_committing_parameters(self):
        wf = self.c.create({'name':'plan defaults', 'mode':'real', 'policy':'strict','resourceIds':['Q11']})
        node_id = wf['nodes'][0]['id']
        self.adapter.build_plan = lambda request: {'parameters': {**request['parameters'], 'points':61, 'shots':2000,
            'capture_repeats':2, 'acquisitions_per_point':1, 'span_hz':4e6}, 'totalRows':122, 'estimated_total_shots':244000}
        before = self.c.get(wf['id'])
        detail = self.c.detail(wf['id'], node_id)
        self.assertEqual(detail['executionDefaults']['points'], 61)
        self.assertEqual(detail['executionDefaultsSource'], 'adapter.build_plan')
        self.assertEqual(detail['resolvedExecutionPlan']['estimatedTotalShots'],244000)
        self.assertNotIn('points', detail['parameters'])
        self.assertEqual(self.c.get(wf['id']), before)
        self.assertEqual(self.adapter.calls, [])
        def reject(request):
            raise APIError('SCIENCE_SOURCE_STALE', 'Source is stale')
        self.adapter.build_plan = reject
        detail = self.c.detail(wf['id'], node_id)
        self.assertEqual(detail['resolvedExecutionPlan']['status'], 'unavailable')
        self.assertEqual(detail['resolvedExecutionPlan']['code'], 'SCIENCE_SOURCE_STALE')
        self.assertEqual(detail['executionDefaults'], {})

    def test_real_adapter_plan_and_budget_use_controller_lease_with_injected_transport(self):
        """Only transport/source authenticators are injected; real V3 planning runs."""
        from adapters.science_v3_adapter import ScienceV3Adapter, build_science_profile
        from backend.common import now
        root = Path(__file__).resolve().parents[1]
        base = json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf-8-sig'))
        profile = build_science_profile(root, base)
        profile['connectionConfirmedAt'] = now()
        manifest = self.c.root/profile['sourceManifest']
        manifest.parent.mkdir(parents=True,exist_ok=True)
        manifest.write_text('[]',encoding='utf-8')
        self.c.adapter = ScienceV3Adapter(self.c.root, profile)
        self.c.profile = self.c.adapter.profile
        self.c._physical_adapter = True
        self.c.adapter_id = profile['id']
        self.c.adapter_profile_hash = self.c._adapter_fingerprint()
        self.adapter.candidate = {'shape_pi_amp': .3}
        observed = []
        def execute(adapter, request, emit, cancel):
            plan = adapter.build_plan(request)
            with self.c.store.connect() as rt:
                observed.append((deepcopy(request),plan,rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0]))
            result = self.adapter.execute(request,emit,cancel)
            result['parameters'] = plan['parameters']
            result['analysisQuality'] = {'accepted':True}
            result['metrics'].update(candidate_agreement_fraction=.2,candidate_signal_agreement=.1,candidate_capture_agreement=.3,candidate_points_acquired=2)
            return result
        def resolve(rt,**kwargs):
            binding={'schema':'test-proof','policyVersion':'test-only','kind':'measurement','scope':kwargs['scope'],
                'inputs':{},'rootReferences':[{'role':'f01','recordId':'f01'},{'role':'readout','recordId':'readout'}],
                'nodes':[{'recordId':'f01'},{'recordId':'readout'}],'edges':[]}
            return {**binding,'closureSha256':digest(binding),'assessment':{'executionEligible':True}}
        with patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=resolve),\
             patch('adapters.science_v3_adapter.verify_extension',return_value={}),\
             patch.object(ScienceV3Adapter,'authenticate_dependency',return_value={'status':'test-only'}),\
             patch.object(ScienceV3Adapter,'preflight',return_value={'ready':True,'staticReady':True,'hardwareConnected':False}),\
             patch('adapters.science_v3.evidence.check_pulse_dependencies'),\
             patch.object(ScienceV3Adapter,'execute',execute),\
             patch.object(self.c,'hardware_ownership',return_value={'verified':True,'owners':[]}):
            wf=self.c.create({'name':'real plan injected acquisition','mode':'real','policy':'strict',
                'resourceIds':['Q11'],'manualProcedureId':'sq_shape_rabi_amplitude',
                'manualParameters':{'points':9,'shots':100,'capture_repeats':2}})
            wf=self.c.action(wf['id'],{'action':'run_step','nodeId':wf['nodes'][0]['id'],
                'operationId':'injected-run','expectedRevision':wf['revision']})
            self.c.run_workflow(wf['id'])
            wf=self.c.get(wf['id'])
            self.assertEqual(wf['engineState'],'waiting_hardware')
            wf=self.c.action(wf['id'],{'action':'confirm_hardware','operationId':'injected-handover',
                'expectedRevision':wf['revision']})
            wf=self.c.action(wf['id'],{'action':'run_step','nodeId':wf['nodes'][0]['id'],
                'operationId':'injected-run-after-handover','expectedRevision':wf['revision']})
            self.c.run_workflow(wf['id'])
            wf=self.c.get(wf['id'])
            self.assertEqual(wf['engineState'],'awaiting_acceptance',wf.get('events'))
            with patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=APIError('STALE','changed upstream')):
                with self.assertRaises(APIError) as caught:
                    self.c.action(wf['id'],{'action':'approve','nodeId':wf['nodes'][0]['id'],
                        'operationId':'reject-stale-accept','expectedRevision':wf['revision']})
                self.assertEqual(caught.exception.code,'CURRENT_EVIDENCE_REQUIRED')
            wf=self.c.action(wf['id'],{'action':'approve','nodeId':wf['nodes'][0]['id'],
                'operationId':'injected-accept','expectedRevision':wf['revision']})
        current=self.c.get(wf['id'])
        self.assertEqual(current['status'],'completed',current.get('events'))
        self.assertEqual(len(observed),2)
        first,second=observed
        self.assertTrue(all(lease_count==1 for _,_,lease_count in observed))
        self.assertEqual(second[0]['parameters']['verify_shape_pi_amp'],.3)
        self.assertEqual(second[1]['totalRows'],first[1]['totalRows']+2)
        self.assertEqual(current['reservedShots'],first[1]['estimated_total_shots']+second[1]['estimated_total_shots'])

    def observed_source_fixture(self):
        """Inject a controller-accepted source; native receipt auth is tested separately."""
        from adapters.arclight import ArcLightAdapter
        root=Path(__file__).resolve().parents[1]
        wf=self.c.create({'name':'observed source fixture','mode':'real','policy':'strict','resourceIds':['Q11']})
        node_id=wf['nodes'][0]['id']
        wf=self.c.action(wf['id'],{'action':'run_step','nodeId':node_id,'operationId':'observe', 'expectedRevision':wf['revision']})
        self.c.run_workflow(wf['id']);wf=self.c.get(wf['id'])
        wf=self.c.action(wf['id'],{'action':'approve','nodeId':node_id,'operationId':'observe-accept','expectedRevision':wf['revision']})
        profile=json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf-8-sig'))
        for relative in (profile['sourceManifest'],'adapters/science_v3/source_manifest.json','adapters/science_v3/execute_worker.py'):
            file=self.c.root/relative;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('[]',encoding='utf-8')
        self.c.adapter=ArcLightAdapter(self.c.root,profile);self.c.profile=self.c.adapter.profile
        self.c._physical_adapter=True;self.c.adapter_id=profile['id'];self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        with self.c.store.transaction() as rt:
            wf=self.c.get(wf['id'],rt);wf.update(adapterId=self.c.adapter_id,adapterProfileHash=self.c.adapter_profile_hash)
            self.c.store.save(rt,wf,'injected-native-source-identity')
        return self.c.get(wf['id'])

    def test_observed_binding_preserves_unknown_design_and_inherits_exact_source_identity(self):
        from adapters.arclight import ArcLightAdapter
        source=self.observed_source_fixture()
        with patch.object(ArcLightAdapter,'preflight',return_value={'ready':True,'staticReady':True}):
            binding=self.c._observed_science_binding(source['id'],'Q11')
            observed=binding['observedResourceBinding']
            self.assertEqual(observed['sourceWorkflowId'],source['id'])
            self.assertIsNone(observed['physicalDesignId'])
            self.assertEqual(binding['resources'][0]['identityStatus'],'observed_resource')
            self.assertIsNone(binding['resources'][0]['physicalId'])
            self.assertEqual(binding['profile']['hardwareLocks'],self.c.profile['hardwareLocks'])
            self.assertFalse(binding['profile']['enabled'])
            child=self.c.create({'name':'observed shape task','mode':'real','policy':'strict','resourceIds':['Q11'],
                'executionProfile':'science-v3','sourceWorkflowId':source['id'],'manualProcedureId':'sq_shape_rabi_amplitude'})
            self.assertEqual(child['observedResourceBinding'],observed)
            self.assertNotIn('chipVersionId',child)
            with self.c.store.connect() as rt:self.c._validate_observed_resource_binding(child,rt)
            inherited=self.c._observed_science_binding(child['id'],'Q11')
            self.assertEqual(inherited['observedResourceBinding'],observed)
            self.assertFalse(child['approved'])

    def test_observed_binding_rejects_profile_source_or_artifact_change(self):
        from adapters.arclight import ArcLightAdapter
        source=self.observed_source_fixture()
        with patch.object(ArcLightAdapter,'preflight',return_value={'ready':True,'staticReady':True}):
            request={'name':'observed shape task','mode':'real','policy':'strict','resourceIds':['Q11'],
                'executionProfile':'science-v3','sourceWorkflowId':source['id'],'manualProcedureId':'sq_shape_rabi_amplitude',
                'operationId':'observed-create-once'}
            child=self.c.create(request)
            observed=child['observedResourceBinding']
            artifact=observed['readoutAnchor']['rawArtifacts'][0]
            path,_=self.c.artifact(artifact['id']);original=path.read_bytes();path.write_bytes(b'changed-source')
            with self.c.store.connect() as rt,self.assertRaises(APIError):self.c._validate_observed_resource_binding(child,rt)
            self.assertEqual(self.c.create(request)['id'],child['id'])
            path.write_bytes(original)
            child['configurationBinding']['sourceProfile']['resources']['Q11']['drive_hz']+=1e6
            with self.c.store.connect() as rt,self.assertRaises(APIError):self.c._validate_observed_resource_binding(child,rt)


if __name__ == '__main__':
    unittest.main()
