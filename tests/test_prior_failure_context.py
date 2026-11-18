"""Offline synthetic fixtures only. No instruments, services or model requests."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
from backend.common import ROOT, dumps
from backend.controller import Controller
from backend.decision_context import current_attempt_evidence_ids
from backend.quality import validate_proposal
from backend.common import APIError
from backend.prior_failure_context import (PROFILE_FILES, ENTRY_FILES, MAX_CONTEXT_BYTES,
    audited_bindings, eligible_header, project_failure_case, bounded_context, attach_prior_context,
    javascript_json_units_upper_bound)


def node_json_check(values, *, decisions=False):
    """Real agent validator only; imports no provider and never sends a request."""
    script = '''import fs from 'node:fs';
import {validateDecisionInput, collectEvidence} from './agent/src/validation.mjs';
const input=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(input.values.map(value=>{
  const units=JSON.stringify(value).length;
  if(!input.decisions)return {units};
  let status='passed';try{validateDecisionInput(value);}catch(error){status=error.message;}
  return {units,status,evidence:[...collectEvidence(value)]};
})));'''
    result = subprocess.run(['node', '--input-type=module', '-e', script],
        input=json.dumps({'values': values, 'decisions': decisions}, ensure_ascii=True, allow_nan=False),
        capture_output=True, encoding='utf-8', cwd=ROOT, check=True, timeout=15)
    return json.loads(result.stdout)


class PriorFailureContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qcal-prior-failure-offline-')
        self.root = Path(self.temp.name)
        self.profile_bytes = {identity: (ROOT / name).read_bytes() for identity, (name, _) in PROFILE_FILES.items()}
        self.manifest_bytes = (ROOT / 'adapters/arclight_runtime/source_manifest.json').read_bytes()
        self.bindings = audited_bindings('adapters.arclight.ArcLightAdapter', self.profile_bytes, self.manifest_bytes)
        for identity, (name, _) in PROFILE_FILES.items():
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(self.profile_bytes[identity])
        destination = self.root / 'adapters/arclight_runtime/source_manifest.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.manifest_bytes)
        self.node = {'id': 'Q11:s01_readout_frequency:ro_power_shift', 'resourceId': 'Q11',
                     'procedureId': 'ro_power_shift', 'latestAttemptId': 'active-current-attempt'}
        self.current = {'id': 'current-run', 'adapterId': 'arclight-private-f8-q11-v2',
            'adapterProfileHash': self.bindings['arclight-private-f8-q11-v2']['fingerprint'],
            'mode': 'real', 'nodes': [deepcopy(self.node)],
            'lastBudgetUpdate': {'source': 'operator', 'reason': 'OFFLINE operator explanation, not measured evidence', 'updatedAt': 'offline'}}

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, *, independent=False, attempt_name='offline-attempt', controller_gate_failure=False):
        binding = self.bindings['arclight-private-f8-q11-v1']
        workflow = {'id': 'archived-run', 'mode': 'real', 'adapterId': 'arclight-private-f8-q11-v1',
            'adapterProfileHash': binding['fingerprint'], 'nodes': [deepcopy(self.node)]}
        if controller_gate_failure:
            workflow.update(gatesVersion='offline-gates/1', gates={'ro_power_shift': {'contrast_snr': {'min': 13.0}}})
        parameters = {k: v for k, v in binding['profile']['resources']['Q11'].items() if k not in ('q', 'calibrationStatus')}
        parameters.update(shots=2000, capture_repeats=2, acquisitions_per_point=1, points=61, span_hz=4e6)
        versions = {k: 0 for k in binding['profile']['resources']['Q11'] if k not in ('q', 'calibrationStatus')}
        attempt = {'id': attempt_name, 'nodeId': self.node['id'], 'procedureId': 'ro_power_shift', 'number': 1,
            'mode': 'real', 'state': 'attention', 'endedAt': '2026-11-18T00:00:00Z',
            'operationId': attempt_name + '/measure', 'parameterVersions': versions}
        if independent:
            attempt['verificationOperationId'] = attempt_name + '/verify'
        effects, checked = [], {}
        operations = [attempt['operationId']] + ([attempt['verificationOperationId']] if independent else [])
        for operation in operations:
            request = {'runId': workflow['id'], 'nodeId': attempt['nodeId'], 'attemptId': attempt['id'],
                'operationId': operation, 'resourceId': 'Q11', 'procedureId': 'ro_power_shift',
                'adapterId': workflow['adapterId'], 'adapterProfileHash': workflow['adapterProfileHash'],
                'parameterVersions': deepcopy(versions), 'parameters': deepcopy(parameters)}
            folder = 'results/offline-fixture'
            job = {'name': 'SYNTHETIC_OFFLINE_POWER', 'kind': 's21_power'}
            plan = {'operationId': operation, 'resourceId': 'Q11', 'procedureId': 'ro_power_shift',
                'parameters': deepcopy(parameters), 'requestIdentity': {k: request[k] for k in
                    ('runId', 'nodeId', 'attemptId', 'operationId', 'resourceId', 'procedureId', 'parameterVersions')},
                'jobs': [job], 'expectedJobRows': [4]}
            is_last = operation == operations[-1]
            raw_bytes = ('SYNTHETIC FIXTURE BYTES, NOT REAL IQ: ' + operation).encode()
            raw_sha = hashlib.sha256(raw_bytes).hexdigest()
            native = {'accepted': not is_last or controller_gate_failure, 'procedureId': 'ro_power_shift',
                'reasons': ['OFFLINE native repeat failure'] if is_last and not controller_gate_failure else [],
                'raw_sha256': raw_sha, 'source_sha256': binding['manifest']['platform_analysis.py']}
            analysis = {'analysis': native, 'metrics': {'contrast_snr': 12.0}, 'series': [], 'candidateParameters': {}}
            documents = [
                ('raw_scan.npz', 'raw', raw_bytes, folder), ('plan.json', 'json', plan, '.'),
                ('status.json', 'json', {'state': 'acquired', 'mode': 'real_hardware', 'job': job['name'], 'points_completed': 4, 'points_expected': 4}, folder),
                ('job.json', 'json', job, folder), ('platform_analysis.json', 'json', analysis, folder),
                ('source_hashes.json', 'json', {k: v for k, v in binding['manifest'].items() if k.startswith('work/server/core/') and k.endswith('.py')}, folder),
                ('entry_source_hashes.json', 'json', {k: binding['manifest'][k] for k in ENTRY_FILES}, folder),
            ]
            artifacts, proofs = [], []
            op_root = self.root / 'data/runs/arclight_operations' / hashlib.sha256(operation.encode()).hexdigest()
            for name, kind, document, relative in documents:
                raw = document if kind == 'raw' else dumps(document).encode()
                sha = hashlib.sha256(raw).hexdigest()
                path = op_root / relative / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                artifact = {'id': 'real-' + sha[:24], 'sha256': sha, 'name': name, 'kind': kind, 'sourcePath': str(path)}
                artifacts.append(artifact)
                proofs.append({'artifactRef': artifact['id'], 'sha256': sha, 'name': name, 'kind': kind,
                    'folder': relative, 'registryHashVerified': True, **({'document': deepcopy(document)} if kind != 'raw' else {})})
            result = {'dataKind': 'real', 'acquiredAt': '2026-11-18T00:00:00Z', 'sourceRunId': operation,
                'parameters': deepcopy(parameters), 'analysisQuality': native, 'metrics': analysis['metrics'],
                'series': [], 'candidateParameters': {}, 'artifacts': artifacts,
                'provenance': {'freshAcquisition': True, 'shutdownCommandsCompleted': True, 'operationId': operation,
                    'runnerSha256': binding['profile']['runnerSha256'], 'profileHash': binding['executionProfileHashes'][1],
                    'sourceManifest': binding['profile']['sourceManifest'], 'parameterVersions': deepcopy(versions)},
                'quality': {'status': 'failed' if is_last else 'unverified', 'reasons': ['OFFLINE fixture']}}
            if controller_gate_failure and is_last:
                result['quality'] = {'status': 'failed', 'ruleVersion': workflow['gatesVersion'],
                    'criteria': deepcopy(workflow['gates']['ro_power_shift']),
                    'reasons': ['contrast_snr=12 does not satisfy min=13', 'Independent verification still required']}
            effects.append({'operation_id': operation, 'workflow_id': workflow['id'], 'attempt_id': attempt['id'],
                            'state': 'completed', 'request': request, 'result': result})
            checked[operation] = proofs
        attempt['result'] = deepcopy(effects[-1]['result'])
        if independent:
            attempt['result']['selectionResult'] = deepcopy(effects[0]['result'])
        return {'workflow': workflow, 'attempt': attempt, 'effects': effects, 'files': checked}

    def project(self, record):
        return project_failure_case(record, self.current, self.node, self.bindings)

    def test_native_accepted_controller_numeric_gate_failure_keeps_both_layers_truthful(self):
        record = self.fixture(controller_gate_failure=True)
        before = deepcopy(record)
        case = self.project(record)
        self.assertEqual(case['failureClass'], 'controller_gate_failed')
        self.assertTrue(case['nativeAccepted'])
        self.assertEqual(case['nativeFailureReasons'], [])
        self.assertEqual(case['controllerFailureReasons'], record['attempt']['result']['quality']['reasons'])
        self.assertEqual(case['controllerGateFailure'], {'ruleVersion': 'offline-gates/1',
            'criteria': {'contrast_snr': {'min': 13.0}},
            'failedCriteria': [{'metric': 'contrast_snr', 'value': 12.0, 'criteria': {'min': 13.0}}]})
        self.assertEqual(record, before)
        old = self.project(self.fixture())
        self.assertEqual(old['failureClass'], 'native_analysis_failed')
        self.assertFalse(old['nativeAccepted'])
        self.assertEqual(old['nativeFailureReasons'], ['OFFLINE native repeat failure'])

    def test_native_accepted_passed_or_missing_verification_only_is_not_a_failure_case(self):
        for damage in ('passed', 'numeric_pass', 'missing_metric', 'criteria_changed', 'rule_version_changed'):
            record = self.fixture(controller_gate_failure=True)
            result = record['attempt']['result']
            if damage == 'passed': result['quality']['status'] = 'passed'
            elif damage == 'numeric_pass': result['metrics']['contrast_snr'] = 14.0
            elif damage == 'missing_metric': result['metrics'] = {}
            elif damage == 'criteria_changed': result['quality']['criteria']['contrast_snr']['min'] = 15.0
            elif damage == 'rule_version_changed': result['quality']['ruleVersion'] = 'other'
            with self.subTest(damage=damage): self.assertIsNone(self.project(record))

    def test_native_accepted_gate_failure_still_requires_complete_unchanged_authenticated_sources(self):
        for damage in ('partial', 'unknown_effect', 'raw_registry', 'source_hash', 'analysis_document'):
            record = self.fixture(controller_gate_failure=True)
            if damage == 'partial': record['attempt']['result']['partial'] = True
            elif damage == 'unknown_effect': record['effects'][0]['state'] = 'unknown'
            elif damage == 'raw_registry': record['files'][record['attempt']['operationId']][0]['registryHashVerified'] = False
            elif damage == 'source_hash': record['attempt']['result']['provenance']['runnerSha256'] = '0' * 64
            elif damage == 'analysis_document':
                document = next(f for f in record['files'][record['attempt']['operationId']] if f['name'] == 'platform_analysis.json')['document']
                document['analysis']['accepted'] = False
            with self.subTest(damage=damage): self.assertIsNone(self.project(record))

    def test_controller_reads_native_accepted_failed_case_but_excludes_tampered_raw(self):
        record = self.fixture(controller_gate_failure=True)
        controller, db = self.database(record)
        try:
            before_changes = db.total_changes
            context = controller._prior_failure_context(SimpleNamespace(db=db), self.current, self.node)
            self.assertEqual(len(context['cases']), 1)
            self.assertEqual(context['cases'][0]['failureClass'], 'controller_gate_failed')
            self.assertEqual(db.total_changes, before_changes)
            raw = next(a for a in record['effects'][0]['result']['artifacts'] if a['kind'] == 'raw')
            Path(raw['sourcePath']).write_bytes(b'OFFLINE TAMPERED FIXTURE')
            self.assertEqual(controller._prior_failure_context(SimpleNamespace(db=db), self.current, self.node)['cases'], [])
        finally:
            db.close()

    def database(self, record):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.executescript('''CREATE TABLE platform_workflows(id TEXT PRIMARY KEY,payload TEXT);
            CREATE TABLE platform_attempts(id TEXT PRIMARY KEY,workflow_id TEXT,node_id TEXT,payload TEXT);
            CREATE TABLE platform_effects(operation_id TEXT,workflow_id TEXT,attempt_id TEXT,state TEXT,request TEXT,result TEXT);
            CREATE TABLE platform_artifacts(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,payload TEXT);''')
        wf, attempt = record['workflow'], record['attempt']
        db.execute('INSERT INTO platform_workflows VALUES(?,?)', (wf['id'], dumps(wf)))
        db.execute('INSERT INTO platform_attempts VALUES(?,?,?,?)', (attempt['id'], wf['id'], attempt['nodeId'], dumps(attempt)))
        for effect in record['effects']:
            db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)', tuple(effect[k] if k not in ('request', 'result') else dumps(effect[k]) for k in ('operation_id', 'workflow_id', 'attempt_id', 'state', 'request', 'result')))
            for artifact in effect['result']['artifacts']:
                db.execute('INSERT OR REPLACE INTO platform_artifacts VALUES(?,?,?,?)', (artifact['id'], artifact['sourcePath'], artifact['sha256'], dumps(artifact)))
        controller = Controller.__new__(Controller)
        controller.root = self.root
        controller.adapter = ArcLightAdapter(self.root, self.root / 'adapters/arclight_profile_v2.json')
        return controller, db

    def test_exact_audited_profiles_and_implementation_only(self):
        self.assertEqual(len(self.bindings), 2)
        changed = dict(self.profile_bytes)
        changed['arclight-private-f8-q11-v2'] += b' '
        self.assertEqual(audited_bindings('adapters.arclight.ArcLightAdapter', changed, self.manifest_bytes), {})
        self.assertEqual(audited_bindings('spoof.ArcLightAdapter', self.profile_bytes, self.manifest_bytes), {})
        self.assertEqual(audited_bindings('adapters.arclight.ArcLightAdapter', self.profile_bytes, self.manifest_bytes + b' '), {})

    def test_full_selection_and_independent_failure_are_explanatory_immutable_cases(self):
        for independent in (False, True):
            record = self.fixture(independent=independent)
            before = dumps(record)
            case = self.project(record)
            self.assertIsNotNone(case)
            self.assertEqual(case['failedStage'], 'independent_verification' if independent else 'selection')
            self.assertEqual(case['sourceAdapter'], 'arclight-private-f8-q11-v1')
            self.assertEqual(dumps(record), before)

    def test_header_rejects_partial_errors_wrong_scope_binding_and_malformed_results(self):
        record = self.fixture()
        for damage in ('partial', 'stale', 'verificationPending', 'error', 'passed', 'native_passed', 'resource', 'procedure', 'binding', 'open', 'malformed'):
            with self.subTest(damage=damage):
                r = deepcopy(record)
                if damage in ('partial', 'stale', 'verificationPending'): r['attempt']['result'][damage] = True
                elif damage == 'error': r['attempt']['error'] = {'message': 'hardware exception'}
                elif damage == 'passed': r['attempt']['result']['quality']['status'] = 'passed'
                elif damage == 'native_passed': r['attempt']['result']['analysisQuality']['accepted'] = True
                elif damage == 'resource': r['workflow']['nodes'][0]['resourceId'] = 'Q18'
                elif damage == 'procedure': r['attempt']['procedureId'] = 'sq_t1'
                elif damage == 'binding': r['workflow']['adapterProfileHash'] = 'different'
                elif damage == 'open': r['attempt']['endedAt'] = None
                else: r['attempt']['result'] = [1]
                self.assertFalse(eligible_header(r['workflow'], r['attempt'], self.current, self.node, self.bindings))
                self.assertIsNone(self.project(r))

    def test_effect_and_file_proofs_reject_missing_unknown_mismatched_sources_and_versions(self):
        record = self.fixture(independent=True)
        for damage in ('missing_effect', 'unknown', 'wrong_run', 'attempt_versions', 'missing_versions', 'analysis', 'source', 'status', 'job', 'plan', 'unregistered', 'missing_raw', 'exception'):
            with self.subTest(damage=damage):
                r = deepcopy(record)
                effect = r['effects'][-1]
                files = r['files'][effect['operation_id']]
                find = lambda name: next(f for f in files if f['name'] == name)
                if damage == 'missing_effect': r['effects'].pop()
                elif damage == 'unknown': effect['state'] = 'unknown'
                elif damage == 'wrong_run': effect['request']['runId'] = 'another'
                elif damage == 'attempt_versions': r['attempt']['parameterVersions']['drive_amp'] = 4
                elif damage == 'missing_versions': effect['request']['parameterVersions'].pop('pulse_compatibility')
                elif damage == 'analysis': find('platform_analysis.json')['document']['analysis']['accepted'] = True
                elif damage == 'source': find('entry_source_hashes.json')['document']['run_single_qubit.py'] = 'wrong'
                elif damage == 'status': find('status.json')['document']['points_completed'] = 3
                elif damage == 'job': find('job.json')['document']['name'] = 'another'
                elif damage == 'plan': find('plan.json')['document']['requestIdentity']['operationId'] = 'another'
                elif damage == 'unregistered': files[0]['registryHashVerified'] = False
                elif damage == 'missing_raw': files.pop(0)
                else:
                    effect['result']['artifacts'].append({'name': 'analysis_error.json'})
                    r['attempt']['result']['artifacts'] = deepcopy(effect['result']['artifacts'])
                self.assertIsNone(self.project(r))

    def test_controller_reads_authentic_files_without_writing_and_excludes_changed_raw(self):
        record = self.fixture(independent=True)
        controller, db = self.database(record)
        try:
            before = db.total_changes
            out = controller._prior_failure_context(SimpleNamespace(db=db), self.current, self.node)
            self.assertEqual(len(out['cases']), 1)
            self.assertEqual(db.total_changes, before)
            raw = next(a for a in record['effects'][-1]['result']['artifacts'] if a['kind'] == 'raw')
            Path(raw['sourcePath']).write_bytes(b'changed after registration')
            self.assertEqual(controller._prior_failure_context(SimpleNamespace(db=db), self.current, self.node)['cases'], [])
            self.assertEqual(db.total_changes, before)
        finally:
            db.close()

    def test_controller_bad_archived_payload_is_excluded_not_a_current_decision_failure(self):
        record = self.fixture()
        controller, db = self.database(record)
        try:
            for malformed in ([1], {'quality': [1]}, {'quality': {'status': 'failed'}, 'analysisQuality': [1]}):
                damaged = deepcopy(record['attempt']); damaged['result'] = malformed
                db.execute('UPDATE platform_attempts SET payload=?', (dumps(damaged),))
                before = db.total_changes
                out = controller._prior_failure_context(SimpleNamespace(db=db), self.current, self.node)
                self.assertEqual(out['cases'], [])
                self.assertEqual(db.total_changes, before)
        finally:
            db.close()

    def test_archive_context_is_size_limited_and_cannot_become_current_evidence(self):
        case = self.project(self.fixture())
        out = bounded_context([case] * 12, self.current, examined=100)
        self.assertLessEqual(len(out['cases']), 6)
        self.assertEqual(out['examinedCandidates'], 64)
        self.assertLessEqual(len(dumps(out).encode()), MAX_CONTEXT_BYTES)
        self.assertEqual(out['operatorContext']['kind'], 'operator_budget_explanation_not_measurement')
        current_node = {**self.node, 'result': {'artifacts': [{'id': 'current-only'}]}}
        before = current_attempt_evidence_ids(current_node['result'])
        for forbidden in (case['caseRef'], case['sourceAttempt'], case['registeredRawSha256'][0][0]):
            with self.assertRaises(APIError):
                validate_proposal({'action': 'wait', 'reason': 'test', 'evidenceIds': [forbidden]}, self.current, current_node, {})
        self.assertEqual(current_attempt_evidence_ids(current_node['result']), before)
        giant = {**case, 'description': '中' * 20000}
        self.assertEqual(bounded_context([giant], self.current)['cases'], [])

    def test_context_never_overflows_existing_agent_input_limit_or_changes_current_observations(self):
        context = bounded_context([self.project(self.fixture())] * 6, self.current)
        payload = {'observations': {'current': '😀' * 97000, 'artifacts': [{'id': 'current-only'}]}}
        output = attach_prior_context(payload, context)
        self.assertEqual(output['observations'], payload['observations'])
        self.assertLessEqual(len(dumps(output).encode('utf-16-le')) // 2, 198000)
        self.assertEqual(len(context['cases']), 6)
        full = {'observations': {'current': 'x' * 198000}}
        self.assertEqual(attach_prior_context(full, context), full)

    def test_independent_real_boundary_fixture_passes_actual_node_validator_after_history_trimming(self):
        fixture_path = ROOT / 'reports/prior-failure-independent-boundary-fixture.json'
        fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
        base, previously_attached = fixture['base'], fixture['attached']
        context = previously_attached['priorFailureContext']
        base_before, context_before = deepcopy(base), deepcopy(context)
        corrected = attach_prior_context(base, context)
        validation = node_json_check([base, previously_attached, corrected], decisions=True)
        self.assertEqual(validation[0]['status'], 'passed')
        self.assertEqual(validation[1]['status'], 'input_too_large')
        self.assertEqual(validation[2]['status'], 'passed')
        self.assertLessEqual(validation[2]['units'], 200000)
        self.assertEqual(validation[2]['evidence'], validation[0]['evidence'])
        self.assertEqual({key: value for key, value in corrected.items() if key != 'priorFailureContext'}, base)
        self.assertEqual(base, base_before)
        self.assertEqual(context, context_before)

    def test_js_number_bound_covers_format_thresholds_extremes_and_seeded_binary64_samples(self):
        values = [-0.0000010000000000000002, -0.0, 0.0, 1e-5, -1e20,
            5e-324, -5e-324, float.fromhex('0x1.fffffffffffffp+1023'), -float.fromhex('0x1.fffffffffffffp+1023'),
            2**53 - 1, -(2**53 - 1), 2**53, 2**53 + 1, -(2**53 + 1), 10**400, -(10**400)]
        for threshold in (1e-6, 1e21, float(2**53 - 1)):
            values.extend([threshold, -threshold, math.nextafter(threshold, 0),
                           -math.nextafter(threshold, 0), math.nextafter(threshold, math.inf),
                           -math.nextafter(threshold, math.inf)])
        generator = random.Random(20261118)
        for _ in range(2500):
            value = struct.unpack('>d', generator.getrandbits(64).to_bytes(8, 'big'))[0]
            if math.isfinite(value):
                values.append(value)
        measured = node_json_check(values)
        self.assertEqual(measured[0]['units'], 25)
        self.assertEqual(measured[1]['units'], 1)
        for value, actual in zip(values, measured):
            self.assertGreaterEqual(javascript_json_units_upper_bound(value), actual['units'], repr(value))

    def test_js_string_bound_covers_utf16_escaping_and_nested_keys(self):
        values = ['量子😀', '\ud800', '\udfff', '\ud83d\ude00', '"\\', ''.join(chr(c) for c in range(32)), '\u2028\u2029',
            {'😀\n': ['中', {'"\\\ud800': '\t😀'}], 'bools': [None, True, False], 'smallFloat': 1e-5}]
        for value, actual in zip(values, node_json_check(values)):
            self.assertGreaterEqual(javascript_json_units_upper_bound(value), actual['units'])
        self.assertEqual(javascript_json_units_upper_bound('量子😀'), 6)
        self.assertEqual(javascript_json_units_upper_bound('\ud800'), 8)

    def test_optional_context_does_not_repair_nonfinite_or_unsupported_current_payload(self):
        context = {'cases': [{'note': 'optional'}]}
        for value in (math.inf, -math.inf, math.nan, object()):
            original = {'observations': {'value': value}}
            output = attach_prior_context(original, context)
            self.assertNotIn('priorFailureContext', output)
            self.assertEqual(set(output), set(original))
            if isinstance(value, float):
                self.assertTrue(math.isnan(output['observations']['value']) if math.isnan(value)
                                else output['observations']['value'] == value)

    def test_controller_never_scans_past_64_recent_attempts(self):
        record = self.fixture()
        controller, db = self.database(record)
        try:
            for index in range(64):
                invalid = {**deepcopy(record['attempt']), 'id': 'new-invalid-' + str(index), 'result': [1]}
                db.execute('INSERT INTO platform_attempts VALUES(?,?,?,?)', (invalid['id'], record['workflow']['id'], invalid['nodeId'], dumps(invalid)))
            out = controller._prior_failure_context(SimpleNamespace(db=db), self.current, self.node)
            self.assertEqual(out['examinedCandidates'], 64)
            self.assertEqual(out['cases'], [])
        finally:
            db.close()

    def test_registered_json_is_parsed_from_same_hashed_read_without_second_text_read(self):
        record = self.fixture()
        controller, db = self.database(record)
        try:
            with patch.object(Path, 'read_text', side_effect=AssertionError('No unverified second text read')):
                out = controller._prior_failure_context(SimpleNamespace(db=db), self.current, self.node)
            self.assertEqual(len(out['cases']), 1)
        finally:
            db.close()

    def test_actual_decide_wiring_keeps_historical_context_outside_current_evidence(self):
        # Reuse a temporary controller harness; _brain is an in-process recorder,
        # never a provider, server request, or measurement executor.
        from tests.test_model_semantics_controller import ModelSemanticsControllerTests
        harness = ModelSemanticsControllerTests(methodName='runTest')
        harness.setUp()
        try:
            workflow_id, node_id = harness.create_at('ro_power_shift')
            with harness.controller.store.transaction() as rt:
                workflow = harness.controller.get(workflow_id, rt)
                node = harness.controller._node(workflow, node_id)
                node['result']['artifacts'] = [{'id': 'current-only'}]
                harness.controller.store.save(rt, workflow, 'offline_current_evidence')
            context = bounded_context([self.project(self.fixture())], self.current)
            with patch.object(harness.controller, '_prior_failure_context', return_value=context):
                self.assertFalse(harness.decide(workflow_id, node_id))
            payload = harness.payloads[-1]
            self.assertEqual(payload['priorFailureContext'], context)
            self.assertEqual(payload['allowedEvidenceIds'], ['current-only'])
            self.assertNotIn('priorFailureContext', payload['observations'])
            self.assertEqual(payload['history'], [])
            before = deepcopy(harness.controller.detail(workflow_id, node_id)['node']['result'])
            harness.controller._brain = lambda *args: {'status': 'ok', 'decision': {
                'action': 'wait', 'reason': 'OFFLINE forbidden historical citation',
                'evidenceIds': [context['cases'][0]['caseRef']]}}
            with patch.object(harness.controller, '_prior_failure_context', return_value=context):
                self.assertFalse(harness.controller._decide(workflow_id, node_id))
            after = harness.controller.detail(workflow_id, node_id)['node']
            self.assertEqual(after['result'], before)
            self.assertEqual(after['lastDecisionError']['code'], 'BAD_EVIDENCE')
        finally:
            harness.tearDown()


if __name__ == '__main__':
    unittest.main()
