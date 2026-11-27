import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.common import APIError, digest, dumps
from tests.test_controller import make_fixture


class ScienceServiceContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='science-contract-')
        self.addCleanup(self.temp.cleanup)
        self.c, self.adapter = make_fixture(Path(self.temp.name))
        self.addCleanup(self.c.close)
        try: cls = importlib.import_module('backend.science_service').ScienceService
        except ModuleNotFoundError: cls = None
        self.assertIsNotNone(cls, 'scientific service is missing')
        self.service = cls(self.c)

    def save(self, kind='bloch', **kwargs):
        return self.service.write('/api/science/records', {'kind': kind, 'input': {}, 'scope': {},
                                  'operationId': 'save-one', **kwargs}, 'POST')

    def test_preview_and_idempotent_save_persist_without_any_device_call(self):
        a = self.save()
        self.assertEqual(self.save(), a)
        other = type(self.service)(self.c)
        self.assertEqual(other.get('/api/science/records/'+a['id'], {})['input'], {})
        self.assertEqual(len(other.get('/api/science/records', {})['records']), 1)
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(a['dataKind'], 'simulation')
        with self.assertRaises(APIError): self.save(input={'durationNs': 50})

    def test_summary_list_is_explicit_and_full_detail_keeps_plot_inputs_and_proof(self):
        saved=self.save(input={'durationNs':50})
        page=self.service.get('/records',{'summary':'true'})
        item=page['records'][0]
        self.assertTrue(item['summary'])
        self.assertNotIn('dependencyProof',item)
        self.assertNotIn('series',item['result'])
        full=self.service.get('/records/'+saved['id'],{})
        self.assertEqual(full['input'],saved['input'])
        self.assertEqual(full['result'],saved['result'])
        self.assertEqual(full['dependencyProof'],saved['dependencyProof'])
        self.assertEqual(page['responseBytes'],len(dumps(page).encode('utf8')))
        self.assertFalse(page['hasMore'])

    def test_full_list_pages_at_byte_budget_without_omitting_record_fields(self):
        first=self.save(operationId='first')
        second=self.save(operationId='second')
        one_size=len(dumps(self.service.get('/records/'+second['id'],{})).encode('utf8'))
        with patch('backend.science_service.MAX_LIST_BYTES',one_size+1024,create=True):
            page=self.service.get('/records',{})
            self.assertEqual([r['id'] for r in page['records']],[second['id']])
            self.assertTrue(page['hasMore']);self.assertTrue(page['nextCursor'])
            self.assertIn('dependencyProof',page['records'][0])
            following=self.service.get('/records',{'cursor':page['nextCursor']})
            self.assertEqual([r['id'] for r in following['records']],[first['id']])
            self.assertFalse(following['hasMore'])
        with patch('backend.science_service.MAX_DETAIL_BYTES',128,create=True),self.assertRaises(APIError) as caught:
            self.service.get('/records/'+first['id'],{})
        self.assertEqual(caught.exception.code,'SCIENCE_RESPONSE_LIMIT')

    def test_input_cannot_claim_real_or_supply_fake_evidence(self):
        for extra in ({'dataKind':'real'},{'evidence':[{'id':'fake'}]},{'accepted':True}):
            with self.subTest(extra=extra), self.assertRaises(APIError): self.save(**extra)
        with self.assertRaises(APIError): self.save(kind='working_point')

    def test_reference_missing_or_wrong_scope_is_rejected(self):
        wf = self.c.create({'name':'test','mode':'real','policy':'strict','resourceIds':['Q11']})
        with self.assertRaises(APIError): self.save(kind='measurement', reference={'workflowId':wf['id'],'attemptId':'fake'})
        with self.assertRaises(APIError): self.save(scope={'workflowId':wf['id'],'resourceId':'Q18'})
        with self.assertRaises(APIError): self.save(scope={'chipVersionId':'missing-version','resourceId':'Q11'})

    def acquired(self):
        wf = self.c.create({'name':'test','mode':'real','policy':'strict','resourceIds':['Q11']})
        self.c.action(wf['id'], {'action':'run_step','nodeId':wf['nodes'][0]['id'],
                               'operationId':'one-step','expectedRevision':wf['revision']})
        self.c.run_workflow(wf['id'])
        wf = self.c.get(wf['id']); node = wf['nodes'][0]
        return wf,node,node['latestAttemptId']

    def test_real_reference_comes_from_record_and_tamper_revokes_current_status(self):
        wf,node,aid = self.acquired()
        r = self.save(kind='measurement', reference={'workflowId':wf['id'],'attemptId':aid})
        self.assertEqual(r['dataKind'], 'real')
        self.assertEqual(r['scope']['resourceId'], 'Q11')
        self.assertFalse(r['acceptedCalibration'])
        with self.c.store.connect() as rt:
            art=rt.db.execute('SELECT path FROM platform_artifacts WHERE id=?',(r['evidence'][0]['id'],)).fetchone()
        Path(art[0]).write_bytes(b'changed')
        loaded = self.service.get('/api/science/records/'+r['id'], {})
        self.assertFalse(loaded['evidenceCurrent'])
        self.assertEqual(loaded['validity'], 'stale')

    def test_working_point_never_promotes_or_modifies_workflow_parameters(self):
        wf,node,aid = self.acquired()
        with self.c.store.connect() as rt: before = self.c.store.get(rt,wf['id'])
        point = self.save(kind='working_point', reference={'workflowId':wf['id'],'attemptId':aid},
                          input={'purpose':'work'})
        with self.c.store.connect() as rt: self.assertEqual(self.c.store.get(rt,wf['id']), before)
        self.assertEqual(point['result']['status'], 'requires_verification')
        self.assertFalse(point['acceptedCalibration'])
        with self.assertRaises(APIError): self.save(kind='working_point',operationId='forge-point',
            reference={'workflowId':wf['id'],'attemptId':aid}, input={'driveFrequencyHz':5.99e9})

    def test_compare_preserves_units_scope_and_rejects_probability_vs_adc(self):
        a = self.save()
        b = self.save(operationId='b', input={'durationNs':1200})
        out = self.service.write('/api/science/compare', {'leftRecordId':a['id'],'rightRecordId':b['id'],
            'leftSeriesId':'p1','rightSeriesId':'p1'}, 'POST')
        self.assertGreater(out['metrics']['comparedPoints'], 1)
        pulse = self.save(kind='pulse',operationId='c')
        with self.assertRaises(APIError): self.service.write('/api/science/compare', {
            'leftRecordId':a['id'],'rightRecordId':pulse['id'],'leftSeriesId':'p1','rightSeriesId':'i'}, 'POST')

    def test_simulation_uses_exact_source_axis_and_frozen_model(self):
        src=self.save(kind='bloch',input={'sequence':'t1','durationNs':20000,'points':13})
        sim=self.save(kind='simulation',operationId='sim',input={'sourceRecordId':src['id'],
            'modelParameters':{'sequence':'t1','t1Ns':50000,'t2Ns':30000}})
        self.assertEqual(sim['result']['axes'],src['result']['axes'])
        self.assertEqual(sim['result']['sourceRecordId'],src['id'])
        self.assertEqual(sim['dataKind'],'simulation')

    def test_missing_grid_points_are_not_interpolated(self):
        a=self.save(input={'sequence':'t1','durationNs':100,'points':3})
        b=self.save(operationId='b',input={'sequence':'t1','durationNs':100,'points':4})
        out=self.service.write('/compare',{'leftRecordId':a['id'],'rightRecordId':b['id']},'POST')
        self.assertEqual(out['metrics']['comparedPoints'],2)
        self.assertEqual(out['metrics']['missingPoints'],3)

    def test_malformed_reference_has_readable_error_not_key_error(self):
        with self.assertRaises(APIError):self.save(kind='measurement',reference={'attemptId':'unknown'})

    def test_saving_point_from_model_grid_stays_unverified_and_exact(self):
        flux=self.save(kind='flux',input={'biasValues':[0,.1],'frequencyHz':[6e9,6.1e9,6.2e9],
                                       'amplitude':[[1,2,3],[3,2,1]],'selectedBiasIndex':0})
        point=self.save(kind='working_point',operationId='point',input={'sourceRecordId':flux['id'],
                       'purpose':'idle','bias':.1,'biasUnit':'V','readoutFrequencyHz':6.1e9})
        self.assertEqual(point['dataKind'],'provided_data')
        self.assertEqual(point['result']['point']['readoutFrequencyHz'],6.1e9)
        self.assertFalse(point['acceptedCalibration'])

    def test_registered_archive_path_matches_controller_allowlist(self):
        wf,node,aid=self.acquired()
        # Path-policy test only. These two virtual archive files are not created or read.
        hashes={}
        with self.c.store.transaction() as rt:
            for row in rt.db.execute('SELECT id,sha256 FROM platform_artifacts').fetchall():
                path=str(Path('D:/总体-8号机测试')/(row['id']+'.qdat'));hashes[path]=row['sha256']
                rt.db.execute('UPDATE platform_artifacts SET path=? WHERE id=?',(path,row['id']))
        with patch('backend.science_service.file_hash',side_effect=lambda p:hashes[str(p)]):
            record=self.save(kind='measurement',reference={'workflowId':wf['id'],'attemptId':aid})
        self.assertEqual(record['dataKind'],'real')
        self.assertTrue(record['evidenceIntact'])

    def test_registered_but_outside_whitelist_file_is_rejected_before_read(self):
        wf,node,aid=self.acquired()
        with self.c.store.transaction() as rt:
            rt.db.execute('UPDATE platform_artifacts SET path=?',(str(Path(self.temp.name).parent/'outside-science-test.qdat'),))
        with patch('backend.science_service.file_hash') as reader, self.assertRaises(APIError):
            self.save(kind='measurement',reference={'workflowId':wf['id'],'attemptId':aid})
        reader.assert_not_called()


if __name__ == '__main__': unittest.main()
