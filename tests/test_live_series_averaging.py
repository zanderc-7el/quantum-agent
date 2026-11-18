"""Regression for live plots after increasing actual ADC blocks per point."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from backend.live_scan_reader import read_batch as READ_BATCH

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, 'NumPy analysis interpreter required')
class PlannedLiveAveragingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.operation = 'offline-planned-blocks'
        self.folder = self.root/'data/runs/arclight_operations'/hashlib.sha256(self.operation.encode()).hexdigest()
        self.folder.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, path, value):
        path.write_text(json.dumps(value), encoding='utf8')

    def fixture(self, specs):
        jobs = []
        for index, (shots, blocks, actual) in enumerate(specs):
            name = f'job_{index}'
            jobs.append({'name':name, 'spec':{'meta':{'shots':shots}}, 'acquisitions_per_point':blocks})
            result = self.folder/'results'/name
            result.mkdir(parents=True)
            self.save(result/'status.json', {'mode':'real_hardware','job':name,'state':'acquired',
                'points_expected':1,'points_completed':1})
            self.save(result/'axes.json',[{'capture':0,'base_index':0,'axes':{'frequency':7e9}}])
            values = np.arange(actual, dtype=float).reshape(1,-1)/max(1,actual)
            np.savez_compressed(result/'point_0000.npz', i=values, q=values*2)
        self.save(self.folder/'plan.json',{'adapter':'arclight','operationId':self.operation,'totalRows':len(jobs),'jobs':jobs})
        self.save(self.folder/'adapter_intent.json',{'operationId':self.operation,'request':{'operationId':self.operation}})

    def read(self):
        return READ_BATCH(self.root, {'operationId':self.operation})

    def test_six_real_blocks_produce_12000_shot_live_point(self):
        self.fixture([(2000,6,12000)])
        point = self.read()['points'][0]
        self.assertEqual(point['shots'],12000)
        self.assertAlmostEqual(point['meanI'],11999/24000)
        self.assertAlmostEqual(point['meanQ'],11999/12000)

    def test_maximum_native_blocks_are_bounded_and_readable(self):
        self.fixture([(2047,16,32752)])
        self.assertEqual(self.read()['points'][0]['shots'],32752)

    def test_each_job_uses_its_own_plan_not_global_sampling(self):
        self.fixture([(2000,2,4000),(2000,6,12000)])
        self.assertEqual([p['shots']for p in self.read()['points']],[4000,12000])

    def test_truncated_point_rejected_even_below_old_size_bound(self):
        self.fixture([(2000,6,8000)])
        with self.assertRaisesRegex(ValueError,'IQ shape'):
            self.read()

    def test_extra_samples_cannot_override_frozen_plan(self):
        self.fixture([(2000,4,12000)])
        with self.assertRaisesRegex(ValueError,'IQ shape'):
            self.read()

    def test_out_of_native_block_cap_rejected(self):
        self.fixture([(2000,17,34000)])
        with self.assertRaisesRegex(ValueError,'planned acquisition blocks'):
            self.read()

    def test_out_of_native_shot_cap_rejected(self):
        self.fixture([(2048,1,2048)])
        with self.assertRaisesRegex(ValueError,'planned block shots'):
            self.read()

    def test_missing_sampling_plan_is_not_assumed(self):
        self.fixture([(2000,6,12000)])
        plan_path = self.folder/'plan.json'
        plan = json.loads(plan_path.read_text(encoding='utf8'))
        del plan['jobs'][0]['acquisitions_per_point']
        self.save(plan_path,plan)
        with self.assertRaisesRegex(ValueError,'planned acquisition blocks'):
            self.read()


if __name__ == '__main__':
    unittest.main()
