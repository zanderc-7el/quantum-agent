"""Actual compile-only subprocess tests. Never opens any instrument."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


class NativeCompilationTests(unittest.TestCase):
    def setUp(self):
        from adapters.science_runtime.compile_client import compile_offline
        self.compile = compile_offline
        self.temp = tempfile.TemporaryDirectory(prefix='compile-only-')
        self.addCleanup(self.temp.cleanup)
        self.counter = 0
        self.request = {'kind': 'drag', 'input': {'shape': 'Gaussian', 'durationNs': 20,
            'amplitude': .3, 'sigmaNs': 4, 'lambda1': .5, 'anharmonicityHz': -200e6,
            'anharmonicitySource': 'explicit_unvalidated_model_input', 'frequencyHz': 4.86e9,
            'gapNs': 2, 'readoutWidthNs': 2000, 'adcDelayNs': 50},
            'scope': {'workflowId': 'test-only', 'resourceId': 'Q11'}, 'source': {'testOnly': True}}

    def run_compile(self, **inputs):
        request = deepcopy(self.request);request['input'].update(inputs)
        self.counter += 1
        output = Path(self.temp.name) / str(self.counter)
        result = self.compile(ROOT, request, output)
        with np.load(output / 'waveforms.npz') as archive:data={key:archive[key] for key in archive.files}
        return result, data

    def test_native_gaussian_drag_sigma_and_sign(self):
        p, a = self.run_compile()
        _, b = self.run_compile(lambda1=-.5)
        _, c = self.run_compile(lambda1=0)
        _, d = self.run_compile(sigmaNs=2)
        np.testing.assert_allclose(a['xy0_i'], b['xy0_i'], atol=1e-14)
        np.testing.assert_allclose(a['xy0_q'], -b['xy0_q'], atol=1e-14)
        self.assertEqual(float(abs(c['xy0_q']).max()), 0)
        beta = -.5 / (2*np.pi*-200e6)
        np.testing.assert_allclose(a['xy0_q'][4:-4], beta*np.gradient(a['xy0_i'][4:-4], 1/4e9), atol=1e-13)
        self.assertGreater(np.max(abs(a['xy0_i']-d['xy0_i'])), .05)
        self.assertAlmostEqual(a['xy0_i'][4], .3*np.exp(-10**2/(2*4**2)), places=13)
        self.assertEqual(p['dataKind'], 'compiled')
        self.assertFalse(p['hardwareConnected'])
        self.assertEqual(p['sandbox']['driverImports'], [])

    def test_grid_and_independent_adc_delay_and_phase(self):
        first, a = self.run_compile()
        second, b = self.run_compile(adcDelayNs=70, phaseRad=.4)
        xy = first['segments'][0]
        self.assertEqual(xy['sampleCount'], 88)  # 20 ns envelope + 2 ns buffer
        self.assertAlmostEqual(xy['envelopeDurationNs'], 20)
        ro = next(s for s in first['segments'] if s['channel']=='readout')
        self.assertAlmostEqual(ro['startNs']-xy['endNs'], 2)
        self.assertEqual([(s['startNs'],s['endNs']) for s in first['segments']],
                         [(s['startNs'],s['endNs']) for s in second['segments']])
        self.assertAlmostEqual(second['adc']['startNs']-first['adc']['startNs'], 20)
        np.testing.assert_array_equal(a['readout_i'], b['readout_i'])
        with self.assertRaises(ValueError): self.run_compile(durationNs=20.1)

    def test_shelve_uses_segment_carriers_and_preserves_prep(self):
        self.request['kind']='shelve'
        self.request['input'].update(f12Hz=4.66e9, probeDurationNs=24, probeAmplitude=.25)
        p,a=self.run_compile(); q,b=self.run_compile(f12Hz=4.61e9)
        xy=[s for s in p['segments'] if s['channel']=='xy']
        self.assertEqual([s['frequencyHz'] for s in xy], [4.86e9,4.66e9])
        for j,s in enumerate(xy):
            times=np.arange(s['startSample'],s['endSample'])/s['sampleRateHz']
            expected=np.real((a[f'xy{j}_i']+1j*a[f'xy{j}_q'])*np.exp(2j*np.pi*s['frequencyHz']*times))
            np.testing.assert_allclose(a[f'xy{j}_rf'],expected,atol=1e-10)
        np.testing.assert_array_equal(a['xy0_rf'],b['xy0_rf'])
        self.assertGreater(np.max(abs(a['xy1_rf']-b['xy1_rf'])),.01)
        np.testing.assert_array_equal(a['readout_i'],b['readout_i'])

    def test_invalid_inputs_rejected_before_spawn(self):
        for item in ({'sigmaNs':0},{'anharmonicityHz':0},{'durationNs':float('nan')},
                     {'durationNs':1e9},{'shape':'custom_function'},{'amplitude':2},{'execute':True}):
            with self.subTest(item=item), patch('subprocess.Popen',side_effect=AssertionError('spawned')):
                with self.assertRaises(ValueError): self.run_compile(**item)

    def test_actual_cosine_and_peak_overflow(self):
        p,a=self.run_compile(shape='CosPulse')
        expected=.3*np.sin(np.pi*np.arange(80)/80)**2
        np.testing.assert_allclose(a['xy0_i'][4:-4],expected,atol=1e-14)
        with self.assertRaises(ValueError):self.run_compile(amplitude=.99,lambda1=10,anharmonicityHz=-1e6)

    def test_artifact_integrity_and_immutable_directory(self):
        p,a=self.run_compile()
        folder=Path(self.temp.name)/str(self.counter)
        self.assertEqual(hashlib.sha256((folder/'waveforms.npz').read_bytes()).hexdigest(),p['artifacts'][0]['sha256'])
        with self.assertRaises(FileExistsError):self.compile(ROOT,self.request,folder)
        manifest=json.loads((ROOT/'adapters/arclight_configurable_runtime/source_manifest.json').read_text())
        self.assertTrue(all(hashlib.sha256((ROOT/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in manifest))

    def test_cancel_before_spawn(self):
        cancelled=threading.Event();cancelled.set()
        with patch('subprocess.Popen',side_effect=AssertionError('spawned')):
            with self.assertRaises(InterruptedError):self.compile(ROOT,self.request,Path(self.temp.name)/'cancel',cancel=cancelled.is_set)

    def test_half_rotation_and_virtual_z_use_native_conventions(self):
        _,a=self.run_compile();_,half=self.run_compile(rotationRad=np.pi/2)
        _,negative=self.run_compile(rotationRad=-np.pi/2)
        _,vz=self.run_compile(virtualZRad=.4);_,phase=self.run_compile(phaseRad=-.4)
        self.assertEqual(len(a['xy0_i']),len(half['xy0_i']))
        np.testing.assert_allclose(half['xy0_i'],a['xy0_i']/2,atol=1e-14)
        np.testing.assert_allclose(half['xy0_q'],a['xy0_q']/2,atol=1e-14)
        np.testing.assert_allclose(negative['xy0_i'],-a['xy0_i']/2,atol=1e-14)
        np.testing.assert_allclose(vz['xy0_i'],phase['xy0_i'],atol=1e-14)
        np.testing.assert_allclose(vz['xy0_q'],phase['xy0_q'],atol=1e-14)

    def test_readout_shift_preserves_xy_boundaries_and_width(self):
        first,a=self.run_compile(readoutDelayNs=50)
        second,b=self.run_compile(readoutDelayNs=70)
        for left,right in zip(first['segments'],second['segments']):
            self.assertAlmostEqual(right['endNs']-right['startNs'],left['endNs']-left['startNs'])
            self.assertAlmostEqual(right['startNs']-left['startNs'],20 if left['channel']=='readout' else 0)
        np.testing.assert_array_equal(a['xy0_rf'],b['xy0_rf'])

    def test_extension_integrity_rejected_before_spawn(self):
        from adapters.science_runtime import policy
        real_sha=policy.sha
        def changed(path):
            return '0'*64 if str(path).endswith('extension.py') else real_sha(path)
        with patch.object(policy,'sha',side_effect=changed),patch('subprocess.Popen',side_effect=AssertionError('spawned')):
            with self.assertRaisesRegex(ValueError,'integrity'):self.run_compile()

    def test_timeout_and_mid_compile_cancel_stop_child(self):
        # An actual Python process is stopped before it can produce a receipt.
        folder=Path(self.temp.name)/'timeout'
        with self.assertRaises(TimeoutError):self.compile(ROOT,self.request,folder,timeout=0)
        self.assertTrue(json.loads((folder/'failure.json').read_text())['childStopped'])
        calls=[]
        def cancel():calls.append(True);return len(calls)>1
        folder=Path(self.temp.name)/'mid-cancel'
        with self.assertRaises(InterruptedError):self.compile(ROOT,self.request,folder,cancel=cancel)
        self.assertTrue(json.loads((folder/'failure.json').read_text())['childStopped'])

    def test_worker_sandbox_blocks_network_subprocess_and_visa_import(self):
        import subprocess
        profile=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        script='''import sys,socket,subprocess
sys.path.insert(0,sys.argv[1])
import compile_only_worker as worker
sys.addaudithook(worker.audit)
sys.meta_path.insert(0,worker.NoDrivers())
def reject(fn):
    try:fn()
    except PermissionError:return
    raise AssertionError('sandbox allowed effect')
with socket.socket() as s:reject(lambda:s.connect(('127.0.0.1',9)))
reject(lambda:subprocess.Popen([sys.executable,'-c','pass']))
reject(lambda:__import__('pyvisa'))
reject(worker.deny)
print('4 blocked effects')
'''
        result=subprocess.run([profile['python'],'-I','-B','-c',script,str(ROOT/'adapters/science_runtime')],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('4 blocked effects',result.stdout)


if __name__ == '__main__':unittest.main()
