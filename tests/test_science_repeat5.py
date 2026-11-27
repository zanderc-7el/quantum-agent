"""R23 fixed five-gate shaped-amplitude calibration, never instruments."""
import math
import unittest
from copy import deepcopy


def parameters():
    return dict(drive_hz=4.85e9,drive_amp=.3,drive_length_s=660e-9,
        readout_hz=7e9,readout_amp=.2,readout_width_s=2e-6,xy_delay_s=0.,
        xy_filter_mhz=4700,xy_attenuation_db=0.,pulse_shape='CosPulse',
        pulse_length_s=660e-9,pulse_sigma_s=165e-9,pulse_phase_rad=0.,
        shape_pi_amp=.8,shots=100,capture_repeats=2,points=41,amp_start=0.,amp_stop=.98)


class Repeat5Plans(unittest.TestCase):
    def test_all_scan_and_fixed_candidate_rows_have_five_identical_x_gates(self):
        from adapters.science_shape_repeat5.plans import build_repeat5,PROCEDURE
        p,rows=build_repeat5({**parameters(),'verify_shape_pi_amp':.82})
        self.assertEqual(len(rows),84)
        for row in rows:
            self.assertEqual(len(row['gates']),5)
            self.assertTrue(all(g==row['gates'][0] for g in row['gates']))
            self.assertEqual(row['gates'][0]['theta'],math.pi)
            self.assertEqual(row['gates'][0]['axis'],'x')
            self.assertEqual(row['gateRepeats'],5)
            self.assertEqual(row['gates'][0]['pulse']['amplitude'],row['axisValue'])
        self.assertEqual(sum(r['condition']=='verification_candidate' for r in rows),2)

    def test_fixed_n_and_unreachable_candidate_are_rejected(self):
        from adapters.science_shape_repeat5.plans import build_repeat5
        for extra in ({'gate_repeats':1},{'gate_repeats':5},{'verify_shape_pi_amp':.98},
                      {'verify_shape_pi_amp':.999},{'drag_lambda1':.2}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):build_repeat5({**parameters(),**extra})

    def test_legacy_rows_and_single_gate_identity_remain_unchanged(self):
        from adapters.science_shape_repeat5.plans import build_repeat5
        from adapters.science_v3.plans import build_rows,pulse_identity
        original=parameters();before=deepcopy(original)
        p,old=build_rows('sq_shape_rabi_amplitude',original);_,new=build_repeat5(original)
        self.assertEqual(original,before)
        self.assertTrue(all(len(r['gates'])==1 for r in old))
        readout={k:p[k] for k in ('readout_hz','readout_amp','readout_width_s','xy_delay_s')}
        for a,b in zip(old,new):
            self.assertEqual(pulse_identity(a['gates'][0]['pulse'],readout),pulse_identity(b['gates'][0]['pulse'],readout))


class Repeat5Native(unittest.TestCase):
    def test_actual_compiler_numeric_and_native_transport(self):
        import json,subprocess
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        profile=json.loads((root/'adapters/arclight_profile.json').read_text('utf8'))
        code='''import sys,types,unittest
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root));sys.path.insert(0,str(root/'adapters/science_runtime'));sys.path.insert(0,str(root/'adapters/arclight_configurable_runtime'))
import compile_only_worker as guard
sys.addaudithook(guard.audit)
core=root/'adapters/arclight_configurable_runtime/work/server/core'
for name,path in [('core',core),('core.compiler',core/'compiler'),('core.experiment',core/'experiment'),('core.circuit',core/'circuit')]:guard.namespace(name,path)
inst=types.ModuleType('core.inst');inst.get_hardware=lambda *a,**k:None;sys.modules['core.inst']=inst
from tests.test_science_repeat5 import native_suite
r=unittest.TextTestRunner(verbosity=2).run(native_suite());raise SystemExit(not r.wasSuccessful())
'''
        r=subprocess.run([profile['python'],'-X','utf8','-I','-B','-c',code,str(root)],capture_output=True,text=True,encoding='utf8',timeout=180)
        import os
        if os.environ.get('R23_NATIVE_LOG'):Path(os.environ['R23_NATIVE_LOG']).write_text(r.stdout+'\n'+r.stderr,encoding='utf8')
        self.assertEqual(r.returncode,0,r.stdout+'\n'+r.stderr)


def native_suite():
    import numpy as np
    import json,os,hashlib,types
    from pathlib import Path
    from unittest.mock import patch
    from adapters.science_shape_repeat5.plans import build_repeat5,PROCEDURE,N
    from adapters.science_shape_repeat5.compiler import compile_row,archive_row
    from adapters.science_shape_repeat5.evidence import validate_compiled_row
    from adapters.science_shape_repeat5.analysis import analyze
    from adapters.science_shape_repeat5.execute_worker import run_acquisition
    from tests.test_science_v3_native import native_suite as original_suite
    Base=next(iter(original_suite())).__class__
    class Cases(Base):
        def make_plan(self,**changes):
            p,rows=build_repeat5({**parameters(),**changes})
            return {**self.plan,'procedureId':PROCEDURE,'parameters':p,'rows':rows,'max_seconds':120}

        def signal(self,plan,pi=.82,seed=1,bad_fixed=False):
            rng=np.random.default_rng(seed)
            y=np.array([.2+.4*np.cos(np.pi*N*r['axisValue']/pi) for r in plan['rows']])
            if bad_fixed:
                for j,row in enumerate(plan['rows']):
                    if row['condition']=='verification_candidate':y[j]+=1 if row['capture']==0 else -1
            i=y[:,None,None]+rng.normal(0,.015,(len(y),1,100))
            return i,rng.normal(0,.015,i.shape)

        def test_real_five_compiled_pulses_and_legacy_single_gate(self):
            from adapters.science_v3_irb.compiler import compile_row as legacy_compile
            from adapters.science_v3.plans import build_rows
            for shape in ('CosPulse','Gaussian'):
                plan=self.make_plan(pulse_shape=shape,pulse_phase_rad=.3)
                row=plan['rows'][25];seq,waves=compile_row(row,self.base,plan['parameters'])
                item=archive_row(self.folder/('five-'+shape+'.npz'),seq,waves);item['rowId']=row['id']
                validate_compiled_row(item,row,self.folder)
                p,old=build_rows('sq_shape_rabi_amplitude',plan['parameters'])
                one,_=legacy_compile(old[25],self.base,p)
                for segment in [s for s in seq.segments if s['channel']=='xy']:
                    self.assertEqual(segment['basebandI']['sha256'],one.segments[0]['basebandI']['sha256'])
                    self.assertEqual(segment['basebandQ']['sha256'],one.segments[0]['basebandQ']['sha256'])
                self.assertEqual(item['repeat5']['gateRepeats'],5)
                bad=deepcopy(item);bad['segments'][0]['areaISeconds']+=1e-9
                with self.assertRaises(ValueError):validate_compiled_row(bad,row,self.folder)

        def test_fit_five_pi_interior_and_reject_extrapolation_and_bad_fixed(self):
            plan=self.make_plan();i,q=self.signal(plan);first=analyze(plan,i,q)
            self.assertTrue(first['analysis']['accepted'],first)
            self.assertAlmostEqual(first['candidateParameters']['shape_pi_amp'],.82,delta=.005)
            self.assertGreater(first['metrics']['cycles'],2.5)
            verify=self.make_plan(verify_shape_pi_amp=first['candidateParameters']['shape_pi_amp'])
            second=analyze(verify,*self.signal(verify,seed=2));self.assertTrue(second['analysis']['accepted'],second)
            self.assertEqual(second['candidateParameters'],first['candidateParameters'])
            bad=analyze(verify,*self.signal(verify,seed=3,bad_fixed=True))
            self.assertFalse(bad['analysis']['accepted']);self.assertGreater(bad['metrics']['candidate_capture_agreement'],1)
            outside=analyze(plan,*self.signal(plan,pi=1.4,seed=4))
            self.assertFalse(outside['analysis']['accepted']);self.assertEqual(outside['candidateParameters'],{})

        def test_actual_native_selection_verification_and_authenticated_migration(self):
            from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter,build_repeat5_profile
            from adapters.science_shape_repeat5.evidence import authenticate_dependency
            from adapters.science_shape_repeat5.policy import verify_sources
            from adapters.science_v3.evidence import result_content,authenticate_dependency as old_auth
            from adapters.science_v3.transport import NativeTransport
            from adapters.science_runtime.policy import digest
            from adapters.f8 import _digest as intent_digest
            from backend.quality import evaluate,SCIENCE_REPEAT5_GATES
            root=Path(__file__).resolve().parents[1]
            profile=build_repeat5_profile(root,json.loads((root/'adapters/arclight_profile.json').read_text('utf8')))
            profile['enabled']=True;adapter=ScienceRepeat5Adapter(root,profile);sources=verify_sources(root)
            candidate=None;ops=[];results=[];fixtures=[]
            for stage in ('selection','verification'):
                operation='TEST_ONLY-repeat5-'+stage
                folder=self.folder/'data/runs/arclight_operations'/hashlib.sha256(operation.encode()).hexdigest();folder.mkdir(parents=True)
                os.environ['QCAL_ARCLIGHT_RUN_DIR']=str(folder)
                supplied=parameters()
                if candidate is not None:supplied.update(shape_pi_amp=candidate,verify_shape_pi_amp=candidate)
                p,rows=build_repeat5(supplied)
                request={'runId':'TEST_ONLY-wf','nodeId':'node','attemptId':'attempt','operationId':operation,
                    'resourceId':'Q11','procedureId':PROCEDURE,'parameters':p,'parameterVersions':{},'leaseToken':'TEST_ONLY-lease'}
                proof={'schema':'qcal-science-dependencies/1','policyVersion':'science-dependency-policy/1','kind':'measurement',
                    'scope':{'workflowId':'TEST_ONLY-wf','resourceId':'Q11'},'inputs':{},
                    'rootReferences':[{'role':r,'recordId':r} for r in ('f01','readout')],
                    'nodes':[{'recordId':r,'parameters':p,'resolvedOutputs':{'candidateParameters':{'drive_hz':p['drive_hz']}}} for r in ('f01','readout')],
                    'edges':[{'role':r,'toRecordId':r} for r in ('f01','readout')]}
                proof['closureSha256']=digest(proof);proof['assessment']={'executionEligible':True,'consumerSha256':digest(request)};request['scienceDependencies']=proof
                with patch.object(adapter,'authenticate_dependency',return_value={'fixtureOnly':True}):plan=adapter.build_plan(request)
                # Only the source seed attestation is a fixture. Actual waveforms,
                # ADC storage, cleanup, result, quality and subsequent migration
                # are exercised below through their production implementations.
                (folder/'plan.json').write_text(json.dumps(plan))
                (folder/'adapter_intent.json').write_text(json.dumps({'request':request,'requestHash':intent_digest(request),'profileHash':intent_digest(profile)}))
                self.hw=type(self.hw)();rng=np.random.default_rng(80+len(results));rows=plan['rows']
                def read(hw,*args,**kwargs):
                    row=rows[hw.reads];hw.reads+=1
                    y=.2+.4*np.cos(np.pi*N*row['axisValue']/.82)
                    return (y+rng.normal(0,.015,(hw.shots,1))).tolist(),rng.normal(0,.015,(hw.shots,1)).tolist()
                self.hw.read=types.MethodType(read,self.hw)
                lock=folder/'fixture-only.lock'
                def acquire(plan,folder):lock.write_text(json.dumps({'token':'test'}));return [lock],'test'
                completed=run_acquisition(plan,folder,lock_acquire=acquire,
                    transport_factory=lambda p,f,c:NativeTransport(p,f,c,native=self.native))
                self.assertEqual(completed['state'],'completed',completed.get('error'))
                self.assertTrue(completed['shutdown_ok']);self.assertFalse(lock.exists())
                self.assertEqual(self.hw.reads,len(rows))
                result=adapter._result(folder,plan,request,completed)
                accepted=evaluate(result,PROCEDURE,'real',SCIENCE_REPEAT5_GATES,independent=stage=='verification')
                self.assertEqual(accepted['quality']['status'],'passed' if stage=='verification' else 'unverified',accepted['quality'])
                candidate=result['candidateParameters']['shape_pi_amp'];results.append(result)
                (folder/'adapter_receipt.json').write_text(json.dumps({'status':'completed','operationId':operation,'requestHash':intent_digest(request),'result':result}))
                ops.append({'stage':stage,'operationId':operation,'adapterRequestSha256':digest(request),
                    'resultContentSha256':digest(result_content(result)),
                    'registeredArtifacts':result['artifacts'],'rawArtifacts':[a for a in result['artifacts'] if a['kind']=='raw']})
                fixtures.append({'stage':stage,'result':result})
            dep={'recordId':'TEST_ONLY-repeat5','procedureId':PROCEDURE,'sourceRoles':['pi01','rabi_amplitude'],
                'scope':{'workflowId':'TEST_ONLY-wf','resourceId':'Q11'},'operations':ops,'parameterVersions':{},
                'parameters':results[0]['parameters'],'targetPulseIdentity':results[0]['targetPulseIdentity'],
                'resolvedOutputs':{'candidateParameters':results[0]['candidateParameters']}}
            consumer={'runId':'TEST_ONLY-wf','resourceId':'Q11','procedureId':'sq_shape_half_verify','parameterVersions':{},'parameters':results[0]['parameters']}
            # Fixture root has immutable runner copy; earlier source closure is
            # checked against the actual candidate runtime, not fabricated.
            runner_copy=self.folder/profile['runner'];runner_copy.parent.mkdir(parents=True,exist_ok=True)
            runner_copy.write_bytes((root/profile['runner']).read_bytes())
            with patch('adapters.science_shape_repeat5.policy.verify_sources',side_effect=lambda _:verify_sources(root)):
                proof=authenticate_dependency(self.folder,consumer,dep,expected_route=adapter._routing('Q11'))
                self.assertTrue(proof['physicalSourceAuthenticated'])
                for path in (Path(results[0]['artifacts'][0]['sourcePath']),):
                    original=path.read_bytes();path.write_bytes(original+b'changed')
                    with self.assertRaises(ValueError):authenticate_dependency(self.folder,consumer,dep,expected_route=adapter._routing('Q11'))
                    path.write_bytes(original)
            with self.assertRaises(ValueError):old_auth(self.folder,consumer,dep,expected_route=adapter._routing('Q11'))
            if os.environ.get('R23_EXPORT_TEST_FIXTURE'):
                Path(os.environ['R23_EXPORT_TEST_FIXTURE']).write_text(json.dumps({'fixtureOnly':True,'physicalAccepted':False,'hardwareConnected':False,'results':fixtures}),encoding='utf8')

        def test_failed_cleanup_retains_lock_and_no_analysis_acceptance(self):
            from adapters.science_v3.transport import NativeTransport
            from cavity_runtime import close_resources
            plan=self.make_plan(points=9);plan['rows']=plan['rows'][:2]
            (self.folder/'plan.json').write_text(json.dumps(plan));lock=self.folder/'fixture-only.lock'
            def acquire(plan,folder):lock.write_text(json.dumps({'token':'test'}));return [lock],'test'
            def close(hw,exp):return {**close_resources(hw,exp),'shutdown_ok':False,'shutdown_errors':{'test_only':'injected'}}
            result=run_acquisition(plan,self.folder,lock_acquire=acquire,
                transport_factory=lambda p,f,c:NativeTransport(p,f,c,native={**self.native,'close_resources':close}))
            self.assertEqual(result['state'],'needs_reconciliation');self.assertTrue(lock.is_file())
            self.assertNotIn('analysis',result);self.assertTrue((self.folder/'results/raw_scan.npz').is_file())

    return unittest.TestSuite(Cases(name) for name in ('test_real_five_compiled_pulses_and_legacy_single_gate','test_fit_five_pi_interior_and_reject_extrapolation_and_bad_fixed','test_actual_native_selection_verification_and_authenticated_migration','test_failed_cleanup_retains_lock_and_no_analysis_acceptance'))


if __name__=='__main__':unittest.main()
