"""Actual native compiler/runner, with explicit injected hardware only."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]

class IsolatedIRB(unittest.TestCase):
    def test_native_full_campaign_and_reference_stop_contracts(self):
        profile=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        code='''import sys,types,unittest
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root));sys.path.insert(0,str(root/'adapters/science_runtime'));sys.path.insert(0,str(root/'adapters/arclight_configurable_runtime'))
import compile_only_worker as guard
sys.addaudithook(guard.audit)
from adapters.science_v3_irb.policy import verify_sources
verify_sources(root)
core=root/'adapters/arclight_configurable_runtime/work/server/core'
for name,path in [('core',core),('core.compiler',core/'compiler'),('core.experiment',core/'experiment'),('core.circuit',core/'circuit')]:guard.namespace(name,path)
inst=types.ModuleType('core.inst');inst.get_hardware=lambda *a,**k:None;sys.modules['core.inst']=inst
from tests.test_science_v3_irb_native import native_suite
result=unittest.TextTestRunner(verbosity=2).run(native_suite())
raise SystemExit(not result.wasSuccessful())
'''
        result=subprocess.run([profile['python'],'-I','-B','-c',code,str(ROOT)],capture_output=True,text=True,timeout=450)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)

def native_suite(only=None):
    import numpy as np
    from copy import deepcopy
    from tests.test_science_v3_native import native_suite as transport_suite
    from tests.test_science_v3_irb import CampaignContracts
    from adapters.science_v3_irb.plans import normalize_parameters,verification_parameters
    from adapters.science_v3_irb.sequences import build_campaign,verify_campaign
    from adapters.science_v3_irb.execute_worker import run_acquisition
    from adapters.science_v3.transport import NativeTransport
    Base=next(iter(transport_suite())).__class__
    class Contracts(Base):
        def test_half_scan_selection_and_frozen_candidate_use_real_native_transport(self):
            from adapters.science_v3_irb.half_scan import build_half_scan,target_identity
            p,bundle=build_half_scan({**CampaignContracts().parameters(),'scan_axis_values':np.linspace(-1,1,21).tolist()})
            frozen=None
            for stage in ('measure','verify'):
                # Production creates a new Hardware after close_resources removes
                # the prior native registry instance; reproduce that lifecycle.
                self.hw=type(self.hw)()
                folder=self.folder/stage;folder.mkdir()
                import os
                os.environ['QCAL_ARCLIGHT_RUN_DIR']=str(folder)
                if frozen is not None:p,bundle=build_half_scan({**p,'verify_drag_lambda1':frozen})
                plan={**self.plan,'procedureId':'sq_drag_half_scan','parameters':p,'campaign':bundle,'rows':bundle['rows'],'max_seconds':180}
                (folder/'plan.json').write_text(json.dumps(plan));transport=None;rng=np.random.default_rng(41 if stage=='measure' else 42)
                def factory(plan,path,cancel):
                    nonlocal transport
                    transport=NativeTransport(plan,path,cancel,native=self.native);return transport
                def read(*args,**kwargs):
                    self.hw.reads+=1;r=plan['rows'][transport.row_id]
                    mean=float(r['logicalState']) if r['condition']=='reference' else .1+.3*(r['axisValue']-.2)**2
                    i=mean+rng.normal(0,.01,self.hw.shots);q=rng.normal(0,.01,self.hw.shots)
                    return i[:,None].tolist(),q[:,None].tolist()
                self.hw.read=read
                result=run_acquisition(plan,folder,transport_factory=factory,lock_acquire=lambda p,f:([],None))
                self.assertEqual(result['state'],'completed',result.get('error'));self.assertTrue(result['shutdown_ok'])
                self.assertTrue(result['analysis']['analysis']['accepted'],result['analysis'])
                candidate=result['analysis']['candidateParameters']['drag_lambda1']
                if frozen is not None:self.assertEqual(candidate,frozen)
                frozen=candidate
                self.assertEqual(result['analysis']['targetPulseIdentity'],target_identity(p,candidate))
                self.assertNotIn('gate_fidelity',result['analysis']['metrics'])
            self.assertAlmostEqual(frozen,.2,delta=.01)
        def test_irb_cancel_preserves_block_and_releases_only_after_cleanup(self):
            plan=self.campaign();pre=[r for r in plan['rows'] if r['stage']=='pre'];branch=next(r for r in plan['rows'] if r['stage']=='rb' and r['axisValue']==1)
            plan['rows']=pre+[branch];(self.folder/'plan.json').write_text(json.dumps(plan));transport=None;rng=np.random.default_rng(71)
            lookup={r['id']:r for r in plan['rows']}
            def factory(p,f,c):
                nonlocal transport
                transport=NativeTransport(p,f,c,native=self.native);return transport
            def read(*args,**kwargs):
                self.hw.reads+=1;row=lookup[transport.row_id];mean=float(row['logicalState'] or 0)
                i=mean+rng.normal(0,.01,self.hw.shots);q=rng.normal(0,.01,self.hw.shots)
                if row['stage']=='rb':(self.folder/'STOP_AFTER_POINT').write_text('explicit fixture stop')
                return i[:,None].tolist(),q[:,None].tolist()
            self.hw.read=read;lock=self.folder/'fixture.lock'
            def acquire(p,f):lock.write_text(json.dumps({'token':'fixture'}));return [lock],'fixture'
            result=run_acquisition(plan,self.folder,transport_factory=factory,lock_acquire=acquire)
            self.assertEqual(result['state'],'cancelled');self.assertTrue(result['shutdown_ok']);self.assertFalse(lock.exists())
            self.assertEqual(result['pointsCompleted'],8)
            self.assertTrue((self.folder/'results'/f'point_{branch["id"]:04d}_block_000.npz').is_file())
            self.assertTrue((self.folder/'results/frozen_pre.json').is_file())
        def test_irb_cleanup_failure_retains_owned_lock(self):
            plan=self.campaign();plan['rows']=plan['rows'][:1];(self.folder/'plan.json').write_text(json.dumps(plan))
            lock=self.folder/'fixture.lock'
            def acquire(p,f):lock.write_text(json.dumps({'token':'fixture'}));return [lock],'fixture'
            original=self.native['close_resources']
            def failed_close(hw,exp):return {**original(hw,exp),'shutdown_ok':False,'fixtureInjectedCleanupFailure':True}
            native={**self.native,'close_resources':failed_close}
            result=run_acquisition(plan,self.folder,lock_acquire=acquire,transport_factory=lambda p,f,c:NativeTransport(p,f,c,native=native))
            self.assertEqual(result['state'],'needs_reconciliation');self.assertTrue(lock.exists())
            self.assertTrue((self.folder/'results/raw_scan.npz').is_file())
        def test_half_diagnostic_compiles_same_actual_half_pulse_and_closes(self):
            from adapters.science_v3_irb.half_scan import build_half_scan
            from adapters.science_v3_irb.compiler import compile_row
            from paired_rb_support import ideal_gate_closure
            p,bundle=build_half_scan({**CampaignContracts().parameters(),'scan_axis_values':[-1.,-.5,0.,.5,1.]})
            row=next(r for r in bundle['rows'] if r['condition']=='diagnostic')
            gates=[['R'+g['axis'].upper(),[1],[g['theta']]] for g in row['gates']]+[['MEASURE',[1],[]]]
            self.assertTrue(ideal_gate_closure(gates)['closed'])
            seq,expected=compile_row(row,self.base,p)
            self.assertEqual(len([s for s in seq.segments if s['channel']=='xy']),24)
            first=seq.pulse_list_per_qubit[1][0][1]
            self.assertAlmostEqual(first.A,.15);self.assertAlmostEqual(first.buff,2e-9)
            self.assertAlmostEqual(first.beta,-row['gates'][0]['pulse']['lambda1']/(2*np.pi*p['anharmonicity_hz']))
        def test_single_carrier_compiler_is_byte_identical_to_v3_segments(self):
            from adapters.science_v3_irb.compiler import compile_row as irb_compile
            from adapters.science_v3.compiler import compile_row as segmented_compile
            plan=self.campaign()
            for row in [plan['rows'][0],plan['rows'][1],next(r for r in plan['rows'] if r['stage']=='irb' and r['axisValue']==128)]:
                old,expected=segmented_compile(row,self.base,plan['parameters'])
                new,actual=irb_compile(row,self.base,plan['parameters'])
                for name in expected:
                    a=old.waves._waves[name].get_data();b=new.waves._waves[name].get_data()
                    self.assertEqual(actual[name],expected[name],str({'wave':name,'maxError':float(np.max(abs(a-b))),'unequal':int(np.sum(a!=b)),'signedZerosA':int(np.sum(np.signbit(a.real)&(a.real==0))),'signedZerosB':int(np.sum(np.signbit(b.real)&(b.real==0)))}))
                self.assertEqual(len(new.segments),len(old.segments))
        def campaign(self):
            p,_=normalize_parameters({**CampaignContracts().parameters(),'rb_bootstrap_replicates':100})
            bundle=build_campaign(p)
            return {**self.plan,'procedureId':'sq_drag_irb','parameters':p,'campaign':bundle,'rows':bundle['rows'],'max_seconds':360}
        def test_paired_native_sequences_same_pulse_independent_seed_and_tamper(self):
            plan=self.campaign();bundle=plan['campaign'];man=bundle['nativeManifests']
            self.assertEqual(len(plan['rows']),416)
            for left,right in zip(man['rb']['sequences'],man['irb']['sequences']):
                self.assertEqual(left['random_clifford_indices'],right['random_clifford_indices'])
                self.assertEqual(left['seed'],right['seed']);self.assertTrue(left['ideal_closure']['closed']);self.assertTrue(right['ideal_closure']['closed'])
            identities={json.dumps(g['pulse'],sort_keys=True) for r in plan['rows'] if r['stage'] in ('rb','irb') for g in r['gates']}
            self.assertEqual(len(identities),1)
            later=build_campaign(verification_parameters(plan['parameters']))
            self.assertNotEqual(man['rb']['sequences'][0]['seed'],later['nativeManifests']['rb']['sequences'][0]['seed'])
            corrupted=deepcopy(bundle);corrupted['rows'][8]['gates'][0]['pulse']['lambda1']+=.01
            with self.assertRaises(ValueError):verify_campaign(corrupted,plan['parameters'])
        def test_full_416_row_actual_native_transport_and_conditional_analysis(self):
            plan=self.campaign();(self.folder/'plan.json').write_text(json.dumps(plan));(self.folder/'results').mkdir()
            transport=None;rng=np.random.default_rng(72)
            def factory(p,folder,cancel):
                nonlocal transport
                transport=NativeTransport(p,folder,cancel,native=self.native)
                return transport
            def read(*args,**kwargs):
                self.hw.reads+=1;row=plan['rows'][transport.row_id]
                if row['stage'] in ('pre','post'):mean=float(row['logicalState'])
                else:
                    key=row['sequenceKey'];prob=.97 if row['stage']=='rb' else .94
                    mean=1-(.45*prob**key['clifford_m']+.5)+.003*np.sin(key['repeat']+key['clifford_m'])
                i=mean+rng.normal(0,.01,self.hw.shots);q=rng.normal(0,.01,self.hw.shots)
                return i[:,None].tolist(),q[:,None].tolist()
            self.hw.read=read;lock=self.folder/'fixture-chassis.lock'
            def acquire(p,folder):lock.write_text(json.dumps({'token':'fixture'}));return [lock],'fixture'
            result=run_acquisition(plan,self.folder,transport_factory=factory,lock_acquire=acquire)
            self.assertEqual(result['state'],'completed',result.get('error'))
            self.assertEqual(result['pointsCompleted'],416);self.assertEqual(self.hw.reads,832)
            self.assertTrue(result['shutdown_ok']);self.assertFalse(lock.exists())
            self.assertEqual(len(transport.uploads),832)
            self.assertTrue(result['analysis']['analysis']['accepted'],result['analysis'])
            self.assertAlmostEqual(result['analysis']['metrics']['gate_fidelity'],(1+.94/.97)/2,delta=.003)
            self.assertTrue((self.folder/'results/frozen_pre.json').is_file())
            for stage in ('pre','rb','irb','post'):self.assertTrue((self.folder/'results'/('raw_'+stage+'.npz')).is_file())
        def test_unresolved_pre_stops_before_any_rb_upload_and_cleans(self):
            plan=self.campaign();pre=[r for r in plan['rows'] if r['stage']=='pre'];branch=next(r for r in plan['rows'] if r['stage']=='rb')
            plan['rows']=pre+[branch];(self.folder/'plan.json').write_text(json.dumps(plan))
            rng=np.random.default_rng(19)
            def read(*a,**k):
                self.hw.reads+=1;i=rng.normal(0,.01,self.hw.shots);q=rng.normal(0,.01,self.hw.shots)
                return i[:,None].tolist(),q[:,None].tolist()
            self.hw.read=read
            result=run_acquisition(plan,self.folder,lock_acquire=lambda p,f:([],None),transport_factory=lambda p,f,c:NativeTransport(p,f,c,native=self.native))
            self.assertEqual(result['state'],'failed_after_shutdown');self.assertEqual(result['pointsCompleted'],8)
            self.assertTrue(result['shutdown_ok']);self.assertEqual(self.hw.reads,16)
            self.assertIn('held-out',result['error']);self.assertTrue((self.folder/'results/partial_raw_scan.npz').is_file())
    names=('test_half_scan_selection_and_frozen_candidate_use_real_native_transport','test_irb_cancel_preserves_block_and_releases_only_after_cleanup','test_irb_cleanup_failure_retains_owned_lock',
        'test_half_diagnostic_compiles_same_actual_half_pulse_and_closes','test_single_carrier_compiler_is_byte_identical_to_v3_segments','test_paired_native_sequences_same_pulse_independent_seed_and_tamper',
        'test_full_416_row_actual_native_transport_and_conditional_analysis','test_unresolved_pre_stops_before_any_rb_upload_and_cleans')
    return unittest.TestSuite(Contracts(name) for name in names if only is None or name==only or (isinstance(only,list) and name in only))
