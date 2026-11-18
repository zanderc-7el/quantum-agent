"""Real archived-data regression checks; these never acquire new data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

try:
    import numpy
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('D:/总体-8号机测试/20261117_Agent_F8_Q11_Q18复现')


@unittest.skipUnless(AVAILABLE, 'Use the F8 Python312 interpreter for offline math tests')
class ArchivedAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('offline_capture_analysis',ROOT/'adapters/analyze_capture.py')
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)

    def analyze_archive(self, job_id):
        request=json.loads((SOURCE/(job_id+'_request.json')).read_text(encoding='utf-8'))
        complete=json.loads((SOURCE/(job_id+'_complete.json')).read_text(encoding='utf-8'))
        path=next(p for p in complete['files'] if p.endswith('.qdat'))
        with tempfile.TemporaryDirectory() as target:
            return self.module.analyze(path, request['job'], target)

    def test_native_rabi_has_actual_measured_frequency_and_fit_metrics(self):
        result=self.analyze_archive('Q11_native_retuned_rabi_v1')
        self.assertGreater(result['metrics']['r_squared'], .8)
        self.assertTrue(1.5e6 < result['metrics']['rabi_frequency_hz'] < 1.8e6)
        self.assertTrue(.29e-6 < result['candidateParameters']['drive_length_s'] < .33e-6)
        self.assertNotIn('gate_fidelity',result['metrics'])

    def test_t1_native_fit_keeps_units_and_uncertainty(self):
        result=self.analyze_archive('Q11_native_retuned_t1_v1')
        self.assertTrue(4e-6 < result['metrics']['t1_s'] < 5e-6)
        self.assertLess(result['metrics']['relative_stderr'], .2)
        self.assertEqual(result['quality']['status'],'unverified')

    def test_readout_is_heldout_preparation_label_accuracy(self):
        result=self.analyze_archive('Q11_native_retuned_readout_pre_v1')
        self.assertTrue(.5 < result['metrics']['assignment_fidelity'] < .7)
        self.assertIn('SPAM',result['analysis']['assignmentScope'])
        self.assertNotIn('gate_fidelity',result['metrics'])

    def test_s21_peak_stays_within_actual_axis(self):
        result=self.analyze_archive('Q11_native_s21_pre_v1')
        self.assertGreater(result['metrics']['contrast_snr'], 0)
        curve=result['series'][0]
        candidate=result['candidateParameters']['readout_hz']
        self.assertTrue(min(curve['x']) < candidate < max(curve['x']))


if __name__=='__main__':unittest.main()
