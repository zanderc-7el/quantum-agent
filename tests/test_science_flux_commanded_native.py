"""Actual native compiler/LF setter bodies on recording I/O; sockets forbidden."""
import json
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]

class NativeCommandedSubprocess(unittest.TestCase):
    def test_commanded_worker_with_actual_native_compiler(self):
        profile=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf8'))
        script='''import sys,types,unittest
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root));sys.path.insert(0,str(root/'adapters/science_runtime'));sys.path.insert(0,str(root/'adapters/arclight_configurable_runtime'))
import compile_only_worker as guard
sys.addaudithook(guard.audit)
native=root/'adapters/arclight_configurable_runtime/work/server/core'
for name,path in [('core',native),('core.compiler',native/'compiler'),('core.experiment',native/'experiment')]:guard.namespace(name,path)
inst=types.ModuleType('core.inst');inst.get_hardware=lambda *a,**k:None;sys.modules['core.inst']=inst
from tests.test_science_flux_commanded_native import native_suite
result=unittest.TextTestRunner(verbosity=2).run(native_suite())
raise SystemExit(not result.wasSuccessful())
'''
        result=subprocess.run([profile['python'],'-I','-B','-c',script,str(ROOT)],capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)

def native_suite():
    import ast,os,tempfile,types
    import numpy as np
    from tests.test_science_flux import Clock
    from tests.test_science_flux_commanded import route,confirmation
    from tests.test_science_flux_plans import parameters
    from adapters.science_runtime.policy import digest
    from adapters.science_flux.command_contracts import validate_command_journal
    from adapters.science_flux.contracts import command_authorization
    from adapters.science_flux.command_session import CommandedBiasSession
    from adapters.science_flux.plans import build_campaign
    from adapters.science_flux.execute_worker import run_acquisition
    from adapters.science_flux.lf_transport import NativeLFSetter
    source=ROOT/'adapters/arclight_configurable_runtime/work/server/core/inst/driver_old/AWG1652AM.py'
    tree=ast.parse(source.read_text(encoding='utf-8-sig'));names={'boardSelect','channSelect','setOffset'}
    tree.body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    namespace={};exec(compile(tree,str(source),'exec'),namespace)
    native=types.SimpleNamespace(**{k:namespace[k] for k in names})
    class Worker(unittest.TestCase):
        def setUp(self):
            self.container=tempfile.TemporaryDirectory(prefix='TEST_ONLY-commanded-native-');self.addCleanup(self.container.cleanup)
            self.temp=tempfile.TemporaryDirectory(dir=self.container.name,prefix='operation-');self.addCleanup(self.temp.cleanup)
        def run_worker(self,*,point=False,stop=False,bad_reply=False,end_failure=False,hold=False):
            folder=Path(self.temp.name);r=route();p=parameters();p['bias_values_v']=[.2]
            p['readout_frequencies_hz']=p['readout_frequencies_hz'][:5];method='ro_flux_scan';source_point=None
            if point:
                method='sq_flux_point_recheck';p['local_readout_frequencies_hz']=p.pop('readout_frequencies_hz');p.pop('bias_values_v')
                p.update(drive_frequencies_hz=[4.8e9+n*1e6 for n in range(5)],drive_amp=.1,spectroscopy_width_s=2e-6)
                source_point={'recordId':'TEST_ONLY','pointId':'bias-0','resourceId':'Qfixture','requestedSettingV':.2,
                    'usableReadoutBandHz':[6.98e9,7.02e9],'routeSha256':digest(r)}
            campaign=build_campaign(method,p,r,point=source_point)
            plan={**campaign,'operationId':'TEST_ONLY-operation','resourceId':'Qfixture','procedureId':method,'biasRoute':r,
                'biasCommandAuthorization':command_authorization(r,'TEST_ONLY-operation',start_confirmation=confirmation('TEST_ONLY-operation',r)),'routing':{'adcSampleRateHz':2.8e9},
                'max_seconds':3000,'hardwareLocks':[str(folder/'owned.lock')]}
            if hold:
                from adapters.science_flux.command_lease import lease_context
                request={'runId':'TEST_ONLY','nodeId':'TEST_ONLY','attemptId':'TEST_ONLY','resourceId':'Qfixture','leaseToken':'TEST_ONLY'}
                plan['commandLease']=lease_context(request,'selection')
            (folder/'plan.json').write_text(json.dumps(plan),encoding='utf8')
            calls=[];clock=Clock();events=[]
            class Handle:
                timeout=10000
                def __init__(self):self.offset_count=0
                def query(self,command):
                    calls.append(command)
                    if command.startswith('AWG:SELMODU'):return 'fixture module'
                    if command.startswith('AWG:SELCHL'):return 'fixture channel'
                    self.offset_count+=1
                    if bad_reply and self.offset_count==2:return 'ERROR unrecognized command'
                    if end_failure and 'stop_rf' in calls:raise TimeoutError('TEST_ONLY end reply lost')
                    return 'fixture offset'
            class Transport:
                hardware=None
                def __init__(self,*args):self.set_offset=NativeLFSetter(Handle(),r,native=native)
                def open(self):calls.append('open');return {'fixtureOnly':True}
                def acquire(self,row,seq,expected):
                    calls.append(('acquire',row['id'],row['phase']))
                    if stop:(folder/'STOP_AFTER_POINT').write_text('stop')
                    x=np.arange(16)[None,:]*.001+row['id']*.01
                    np.savez_compressed(folder/'results'/f'point_{row["id"]:04d}.npz',i=x,q=x*.1,acquisition_block_index=np.zeros(16,dtype=int))
                    return x,x*.1
                def stop_rf(self):calls.append('stop_rf');return {'verified':True}
                def close(self):calls.append('close');return {'shutdown_ok':True}
            def lock_acquire(plan,folder):
                lock=folder/'owned.lock';lock.write_text(json.dumps({'token':'TEST_ONLY-token','operationId':plan['operationId']}));return [lock],'TEST_ONLY-token'
            def factory(r,provider,setter,path,**kwargs):return CommandedBiasSession(r,provider,setter,path,**kwargs,monotonic=clock.monotonic,sleep=clock.sleep)
            def analyze(cut,rows,i,q,p,blocks):return {'accepted':True,'candidateHz':cut['frequenciesHz'][2],
                'contrast_snr':10.,'responseMagnitude':[1]*len(cut['frequenciesHz']),'series':[]}
            os.environ['QCAL_ARCLIGHT_RUN_DIR']=str(folder)
            result=run_acquisition(plan,folder,transport_factory=Transport,lock_acquire=lock_acquire,
                bias_factory=factory,analyze_function=analyze,emit=events.append)
            return result,calls,plan,events

        def test_same_commanded_point_real_compile_adaptive_f01_and_declared_end(self):
            result,calls,plan,events=self.run_worker(point=True)
            self.assertEqual(result['state'],'completed');self.assertTrue(result['testOnly']);self.assertTrue(result['shutdown_ok'])
            self.assertEqual(len(result['points']),2);self.assertTrue(all(p['requestedSettingV']==.2 for p in result['points']))
            self.assertTrue(all(p['biasReceipt']['measurement'] is None for p in result['points']))
            journal=json.loads((Path(self.temp.name)/'results/bias_journal.json').read_text())
            validate_command_journal(journal,route(),{'operationId':plan['operationId'],'resourceId':'Qfixture'},plan['biasCommandAuthorization'])
            self.assertEqual(result['restoration']['requestedSettingV'],-.1);self.assertFalse(result['restoration']['verified'])
            self.assertLess(calls.index('stop_rf'),calls.index('AWG:DCOFFSET -0.1V'));self.assertEqual(calls[-1],'close')
            self.assertFalse((Path(self.temp.name)/'owned.lock').exists())
            self.assertNotIn('restoration_verified',result['analysis']['metrics']);self.assertNotIn('bias_readback_coverage',result['analysis']['metrics'])
            self.assertTrue(all(e['currentAxes']['measuredOutputV'] is None for e in events))
            compiled=json.loads((Path(self.temp.name)/'results/compiled_plan.json').read_text())
            adaptive=[x for x in compiled if 'adaptiveReadoutHz' in x]
            self.assertTrue(adaptive);self.assertTrue(all(x['sourceLocalCutSha256']==result['points'][0]['cutSha256'] for x in adaptive))

        def test_stop_retains_adc_and_returns_declared_end(self):
            result,calls,_,_=self.run_worker(stop=True)
            self.assertEqual(result['state'],'cancelled');self.assertTrue(result['shutdown_ok'])
            self.assertEqual(result['restoration']['requestedSettingV'],-.1)
            self.assertTrue((Path(self.temp.name)/'results/partial_raw_scan.npz').exists())
            self.assertFalse((Path(self.temp.name)/'owned.lock').exists())

        def test_unknown_native_reply_no_adc_or_blind_cleanup_write_and_lock_retained(self):
            result,calls,_,_=self.run_worker(bad_reply=True)
            self.assertEqual(result['state'],'needs_reconciliation');self.assertFalse(result['shutdown_ok'])
            self.assertFalse(any(isinstance(c,tuple) and c[0]=='acquire' for c in calls))
            self.assertTrue((Path(self.temp.name)/'owned.lock').exists())
            self.assertFalse(any(str(c).startswith('AWG:') for c in calls[calls.index('stop_rf')+1:]))
            journal=json.loads((Path(self.temp.name)/'results/bias_journal.json').read_text())
            self.assertIn('ERROR unrecognized command',journal['events'][-1]['replyEvidence']['replies'])
            self.assertFalse(journal['events'][-1]['replyEvidence']['commandConfirmed'])

        def test_end_timeout_retains_raw_and_quarantines(self):
            result,calls,_,_=self.run_worker(end_failure=True)
            self.assertEqual(result['state'],'needs_reconciliation');self.assertFalse(result['restoration']['commandEndVerified'])
            self.assertTrue((Path(self.temp.name)/'owned.lock').exists());self.assertTrue((Path(self.temp.name)/'results/raw_scan.npz').exists())

        def test_selection_retains_all_shared_locks_for_verified_end_handoff(self):
            result,calls,plan,_=self.run_worker(hold=True)
            self.assertEqual(result['state'],'completed');self.assertTrue(result['shutdown_ok'])
            lock=json.loads((Path(self.temp.name)/'owned.lock').read_text())
            self.assertEqual(lock['commandLeaseState'],'awaiting_verification')
            continuation=result['commandContinuation']
            self.assertEqual(continuation['endCommandSha256'],result['restoration']['command']['sha256'])
            self.assertEqual(continuation['lease'],plan['commandLease']);self.assertEqual(calls[-1],'close')

        def test_numeric_result_contract_separates_commanded_voltage_and_real_iq(self):
            from tests.test_science_flux_commanded_numeric_fixture import native_fixture
            for method in ('ro_flux_scan','sq_flux_point_recheck'):
                with self.subTest(method=method):
                    fixture=native_fixture(method);self.assertTrue(fixture['neverAcquiredFromHardware'])
                    selection,verification=fixture['selectionResult'],fixture['verificationResult']
                    a=selection['provenance']['scienceFlux'];b=verification['provenance']['scienceFlux']
                    self.assertEqual(b['commandAuthorization']['entryEvidence']['completionSha256'],a['completionSha256'])
                    self.assertEqual(b['commandAuthorization']['entrySettingV'],-.1)
                    self.assertTrue(selection['analysisQuality']['accepted']);self.assertTrue(verification['analysisQuality']['accepted'])
                    self.assertFalse(selection['provenance']['biasRestorationReadbackVerified'])
                    self.assertTrue(all(p['biasReceipt']['measuredOutputV'] is None for p in b['points']))
                    if method=='ro_flux_scan':self.assertEqual(selection['methodLabel'],'Commanded bias / cavity scan')

        def test_stop_with_continuation_context_releases_only_after_known_end(self):
            result,_,plan,_=self.run_worker(hold=True,stop=True)
            self.assertEqual(result['state'],'cancelled');self.assertTrue(result['shutdown_ok'])
            self.assertFalse((Path(self.temp.name)/'owned.lock').exists())
            index=Path(self.temp.name).parent/'_command_leases'/(digest('TEST_ONLY')+'.json')
            self.assertEqual(json.loads(index.read_text())['state'],'released_after_shutdown')
            # Fixture registry outside the temporary operation mirrors the real
            # operations directory; remove only this explicitly named test file.
            index.unlink()
    return unittest.defaultTestLoader.loadTestsFromTestCase(Worker)

if __name__=='__main__':unittest.main()
