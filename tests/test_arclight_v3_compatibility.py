"""Exact v3 compatibility, temporary DB and explicit synthetic receipts only.

No device discovery/execution. Native plan construction and receipt readers are
real; synthetic receipt data test admission, never qualify a physical source.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
from adapters.f8 import _digest as native_digest
from backend.common import ROOT, APIError, dumps
from backend.controller import Controller
from backend.model_parameter_semantics import build_model_parameter_contract, METHOD_KNOBS
from backend.prior_failure_context import load_audited_bindings, ENTRY_FILES, V3_ID
from backend.quality import evaluate
from backend.workspace import device_key
from tests import test_working_point as working
from tests import test_backtrack_upstream as backtrack
from tests import test_acquisition_budget as budget


def use_v3(case):
    case.adapter = ArcLightAdapter(ROOT, ROOT / 'adapters/arclight_profile_v3.json')
    case.controller.adapter = case.adapter
    case.controller.profile = case.adapter.profile
    case.controller.adapter_id = case.adapter.profile['id']
    case.controller.adapter_profile_hash = case.controller._adapter_fingerprint()


class WorkingPointV3(unittest.TestCase):
    def setUp(self):
        working.WorkingPointTests.setUp(self)
        use_v3(self)
    tearDown = working.WorkingPointTests.tearDown
    create = working.WorkingPointTests.create
    test_new_scope_has_no_inherited_acceptance = working.WorkingPointTests.test_new_ten_node_scope_preserves_all_remaining_full_dependencies_and_old_workflow
    test_profile_or_scope_tamper_rejected = working.WorkingPointTests.test_rejects_other_scope_unapproved_adapter_and_changed_profile_without_creating_task
    test_seed_tamper_rejected = working.WorkingPointTests.test_seed_hash_failure_blocks_creation_and_bounded_reader_rejects_changes
    test_native_plan_budget = working.WorkingPointTests.test_all_ten_native_plans_fit_budget_without_importing_historical_acceptance

    def test_seed_names_the_new_profile_and_manifest_without_historical_acceptance(self):
        wf = self.create(policy='strict')
        binding = load_audited_bindings(ROOT, 'adapters.arclight.ArcLightAdapter')[V3_ID]
        self.assertEqual(wf['seedReference']['currentAdapter']['profileFileSha256'], binding['profileFileSha256'])
        self.assertEqual(wf['seedReference']['currentAdapter']['sourceManifestSha256'], binding['sourceManifestSha256'])
        self.assertEqual(wf['adapterProfileHash'], binding['fingerprint'])
        self.assertFalse(wf['workflowComplete'])
        self.assertEqual(wf['policy'], 'strict')
        self.assertTrue(all(n['attempt'] == 0 for n in wf['nodes']))
        self.adapter.profile['id'] = 'arclight-private-f8-q11-v999'
        with self.assertRaises(APIError) as error:
            working.build_working_point_seed(self.adapter)
        self.assertEqual(error.exception.code, 'WORKING_POINT_ADAPTER_REQUIRED')


class ModelSemanticsV3(unittest.TestCase):
    def test_same_v2_closed_loop_projection_and_unknown_identity_unchanged(self):
        profile = json.loads((ROOT / 'adapters/arclight_profile_v3.json').read_text(encoding='utf8'))
        bounds = {k: {'enum': v['enum']} if isinstance(v, dict) else {'min': v[0], 'max': v[1]}
                  for k, v in profile['parameterBounds'].items()}
        for method in METHOD_KNOBS:
            common = dict(implementation_id='adapters.arclight.ArcLightAdapter', procedure_id=method,
                          parameter_bounds=bounds, effective_parameters={'capture_repeats': 1})
            old = build_model_parameter_contract(profile_id='arclight-private-f8-q11-v2', **common)
            new = build_model_parameter_contract(profile_id=V3_ID, **common)
            self.assertTrue(new['applied'], method)
            self.assertEqual({**new, 'profileId': old['profileId']}, old)
            unknown = build_model_parameter_contract(profile_id='arclight-private-f8-q11-v999', **common)
            self.assertFalse(unknown['applied'])
            self.assertEqual(unknown['parameterBounds'], bounds)


class BacktrackV3(unittest.TestCase):
    def setUp(self):
        budget.NativeBudgetControllerTests.setUp(self)
        use_v3(self)
    tearDown = budget.NativeBudgetControllerTests.tearDown
    create_at = budget.NativeBudgetControllerTests.create_at
    fixture = backtrack.ControllerBacktrackTests.fixture
    def test_model_attempt_bound_and_native_parameter_projection(self):
        backtrack.ControllerBacktrackTests.test_model_cannot_invalidate_exhausted_target_even_if_proposed(self)
        payload = self.controller._brain.call_args.args[1]
        self.assertTrue(payload['modelParameterSemantics']['applied'])
        self.assertEqual(payload['modelParameterSemantics']['profileId'], V3_ID)
        self.assertFalse(payload['modelParameterSemantics']['isExecutionApproval'])
        self.assertTrue({'drive_amp','drive_length_s','readout_hz'}.isdisjoint(payload['parameterBounds']))
    test_operator_atomic_budgets = backtrack.ControllerBacktrackTests.test_operator_rejection_is_atomic_for_attempt_global_and_joint_shots_limits
    test_normal_queue_idempotence_and_lease = backtrack.ControllerBacktrackTests.test_operator_resource_busy_still_precedes_admission_and_valid_queue_does_not_reserve


class DiagnosticV3(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-v3-diagnostic-TEST-ONLY-')
        self.root = Path(self.directory.name)
        self.identity = getattr(self, 'identity', V3_ID)
        self.binding = load_audited_bindings(ROOT, 'adapters.arclight.ArcLightAdapter')[self.identity]
        # Include v3 beside old files to exercise a default-v3 reader of a v2
        # archive; source authentication still selects only that archive's pin.
        names = [e['path'] for e in load_audited_bindings(ROOT, 'adapters.arclight.ArcLightAdapter')[V3_ID]['sourceFiles']] + [
            'adapters/arclight_profile.json', 'adapters/arclight_profile_v2.json',
            'adapters/arclight_analysis_v3/source_manifest.json', 'knowledge/working_point_q11_seed_v1.json']
        for name in names:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        imports = self.root / 'data/imports'
        imports.mkdir(parents=True)
        (imports / 'history.json').write_text(dumps({'version': 1, 'resources': [{'id': 'Q11', 'label': 'Q11'}], 'records': []}), encoding='utf8')
        self.adapter = ArcLightAdapter(self.root, self.binding['profile'])
        self.c = Controller(self.root, adapter=self.adapter, start_worker=False)
        self.guards = [patch.object(ArcLightAdapter, 'execute', side_effect=AssertionError('NO HARDWARE')),
                       patch.object(ArcLightAdapter, 'hardware_ownership', side_effect=AssertionError('NO DISCOVERY'))]
        for guard in self.guards: guard.start()
        self.wf = self.c.create({'name': 'TEST ONLY receipt contract', 'mode': 'real', 'policy': 'autopilot',
                                 'resourceIds': ['Q11'], 'coverage': 'working_point'})
        with self.c.store.transaction() as rt:
            wf = self.c.get(self.wf['id'], rt)
            for node in wf['nodes']:
                if node['procedureId'] == 'sq_rb': continue
                node.update(engineState='verified', status='success', mainProcedureComplete=True,
                            result={'dataKind': 'real', 'quality': {'status': 'passed'}, 'testOnly': True})
                if node['procedureId'] in ('sq_rabi_amplitude', 'sq_rabi_time', 'ro_iq01', 'ro_optimize', 'sq_cpmg'):
                    self.archive(rt, wf, node)
            if self.identity != V3_ID:
                # This fixture represents the historical v2 registered graph,
                # whose bus identifier must remain accepted byte-for-byte.
                for node in wf['nodes']:
                    key = wf['id'] + '/' + node['id']
                    spec = json.loads(rt.db.execute('SELECT spec FROM steps WHERE id=?', (key,)).fetchone()[0])
                    spec['resources'] = ['CY8_SHARED_BUS1']
                    rt.db.execute('UPDATE steps SET spec=? WHERE id=?', (dumps(spec), key))
            wf.update(status='attention', engineState='quality_attention', maxTotalShots=100_000_000,
                      currentNodeId=next(n['id'] for n in wf['nodes'] if n['procedureId'] == 'sq_cpmg'))
            self.c.store.save(rt, wf, 'test_only_receipt_fixture')
        self.wf = self.c.get(self.wf['id'])

    def tearDown(self):
        for guard in reversed(self.guards): guard.stop()
        self.c.close()
        self.directory.cleanup()

    def archive(self, rt, wf, node):
        procedure = node['procedureId']; independent = procedure != 'sq_cpmg'
        parameters, versions = self.c._parameters(rt, wf, node)
        attempt_id = 'TEST-ONLY-' + procedure
        attempt = {'id': attempt_id, 'nodeId': node['id'], 'procedureId': procedure, 'number': 1,
                   'mode': 'real', 'state': 'verified' if independent else 'attention', 'endedAt': 'TEST ONLY',
                   'parameters': deepcopy(parameters), 'parameterVersions': versions,
                   'operationId': attempt_id + '/measure'}
        if independent: attempt['verificationOperationId'] = attempt_id + '/verify'
        results = []
        for stage in ('measure', 'verify') if independent else ('measure',):
            operation = attempt_id + '/' + stage
            request = {'runId': wf['id'], 'nodeId': node['id'], 'attemptId': attempt_id, 'operationId': operation,
                       'resourceId': 'Q11', 'procedureId': procedure, 'parameters': parameters,
                       'parameterVersions': versions, 'adapterId': self.identity, 'adapterProfileHash': wf['adapterProfileHash']}
            plan = self.adapter.build_plan(request)
            self.assertEqual(len(plan['jobs']), 1)
            op_root = self.adapter._operation_dir(operation); folder = op_root / 'results/TEST_ONLY'
            folder.mkdir(parents=True)
            raw = ('SYNTHETIC RECEIPT CONTRACT; NOT PHYSICAL IQ; ' + operation).encode()
            raw_sha = hashlib.sha256(raw).hexdigest()
            native = {'procedureId': procedure, 'accepted': independent, 'reasons': [] if independent else ['TEST ONLY CPMG rejection'],
                      'raw_sha256': raw_sha, 'source_sha256': self.binding['manifest']['platform_analysis.py']}
            metrics = {'r_squared': .95 if independent else .1, 'relative_stderr': .1,
                       'contrast_snr': 10., 'cycles': 2., 'assignment_fidelity': .95}
            candidate = ({'drive_length_s': parameters['drive_length_s']} if procedure == 'sq_rabi_time'
                         else {'drive_amp': parameters['drive_amp']} if procedure == 'sq_rabi_amplitude' else {})
            analysis = {'analysis': native, 'metrics': metrics, 'series': [], 'candidateParameters': candidate}
            job = plan['jobs'][0]; count = plan['expectedJobRows'][0]
            docs = {'raw_scan.npz': raw, 'plan.json': plan, 'axes.json': [], 'job.json': job,
                    'status.json': {'state': 'acquired', 'mode': 'real_hardware', 'job': job['name'],
                                    'points_completed': count, 'points_expected': count},
                    'platform_analysis.json': analysis,
                    'source_hashes.json': {k: v for k, v in self.binding['manifest'].items() if k.startswith('work/server/core/') and k.endswith('.py')},
                    'entry_source_hashes.json': {k: self.binding['manifest'][k] for k in ENTRY_FILES}}
            inventory = []
            for name, document in docs.items():
                path = (op_root if name == 'plan.json' else folder) / name
                content = document if isinstance(document, bytes) else dumps(document).encode('utf8')
                path.write_bytes(content); sha = hashlib.sha256(content).hexdigest()
                item = {'id': 'real-' + sha[:24], 'name': name, 'sha256': sha,
                        'kind': 'raw' if name.endswith('.npz') else 'json', 'sourcePath': str(path)}
                rt.db.execute('INSERT OR REPLACE INTO platform_artifacts VALUES(?,?,?,?)',
                              (item['id'], str(path), sha, dumps(item)))
                inventory.append(item)
            result = {'dataKind': 'real', 'acquiredAt': 'TEST ONLY', 'sourceRunId': operation,
                      'parameters': plan['parameters'], 'metrics': metrics, 'series': [], 'candidateParameters': candidate,
                      'analysisQuality': native, 'artifacts': inventory, 'provenance': {
                          'freshAcquisition': True, 'shutdownCommandsCompleted': True, 'operationId': operation,
                          'runnerSha256': self.binding['profile']['runnerSha256'], 'profileHash': self.binding['executionProfileHashes'][0],
                          'sourceManifest': self.binding['profile']['sourceManifest'], 'parameterVersions': versions}}
            result = evaluate(result, procedure, 'real', wf['gates'], independent=stage == 'verify', rule_version=wf['gatesVersion'])
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',
                          (operation, wf['id'], attempt_id, 'completed', dumps(request), dumps(result)))
            (op_root / 'adapter_intent.json').write_text(dumps({'request': request, 'operationId': operation,
                'requestHash': native_digest(request), 'profileHash': native_digest({**self.adapter.profile, 'enabled': True})}), encoding='utf8')
            (op_root / 'adapter_receipt.json').write_text(dumps({'status': 'completed', 'result': result,
                'operationId': operation, 'requestHash': native_digest(request)}), encoding='utf8')
            (op_root / 'completion.json').write_text(dumps({'state': 'completed', 'shutdown_ok': True,
                'acquiredAt': result['acquiredAt'], 'analysis': analysis, 'files': [a['sourcePath'] for a in inventory]}), encoding='utf8')
            results.append(result)
        result = deepcopy(results[-1])
        if independent:
            result['selectionResult'] = results[0]
            result['verificationCandidateParameters'] = deepcopy(result['candidateParameters'])
            result['candidateParameters'] = self.c._normalize_candidate(procedure, results[0]['candidateParameters'])
        attempt['result'] = result
        node.update(attempt=1, latestAttemptId=attempt_id, result=result, parameterVersions=versions,
                    engineState=attempt['state'], status='success' if independent else 'attention')
        rt.db.execute('INSERT INTO platform_attempts VALUES(?,?,?,?,?)', (attempt_id, wf['id'], node['id'], 1, dumps(attempt)))

    def test_actual_inspection_and_queue_authenticate_new_sources_and_native_pair_budget(self):
        with self.c.store.connect() as rt: before = list(rt.db.iterdump())
        inspected = self.c.inspect_rb_diagnostic(self.wf['id'])
        self.assertTrue(inspected['eligible'], inspected)
        self.assertEqual(inspected['budgetLookahead']['jointShots'], 5_312_000)
        self.assertFalse(inspected['budgetLookahead']['reservation'])
        with self.c.store.connect() as rt: self.assertEqual(before, list(rt.db.iterdump()))
        payload = {'action': 'queue_rb_diagnostic', 'operationId': 'TEST-ONLY-QUEUE', 'expectedRevision': self.wf['revision'],
                   'nodeId': inspected['sourceNodeId'], 'attemptId': inspected['sourceAttemptId'],
                   'policyVersion': inspected['policyVersion'], 'sourceProofSha256': inspected['sourceProofSha256']}
        queued = self.c.action(self.wf['id'], payload)
        self.assertEqual(queued['rbDiagnostic']['adapterId'], V3_ID)
        self.assertEqual(queued['reservedShots'], self.wf['reservedShots'])
        self.assertEqual(queued['rbDiagnostic']['publication'], 'disabled_diagnostic_only')
        self.assertEqual(self.c.action(self.wf['id'], payload), queued)

    def test_unknown_binding_changed_source_wrong_resource_and_lease_rejected(self):
        # Start from a fully valid actual native proof, then corrupt one edge.
        self.assertTrue(self.c.inspect_rb_diagnostic(self.wf['id'])['eligible'])
        with self.c.store.transaction() as rt:
            key = self.wf['id'] + '/' + self.wf['nodes'][0]['id']
            raw = rt.db.execute('SELECT spec FROM steps WHERE id=?', (key,)).fetchone()[0]
            spec = json.loads(raw); spec['resources'] = ['foreign-device']
            rt.db.execute('UPDATE steps SET spec=? WHERE id=?', (dumps(spec), key))
        self.assertEqual(self.c.inspect_rb_diagnostic(self.wf['id'])['reasons'][0]['code'], 'RB_DIAGNOSTIC_GRAPH')
        with self.c.store.transaction() as rt: rt.db.execute('UPDATE steps SET spec=? WHERE id=?', (raw, key))
        source = self.root / 'adapters/arclight_analysis_v3/platform_analysis.py'
        original = source.read_bytes(); source.write_bytes(original + b'\n# test tamper\n')
        self.assertFalse(self.c.inspect_rb_diagnostic(self.wf['id'])['eligible'])
        source.write_bytes(original)
        with self.c.store.transaction() as rt:
            rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)', (device_key(self.adapter.profile), 'other', 'a', 'quarantined', 'token'))
        self.assertEqual(self.c.inspect_rb_diagnostic(self.wf['id'])['reasons'][0]['code'], 'RECONCILIATION_REQUIRED')

    def test_raw_receipt_budget_and_unknown_profile_fail_closed(self):
        from backend.rb_diagnostic import build_proof
        self.assertTrue(self.c.inspect_rb_diagnostic(self.wf['id'])['eligible'])
        source = next(n for n in self.wf['nodes'] if n['procedureId'] == 'sq_cpmg')
        raw = Path(next(a['sourcePath'] for a in source['result']['artifacts'] if a['kind'] == 'raw'))
        old = raw.read_bytes(); raw.write_bytes(old + b'changed')
        self.assertEqual(self.c.inspect_rb_diagnostic(self.wf['id'])['reasons'][0]['code'], 'RB_DIAGNOSTIC_SOURCE')
        raw.write_bytes(old)
        timing = next(n for n in self.wf['nodes'] if n['procedureId'] == 'sq_rabi_time')
        intent = self.adapter._operation_dir(timing['latestAttemptId'] + '/verify') / 'adapter_intent.json'
        old = intent.read_bytes(); value = json.loads(old); value['requestHash'] = '0' * 64
        intent.write_text(dumps(value), encoding='utf8')
        self.assertEqual(self.c.inspect_rb_diagnostic(self.wf['id'])['reasons'][0]['code'], 'GATE_CALIBRATION_EVIDENCE_REQUIRED')
        intent.write_bytes(old)
        with self.c.store.connect() as rt:
            parameters, versions = self.c._parameters(rt, self.wf, next(n for n in self.wf['nodes'] if n['procedureId'] == 'sq_rb'))
            for identity in ('arclight-private-f8-q11-v1', 'arclight-private-f8-q11-v999'):
                with self.subTest(identity=identity), self.assertRaises(APIError) as error:
                    build_proof(self.root, rt.db, {**self.wf, 'adapterId': identity}, parameters, versions,
                                implementation_id='adapters.arclight.ArcLightAdapter')
                self.assertEqual(error.exception.code, 'RB_DIAGNOSTIC_SCOPE')
        with self.c.store.transaction() as rt:
            wf = self.c.get(self.wf['id'], rt); wf['maxTotalShots'] = wf['reservedShots'] + 5_311_999
            self.c.store.save(rt, wf, 'TEST ONLY insufficient budget')
        self.assertEqual(self.c.inspect_rb_diagnostic(self.wf['id'])['reasons'][0]['code'], 'JOINT_SHOTS_BUDGET')

    def test_default_v3_inspects_old_v2_with_original_binding_and_graph(self):
        old = DiagnosticV3('test_actual_inspection_and_queue_authenticate_new_sources_and_native_pair_budget')
        old.identity = 'arclight-private-f8-q11-v2'
        old.setUp()
        try:
            before = old.c.inspect_rb_diagnostic(old.wf['id'])
            self.assertTrue(before['eligible'], before)
            old.c.adapter = ArcLightAdapter(old.root, old.root / 'adapters/arclight_profile_v3.json')
            old.c.profile = old.c.adapter.profile; old.c.adapter_id = V3_ID
            old.c.adapter_profile_hash = old.c._adapter_fingerprint()
            after = old.c.inspect_rb_diagnostic(old.wf['id'])
            self.assertEqual(after, before)
            bound = old.c.for_workflow(old.wf['id'])
            self.assertEqual(bound.adapter_id, 'arclight-private-f8-q11-v2')
            with old.c.store.connect() as rt:
                proof = bound._rb_diagnostic_proof(rt, old.wf)[0]
            self.assertEqual(proof['adapterProfileHash'], old.binding['fingerprint'])
            self.assertEqual(proof['adapterId'], 'arclight-private-f8-q11-v2')
        finally:
            # Restore nested class patches before the outer fixture's patches.
            old.tearDown()

    def test_old_analysis_cannot_be_relabelled_as_new_source_even_with_consistent_registry(self):
        with self.c.store.transaction() as rt:
            wf = self.c.get(self.wf['id'], rt)
            node = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_cpmg')
            result = node['result']; operation = result['sourceRunId']
            result['analysisQuality']['source_sha256'] = load_audited_bindings(
                ROOT, 'adapters.arclight.ArcLightAdapter')['arclight-private-f8-q11-v2']['manifest']['platform_analysis.py']
            artifact = next(a for a in result['artifacts'] if a['name'] == 'platform_analysis.json')
            path = Path(artifact['sourcePath']); doc = json.loads(path.read_bytes())
            doc['analysis'] = deepcopy(result['analysisQuality']); path.write_text(dumps(doc), encoding='utf8')
            sha = hashlib.sha256(path.read_bytes()).hexdigest(); artifact.update(sha256=sha, id='real-' + sha[:24])
            rt.db.execute('INSERT OR REPLACE INTO platform_artifacts VALUES(?,?,?,?)', (artifact['id'], str(path), sha, dumps(artifact)))
            rt.db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?', (dumps(result), operation))
            attempt = json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', (node['latestAttemptId'],)).fetchone()[0])
            attempt['result'] = deepcopy(result)
            rt.db.execute('UPDATE platform_attempts SET payload=? WHERE id=?', (dumps(attempt), attempt['id']))
            self.c.store.save(rt, wf, 'TEST ONLY wrong version source')
        self.assertEqual(self.c.inspect_rb_diagnostic(self.wf['id'])['reasons'][0]['code'], 'RB_DIAGNOSTIC_SOURCE')


if __name__ == '__main__':
    unittest.main()
