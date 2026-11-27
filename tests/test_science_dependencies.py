"""Dependency proofs use temporary records and explicit fake acquisition only."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace
import hashlib
import shutil
from unittest.mock import patch

from backend.common import APIError, digest, dumps
from backend.science_service import ScienceService
from tests.test_controller import make_fixture


class ScienceDependencyContracts(unittest.TestCase):
    def setUp(self):
        try:
            module=importlib.import_module('backend.science_dependencies')
        except ModuleNotFoundError:
            module=None
        self.assertIsNotNone(module,'dependency resolver is missing')
        self.temp=tempfile.TemporaryDirectory(prefix='science-dependencies-')
        self.addCleanup(self.temp.cleanup)
        self.c,self.adapter=make_fixture(Path(self.temp.name))
        self.addCleanup(self.c.close)
        self.science=ScienceService(self.c)
        self.resolver=module.ScienceDependencyResolver(self.c)
        self.wf=None

    def acquired(self,procedure='sq_rabi_amplitude'):
        if self.wf is None:
            wf=self.c.create(dict(name='dependency fixture',mode='real',resourceIds=['Q11'],coverage='main'))
            self.c.action(wf['id'],dict(operationId='acquire',action='start',expectedRevision=wf['revision']))
            self.c.run_workflow(wf['id'])
            self.wf=self.c.get(wf['id'])
        node=next(n for n in self.wf['nodes'] if n['procedureId']==procedure)
        record=self.science.write('/records',dict(operationId='save-'+procedure,kind='measurement',input={},scope={},
            reference=dict(workflowId=self.wf['id'],attemptId=node['latestAttemptId'])),'POST')
        return record,node

    def resolve(self,refs=None,inputs=None,scope=None,kind='drag',**kwargs):
        with self.c.store.connect() as rt:
            return self.resolver.resolve(rt,kind=kind,scope=scope or {},refs=refs or [],inputs=inputs or {},**kwargs)

    def science_fixture(self,original,procedure,counts=(2,2),parents=(),native_fixture_root=None):
        """Temporary fake acquisition with actual independent quality rules and file cardinality.

        Counts are native default-operation inventory counts, not measured data.
        No physical adapter, native compiler or hardware is invoked by this fixture.
        """
        from backend.quality import evaluate,SCIENCE_GATES,SCIENCE_GATES_VERSION
        _,node=self.acquired(original)
        upstream=self.resolve(refs=list(parents),kind='measurement') if parents else None
        with self.c.store.transaction() as rt:
            wf=self.c.store.get(rt,self.wf['id'])
            wf.update(gates={**wf['gates'],**deepcopy(SCIENCE_GATES)},gatesVersion=SCIENCE_GATES_VERSION)
            current=next(n for n in wf['nodes'] if n['id']==node['id']);current['procedureId']=procedure
            row=rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(node['latestAttemptId'],)).fetchone()
            attempt=json.loads(row[0]);attempt['procedureId']=procedure
            results=[]
            for index,operation in enumerate((attempt['operationId'],attempt['verificationOperationId'])):
                effect=rt.db.execute('SELECT request,result FROM platform_effects WHERE operation_id=?',(operation,)).fetchone()
                request=json.loads(effect['request']);request['procedureId']=procedure
                result=json.loads(effect['result']);artifacts=[]
                folder=Path(self.temp.name)/'source-volume'/procedure/str(index);folder.mkdir(parents=True,exist_ok=True)
                native=[] if native_fixture_root is None else json.loads((Path(native_fixture_root)/procedure/('measure' if index==0 else 'verify')/'artifact_fixture.json').read_text())['artifacts']
                if native:self.assertEqual(len(native),counts[index])
                for number in range(counts[index]):
                    if native:
                        artifact=deepcopy(native[number]);source=Path(artifact.get('localPath') or artifact['sourcePath'])
                        path=folder/source.name;shutil.copyfile(source,path)
                        artifact.update(localPath=str(path),sourcePath=str(path))
                    else:
                        path=folder/('point_'+str(number)+'.npz')
                        path.write_bytes((procedure+':'+str(index)+':'+str(number)).encode())
                        artifact={'id':'fixture-'+hashlib.sha256(str(path).encode()).hexdigest()[:32],
                                  'name':path.name,'kind':'raw' if number%2 else 'text',
                                  'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'localPath':str(path)}
                    artifacts.append(self.c._artifact(rt,artifact))
                result.update(artifacts=artifacts,analysisQuality={'accepted':True,'fixtureOnly':True})
                result['metrics'].update(r_squared=.99,contrast_snr=10,cycles=2,half_rotation_rms=.03,
                    diagnostic_improvement=.1,curvature_z=5,candidate_agreement_fraction=.1,
                    candidate_signal_agreement=.1,candidate_capture_agreement=.1,candidate_points_acquired=1)
                if upstream:
                    request['scienceDependencies']=deepcopy(upstream)
                    result.setdefault('provenance',{})['scienceDependencies']=deepcopy(upstream)
                result=evaluate(result,procedure,'real',wf['gates'],independent=bool(index),rule_version=SCIENCE_GATES_VERSION)
                results.append(result)
                rt.db.execute('UPDATE platform_effects SET request=?,result=? WHERE operation_id=?',(dumps(request),dumps(result),operation))
            attempt['result']={**results[1],'selectionResult':results[0]};attempt['state']='verified'
            current.update(result=deepcopy(attempt['result']),engineState='verified')
            rt.db.execute('UPDATE platform_attempts SET payload=? WHERE id=?',(dumps(attempt),attempt['id']))
            self.c.store.save(rt,wf,'temporary_science_inventory_fixture')
        return self.science.write('/records',dict(operationId='native-size-'+procedure,kind='measurement',input={},scope={},
            reference=dict(workflowId=self.wf['id'],attemptId=node['latestAttemptId'])),'POST')

    def test_actual_independent_candidate_criteria_remain_current_and_hashes_must_match(self):
        source=self.science_fixture('sq_rabi_amplitude','sq_shape_rabi_amplitude')
        proof=self.resolve(refs=[{'role':'pi01','recordId':source['id']}],kind='measurement')
        state=proof['assessment']['nodes'][source['id']]
        self.assertTrue(state['current'])
        self.assertTrue(state['controllerVerified'])
        self.assertFalse(proof['physicalAccepted'])
        with self.c.store.transaction() as rt:
            row=rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(source['reference']['attemptId'],)).fetchone()
            attempt=json.loads(row[0]);attempt['result']['quality']['criteriaSha256']='0'*64
            rt.db.execute('UPDATE platform_attempts SET payload=? WHERE id=?',(dumps(attempt),attempt['id']))
        altered=self.resolve(refs=[{'role':'pi01','recordId':source['id']}],kind='measurement')
        self.assertFalse(altered['assessment']['nodes'][source['id']]['current'])
        self.assertFalse(self.science.get('/records/'+source['id'],{})['evidenceCurrent'])

    def test_default_shape_half_drag_closure_keeps_every_file_and_detects_late_corruption(self):
        # Native inventories: shape 244/248 rows, half 20/20, DRAG 92/96;
        # four per-row files at A=1, plus nine fixed files per operation.
        shape=self.science_fixture('sq_rabi_amplitude','sq_shape_rabi_amplitude',(985,1001))
        half=self.science_fixture('sq_t1','sq_shape_half_verify',(89,89),[{'role':'pi01','recordId':shape['id']}])
        drag=self.science_fixture('sq_cpmg','sq_drag_scan',(377,393),[
            {'role':'pi01','recordId':shape['id']},{'role':'half01','recordId':half['id']}])
        proof=self.resolve(refs=[{'role':'coefficient_scan','recordId':drag['id']}],kind='measurement')
        self.assertEqual(len(proof['nodes']),3)
        self.assertTrue(proof['assessment']['evidenceCurrent'])
        self.assertTrue(all(state['controllerVerified'] for state in proof['assessment']['nodes'].values()))
        self.assertFalse(proof['assessment']['executionEligible'])
        self.assertEqual(sum(len(op['registeredArtifacts']) for node in proof['nodes'] for op in node['operations']),2934)
        source=next(node for node in proof['nodes'] if node['recordId']==shape['id'])
        late=source['operations'][1]['registeredArtifacts'][-1]
        with self.c.store.connect() as rt:
            path=rt.db.execute('SELECT path FROM platform_artifacts WHERE id=?',(late['id'],)).fetchone()[0]
        Path(path).write_bytes(b'late corruption remains detectable')
        self.assertFalse(self.validate(proof)['assessment']['integrityIntact'])

    def test_evidence_inventory_has_per_operation_global_and_metadata_bounds(self):
        source=self.science_fixture('sq_rabi_amplitude','sq_shape_rabi_amplitude',(4,4))
        for key,limit in [('MAX_OPERATION_ARTIFACTS',3),('MAX_ARTIFACTS',7),('MAX_PROOF_BYTES',128)]:
            with self.subTest(bound=key),patch('backend.science_dependencies.'+key,limit,create=True):
                with self.assertRaises(APIError) as caught:
                    self.resolve(refs=[{'role':'pi01','recordId':source['id']}],kind='measurement')
                self.assertEqual(caught.exception.code,'SCIENCE_DEPENDENCY_LIMIT')

    def test_summary_hash_cache_is_shared_only_within_one_request(self):
        from backend.common import file_hash
        source=self.science_fixture('sq_rabi_amplitude','sq_shape_rabi_amplitude',(4,4))
        for n in range(2):
            self.science.write('/records',dict(operationId='shared-source-'+str(n),kind='drag',input={},scope={},
                dependencies=[{'role':'rabi_amplitude','recordId':source['id']}]),'POST')
        calls=[]
        def counted(path):calls.append(str(path));return file_hash(path)
        with patch('backend.science_service.file_hash',side_effect=counted),patch('backend.science_dependencies.file_hash',side_effect=counted):
            page=self.science.get('/records',{'summary':'true'})
        self.assertEqual(len(calls),len(set(calls)))
        selected=next(record for record in page['records'] if record['id']==source['id'])
        self.assertTrue(selected['provenance']['independentlyVerified'])
        self.assertEqual(selected['result']['quality']['status'],'passed')
        Path(calls[-1]).write_bytes(b'changed between list requests')
        following=self.science.get('/records',{'summary':'true'})
        self.assertTrue(any(record.get('dependencyAssessment',{}).get('status')=='invalid' for record in following['records']))

    def validate(self,proof,**kwargs):
        with self.c.store.connect() as rt:return self.resolver.validate(rt,proof,**kwargs)

    def test_unbound_model_remains_savable_without_acquisition_claim(self):
        proof=self.resolve(inputs={'alphaNs':.5})
        self.assertEqual(proof['assessment']['status'],'unbound')
        self.assertEqual(proof['nodes'],[])
        self.assertFalse(proof['acceptedCalibration'])
        self.assertFalse(proof['physicalAccepted'])
        self.assertFalse(proof['assessment']['executionEligible'])
        self.assertEqual(self.adapter.calls,[])

    def test_explicit_missing_source_is_rejected_not_silently_annotated(self):
        with self.assertRaises(APIError) as caught:
            self.resolve(inputs={'rabiSourceRecordId':'missing'})
        self.assertEqual(caught.exception.code,'SCIENCE_DEPENDENCY_NOT_FOUND')
        self.assertEqual(self.adapter.calls,[])

    def test_role_method_and_resource_mismatch_are_rejected(self):
        source,_=self.acquired('sq_t1')
        with self.assertRaises(APIError) as caught:
            self.resolve(refs=[{'role':'rabi_amplitude','recordId':source['id']}])
        self.assertEqual(caught.exception.code,'SCIENCE_DEPENDENCY_ROLE_MISMATCH')
        source,_=self.acquired()
        with self.assertRaises(APIError) as caught:
            self.resolve(refs=[{'role':'rabi_amplitude','recordId':source['id']}],scope={**source['scope'],'resourceId':'Q18'})
        self.assertEqual(caught.exception.code,'SCIENCE_SCOPE_MISMATCH')

    def test_resolved_proof_freezes_both_distinct_operations_and_raw_hashes(self):
        source,_=self.acquired()
        proof=self.resolve(inputs={'rabiSourceRecordId':source['id']})
        self.assertEqual(proof['rootReferences'],[{'role':'rabi_amplitude','recordId':source['id']}])
        item=proof['nodes'][0]
        self.assertEqual(item['scope']['resourceId'],'Q11')
        self.assertEqual(item['procedureId'],'sq_rabi_amplitude')
        self.assertEqual(len(item['operations']),2)
        first,second=item['operations']
        self.assertNotEqual(first['operationId'],second['operationId'])
        self.assertTrue({a['sha256'] for a in first['rawArtifacts']}.isdisjoint(a['sha256'] for a in second['rawArtifacts']))
        self.assertEqual(item['resolvedOutputs']['candidateParameters'],{})
        self.assertEqual(proof['closureSha256'],self.validate(proof)['closureSha256'])
        self.assertFalse(proof['physicalAccepted'])

    def test_duplicate_roles_and_conflicting_legacy_references_are_rejected(self):
        source,_=self.acquired()
        for refs,inputs in [([{'role':'rabi_amplitude','recordId':source['id']}]*2,{}),
                            ([{'role':'rabi_amplitude','recordId':'different'}],{'rabiSourceRecordId':source['id']})]:
            with self.subTest(refs=refs),self.assertRaises(APIError):self.resolve(refs,inputs)

    def test_models_cannot_be_used_as_calibration_dependencies(self):
        model=self.science.write('/records',dict(operationId='model',kind='drag',input={},scope={}),'POST')
        with self.assertRaises(APIError):
            self.resolve(refs=[{'role':'rabi_amplitude','recordId':model['id']}])

    def test_source_hash_change_invalidates_frozen_proof(self):
        source,_=self.acquired()
        proof=self.resolve(inputs={'rabiSourceRecordId':source['id']})
        with self.c.store.connect() as rt:
            row=rt.db.execute('SELECT path FROM platform_artifacts WHERE id=?',(source['evidence'][0]['id'],)).fetchone()
        Path(row[0]).write_bytes(b'changed after proof')
        checked=self.validate(proof)
        self.assertEqual(checked['assessment']['status'],'invalid')
        self.assertFalse(checked['assessment']['integrityIntact'])
        self.assertFalse(checked['assessment']['executionEligible'])

    def test_upstream_invalidation_preserves_integrity_but_blocks_current_use(self):
        source,node=self.acquired()
        proof=self.resolve(inputs={'rabiSourceRecordId':source['id']})
        with self.c.store.transaction() as rt:
            wf=self.c.store.get(rt,self.wf['id'])
            self.c._invalidate(wf,node['id'],include_target=True)
            self.c.store.save(rt,wf,'test_invalidated')
        checked=self.validate(proof)
        self.assertEqual(checked['assessment']['status'],'historical')
        self.assertTrue(checked['assessment']['integrityIntact'])
        self.assertFalse(checked['assessment']['evidenceCurrent'])

    def test_nested_source_chain_and_input_annotations_are_both_checked(self):
        source,_=self.acquired()
        child=self.science.write('/records',dict(operationId='child',kind='drag',input={'rabiSourceRecordId':source['id']},scope=source['scope']),'POST')
        proof=self.resolve(kind='simulation',refs=[{'role':'source_record','recordId':child['id']}])
        self.assertEqual({n['recordId'] for n in proof['nodes']},{source['id'],child['id']})
        with self.c.store.connect() as rt:
            path=rt.db.execute('SELECT path FROM platform_artifacts WHERE id=?',(source['evidence'][0]['id'],)).fetchone()[0]
        Path(path).write_bytes(b'changed ancestor')
        self.assertEqual(self.validate(proof)['assessment']['status'],'invalid')

    def test_changed_source_payload_is_not_rebound_silently(self):
        source,_=self.acquired()
        proof=self.resolve(inputs={'rabiSourceRecordId':source['id']})
        with self.c.store.transaction() as rt:
            row=rt.db.execute('SELECT payload FROM platform_science_records WHERE id=?',(source['id'],)).fetchone()
            payload=json.loads(row[0]);payload['label']='different stored payload'
            rt.db.execute('UPDATE platform_science_records SET payload=?,payload_hash=? WHERE id=?',(dumps(payload),digest(payload),source['id']))
        checked=self.validate(proof)
        self.assertEqual(checked['assessment']['status'],'invalid')
        self.assertFalse(checked['assessment']['integrityIntact'])

    def test_client_cannot_rewrite_frozen_closure(self):
        source,_=self.acquired()
        proof=self.resolve(inputs={'rabiSourceRecordId':source['id']})
        proof['nodes'][0]['procedureId']='sq_drag_scan'
        with self.assertRaises(APIError) as caught:self.validate(proof)
        self.assertEqual(caught.exception.code,'SCIENCE_DEPENDENCY_PROOF_CHANGED')

    def test_fake_acquisition_never_confers_physical_execution_eligibility(self):
        source,_=self.acquired('sq_spectroscopy');readout,_=self.acquired('ro_iq01')
        consumer=dict(runId=self.wf['id'],resourceId='Q11',procedureId='sq_shape_rabi_amplitude',parameters={},parameterVersions={})
        with self.assertRaises(APIError) as caught:
            self.resolve(refs=[{'role':'f01','recordId':source['id']},{'role':'readout','recordId':readout['id']}],use='execution',consumer=consumer)
        self.assertEqual(caught.exception.code,'SCIENCE_DEPENDENCY_NOT_EXECUTABLE')

    def test_cycles_are_rejected_without_unbounded_recursion(self):
        record=self.science.write('/records',dict(operationId='cycle',kind='bloch',input={},scope={}),'POST')
        with self.c.store.transaction() as rt:
            record['sourceRecordId']=record['id']
            rt.db.execute('UPDATE platform_science_records SET payload=?,payload_hash=? WHERE id=?',(dumps(record),digest(record),record['id']))
        with self.assertRaises(APIError) as caught:
            self.resolve(kind='simulation',refs=[{'role':'source_record','recordId':record['id']}])
        self.assertEqual(caught.exception.code,'SCIENCE_DEPENDENCY_CYCLE')

    def test_save_checks_explicit_references_and_never_silently_stores_missing_id(self):
        with self.assertRaises(APIError) as caught:
            self.science.write('/records',dict(operationId='bad-reference',kind='drag',input={'rabiSourceRecordId':'missing'},scope={}), 'POST')
        self.assertEqual(caught.exception.code,'SCIENCE_DEPENDENCY_NOT_FOUND')
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM platform_science_records').fetchone()[0],0)

    def test_save_freezes_dependency_and_read_checks_it_without_rewriting_metadata(self):
        source,node=self.acquired()
        body=dict(operationId='bound-model',kind='drag',input={},scope=source['scope'],
                  dependencies=[{'role':'rabi_amplitude','recordId':source['id']}])
        saved=self.science.write('/records',body,'POST')
        frozen=deepcopy(saved['dependencyProof'])
        self.assertEqual(saved['dependencyAssessment']['status'],'incomplete')
        self.assertEqual(saved['dataKind'],'simulation')
        with self.c.store.transaction() as rt:
            wf=self.c.store.get(rt,self.wf['id']);self.c._invalidate(wf,node['id'],include_target=True)
            self.c.store.save(rt,wf,'invalidate')
            row=rt.db.execute('SELECT payload,payload_hash FROM platform_science_records WHERE id=?',(saved['id'],)).fetchone()
            before=tuple(row)
        loaded=self.science.get('/records/'+saved['id'],{})
        self.assertEqual(loaded['dependencyProof'],frozen)
        self.assertEqual(loaded['dependencyAssessment']['status'],'historical')
        self.assertFalse(loaded['evidenceCurrent']);self.assertTrue(loaded['evidenceIntact'])
        self.assertFalse(loaded['acceptedCalibration'])
        self.assertFalse(loaded['physicalAccepted'])
        with self.c.store.connect() as rt:
            self.assertEqual(tuple(rt.db.execute('SELECT payload,payload_hash FROM platform_science_records WHERE id=?',(saved['id'],)).fetchone()),before)
        # Retrying a completed save returns its immutable receipt, not a new record.
        self.assertEqual(self.science.write('/records',body,'POST'),saved)

    def test_new_bound_model_inherits_raw_file_invalidation(self):
        source,_=self.acquired()
        saved=self.science.write('/records',dict(operationId='raw-dependent',kind='drag',input={'rabiSourceRecordId':source['id']},scope={}), 'POST')
        self.assertEqual(saved['scope'],source['scope'])
        with self.c.store.connect() as rt:
            path=rt.db.execute('SELECT path FROM platform_artifacts WHERE id=?',(source['evidence'][0]['id'],)).fetchone()[0]
        Path(path).write_bytes(b'changed source')
        loaded=self.science.get('/records/'+saved['id'],{})
        self.assertEqual(loaded['dependencyAssessment']['status'],'invalid')
        self.assertFalse(loaded['evidenceIntact'])
        self.assertEqual(loaded['validity'],'stale')

    def test_legacy_model_is_not_promoted_or_silently_rebound(self):
        saved=self.science.write('/records',dict(operationId='legacy',kind='drag',input={},scope={}), 'POST')
        with self.c.store.transaction() as rt:
            value=json.loads(rt.db.execute('SELECT payload FROM platform_science_records WHERE id=?',(saved['id'],)).fetchone()[0])
            value.pop('dependencyProof',None);value.pop('dependencyAssessment',None)
            value['input']['rabiSourceRecordId']='old-unverified-annotation'
            rt.db.execute('UPDATE platform_science_records SET payload=?,payload_hash=? WHERE id=?',(dumps(value),digest(value),saved['id']))
        loaded=self.science.get('/records/'+saved['id'],{})
        self.assertEqual(loaded['dependencyAssessment']['status'],'legacy_unvalidated')
        self.assertEqual(loaded['dataKind'],'simulation')
        self.assertFalse(loaded['dependencyAssessment']['executionEligible'])
        self.assertFalse(loaded['acceptedCalibration'])
        self.assertNotIn('dependencyProof',loaded)

    def test_client_cannot_supply_immutable_proof_or_assessment(self):
        for field in ('dependencyProof','dependencyAssessment','physicalAccepted'):
            with self.subTest(field=field),self.assertRaises(APIError):
                self.science.write('/records',dict(operationId='forged-'+field,kind='drag',input={},scope={},**{field:{}}),'POST')

    def test_changed_gate_policy_makes_historical_without_false_file_corruption(self):
        source,_=self.acquired();proof=self.resolve(inputs={'rabiSourceRecordId':source['id']})
        with self.c.store.transaction() as rt:
            wf=self.c.store.get(rt,self.wf['id']);wf['gatesVersion']='revised-policy'
            self.c.store.save(rt,wf,'new_policy')
        checked=self.validate(proof)
        self.assertEqual(checked['assessment']['status'],'historical')
        self.assertTrue(checked['assessment']['integrityIntact'])
        self.assertFalse(checked['assessment']['nodes'][source['id']]['controllerVerified'])

    def test_nonverified_current_node_cannot_borrow_verified_old_attempt_state(self):
        source,node=self.acquired()
        with self.c.store.transaction() as rt:
            wf=self.c.store.get(rt,self.wf['id'])
            next(n for n in wf['nodes'] if n['id']==node['id'])['engineState']='failed'
            self.c.store.save(rt,wf,'changed_state')
        proof=self.resolve(inputs={'rabiSourceRecordId':source['id']})
        self.assertFalse(proof['assessment']['nodes'][source['id']]['controllerVerified'])

    def test_malformed_roles_and_proofs_fail_with_api_error(self):
        for role in ({},[],None):
            with self.subTest(role=role),self.assertRaises(APIError):
                self.resolve(refs=[{'role':role,'recordId':'id'}])
        proof=self.resolve()
        proof['nodes']={}
        from backend.science_dependencies import BINDING_KEYS
        proof['closureSha256']=digest({k:proof[k] for k in BINDING_KEYS})
        with self.assertRaises(APIError):self.validate(proof)

    def test_execution_attestation_uses_two_arguments_and_nonrecursive_consumer_hash(self):
        # Pure contract stub: no adapter or device is invoked, and even this
        # eligibility proof is never marked as a physical accepted calibration.
        from backend.science_dependencies import consumer_content
        source,_=self.acquired('sq_spectroscopy');readout,_=self.acquired('ro_iq01')
        proof=self.resolve(refs=[{'role':'f01','recordId':source['id']},{'role':'readout','recordId':readout['id']}])
        for state in proof['assessment']['nodes'].values():state.update(current=True,controllerVerified=True)
        proof['assessment']['evidenceCurrent']=True
        consumer=dict(runId=self.wf['id'],resourceId='Q11',procedureId='sq_shape_rabi_amplitude',adapterProfileHash='new-reviewed-profile',scienceDependencies=proof)
        observed=[]
        def authenticate(request,dependency):
            observed.append(deepcopy(dependency))
            self.assertNotIn('scienceDependencies',request)
            return dict(status='valid',dependencySha256=digest(dependency),consumerSha256=digest(request),evidenceSha256='a'*64)
        bound=SimpleNamespace(_physical_adapter=True,adapter=SimpleNamespace(authenticate_dependency=authenticate))
        with patch.object(self.c,'for_workflow',return_value=bound),self.c.store.connect() as rt:
            self.resolver._execution(rt,proof,consumer)
        self.assertEqual({tuple(d['sourceRoles']) for d in observed},{('f01',),('readout',)})
        self.assertEqual(proof['assessment']['consumerSha256'],digest(consumer_content(consumer)))
        self.assertTrue(proof['assessment']['executionEligible'])
        self.assertFalse(proof['physicalAccepted'])

    def rewrite_fake_attempt_result(self,node,change):
        """Change only our temporary FakeAdapter fixture, keeping DB effects coherent."""
        with self.c.store.transaction() as rt:
            attempt=json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(node['latestAttemptId'],)).fetchone()[0])
            change(attempt['result'])
            if attempt['result'].get('selectionResult'):change(attempt['result']['selectionResult'])
            rt.db.execute('UPDATE platform_attempts SET payload=? WHERE id=?',(dumps(attempt),attempt['id']))
            for effect in rt.db.execute('SELECT operation_id,result FROM platform_effects WHERE attempt_id=?',(attempt['id'],)).fetchall():
                result=json.loads(effect['result']);change(result)
                rt.db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?',(dumps(result),effect['operation_id']))
            wf=self.c.store.get(rt,self.wf['id'])
            next(n for n in wf['nodes'] if n['id']==node['id'])['result']=deepcopy(attempt['result'])
            self.c.store.save(rt,wf,'fixture_only')

    def test_measurement_captures_native_ancestor_proof_and_read_propagates_damage(self):
        ancestor,_=self.acquired();_,node=self.acquired('sq_t1')
        upstream=self.resolve(refs=[{'role':'pi01','recordId':ancestor['id']}])
        def attach(result):result.setdefault('provenance',{})['scienceDependencies']=deepcopy(upstream)
        self.rewrite_fake_attempt_result(node,attach)
        saved=self.science.write('/records',dict(operationId='native-ancestor',kind='measurement',input={},scope={},
            reference=dict(workflowId=self.wf['id'],attemptId=node['latestAttemptId'])),'POST')
        self.assertIn(ancestor['id'],{n['recordId'] for n in saved['dependencyProof']['nodes']})
        with self.c.store.connect() as rt:
            path=rt.db.execute('SELECT path FROM platform_artifacts WHERE id=?',(ancestor['evidence'][0]['id'],)).fetchone()[0]
        Path(path).write_bytes(b'changed native ancestor')
        loaded=self.science.get('/records/'+saved['id'],{})
        self.assertFalse(loaded['evidenceIntact'])
        self.assertEqual(loaded['dependencyAssessment']['status'],'invalid')

    def test_inconsistent_pulse_identity_digest_is_not_frozen_as_valid_evidence(self):
        source,node=self.acquired()
        self.rewrite_fake_attempt_result(node,lambda result:result.update(targetPulseIdentity={'identity':{'amplitude':.3},'sha256':'a'*64}))
        with self.assertRaises(APIError) as caught:self.resolve(refs=[{'role':'pi01','recordId':source['id']}])
        self.assertEqual(caught.exception.code,'SCIENCE_DEPENDENCY_EVIDENCE')

    def test_full_config_identity_allows_cross_task_without_rewriting_source_identity(self):
        _,node=self.acquired()
        with self.c.store.transaction() as rt:
            wf=self.c.store.get(rt,self.wf['id'])
            wf.update(chipVersionId='fixture-chip-v1',deploymentVersionId='fixture-wiring-v1')
            self.c.store.save(rt,wf,'fixture_config')
        source=self.science.write('/records',dict(operationId='bound-source',kind='measurement',input={},scope={},
            reference=dict(workflowId=self.wf['id'],attemptId=node['latestAttemptId'])),'POST')
        consumer_scope={**source['scope'],'workflowId':'a-different-consumer-task'}
        proof=self.resolve(refs=[{'role':'pi01','recordId':source['id']}],scope=consumer_scope)
        self.assertEqual(proof['scope'],consumer_scope)
        self.assertEqual(proof['nodes'][0]['scope'],source['scope'])
        self.assertEqual(proof['nodes'][0]['reference']['workflowId'],self.wf['id'])
        for key,value in [('chipVersionId','other-chip'),('deploymentVersionId','other-wiring'),('resourceId','Q18')]:
            with self.subTest(key=key),self.assertRaises(APIError) as caught:
                self.resolve(refs=[{'role':'pi01','recordId':source['id']}],scope={**consumer_scope,key:value})
            self.assertEqual(caught.exception.code,'SCIENCE_SCOPE_MISMATCH')

    def test_cross_task_legacy_identity_is_rejected_even_on_same_named_qubit(self):
        source,_=self.acquired()
        with self.assertRaises(APIError) as caught:
            self.resolve(refs=[{'role':'pi01','recordId':source['id']}],scope={**source['scope'],'workflowId':'different-task'})
        self.assertEqual(caught.exception.code,'SCIENCE_SCOPE_MISMATCH')

    def test_observed_fork_requires_server_validated_origin_not_just_same_qubit(self):
        source,_=self.acquired()
        child=self.c.create(dict(name='observed fork fixture',mode='real',resourceIds=['Q11'],coverage='main'))
        binding={'schema':'qcal-observed-resource/1','kind':'observed_resource','resourceId':'Q11','sourceWorkflowId':self.wf['id'],
                 'identityClaim':'measured_resource_not_design_identity','physicalDesignId':None}
        binding['sha256']=digest(binding)
        with self.c.store.transaction() as rt:
            wf=self.c.store.get(rt,child['id']);wf['observedResourceBinding']=binding
            self.c.store.save(rt,wf,'fixture_observed_binding')
        # A contract stub isolates scope plumbing; the real controller owns
        # origin readout/raw-file/device validation and this does not run hardware.
        with patch.object(self.c,'_validate_observed_resource_binding',create=True,return_value=None) as validate:
            proof=self.resolve(refs=[{'role':'pi01','recordId':source['id']}],scope={'workflowId':child['id'],'resourceId':'Q11'})
            self.assertTrue(validate.called)
            self.assertEqual(proof['scope']['observedResourceBindingSha256'],binding['sha256'])
            self.assertEqual(proof['nodes'][0]['scope']['workflowId'],self.wf['id'])
            self.assertNotIn('chipVersionId',proof['scope'])
        with patch.object(self.c,'_validate_observed_resource_binding',create=True,side_effect=APIError('ORIGIN_CHANGED','fixture origin changed',409)):
            self.assertEqual(self.validate(proof)['assessment']['status'],'invalid')
        unrelated=self.c.create(dict(name='unrelated Q11',mode='real',resourceIds=['Q11'],coverage='main'))
        with patch.object(self.c,'_validate_observed_resource_binding',create=True,return_value=None),self.assertRaises(APIError):
            self.resolve(refs=[{'role':'pi01','recordId':source['id']}],scope={'workflowId':unrelated['id'],'resourceId':'Q11'})

    def test_classifier_and_heldout_counts_are_part_of_receipt_projection(self):
        from backend.science_dependencies import result_content
        base={'dataKind':'real','comparison':{'on':{'counts':[[9,1],[1,9]]}},'classifierProtocol':{'trainingCaptures':[0,1],'validationCaptures':[2,3]}}
        changed=deepcopy(base);changed['classifierProtocol']['validationCaptures']=[0,1]
        self.assertNotEqual(digest(result_content(base)),digest(result_content(changed)))
        changed=deepcopy(base);changed['comparison']['on']['counts']=[[10,0],[0,10]]
        self.assertNotEqual(digest(result_content(base)),digest(result_content(changed)))


if __name__=='__main__':unittest.main()
