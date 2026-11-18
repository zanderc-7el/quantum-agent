"""Offline new-task scope and seed authentication; no device or model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
from backend.catalog import WORKING_POINT_OMITTED, WORKING_POINT_VERSION
from backend.common import ROOT, APIError, dumps
from backend.controller import Controller
from backend.server import html_report
from backend.working_point import _read, build_working_point_seed, SEED_SHA

try:
    from test_controller import FakeAdapter
except ImportError:
    from tests.test_controller import FakeAdapter


PULSE_METHODS = {'sq_rabi_amplitude', 'sq_rabi_time', 'ro_iq01', 'ro_optimize', 'sq_t1',
                 'sq_allxy', 'sq_ramsey', 'sq_echo', 'sq_cpmg', 'sq_rb'}


class WorkingPointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-working-point-offline-')
        self.root = Path(self.directory.name)
        imports = self.root / 'data/imports'
        imports.mkdir(parents=True)
        (imports / 'history.json').write_text(json.dumps({'version': 1,
            'resources': [{'id': 'Q11', 'label': 'Q11'}, {'id': 'Q18', 'label': 'Q18'}], 'records': []}))
        self.adapter = ArcLightAdapter(ROOT, ROOT / 'adapters/arclight_profile_v2.json')
        self.controller = Controller(self.root, adapter=self.adapter, start_worker=False)
        self.controller._brain = lambda *a, **kw: self.fail('No model request allowed')

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def create(self, **changes):
        payload = {'name': 'OFFLINE working-point scope', 'mode': 'real', 'experiment': 'single',
                   'policy': 'autopilot', 'resourceIds': ['Q11'], 'coverage': 'working_point'}
        payload.update(changes)
        with (patch.object(self.adapter, 'execute', side_effect=AssertionError('No hardware execution')),
              patch.object(self.adapter, 'hardware_ownership', side_effect=AssertionError('No hardware discovery'))):
            return self.controller.create(payload)

    def test_new_ten_node_scope_preserves_all_remaining_full_dependencies_and_old_workflow(self):
        old = self.create(coverage='full')
        with self.controller.store.transaction() as rt:
            rt.db.execute('INSERT INTO parameters VALUES(?,?,?)', (old['id'] + ':Q11:readout_hz', dumps(7009576666.666667), 1))
        before = self.controller.get(old['id'])
        full = deepcopy(old['nodes'])
        wf = self.create()
        self.assertEqual(self.controller.get(old['id']), before)
        self.assertEqual(wf['blueprintVersion'], WORKING_POINT_VERSION)
        self.assertEqual(wf['coverage'], 'working_point')
        self.assertEqual(len(wf['nodes']), 10)
        self.assertEqual({n['procedureId'] for n in wf['nodes']}, PULSE_METHODS)
        omitted_ids = {n['id'] for n in full if n['procedureId'] in WORKING_POINT_OMITTED}
        for node in wf['nodes']:
            original = next(n for n in full if n['id'] == node['id'])
            self.assertEqual(node['dependsOn'], [d for d in original['dependsOn'] if d not in omitted_ids])
            self.assertEqual(node['engineState'], 'pending')
            self.assertEqual(node['status'], 'pending')
            self.assertEqual(node['attempt'], 0)
            self.assertFalse(node['mainProcedureComplete'])
            self.assertNotIn('result', node)
            self.assertEqual(set(node['parameterVersions'].values()), {0})
            with self.controller.store.connect() as rt:
                parameters, versions = self.controller._parameters(rt, wf, node)
            self.assertEqual(parameters['readout_hz'], 7009510000)
            self.assertEqual(versions['readout_hz'], 0)
        first = wf['nodes'][0]
        self.assertEqual(first['procedureId'], 'sq_rabi_amplitude')
        self.assertEqual(first['dependsOn'], [])
        self.assertEqual(len(self.create(coverage='main')['nodes']), 8)
        self.assertEqual(len(self.create(coverage='full')['nodes']), 13)
        self.assertEqual(wf['gates'], old['gates'])
        self.assertEqual(wf['maxTotalShots'], 30000000)
        self.assertFalse(wf['workflowComplete'])
        self.assertEqual(wf['seedReference']['status'], 'seed_only')
        self.assertEqual(wf['seedReference']['seedMetadataSha256'], SEED_SHA)
        self.assertEqual(wf['completionScope']['selectedNodeCount'], 10)
        self.assertFalse(wf['completionScope']['full13Fresh'])
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_attempts WHERE workflow_id=?', (wf['id'],)).fetchone()[0], 0)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_artifacts').fetchone()[0], 0)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM parameters WHERE name LIKE ?', (wf['id'] + ':%',)).fetchone()[0], 0)

    def test_exact_v1_and_v2_sources_are_seed_only_and_do_not_connect(self):
        for filename in ('arclight_profile.json', 'arclight_profile_v2.json'):
            adapter = ArcLightAdapter(ROOT, ROOT / 'adapters' / filename)
            with (patch.object(adapter, 'execute', side_effect=AssertionError('No execute')),
                  patch.object(adapter, 'hardware_ownership', side_effect=AssertionError('No ownership'))):
                seed = build_working_point_seed(adapter)
            self.assertEqual(seed['status'], 'seed_only')
            self.assertEqual(seed['parameters']['drive_hz'], 4862690000)
            self.assertEqual(seed['parameters']['drive_amp'], .98)
            self.assertEqual(seed['parameters']['drive_length_s'], 320e-9)
            self.assertEqual(seed['parameters']['half_pi_amp'], .98)
            self.assertEqual(seed['parameters']['readout_width_s'], 2.2e-6)
            self.assertEqual({f['kind'] for f in seed['sourceFiles']}, {'raw', 'job', 'receipt', 'analysis', 'completion'})
            self.assertNotIn('verified', seed)
        raw = next(f for f in seed['sourceFiles'] if f['kind'] == 'raw')
        self.assertEqual(raw['sha256'], 'f652f798ca0c04f2d22bf3fa55de266d34b02025b8979114f7c6cee766d2412f')

    def test_rejects_other_scope_unapproved_adapter_and_changed_profile_without_creating_task(self):
        for change in ({'mode': 'replay'}, {'experiment': 'two'}, {'resourceIds': ['Q18']}, {'resourceIds': ['Q11', 'Q18']}):
            with self.subTest(change=change), self.assertRaises(APIError):
                self.create(**change)
        for key, value in (('readout_hz', 7009576666.666667), ('half_pi_amp', .49)):
            original = self.adapter.profile['resources']['Q11'][key]
            try:
                self.adapter.profile['resources']['Q11'][key] = value
                with self.assertRaises(APIError) as error:
                    self.create()
                self.assertEqual(error.exception.code, 'WORKING_POINT_PROFILE_MISMATCH')
            finally:
                self.adapter.profile['resources']['Q11'][key] = original
        with self.assertRaises(APIError) as error:
            build_working_point_seed(FakeAdapter(self.root))
        self.assertEqual(error.exception.code, 'WORKING_POINT_ADAPTER_REQUIRED')
        self.assertEqual(self.controller.list(), [])

    def test_seed_hash_failure_blocks_creation_and_bounded_reader_rejects_changes(self):
        path = self.root / 'offline-seed.txt'
        path.write_bytes(b'old')
        expected = hashlib.sha256(b'old').hexdigest()
        self.assertEqual(_read(path, expected, 3), b'old')
        path.write_bytes(b'new')
        with self.assertRaises(APIError) as error:
            _read(path, expected, 3)
        self.assertEqual(error.exception.code, 'WORKING_POINT_SEED_CHANGED')
        with self.assertRaises(APIError):
            _read(path, expected, 2)
        with patch('backend.working_point._read', side_effect=APIError('WORKING_POINT_SEED_CHANGED', 'offline hash mismatch')):
            with self.assertRaises(APIError):
                self.create()
        self.assertEqual(self.controller.list(), [])

    def test_readout_rebuild_and_rb_current_evidence_requirements_remain(self):
        wf = self.create()
        rabi = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude')
        optimizer = next(n for n in wf['nodes'] if n['procedureId'] == 'ro_optimize')
        rb = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rb')
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            values, versions = self.controller._parameters(rt, current, rb)
            snapshot = self.controller._validation_snapshot(rt, current, rb, values, versions)
            self.assertEqual(snapshot['acceptedEvidence'], [])
            target = self.controller._start_readout_revalidation(rt, current, optimizer)
            self.assertEqual(target, rabi['id'])
            self.assertEqual(set(current['readoutRevalidations']['Q11']['lockedNodeIds']), {n['id'] for n in current['nodes']})
        request = {'runId': wf['id'], 'nodeId': rb['id'], 'attemptId': 'offline', 'operationId': 'offline/rb',
                   'resourceId': 'Q11', 'procedureId': 'sq_rb', 'parameters': values,
                   'parameterVersions': versions, 'validationSnapshot': snapshot}
        with self.assertRaises(Exception) as error:
            self.adapter.build_plan(request)
        self.assertIn(getattr(error.exception, 'code', ''), {'RB_VALIDATION_INCOMPLETE', 'RB_VALIDATION_REQUIRED'})

    def test_all_ten_native_plans_fit_budget_without_importing_historical_acceptance(self):
        wf = self.create()
        costs = []
        # Planning only: bypass RB evidence exclusively to count its bounded jobs.
        with patch.object(self.adapter, '_validate_snapshot', return_value={'offline_budget_only': True}):
            for node in wf['nodes']:
                request = {'runId': wf['id'], 'nodeId': node['id'], 'attemptId': 'offline-plan',
                    'operationId': 'offline-plan/' + node['procedureId'], 'resourceId': 'Q11',
                    'procedureId': node['procedureId'], 'parameters': node['parameters'],
                    'parameterVersions': node['parameterVersions']}
                plan = self.adapter.build_plan(request)
                self.assertEqual(plan['parameters']['readout_hz'], 7009510000)
                self.assertEqual(plan['parameters']['drive_hz'], 4862690000)
                self.assertGreater(plan['estimated_total_shots'], 0)
                self.assertLessEqual(plan['estimated_total_shots'], self.adapter.profile['maximumTotalShots'])
                costs.append(plan['estimated_total_shots'])
        self.assertLess(2 * sum(costs), wf['maxTotalShots'])
        self.assertFalse(wf['workflowComplete'])

    def test_completion_is_only_the_selected_ten_and_html_says_seed_only(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            for node in current['nodes'][:-1]:
                node['engineState'] = 'verified'
            self.controller.store.save(rt, current, 'offline-nine-complete')
        self.controller._complete(wf['id'])
        self.assertFalse(self.controller.get(wf['id'])['workflowComplete'])
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current['nodes'][-1]['engineState'] = 'verified'
            self.controller.store.save(rt, current, 'offline-ten-complete')
        self.controller._complete(wf['id'])
        done = self.controller.get(wf['id'])
        self.assertTrue(done['workflowComplete'])
        self.assertEqual(done['resultSummary']['totalNodes'], 10)
        self.assertFalse(done['completionScope']['full13Fresh'])
        page = html_report(self.controller, self.controller.report(wf['id'])).decode('utf-8')
        self.assertIn('seed_only', page)
        self.assertIn('不代表原13项全流程均已新测', page)


if __name__ == '__main__':
    unittest.main()
