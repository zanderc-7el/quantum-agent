"""Actual frozen native compiler + recording LF/ADC only; audit blocks network."""
import json
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]

class NativeSubprocess(unittest.TestCase):
    def test_native_flux_contracts(self):
        profile=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        script='''import sys,types,unittest
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root));sys.path.insert(0,str(root/'adapters/science_runtime'));sys.path.insert(0,str(root/'adapters/arclight_configurable_runtime'))
import compile_only_worker as guard
sys.addaudithook(guard.audit)
native=root/'adapters/arclight_configurable_runtime/work/server/core'
for name,path in [('core',native),('core.compiler',native/'compiler'),('core.experiment',native/'experiment')]:guard.namespace(name,path)
inst=types.ModuleType('core.inst');inst.get_hardware=lambda *a,**k:None;sys.modules['core.inst']=inst
from tests.test_science_flux_native import native_suite
result=unittest.TextTestRunner(verbosity=2).run(native_suite())
raise SystemExit(not result.wasSuccessful())
'''
        result=subprocess.run([profile['python'],'-I','-B','-c',script,str(ROOT)],capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)

def native_suite():
    import tempfile,os
    import numpy as np
    from adapters.science_flux.compiler import compile_row
    from tests.test_science_flux_plans import parameters
    class CompilerContracts(unittest.TestCase):
        def test_wrong_station_identity_rejects_before_rf_adc_configuration(self):
            import types
            from adapters.science_flux.transport import FluxTransport
            from tests.test_science_flux import route
            writes=[]
            handle=types.SimpleNamespace(query=lambda command:'4700' if command.startswith('AWG') else 'WRONG-STATION',timeout=10000,close=lambda:None)
            hw=types.SimpleNamespace(set_channel=lambda *a:None,open=lambda:None,
                driver=types.SimpleNamespace(handle=handle,select_rf_filter=lambda *a:None),
                write=lambda *a,**k:writes.append((a,k)),set_wave=lambda *a,**k:None)
            native={'create_hardware':lambda *a,**k:hw,'osc':types.SimpleNamespace(oscboardSelect=lambda *a:None,oscSamRate=lambda *a:2.8),
                'runner':lambda:None,'delegated_lock':lambda:types.SimpleNamespace(acquire=lambda *a:None,release=lambda:None)}
            plan={'operationId':'fixture','resourceId':'Qfixture','biasRoute':route(),'instrument':{'address':'fixture'},'visaLibrary':'unused',
                'parameters':parameters(),'routing':{'readoutLogical':[14,0],'adcLogical':[15,0],'xyLogical':[6,0],'adcPhysical':[0,0],'adcSampleRateHz':2.8e9}}
            t=FluxTransport(plan,ROOT,lambda:None,native=native)
            with self.assertRaisesRegex(ValueError,'identity'):t.open()
            self.assertTrue(t.stop_rf()['verified']);self.assertTrue(t.close()['shutdown_ok'])
            self.assertEqual(writes,[])
        def test_actual_spectrum_analysis_exposes_means_and_noise(self):
            from adapters.science_flux.analysis import analyze_cut
            from adapters.science_flux.plans import build_campaign
            from tests.test_science_flux import route
            from adapters.science_runtime.policy import digest
            p=parameters();p.pop('bias_values_v');p['local_readout_frequencies_hz']=p.pop('readout_frequencies_hz')
            p.update(drive_frequencies_hz=[4.79e9+n*1e6 for n in range(21)],drive_amp=.1,spectroscopy_width_s=2e-6)
            source={'resourceId':'Qfixture','requestedSettingV':.2,'usableReadoutBandHz':[6.98e9,7.02e9],'pointId':'bias-0','recordId':'fixture','routeSha256':digest(route())}
            c=build_campaign('sq_flux_point_recheck',p,route(),point=source);cut=c['cuts'][1];rows=[c['rows'][n] for n in cut['rows']]
            rng=np.random.default_rng(101);i=[];q=[]
            for r in rows:
                signal=.2*np.exp(-.5*((r['frequencyHz']-4.8e9)/2e6)**2) if r['condition']=='on' else 0
                i.append(signal+rng.normal(0,.001,(1,p['shots'])));q.append(rng.normal(0,.001,(1,p['shots'])))
            i=np.stack(i);q=np.stack(q);a=analyze_cut(cut,rows,i,q,c['parameters'],np.zeros_like(i,dtype=int))
            self.assertTrue(a['accepted']);self.assertEqual(len(a['signalCaptures']),2)
            for n,s in enumerate(a['signalCaptures']):
                delta=np.array(s['onMeanI'])-s['offMeanI']+1j*(np.array(s['onMeanQ'])-s['offMeanQ'])
                edge=max(1,len(delta)//8);bg=np.median(np.r_[delta[:edge].real,delta[-edge:].real])+1j*np.median(np.r_[delta[:edge].imag,delta[-edge:].imag])
                y=np.abs(delta-bg);k=int(y.argmax());snr=y[k]/np.hypot(s['offComplexSem'][k],s['onComplexSem'][k])
                self.assertTrue(np.allclose(y,a['series'][n]['y']));self.assertAlmostEqual(snr,a['captureCandidates'][n]['candidate_significance'])
        def test_real_dc_off_on_and_readout_frequency(self):
            base=json.loads((ROOT/'adapters/arclight_configurable_runtime/base_config.json').read_text(encoding='utf-8'))
            p={**parameters(),'drive_amp':.2,'spectroscopy_width_s':2e-6}
            for phase,condition in [('cavity','off'),('f01','off'),('f01','on')]:
                row={'phase':phase,'frequencyHz':4.8e9 if phase=='f01' else 7e9,'condition':condition}
                seq,expected=compile_row(row,base,p,readout_hz=7.001e9)
                self.assertEqual(set(expected),{'xy_1','ro_B1'})
                xy=seq.waves._waves['xy_1'].get_data()
                self.assertEqual(bool(np.any(xy)),condition=='on')
                self.assertTrue(all(s['channel'] in ('xy','readout') for s in seq.segments))
                if condition=='on':self.assertAlmostEqual(next(s for s in seq.segments if s['channel']=='xy')['envelopeDurationNs'],2000)
                ro=next(s for s in seq.segments if s['channel']=='readout')
                self.assertAlmostEqual(ro['frequencyHz'],1e6 if phase=='f01' else 0,places=3)
    class WorkerContracts(unittest.TestCase):
        def run_worker(self,*,mismatch=False,restore_fail=False,cancel=False,point=False,cavity_ok=True):
            from adapters.science_flux.execute_worker import run_acquisition
            from adapters.science_flux.plans import build_campaign
            from adapters.science_flux.bias_session import BiasSession
            from tests.test_science_flux import Provider,Clock,route
            from adapters.science_runtime.policy import digest
            folder=Path(self.temp.name);p=parameters();p['readout_frequencies_hz']=p['readout_frequencies_hz'][:5];p['bias_values_v']=[.2]
            source=None;method='ro_flux_scan'
            if point:
                method='sq_flux_point_recheck';p['local_readout_frequencies_hz']=p.pop('readout_frequencies_hz');p.pop('bias_values_v')
                p.update(drive_frequencies_hz=[4.8e9+i*1e6 for i in range(5)],drive_amp=.1,spectroscopy_width_s=2e-6)
                source={'resourceId':'Qfixture','requestedSettingV':.2,'usableReadoutBandHz':[6.98e9,7.02e9],
                    'pointId':'bias-0','recordId':'fixture','routeSha256':digest(route())}
            campaign=build_campaign(method,p,route(),point=source)
            plan={**campaign,'operationId':'fixture-no-hardware','resourceId':'Qfixture','procedureId':method,'biasRoute':route(),
                'routing':{'adcSampleRateHz':2.8e9},'max_seconds':3000,'hardwareLocks':[str(folder/'owned.lock')]}
            provider=Provider();clock=Clock();events=[];calls=[];locks=[]
            class Transport:
                hardware=None
                def __init__(self,*args):pass
                def open(self):calls.append('open');return {'fixture':True}
                def set_offset(self,v,*,timeout_seconds):
                    calls.append(('set',v))
                    if restore_fail and v<provider.setting and provider.setting>.1:raise TimeoutError('restore failed')
                    provider.setting=v
                    if mismatch and v>.1:provider.bad=True
                    return {'ACK':True}
                def acquire(self,row,seq,expected):
                    calls.append(('acquire',row['id'],row['phase']))
                    if cancel:(folder/'STOP_AFTER_POINT').write_text('stop')
                    x=np.arange(16)[None,:]*.001+row['id']*.01
                    np.savez_compressed(folder/'results'/f'point_{row["id"]:04d}.npz',i=x,q=x*.1,acquisition_block_index=np.zeros(16,dtype=int))
                    return x,x*.1
                def stop_rf(self):calls.append('stop_rf');return {'verified':True}
                def close(self):calls.append('close');return {'shutdown_ok':True}
            def lock_acquire(plan,folder):
                lock=folder/'owned.lock';lock.write_text(json.dumps({'token':'fixture-token'}));locks.append(lock);return [lock],'fixture-token'
            def bias_factory(r,provider,setter,path,**kwargs):return BiasSession(r,provider,setter,path,**kwargs,monotonic=clock.monotonic,sleep=clock.sleep)
            def analyze(cut,rows,i,q,p,blocks):
                return {'accepted':cavity_ok,'reasons':[] if cavity_ok else ['no cavity'],
                    'candidateHz':cut['frequenciesHz'][2] if cavity_ok else None,'contrast_snr':10.,
                    'frequenciesHz':cut['frequenciesHz'],'responseMagnitude':[1]*len(cut['frequenciesHz']),'series':[]}
            os.environ['QCAL_ARCLIGHT_RUN_DIR']=str(folder)
            result=run_acquisition(plan,folder,transport_factory=Transport,provider=provider,lock_acquire=lock_acquire,
                bias_factory=bias_factory,analyze_function=analyze,emit=events.append)
            return result,calls,locks,provider
        def setUp(self):self.temp=tempfile.TemporaryDirectory(prefix='flux-fixture-');self.addCleanup(self.temp.cleanup)
        def test_complete_restores_before_close_and_releases(self):
            result,calls,locks,p=self.run_worker()
            self.assertEqual(result['state'],'completed');self.assertTrue(result['testOnly']);self.assertTrue(result['shutdown_ok'])
            self.assertAlmostEqual(p.setting,.1);self.assertFalse(locks[0].exists())
            self.assertLess(calls.index('stop_rf'),calls.index(('set',.1)));self.assertLess(calls.index(('set',.1)),calls.index('close'))
            self.assertTrue((Path(self.temp.name)/'results/raw_scan.npz').is_file())
        def test_mismatch_prevents_target_adc_and_restore_failure_retains_lock(self):
            result,calls,locks,p=self.run_worker(mismatch=True)
            self.assertEqual(result['state'],'needs_reconciliation');self.assertTrue(locks[0].exists())
            self.assertEqual(len([x for x in calls if isinstance(x,tuple) and x[0]=='acquire']),10)
        def test_stop_retains_partial_and_restores(self):
            result,calls,locks,p=self.run_worker(cancel=True)
            self.assertEqual(result['state'],'cancelled');self.assertTrue(result['shutdown_ok']);self.assertFalse(locks[0].exists())
            self.assertTrue((Path(self.temp.name)/'results/partial_raw_scan.npz').is_file())
        def test_no_cavity_never_launches_f01(self):
            result,calls,locks,p=self.run_worker(point=True,cavity_ok=False)
            self.assertEqual(result['state'],'completed');self.assertFalse(result['analysis']['analysis']['accepted'])
            self.assertFalse(any(isinstance(x,tuple) and len(x)>2 and x[2]=='f01' for x in calls))
        def test_restore_timeout_quarantines_owned_lock(self):
            result,calls,locks,p=self.run_worker(restore_fail=True)
            self.assertEqual(result['state'],'needs_reconciliation');self.assertFalse(result['restoration']['verified'])
            self.assertTrue(locks[0].exists());self.assertIn('close',calls)
        def test_valid_cavity_launches_same_bias_f01(self):
            result,calls,locks,p=self.run_worker(point=True)
            self.assertEqual(result['state'],'completed');self.assertEqual(len(result['points']),2)
            self.assertTrue(any(isinstance(x,tuple) and len(x)>2 and x[2]=='f01' for x in calls))
    return unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (CompilerContracts,WorkerContracts)])

if __name__=='__main__':unittest.main()
