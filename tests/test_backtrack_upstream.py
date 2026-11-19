"""Offline temporary DB/native receipt fixtures. Never call hardware or model."""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock, patch

from backend.backtrack_admission import admit_backtrack, eligible_backtracks
from backend.accepted_upstream_context import resolve_accepted_upstream, attach_accepted_upstream, MAX_BYTES
from backend.common import APIError, ident
from backend.quality import GATES, evaluate, validate_proposal
from backend.decision_context import build_decision_context
from backend.prior_failure_context import javascript_json_units_upper_bound
from tests import test_acquisition_budget as budget_fixtures
from tests import test_capture_diagnostics as source_fixtures


class AttemptAdmissionTests(unittest.TestCase):
    def test_known_integer_limits_filter_without_any_mutation(self):
        wf = {'maxAttemptsPerNode': 8, 'iterations': 20, 'maxWorkflowAttempts': 60,
              'reservedShots': 82_168_000, 'nodes': [{'id': 'full', 'attempt': 8}, {'id': 'open', 'attempt': 7}]}
        before = deepcopy(wf)
        summary = eligible_backtracks(wf, ['full', 'open'])
        self.assertEqual(summary['allowed'], ['open'])
        self.assertEqual(summary['excluded'][0]['reasonCode'], 'ATTEMPT_BUDGET')
        self.assertEqual(wf, before)
        for key, value in [('maxAttemptsPerNode', True), ('iterations', None), ('maxWorkflowAttempts', 60.0)]:
            with self.subTest(key=key), self.assertRaises(APIError):
                admit_backtrack({**wf, key: value}, wf['nodes'][1])
        with self.assertRaises(APIError) as error:
            admit_backtrack({**wf, 'iterations': 60}, wf['nodes'][1])
        self.assertEqual(error.exception.code, 'WORKFLOW_ATTEMPT_BUDGET')


class ControllerBacktrackTests(unittest.TestCase):
    setUp = budget_fixtures.NativeBudgetControllerTests.setUp
    tearDown = budget_fixtures.NativeBudgetControllerTests.tearDown
    create_at = budget_fixtures.NativeBudgetControllerTests.create_at

    def fixture(self, *, exhausted=True, global_exhausted=False, expensive=False):
        wf = self.create_at(reserved=44_008_000 if expensive else 0)
        target_id = next(n['id'] for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        with self.controller.store.transaction() as rt:
            wf = self.controller.get(wf['id'], rt)
            wf.update(status='attention', engineState='quality_attention')
            if global_exhausted: wf['iterations'] = wf['maxWorkflowAttempts']
            target = self.controller._node(wf, target_id)
            target.update(status='success', engineState='verified', mainProcedureComplete=True,
                          result={'dataKind': 'real', 'quality': {'status': 'passed'}, 'sentinel': 'preserve'},
                          parameterVersions={'readout_amp': 2}, latestAttemptId='offline-old',
                          attempt=wf['maxAttemptsPerNode'] if exhausted else 1)
            if expensive:
                target['parameterOverrides'] = {'points': 121, 'capture_repeats': 6, 'acquisitions_per_point': 8, 'shots': 2000}
            self.controller.store.save(rt, wf, 'offline_backtrack_fixture')
        return self.controller.get(wf['id']), target_id

    def test_model_cannot_invalidate_exhausted_target_even_if_proposed(self):
        wf, target_id = self.fixture()
        target_before = deepcopy(self.controller._node(wf, target_id))
        self.controller._brain = Mock(return_value={'status': 'ok', 'decision': {'action': 'backtrack',
            'targetNodeId': target_id, 'reason': 'OFFLINE', 'evidenceIds': []}})
        with patch.object(self.controller, '_validate_plan', side_effect=AssertionError('attempt check must come first')):
            self.assertFalse(self.controller._decide(wf['id'], wf['currentNodeId']))
        current = self.controller.get(wf['id'])
        self.assertEqual(self.controller._node(current, target_id), target_before)
        self.assertEqual(current['edges'], wf['edges'])
        self.assertEqual(current['reservedShots'], wf['reservedShots'])
        self.assertEqual(current['currentNodeId'], wf['currentNodeId'])
        self.assertEqual(self.controller._node(current)['lastDecisionError']['code'], 'ATTEMPT_BUDGET')
        payload = self.controller._brain.call_args.args[1]
        self.assertNotIn(target_id, payload['allowedBacktrackTargets'])
        self.assertTrue(any(x['nodeId'] == target_id for x in payload['backtrackAdmission']['excluded']))
        with self.controller.store.connect() as rt:
            actions = [json.loads(r[0])['action'] for r in rt.db.execute('SELECT payload FROM platform_decisions')]
            self.assertEqual(actions, ['reject'])

    def test_operator_rejection_is_atomic_for_attempt_global_and_joint_shots_limits(self):
        for options, code in [({}, 'ATTEMPT_BUDGET'), ({'exhausted': False, 'global_exhausted': True}, 'WORKFLOW_ATTEMPT_BUDGET'),
                              ({'exhausted': False, 'expensive': True}, 'JOINT_SHOTS_BUDGET')]:
            wf, target_id = self.fixture(**options)
            with self.controller.store.connect() as rt:
                before = self.controller.store.get(rt, wf['id'])
                decisions = rt.db.execute('SELECT count(*) FROM platform_decisions').fetchone()[0]
                commands = rt.db.execute('SELECT count(*) FROM platform_commands').fetchone()[0]
            with self.subTest(code=code), self.assertRaises(APIError) as error:
                self.controller.action(wf['id'], {'action': 'backtrack', 'targetNodeId': target_id,
                    'expectedRevision': wf['revision'], 'operationId': ident('offline-command')})
            self.assertEqual(error.exception.code, code)
            with self.controller.store.connect() as rt:
                self.assertEqual(self.controller.store.get(rt, wf['id']), before)
                self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_decisions').fetchone()[0], decisions)
                self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_commands').fetchone()[0], commands)

    def test_operator_resource_busy_still_precedes_admission_and_valid_queue_does_not_reserve(self):
        wf, target_id = self.fixture(exhausted=False)
        request = {'action': 'backtrack', 'targetNodeId': target_id, 'expectedRevision': wf['revision'], 'operationId': ident('offline-command')}
        with patch.object(self.controller, '_has_lease', return_value=True), self.assertRaises(APIError) as error:
            self.controller.action(wf['id'], request)
        self.assertEqual(error.exception.code, 'RESOURCE_BUSY')
        queued = self.controller.action(wf['id'], request)
        self.assertEqual(queued['engineState'], 'backtrack_queued')
        self.assertEqual(queued['reservedShots'], wf['reservedShots'])
        self.assertEqual(self.controller._node(queued, target_id)['attempt'], 1)
        self.assertEqual(self.controller._node(queued, target_id)['result']['quality']['status'], 'passed')
        self.assertTrue(self.controller._node(queued, target_id)['result']['stale'])
        self.assertEqual(self.controller.action(wf['id'], request), queued)


class AcceptedUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_fixtures.CaptureDiagnosticsTests.setUpClass()
        base = source_fixtures.CaptureDiagnosticsTests
        cls.root, cls.bindings, cls.fixtures = base.root, base.bindings, base.fixtures

    @classmethod
    def tearDownClass(cls):
        source_fixtures.CaptureDiagnosticsTests.tearDownClass()

    def fixture(self):
        source_fixtures.CaptureDiagnosticsTests.fixture(self, independent=True)
        self.upstream = self.node
        self.upstream.update(status='success', engineState='verified', mainProcedureComplete=True,
                             parameterVersions=deepcopy(self.attempt['parameterVersions']), attempt=1, dependsOn=[])
        self.attempt.update(state='verified', number=1)
        self.workflow.update(gates=deepcopy(GATES), gatesVersion='diagnostic-gates/1')
        self.node = {'id': 'Q11:current:cpmg', 'resourceId': 'Q11', 'procedureId': 'sq_cpmg',
                     'dependsOn': [self.upstream['id']], 'result': {'dataKind': 'real', 'artifacts': []}}
        self.workflow['nodes'].append(self.node)
        db = sqlite3.connect(self.db_path); db.row_factory = sqlite3.Row
        db.executescript('ALTER TABLE platform_attempts ADD COLUMN number INTEGER; DROP TABLE IF EXISTS parameters; '
                         'CREATE TABLE parameters(name TEXT PRIMARY KEY,value TEXT,revision INTEGER);')
        results = []
        for row in db.execute('SELECT * FROM platform_effects ORDER BY operation_id').fetchall():
            result = json.loads(row['result'])
            result['analysisQuality'].update(accepted=True, reasons=[])
            result['metrics'].update(relative_stderr=.1, t2_s=2e-6 if row['operation_id'].endswith('/measure') else 1.6e-6)
            artifact = next(a for a in result['artifacts'] if a['name'] == 'platform_analysis.json')
            path = Path(artifact['sourcePath'])
            doc = json.loads(path.read_text(encoding='utf8'))
            doc.update(analysis=result['analysisQuality'], metrics=result['metrics'])
            path.write_text(json.dumps(doc), encoding='utf8')
            artifact.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            artifact['id'] = 'real-' + artifact['sha256'][:24]
            db.execute('INSERT OR REPLACE INTO platform_artifacts VALUES(?,?,?,?)',
                       (artifact['id'], str(path), artifact['sha256'], json.dumps(artifact)))
            result = evaluate(result, 'sq_echo', 'real', GATES, independent=row['operation_id'].endswith('/verify'))
            db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?', (json.dumps(result), row['operation_id']))
            results.append(result)
        result = deepcopy(results[1]); result['selectionResult'] = results[0]
        self.upstream['result'] = self.attempt['result'] = result
        db.execute('UPDATE platform_attempts SET number=1,payload=?', (json.dumps(self.attempt),))
        db.execute('UPDATE platform_workflows SET payload=?', (json.dumps(self.workflow),))
        db.commit(); db.close()

    def resolve(self, wf=None, node=None, implementation=source_fixtures.IMPLEMENTATION):
        return resolve_accepted_upstream(self.root, self.db_path, wf or self.workflow, node or self.node,
                                         implementation_id=implementation)

    def persist(self):
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute('UPDATE platform_workflows SET payload=?', (json.dumps(self.workflow),))
            db.execute('UPDATE platform_attempts SET payload=?', (json.dumps(self.attempt),))

    def test_authenticated_pair_keeps_stage_metrics_and_does_not_expand_current_evidence(self):
        self.fixture(); before = deepcopy(self.workflow)
        context = self.resolve()
        self.assertEqual(len(context['cases']), 1, context)
        case = context['cases'][0]
        self.assertEqual([o['metrics']['t2_s'] for o in case['operations']], [2e-6, 1.6e-6])
        self.assertEqual([o['formalQuality']['status'] for o in case['operations']], ['unverified', 'passed'])
        self.assertNotEqual(*[o['sourceReferences'][0]['sha256'] for o in case['operations']])
        payload = build_decision_context({'runId': self.workflow['id'], 'nodeId': self.node['id'],
            'revision': self.workflow['revision'], 'observations': {'result': self.node['result']}})
        attached = attach_accepted_upstream(payload, context)
        self.assertEqual({k:v for k,v in attached.items() if k != 'acceptedUpstreamContext'}, payload)
        upstream_id = case['operations'][0]['sourceReferences'][0]['artifactRef']
        with self.assertRaises(APIError) as error:
            validate_proposal({'action': 'wait', 'reason': 'offline', 'evidenceIds': [upstream_id]}, self.workflow, self.node, {})
        self.assertEqual(error.exception.code, 'BAD_EVIDENCE')
        self.assertEqual(self.workflow, before)

    def test_stale_partial_latest_and_native_failed_never_become_reference(self):
        for change in ('stale', 'partial', 'latest', 'native_failed', 'missing_verify', 'quality', 'node_elapsed'):
            self.fixture()
            if change == 'stale': self.upstream['engineState'] = 'stale'
            elif change == 'partial': self.upstream['result']['partial'] = True
            elif change == 'latest': self.upstream['latestAttemptId'] = 'different'
            elif change == 'native_failed': self.upstream['result']['analysisQuality']['accepted'] = False
            elif change == 'missing_verify': self.attempt['verificationOperationId'] = None
            elif change == 'quality': self.upstream['result']['quality']['criteria'] = {}
            elif change == 'node_elapsed':
                changed = deepcopy(self.workflow); changed['nodes'][0]['elapsed'] = 123
                self.assertEqual(self.resolve(changed)['reasonCode'], 'stale_workflow_snapshot'); continue
            self.persist()
            with self.subTest(change=change):
                self.assertEqual(self.resolve()['cases'], [])

    def test_ledger_value_or_version_source_hash_and_cleanup_are_required(self):
        for change in ('value', 'version', 'hash', 'cleanup', 'effect_state'):
            self.fixture()
            with closing(sqlite3.connect(self.db_path)) as db, db:
                if change in ('value', 'version'):
                    db.execute('INSERT INTO parameters VALUES(?,?,?)',
                        (self.workflow['id'] + ':Q11:readout_amp', json.dumps(.123 if change == 'value' else .8), 1 if change == 'version' else 0))
                elif change == 'hash':
                    raw = next(a for a in self.upstream['result']['artifacts'] if a['kind'] == 'raw')
                    Path(raw['sourcePath']).write_bytes(b'tampered')
                elif change == 'cleanup':
                    row = db.execute('SELECT operation_id,result FROM platform_effects LIMIT 1').fetchone()
                    value = json.loads(row[1]); value['provenance']['shutdownCommandsCompleted'] = False
                    db.execute('UPDATE platform_effects SET result=? WHERE operation_id=?', (json.dumps(value), row[0]))
                else: db.execute("UPDATE platform_effects SET state='unknown'")
            with self.subTest(change=change): self.assertEqual(self.resolve()['cases'], [])

    def test_snapshot_identity_oversize_optional_context_does_not_displace_core(self):
        self.fixture()
        changed = deepcopy(self.workflow); changed['revision'] += 1
        self.assertEqual(self.resolve(changed)['reasonCode'], 'stale_workflow_snapshot')
        self.assertEqual(self.resolve(implementation='spoof.Adapter')['reasonCode'], 'unaudited_binding')
        context = self.resolve()
        payload = {'runId': self.workflow['id'], 'nodeId': self.node['id'], 'revision': self.workflow['revision'],
                   'observations': {'preserve': 'x' * 190000}}
        output = attach_accepted_upstream(payload, {**context, 'extra': 'x' * (MAX_BYTES + 1)})
        self.assertEqual(output['observations'], payload['observations'])
        self.assertEqual(output['acceptedUpstreamContext']['reasonCode'], 'optional_context_budget')
        self.assertLess(javascript_json_units_upper_bound(output), 198000)


if __name__ == '__main__':
    unittest.main()
