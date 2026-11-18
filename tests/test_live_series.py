"""Read-only summaries from explicit fixtures/actual archived data; no drivers."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from backend.live_scan_reader import read_batch, UnsafeLiveSource
from backend.live_series import LiveSeries

try:
    import numpy as np
except ImportError:
    np = None


class LiveSeriesPersistence(unittest.TestCase):
    def test_cursor_restart_dedup_and_bounded_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            def reader(request):
                calls.append(request)
                points=[{'operationId':'op','jobId':'job','pointIndex':i,'pointSha256':str(i),
                         'meanI':float(i),'meanQ':0.,'axes':{'freq':float(i)}} for i in range(4)]
                return {'operationId':'op','points':points,'availablePoints':4,'complete':True,
                        'terminal':True,'state':'completed'}
            service=LiveSeries(temp,'unused',reader=reader,poll_seconds=0)
            first=service.read('op',limit=2)
            self.assertEqual([p['pointIndex'] for p in first['points']],[0,1])
            self.assertTrue(first['hasMore'])
            rebuilt=LiveSeries(temp,'unused',reader=reader,poll_seconds=0)
            second=rebuilt.read('op',after=first['cursor'],limit=2)
            self.assertEqual([p['pointIndex'] for p in second['points']],[2,3])
            self.assertFalse(second['hasMore'])
            self.assertEqual(len(calls),1)
            self.assertEqual(rebuilt.read('op',after=second['cursor'])['points'],[])
            self.assertEqual(second['quality'],'unverified')

    def test_singleflight_multiple_readers_one_subprocess(self):
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            def reader(request):
                calls.append(request);time.sleep(.05)
                return {'operationId':'op','points':[],'availablePoints':0,'complete':False,'terminal':False,'state':'acquiring'}
            service=LiveSeries(temp,'unused',reader=reader,poll_seconds=1)
            results=[]
            threads=[threading.Thread(target=lambda:results.append(service.read('op'))) for _ in range(4)]
            for t in threads:t.start()
            for t in threads:t.join()
            self.assertEqual(len(results),4)
            self.assertEqual(len(calls),1)

    def test_rejects_wrong_operation_and_invalid_cursor(self):
        with tempfile.TemporaryDirectory() as temp:
            service=LiveSeries(temp,'unused',reader=lambda _: {'operationId':'other','points':[]})
            with self.assertRaisesRegex(ValueError,'identity'):service.read('op')
            for cursor in (-1,True,2**64):
                with self.assertRaises(ValueError):service.read('op',after=cursor)
            with self.assertRaises(ValueError):service.read('op',limit=257)


@unittest.skipIf(np is None,'NumPy analysis interpreter required')
class LivePointReader(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name);self.op='fixture-operation'
        self.operation=self.root/'data/runs/arclight_operations'/hashlib.sha256(self.op.encode()).hexdigest()
        self.folder=self.operation/'results'/'fixture-job'
        self.folder.mkdir(parents=True)
        self.save(self.operation/'plan.json',{'adapter':'arclight','operationId':self.op,'totalRows':3,'jobs':[{'name':'planned','spec':{'meta':{'shots':2}},'acquisitions_per_point':1}]})
        self.save(self.operation/'adapter_intent.json',{'operationId':self.op,'request':{'operationId':self.op}})
        self.axes=[{'capture':0,'base_index':i,'axes':{'freq':100-i}} for i in range(3)]
        self.save(self.folder/'axes.json',self.axes)
        # Deliberately inconsistent transient current_axes/mean must be ignored.
        self.status={'mode':'real_hardware','state':'measuring','job':'planned','points_expected':3,
                     'points_completed':2,'current_axes':self.axes[2],'mean_i':999,'mean_q':999}
        self.save(self.folder/'status.json',self.status)
        for i in range(3):np.savez_compressed(self.folder/f'point_{i:04d}.npz',i=np.array([[i+1.,i+1.]]),q=np.array([[2.,2.]]))
    def tearDown(self):self.temp.cleanup()
    @staticmethod
    def save(path,value):path.write_text(json.dumps(value),encoding='utf8')
    def read(self,**overrides):return read_batch(self.root,{'operationId':self.op,**overrides})

    def test_axis_atomicity_completed_count_and_real_point_hash(self):
        result=self.read()
        self.assertEqual(len(result['points']),2)  # point 2 exists, but is not confirmed.
        self.assertEqual([p['axes']['freq'] for p in result['points']],[100,99])
        self.assertEqual([p['meanI'] for p in result['points']],[1.,2.])
        self.assertEqual(result['points'][0]['pointSha256'],hashlib.sha256((self.folder/'point_0000.npz').read_bytes()).hexdigest())
        self.assertAlmostEqual(result['points'][0]['meanAbs'],5**.5)
        self.assertFalse(result['complete'])
        self.assertEqual(self.read(afterByJob={'fixture-job':0})['points'][0]['pointIndex'],1)

    def test_unfinished_zip_never_advances_past_missing_point(self):
        (self.folder/'point_0000.npz').write_bytes(b'PK')
        result=self.read()
        self.assertEqual(result['points'],[])
        self.assertTrue(result['pending'])

    def test_offline_and_mismatched_identity_are_never_live_data(self):
        self.status['mode']='offline_compile_only';self.save(self.folder/'status.json',self.status)
        self.assertEqual(self.read()['points'],[])
        self.save(self.operation/'adapter_intent.json',{'operationId':'other','request':{'operationId':'other'}})
        with self.assertRaisesRegex(UnsafeLiveSource,'identity'):self.read()

    def test_completed_receipt_does_not_make_partial_points_complete(self):
        self.save(self.operation/'completion.json',{'state':'completed'})
        self.assertFalse(self.read()['complete'])
        self.status.update(state='acquired',points_completed=3);self.save(self.folder/'status.json',self.status)
        self.assertTrue(self.read()['complete'])

    def test_linked_or_escaped_source_rejected_before_read(self):
        from backend.live_scan_reader import safe_path
        with self.assertRaises(UnsafeLiveSource):safe_path(self.root.parent,self.operation)
        link=self.folder/'aliased.npz'
        try:link.symlink_to(self.folder/'point_0000.npz')
        except OSError:self.skipTest('Symlink creation unavailable')
        with self.assertRaisesRegex(UnsafeLiveSource,'Linked'):safe_path(link,self.operation)

    def test_actual_completed_q11_data_read_without_mutation(self):
        project=Path(__file__).resolve().parents[1]
        op='attempt-f9e42cf66e834a58a03536895866bedc/measure'
        folder=project/'data/runs/arclight_operations'/hashlib.sha256(op.encode()).hexdigest()
        if not folder.exists():self.skipTest('Local completed acquisition unavailable')
        sources=list((folder/'results').glob('*/point_0000.npz'))
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        result=read_batch(project,{'operationId':op,'limit':3})
        self.assertEqual(len(result['points']),3)
        self.assertTrue(result['complete'])
        for point in result['points']:
            path=folder/point['pointFile']
            with np.load(path,allow_pickle=False) as raw:
                self.assertAlmostEqual(point['meanI'],float(raw['i'].mean()))
                self.assertAlmostEqual(point['meanQ'],float(raw['q'].mean()))
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})

    def test_actual_isolated_helper_process_uses_analysis_python(self):
        project=Path(__file__).resolve().parents[1]
        profile_path=project/'adapters/arclight_profile.json'
        if not profile_path.is_file():self.skipTest('Local analysis runtime unavailable')
        profile=json.loads(profile_path.read_text(encoding='utf8'))
        if not Path(profile['python']).is_file():self.skipTest('Local analysis interpreter unavailable')
        op='attempt-f9e42cf66e834a58a03536895866bedc/measure'
        operation=project/'data/runs/arclight_operations'/hashlib.sha256(op.encode()).hexdigest()
        if not operation.is_dir():self.skipTest('Completed local Q11 acquisition unavailable')
        with tempfile.TemporaryDirectory(prefix='live-reader-test-',dir=project/'data') as temp:
            service=LiveSeries(project,profile['python'],database=Path(temp)/'summary.sqlite')
            result=service.read(op,limit=5)
            self.assertEqual(len(result['points']),5)
            self.assertEqual(result['availablePoints'],122)
            self.assertEqual(result['cachedPoints'],122)
            self.assertTrue(result['complete'])
            self.assertTrue(result['hasMore'])
            self.assertEqual(result['points'][0]['meanAbs'],result['points'][0]['meanMagnitude'])


if __name__=='__main__':unittest.main()
