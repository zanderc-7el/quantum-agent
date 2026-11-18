"""Experience admission uses real-mode fake receipts, not synthetic fallback data."""
from copy import deepcopy
import json
import tempfile
import unittest

from backend.common import APIError, digest, dumps
from backend.experience import ExperienceLedger
try:
    from test_controller import make_fixture
except ImportError:
    from tests.test_controller import make_fixture


class ExperienceContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-experience-test-')
        self.controller, self.adapter = make_fixture(self.directory.name)
        self.ledger = ExperienceLedger(parameter_bounds=self.controller._bounds())

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def source(self):
        wf = self.controller.create({'name': 'experience fake receipts', 'mode': 'real',
                                     'resourceIds': ['Q11'], 'policy': 'threshold'})
        node_id = wf['nodes'][0]['id']
        self.adapter.same_bytes = True
        self.assertFalse(self.controller._execute_node(wf['id'], node_id))
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            node = current['nodes'][0]
            prior = self.controller._record_decision(rt, current, node_id,
                {'action': 'retry', 'reason': 'Test observation supports changing drive amplitude',
                 'evidenceIds': [a['id'] for a in node['result']['artifacts']],
                 'parameterChanges': {'drive_amp': .25}}, 'model')
            node['parameterOverrides'] = {'drive_amp': .25}
            current['status'] = 'queued'
            self.controller.store.save(rt, current, 'fixture_retry')
        self.adapter.same_bytes = False
        self.assertTrue(self.controller._execute_node(wf['id'], node_id))
        current = self.controller.get(wf['id'])
        node = current['nodes'][0]
        attempt = self.controller.detail(wf['id'], node_id)['attempts'][-1]
        return current, node, attempt, prior

    def candidate(self):
        source = self.source()
        with self.controller.store.transaction() as rt:
            candidate = self.ledger.observe_verified(rt, *source)
        self.assertIsNotNone(candidate)
        return candidate, source

    def assert_code(self, code, callback):
        with self.assertRaises(APIError) as error:
            callback()
        self.assertEqual(error.exception.code, code)

    def test_empty_ledger_schema_does_not_commit_callers_transaction(self):
        with self.assertRaises(RuntimeError):
            with self.controller.store.transaction() as rt:
                self.ledger.ensure_schema(rt)
                rt.db.execute('INSERT INTO platform_meta VALUES(?,?)', ('rollback-proof', 'value'))
                self.assertEqual(self.ledger.list(rt), [])
                self.assertEqual(self.ledger.published_rules(rt), [])
                raise RuntimeError('rollback caller transaction')
        with self.controller.store.connect() as rt:
            self.assertIsNone(rt.db.execute('SELECT value FROM platform_meta WHERE key=?', ('rollback-proof',)).fetchone())

    def test_verified_retry_creates_scoped_candidate_and_publish_is_idempotent(self):
        candidate, source = self.candidate()
        wf, node, attempt, prior = source
        before = self.controller.get(wf['id'])
        with self.controller.store.connect() as rt:
            persisted_before = tuple(rt.db.execute('SELECT revision,payload FROM platform_workflows WHERE id=?', (wf['id'],)).fetchone())
        with self.controller.store.transaction() as rt:
            self.assertEqual(self.ledger.observe_verified(rt, *source)['id'], candidate['id'])
            self.assertEqual(len(self.ledger.list(rt)), 1)
            self.assertEqual(self.ledger.published_rules(rt), [])
            rule = self.ledger.publish(rt, candidate['id'], 'publish-one')
            self.assertEqual(self.ledger.publish(rt, candidate['id'], 'publish-one'), rule)
            self.assertEqual(self.ledger.publish(rt, candidate['id'], 'publish-second-click'), rule)
            self.assertEqual(len(self.ledger.published_rules(rt)), 1)
            self.assertEqual(self.ledger.list(rt)[0]['status'], 'published')
            self.assert_code('IDEMPOTENCY_CONFLICT', lambda: self.ledger.publish(rt, 'different', 'publish-one'))
        after = self.controller.get(wf['id'])
        # elapsed is a read-time wall-clock projection, not mutable workflow
        # state. Every other exposed field and the complete persisted payload
        # must stay unchanged when an experience rule is published.
        self.assertEqual({k: v for k, v in after.items() if k != 'elapsed'},
                         {k: v for k, v in before.items() if k != 'elapsed'},
                         'Publishing cannot change an existing workflow')
        with self.controller.store.connect() as rt:
            persisted_after = tuple(rt.db.execute('SELECT revision,payload FROM platform_workflows WHERE id=?', (wf['id'],)).fetchone())
        self.assertEqual(persisted_after, persisted_before, 'Publishing cannot write the existing workflow or its revision')
        self.assertEqual(after['gates'], before['gates'], 'Running acceptance gates must remain frozen')
        self.assertEqual(after['gatesVersion'], before['gatesVersion'])
        self.assertEqual(rule['scope']['resourceId'], 'Q11')
        self.assertEqual(rule['scope']['ownerId'], node['ownerId'])
        self.assertEqual(rule['provenance']['attemptId'], attempt['id'])
        self.assertEqual(rule['provenance']['decisionId'], prior['id'])
        self.assertEqual(rule['action']['parameterChanges'], {'drive_amp': .25})
        self.assertFalse(rule['action']['automaticExecution'])
        self.assertTrue(rule['action']['requiresFreshVerification'])
        self.assertEqual(rule['provenance']['gatesSha256'], digest(before['gates']))

    def test_replay_unverified_or_non_model_sources_are_ineligible(self):
        source = self.source()
        wf, node, attempt, prior = source
        cases = [({**wf, 'mode': 'replay'}, node, attempt, prior),
                 (wf, node, {**attempt, 'mode': 'replay'}, prior),
                 (wf, {**node, 'engineState': 'stale'}, attempt, prior),
                 (wf, node, {**attempt, 'state': 'attention'}, prior),
                 (wf, node, attempt, {**prior, 'source': 'operator'}),
                 (wf, node, attempt, {**prior, 'id': 'invented-model-decision'})]
        with self.controller.store.transaction() as rt:
            self.ledger.ensure_schema(rt)
            # Integration may already have observed the real fixture; these bad
            # inputs must never create any additional candidates.
            count = len(self.ledger.list(rt))
            for values in cases:
                self.assertIsNone(self.ledger.observe_verified(rt, *values))
            self.assertEqual(len(self.ledger.list(rt)), count)

    def test_reused_validation_raw_data_is_ineligible(self):
        wf, node, attempt, prior = self.source()
        attempt = deepcopy(attempt)
        attempt['result']['selectionResult']['artifacts'] = deepcopy(attempt['result']['artifacts'])
        with self.controller.store.transaction() as rt:
            self.controller.store.attempt(rt, wf['id'], attempt)
            self.assertIsNone(self.ledger.observe_verified(rt, wf, node, attempt, prior))

    def test_publication_rechecks_current_bounds_and_hides_outdated_rules(self):
        candidate, _ = self.candidate()
        with self.controller.store.transaction() as rt:
            strict = ExperienceLedger({'drive_amp': {'min': 0, 'max': .2}})
            self.assert_code('EXPERIENCE_PARAMETER_OUT_OF_RANGE', lambda: strict.publish(rt, candidate['id'], 'too-high'))
            self.assertEqual(strict.published_rules(rt), [])
            self.ledger.publish(rt, candidate['id'], 'accepted-bounds')
            self.assertEqual(strict.published_rules(rt), [])
            self.assertEqual(len(self.ledger.published_rules(rt)), 1)

    def test_publication_rechecks_raw_file_hash(self):
        candidate, _ = self.candidate()
        artifact_id = candidate['verification']['selection'][0]['id']
        raw_path, _ = self.controller.artifact(artifact_id)
        raw_path.write_text('tampered after verification', encoding='utf-8')
        with self.controller.store.transaction() as rt:
            self.assert_code('EXPERIENCE_EVIDENCE_CHANGED', lambda: self.ledger.publish(rt, candidate['id'], 'changed-raw'))
            self.assertEqual(self.ledger.published_rules(rt), [])

    def test_publication_rejects_changed_frozen_gate(self):
        candidate, source = self.candidate()
        with self.controller.store.transaction() as rt:
            wf = self.controller.get(source[0]['id'], rt)
            wf['gates']['ro_resonator_survey']['contrast_snr']['min'] = 9
            self.controller.store.save(rt, wf, 'test_tampered_gate')
            self.assert_code('EXPERIENCE_GATE_FAILED', lambda: self.ledger.publish(rt, candidate['id'], 'changed-gate'))

    def test_candidate_hash_tampering_is_rejected(self):
        candidate, _ = self.candidate()
        with self.controller.store.transaction() as rt:
            changed = deepcopy(candidate)
            changed['parameterChanges']['drive_amp'] = .9
            rt.db.execute('UPDATE platform_experience_candidates SET payload=? WHERE id=?', (dumps(changed), candidate['id']))
            self.assert_code('EXPERIENCE_RECORD_CHANGED', lambda: self.ledger.publish(rt, candidate['id'], 'changed-candidate'))


if __name__ == '__main__':
    unittest.main()
