"""Run native numeric/runner contracts inside a guarded isolated interpreter."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]

class NativeSubprocessTests(unittest.TestCase):
    def test_actual_native_compiler_transport_and_cleanup(self):
        profile=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        script='''import sys,types,unittest
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root));sys.path.insert(0,str(root/'adapters/science_runtime'));sys.path.insert(0,str(root/'adapters/arclight_configurable_runtime'))
import compile_only_worker as guard
sys.addaudithook(guard.audit)
from adapters.science_v3_adapter import verify_extension
verify_extension(root)
native=root/'adapters/arclight_configurable_runtime/work/server/core'
for name,path in [('core',native),('core.compiler',native/'compiler'),('core.experiment',native/'experiment')]:guard.namespace(name,path)
inst=types.ModuleType('core.inst');inst.get_hardware=lambda *a,**k:None;sys.modules['core.inst']=inst
from tests.test_science_v3_native import native_suite
result=unittest.TextTestRunner(verbosity=2).run(native_suite())
raise SystemExit(not result.wasSuccessful())
'''
        result=subprocess.run([profile['python'],'-I','-B','-c',script,str(ROOT)],capture_output=True,text=True,timeout=50)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)

def native_suite():
    import os,tempfile,types
    import numpy as np
    from unittest.mock import patch
    from adapters.science_v3.compiler import compile_row
    from adapters.science_v3.transport import NativeTransport
    from adapters.science_v3.plans import build_rows
    import core.inst as inst
    import core.experiment.runner as runner
    import repeated_iq_acquisition as repeated
    from cavity_runtime import close_resources
    class Driver:
        def __init__(self):self.handle=None;self.last_close_result={};self.last_cleanup={}
        def query(self,value):return '4700' if value.startswith('AWG') else 'EXPLICIT TEST FAKE'
        def select_rf_filter(self,*args):return {}
        def cleanup_used_channels(self):self.last_cleanup={'fake':{'ok':True}};return self.last_cleanup
        def close(self):self.handle=None;self.last_close_result={'ok':True}
    class Hardware:
        name='CeyearStationF8'
        _param_specs_inst={'start_cap':types.SimpleNamespace(use_cache=False)}
        def __init__(self):self.driver=Driver();self.writes=[];self.uploads=[];self.reads=0;self.shots=0
        def set_channel(self,*args):pass
        def open(self):self.driver.handle=self.driver
        def close(self):self.driver.close()
        def remove_instance(self,*args):pass
        def write(self,*args,**kwargs):self.writes.append((args,kwargs))
        def set_wave(self,wave,ch):self.uploads.append((wave.name,ch))
        def set_delay(self,*args,**kwargs):pass
        def set_shot(self,shots,**kwargs):self.shots=shots
        def set_point_num(self,*args,**kwargs):pass
        def set_coeff(self,*args,**kwargs):pass
        def set_start_cap(self,*args,**kwargs):pass
        def set_trigger(self,*args,**kwargs):pass
        def read(self,*args,**kwargs):
            self.reads+=1;x=np.arange(self.shots)*.001+self.reads*.1
            return x[:,None].tolist(),(x*.2)[:,None].tolist()
    class Contracts(unittest.TestCase):
        def setUp(self):
            self.temp=tempfile.TemporaryDirectory(prefix='v3-native-');self.addCleanup(self.temp.cleanup)
            self.folder=Path(self.temp.name);os.environ['QCAL_ARCLIGHT_RUN_DIR']=str(self.folder);repeated.ROOT=self.folder
            self.hw=Hardware();inst.get_hardware=lambda *a,**k:self.hw;runner.get_hardware=inst.get_hardware
            self.p={'drive_hz':4.86e9,'drive_amp':.3,'drive_length_s':20e-9,'readout_hz':7.01e9,'readout_amp':.4,
                    'readout_width_s':2e-6,'xy_delay_s':100e-9,'xy_filter_mhz':4700,'xy_attenuation_db':3.75,
                    'pulse_shape':'Gaussian','pulse_length_s':20e-9,'pulse_sigma_s':4e-9,'shape_pi_amp':.3,
                    'probe12_shape':'CosPulse','probe12_length_s':24e-9,'probe12_amplitude':.2,'f12_hz':4.66e9,
                    'shots':10,'capture_repeats':4,'acquisitions_per_point':2,'scan_axis_values':[.1,.2,.3]}
            self.p,self.rows=build_rows('sq_shape_rabi_amplitude',self.p)
            self.base=json.loads((ROOT/'adapters/arclight_configurable_runtime/base_config.json').read_text(encoding='utf-8'))
            self.route={'xyLogical':[6,0],'readoutLogical':[14,0],'adcLogical':[15,0],'adcPhysical':[0,0],'adcSampleRateHz':2.8e9}
            self.plan={'operationId':'fake-native-only','resourceId':'Q11','routing':self.route,'parameters':self.p,
                       'instrument':{'address':'TEST FAKE'},'visaLibrary':'not-used'}
            osc=types.SimpleNamespace(oscboardSelect=lambda *a:None,oscSamRate=lambda *a:2.8)
            self.native={'create_hardware':lambda *a,**k:self.hw,'runner':runner.SequenceRunner,'osc':osc,
                         'close_resources':close_resources,'acquire':repeated.get_repeated_raw_data,
                         'delegated_lock':lambda:types.SimpleNamespace(acquire=lambda info:None,release=lambda:None)}
            sleeper=patch('core.experiment.runner.time.sleep',lambda seconds:None);sleeper.start();self.addCleanup(sleeper.stop)
        def test_real_native_runner_uploads_and_fresh_blocks(self):
            seq,expected=compile_row(self.rows[1],self.base,self.p)
            transport=NativeTransport(self.plan,self.folder,lambda:None,native=self.native);transport.open()
            try:i,q=transport.acquire(self.rows[1],seq,expected)
            finally:receipt=transport.close()
            self.assertEqual(i.shape,(1,20));self.assertEqual(self.hw.reads,2)
            self.assertEqual(len(self.hw.uploads),2);self.assertEqual(len(transport.uploads),2)
            self.assertTrue(receipt['shutdown_ok']);self.assertIsNone(self.hw.driver.handle)
            self.assertNotEqual(i[0,0],i[0,10])
            manifest=json.loads((self.folder/'point_0001_acquisition_blocks.json').read_text())
            self.assertEqual(manifest['blocks_completed'],2)
            self.assertNotEqual(manifest['blocks'][0]['raw_iq_sha256'],manifest['blocks'][1]['raw_iq_sha256'])
        def test_identical_native_archives_keep_distinct_registered_file_identity(self):
            from adapters.science_v3.compiler import archive_row
            from adapters.science_v3_adapter import ScienceV3Adapter,build_science_profile
            from backend.store import Store
            from backend.controller import Controller
            from backend.science_dependencies import ScienceDependencyResolver
            seq,expected=compile_row(self.rows[1],self.base,self.p)
            paths=[self.folder/'preflight_0001.npz',self.folder/'preflight_0004.npz']
            receipts=[archive_row(path,seq,expected) for path in paths]
            self.assertEqual(receipts[0]['sha256'],receipts[1]['sha256'])
            profile=build_science_profile(ROOT,json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8')))
            artifacts=ScienceV3Adapter(ROOT,profile)._artifacts(self.folder,[str(p) for p in paths])
            controller=types.SimpleNamespace(root=self.folder)
            store=Store(self.folder/'platform.sqlite3',root=self.folder)
            with store.connect() as rt:
                registered=[Controller._artifact(controller,rt,item) for item in artifacts]
                resolved=ScienceDependencyResolver(controller)._artifacts(rt,{'artifacts':registered},{})
            self.assertEqual(len(resolved),2);self.assertEqual(len({a['id'] for a in resolved}),2)
            self.assertEqual({a['name'] for a in resolved},{p.name for p in paths})
        def test_changed_compilation_or_upload_identity_rejects_before_upload(self):
            seq,expected=compile_row(self.rows[1],self.base,self.p)
            transport=NativeTransport(self.plan,self.folder,lambda:None,native=self.native);transport.open()
            try:
                expected['xy_1']['sha256']='0'*64
                with self.assertRaisesRegex(ValueError,'Upload bytes'):transport.acquire(self.rows[1],seq,expected)
                self.assertEqual(self.hw.uploads,[])
            finally:self.assertTrue(transport.close()['shutdown_ok'])
            seq,expected=compile_row(self.rows[1],self.base,self.p)
            seq.pulse_list_per_qubit[1][0][1].A+=.01
            with self.assertRaisesRegex(ValueError,'Regenerated'):seq.perform_sequence_awg()
        def test_shelve_native_carriers_and_adc_demod(self):
            p,rows=build_rows('sq_shelve_spectrum12',{**self.p,'scan_axis_values':[4.6e9,4.65e9,4.7e9]})
            on=[r for r in rows if r['condition']=='prep_on']
            a,_=compile_row(on[0],self.base,p);b,_=compile_row(on[1],self.base,p)
            np.testing.assert_array_equal(a.arrays['xy0_rf'],b.arrays['xy0_rf'])
            self.assertGreater(np.max(abs(a.arrays['xy1_rf']-b.arrays['xy1_rf'])),.01)
            self.assertEqual(a.config.ad_freq([1]),[p['readout_hz']])
        def test_all_six_families_compile_actual_native_segments(self):
            from adapters.science_v3.plans import METHODS
            for method in METHODS:
                supplied={k:v for k,v in self.p.items() if k!='scan_axis_values'}
                if method=='sq_shelve_spectrum12':supplied['scan_axis_values']=[4.6e9,4.65e9,4.7e9]
                elif method=='sq_drag_scan':supplied.update(scan_axis_values=[-1,-.5,0,.5,1],anharmonicity_hz=-200e6)
                elif method not in ('sq_shape_half_verify','sq_shelve_readout_compare'):supplied['scan_axis_values']=[.1,.2,.3]
                p,rows=build_rows(method,supplied)
                for row in (r for r in rows if r['capture']==0):
                    with self.subTest(method=method,condition=row['condition'],axis=row['axisValue']):
                        seq,expected=compile_row(row,self.base,p)
                        self.assertEqual(set(expected),{'xy_1','ro_B1'})
                        segments=[s for s in seq.segments if s['channel']=='xy']
                        self.assertEqual(len(segments),len(row['gates']))
                        for segment,gate in zip(segments,row['gates']):
                            pulse=gate['pulse']
                            self.assertEqual(segment['sampleCount'],round((pulse['durationNs']+2)*4))
                            self.assertAlmostEqual(segment['frequencyHz'],pulse['frequencyHz'])
                            if pulse['lambda1']:
                                self.assertAlmostEqual(segment['betaSeconds'],-pulse['lambda1']/(2*np.pi*pulse['anharmonicityHz']))
                        if method=='sq_shape_half_verify' and row['axisValue']==.5:
                            self.assertAlmostEqual(max(abs(seq.arrays['xy0_i'])),p['shape_pi_amp']/2)
        def test_stop_inside_block_preserves_raw_then_cleanup(self):
            seq,expected=compile_row(self.rows[1],self.base,self.p)
            calls=[]
            def cancel():
                calls.append(True)
                if len(calls)>=5:raise InterruptedError('explicit test stop')
            transport=NativeTransport(self.plan,self.folder,cancel,native=self.native);transport.open()
            try:
                with self.assertRaises(InterruptedError):transport.acquire(self.rows[1],seq,expected)
            finally:receipt=transport.close()
            self.assertTrue(receipt['shutdown_ok']);self.assertTrue((self.folder/'point_0001_block_000.npz').is_file())
        def test_worker_failed_cleanup_retains_lock_and_all_raw_blocks(self):
            from adapters.science_v3.execute_worker import run_acquisition
            plan={**self.plan,'procedureId':'sq_shape_rabi_amplitude','rows':self.rows[:2],'max_seconds':15}
            (self.folder/'plan.json').write_text(json.dumps(plan))
            lock=self.folder/'explicit-test-chassis.lock'
            def acquire(plan,folder):lock.write_text(json.dumps({'token':'test'}));return [lock],'test'
            def failed_close(hw,exp):
                receipt=close_resources(hw,exp);return {**receipt,'shutdown_ok':False,'shutdown_errors':{'fake_rf':'injected cleanup failure'}}
            native={**self.native,'close_resources':failed_close}
            result=run_acquisition(plan,self.folder,lock_acquire=acquire,
                transport_factory=lambda p,f,c:NativeTransport(p,f,c,native=native))
            self.assertEqual(result['state'],'needs_reconciliation');self.assertTrue(lock.is_file())
            self.assertTrue((self.folder/'results/raw_scan.npz').is_file())
            self.assertTrue((self.folder/'results/uploads.jsonl').is_file())
        def test_worker_completed_acquisition_releases_only_owned_lock(self):
            from adapters.science_v3.execute_worker import run_acquisition
            plan={**self.plan,'procedureId':'sq_shape_rabi_amplitude','rows':self.rows,'max_seconds':15}
            (self.folder/'plan.json').write_text(json.dumps(plan));lock=self.folder/'explicit-test-chassis.lock'
            # Production bootstrap.initialize creates this empty directory.
            (self.folder/'results').mkdir()
            def acquire(plan,folder):lock.write_text(json.dumps({'token':'test'}));return [lock],'test'
            result=run_acquisition(plan,self.folder,lock_acquire=acquire,
                transport_factory=lambda p,f,c:NativeTransport(p,f,c,native=self.native))
            self.assertEqual(result['state'],'completed');self.assertTrue(result['shutdown_ok']);self.assertFalse(lock.exists())
            self.assertEqual(result['pointsCompleted'],len(self.rows));self.assertEqual(self.hw.reads,2*len(self.rows))
    return unittest.defaultTestLoader.loadTestsFromTestCase(Contracts)
