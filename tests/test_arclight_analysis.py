"""Analysis only. Archived IQ remains historical and sources are never written."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import shutil
import unittest

try:
    import numpy as np
    from adapters.arclight_runtime.platform_analysis import _fit_metrics, _load, native_module, analyze_job
except ImportError:
    np = None

ARCHIVE = Path('D:/豆包/量子/arclight_单比特全流程_20261117/results/20261118_000104_442041_live_Q11_historical_point_rabi')


@unittest.skipIf(np is None, 'Requires analysis interpreter with NumPy/SciPy')
class ArcLightAnalysis(unittest.TestCase):
    def test_full_readout_analysis_uses_archived_copy_and_produces_heldout_plot(self):
        archive=ARCHIVE.parent/'20261118_013547_343476_live_Q11_historical_rb_readout_pre'
        if not archive.is_dir():self.skipTest('Local source archive unavailable')
        before=hashlib.sha256((archive/'raw_scan.npz').read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix='historical-offline-analysis-') as temp:
            folder=Path(temp)
            files=[archive/name for name in ('status.json','axes.json','job.json','raw_scan.npz')]+list(archive.glob('point_*.npz'))
            for source in files:shutil.copy2(source,folder/source.name)
            job=json.loads((folder/'job.json').read_text(encoding='utf-8'))
            result=analyze_job(folder,job,'ro_iq01')
            self.assertTrue(result['analysis']['accepted'],result['analysis'])
            self.assertTrue(.5<result['metrics']['assignment_fidelity']<=1)
            self.assertGreater((folder/'platform_readout_diagnostics.png').stat().st_size,20000)
            self.assertEqual(result['analysis']['raw_sha256'],before)
            self.assertTrue(all(len(s['x'])==len(s['y']) for s in result['series']))
        self.assertEqual(before,hashlib.sha256((archive/'raw_scan.npz').read_bytes()).hexdigest())

    def test_archived_native_fit_metrics_keep_measured_residual_and_units(self):
        if not ARCHIVE.is_dir():
            self.skipTest('Local source archive unavailable')
        path=ARCHIVE/'raw_scan.npz'; before=hashlib.sha256(path.read_bytes()).hexdigest()
        job=json.loads((ARCHIVE/'job.json').read_text(encoding='utf-8'))
        axes=json.loads((ARCHIVE/'axes.json').read_text(encoding='utf-8'))
        with np.load(path,allow_pickle=False) as raw:
            native,views=native_module('analyze_single_qubit').analyze_arrays(axes,raw['i'],raw['q'],job)
        metrics,candidate,reasons=_fit_metrics(native,views,job,'sq_rabi_time')
        self.assertFalse(reasons)
        self.assertAlmostEqual(metrics['rabi_frequency_hz'],1527439.97,delta=100)
        self.assertAlmostEqual(candidate['drive_length_s'],327.345e-9,delta=1e-10)
        # A good-looking historical fit remains below the platform's R2=.8 gate.
        self.assertTrue(.78<metrics['r_squared']<.8)
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_five_gate_amplitude_calibration_includes_repeat_factor(self):
        x=np.linspace(0,.98,61); f=2.6
        fit={'x_origin':0,'x_span':.98,'model':'sinusoid_decay_unresolved',
             'fit_parameters_normalized_x':[0,0,1,f*.98,0], 'value':f,
             'standard_error_model':.01,'residual_rms':.01,'cycles_in_window':f*.98}
        native={'groups':[{'repeat_validated':True,'repeats':[{'fit':fit},{'fit':fit}]}],
                'x_axis':{'native_key':'amp'}}
        y=np.cos(2*np.pi*f*x)
        views={'plots':[{'blocks':[np.arange(61),np.arange(61)]}],
               'axes':[{'amp':v} for v in x], 'projection':y, 'projection_sem':np.full(61,.01)}
        _,candidate,_=_fit_metrics(native,views,{'kind':'rabi_amplitude','gate_repeats':5},'sq_rabi_amplitude')
        self.assertAlmostEqual(candidate['drive_amp'],5/(2*f))

    def test_missing_native_repeat_fit_is_never_an_accepted_candidate(self):
        native={'groups':[{'repeat_validated':False,'repeats':[{'fit':None,'diagnostics':{'rejection_reasons':['flat']}}]}]}
        metrics,candidate,reasons=_fit_metrics(native,{'plots':[{'blocks':[[]]}]}, {'kind':'rabi_time'},'sq_rabi_time')
        self.assertEqual(candidate,{})
        self.assertEqual(metrics,{})
        self.assertIn('flat',reasons)

    def test_incomplete_or_offline_receipt_rejected_before_raw_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            for mode,state in [('offline_compile_only','acquired'),('real_hardware','measuring')]:
                (folder/'status.json').write_text(json.dumps({'mode':mode,'state':state}))
                with self.assertRaisesRegex(ValueError,'complete real hardware'):
                    _load(folder)


if __name__=='__main__':
    unittest.main()
