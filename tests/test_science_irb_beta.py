"""R19 dimensionful derivative policy; no device access."""
import math
import unittest
from copy import deepcopy
from tests import test_science_v3_irb as existing


class BetaContracts(unittest.TestCase):
    def parameters(self):
        p=existing.CampaignContracts().parameters()
        p.pop('anharmonicity_hz',None);p.pop('drag_lambda1',None)
        return {**p,'drag_mode':'empirical_beta','drag_beta_s':.2e-9}

    def test_half_rows_have_real_seconds_and_zero_reference_without_alpha(self):
        from adapters.science_v3_irb.half_scan import build_half_scan,target_identity
        p,b=build_half_scan({**self.parameters(),'scan_axis_values':[-1e-9,-.5e-9,0.,.5e-9,1e-9]})
        self.assertNotIn('anharmonicity_hz',p)
        for row in b['rows']:
            for gate in row['gates']:
                pulse=gate['pulse'];self.assertIsNone(pulse['anharmonicityHz']);self.assertIsNone(pulse['lambda1'])
                self.assertEqual(pulse['betaSeconds'],row['axisValue'] if row['condition']=='diagnostic' else 0.)
                if row['condition']=='diagnostic':self.assertEqual(gate['theta'],math.pi/2)
        ident=target_identity(p,.2e-9)['identity']
        self.assertEqual(ident['dragMode'],'empirical_beta');self.assertEqual(ident['betaSeconds'],.2e-9)
        self.assertEqual(ident['rotationRad'],math.pi/2)

    def test_irb_exact_mode_identity_and_fresh_seeds(self):
        from adapters.science_v3_irb.plans import normalize_parameters,identity,verification_parameters
        p,b=normalize_parameters(self.parameters());other=verification_parameters(p)
        self.assertEqual(b['rows'],416);self.assertEqual(identity(p),identity(other))
        self.assertNotEqual(p['rb_master_seed'],other['rb_master_seed'])
        changed={**p,'drag_beta_s':-.2e-9};self.assertNotEqual(identity(p),identity(changed))

    def test_no_alpha_substitution_and_bounded_axis(self):
        from adapters.science_v3_irb.half_scan import build_half_scan
        for extra in ({'scan_axis_values':[.1e-9,.2e-9,.3e-9,.4e-9,.5e-9]},
                      {'drag_mode':'invented'}, {'drag_beta_s':float('nan')},
                      {'scan_axis_values':[-1.,-.5,0.,.5,1.]}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):build_half_scan({**self.parameters(),**extra})

    def test_candidate_and_verification_use_seconds(self):
        from adapters.science_v3_irb.adapter import ScienceIRBAdapter
        obj=object.__new__(ScienceIRBAdapter)
        p=self.parameters();v=obj.build_verification_parameters('sq_drag_half_scan',p,{'drag_beta_s':.3e-9})
        self.assertEqual(v['verify_drag_beta_s'],.3e-9);self.assertNotIn('verify_drag_lambda1',v)

    def test_five_point_second_axis_and_unknown_mode_preserve_legacy(self):
        from adapters.science_v3_irb.half_scan import build_half_scan
        p,_=build_half_scan({**self.parameters(),'beta_start_s':-2e-9,'beta_stop_s':2e-9,'points':5})
        self.assertEqual(len(p['scan_axis_values']),5);self.assertEqual(p['scan_axis_values'][2],0.)
        old=existing.CampaignContracts().parameters()
        left=build_half_scan(old);right=build_half_scan({**old,'drag_mode':'normalized_alpha'})
        self.assertEqual(left[1],right[1]);self.assertNotIn('drag_mode',left[0])


class NativeBeta(unittest.TestCase):
    def test_actual_native_derivative_transport_and_legacy_bytes(self):
        self.run_native(False)

    def test_full_empirical_irb_and_stop_cleanup_contracts(self):
        self.run_native(True)

    def run_native(self,long):
        import json,subprocess
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        profile=json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
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
from tests.test_science_irb_beta import native_suite
result=unittest.TextTestRunner(verbosity=2).run(native_suite(long=sys.argv[2]=='true'))
raise SystemExit(not result.wasSuccessful())
'''
        result=subprocess.run([profile['python'],'-X','utf8','-I','-B','-c',code,str(root),str(long).lower()],capture_output=True,text=True,encoding='utf-8',timeout=450)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)


def native_suite(long=False):
    import numpy as np
    import json,os,hashlib
    from adapters.science_v3_irb.compiler import compile_row,archive_row
    from adapters.science_v3_irb.half_scan import build_half_scan
    from adapters.science_v3_irb.sequences import build_campaign
    from adapters.science_v3_irb.execute_worker import run_acquisition
    from adapters.science_v3.transport import NativeTransport
    from tests.test_science_v3_native import native_suite as old_suite
    Base=next(iter(old_suite())).__class__
    if long:
        from tests.test_science_v3_irb_native import native_suite as inherited_suite
        Parent=next(iter(inherited_suite())).__class__
        class FullEmpirical(Parent):
            def campaign(self):
                from adapters.science_v3_irb.plans import normalize_parameters
                p,_=normalize_parameters({**BetaContracts().parameters(),'rb_bootstrap_replicates':100})
                b=build_campaign(p)
                return {**self.plan,'procedureId':'sq_drag_irb','parameters':p,'campaign':b,'rows':b['rows'],'max_seconds':360}
        return unittest.TestSuite(FullEmpirical(name) for name in (
            'test_full_416_row_actual_native_transport_and_conditional_analysis',
            'test_irb_cancel_preserves_block_and_releases_only_after_cleanup',
            'test_irb_cleanup_failure_retains_owned_lock',
            'test_unresolved_pre_stops_before_any_rb_upload_and_cleans'))
    class Cases(Base):
        def parameters(self):return {**BetaContracts().parameters(),'capture_repeats':2,'shots':100,'drag_repeats':1}

        def test_signed_beta_phase_sigma_and_archived_arrays(self):
            for shape in ('CosPulse','Gaussian'):
                for phase in (0.,math.pi/2):
                    p,b=build_half_scan({**self.parameters(),'pulse_shape':shape,'pulse_sigma_s':4e-9,'pulse_phase_rad':phase,
                                        'scan_axis_values':[-1e-9,-.5e-9,0.,.5e-9,1e-9]})
                    samples={}
                    for value in (-1e-9,0.,1e-9):
                        row=next(r for r in b['rows'] if r['condition']=='diagnostic' and r['axisValue']==value)
                        seq,expected=compile_row(row,self.base,p)
                        actual=seq.baseband_arrays['xy0_i']+1j*seq.baseband_arrays['xy0_q'];samples[value]=actual*np.exp(-1j*phase)
                        segment=seq.segments[0];self.assertEqual(segment['betaSeconds'],value)
                        self.assertEqual(segment['sampleRateHz'],4e9);self.assertEqual(segment['bufferNs'],2.)
                        if shape=='Gaussian':self.assertAlmostEqual(segment['shapeParameters']['sigma'],4e-9)
                        artifact=archive_row(self.folder/'native_beta.npz',seq,expected)
                        with np.load(artifact['file']) as saved:np.testing.assert_array_equal(saved['xy0_q'],seq.baseband_arrays['xy0_q'])
                    reference=samples[0.].real[4:-4]
                    deriv=np.gradient(reference,.25e-9)
                    np.testing.assert_allclose(samples[1e-9].imag[4:-4],1e-9*deriv,rtol=1e-12,atol=1e-15)
                    np.testing.assert_allclose(samples[-1e-9].imag[4:-4],-1e-9*deriv,rtol=1e-12,atol=1e-15)
                    np.testing.assert_allclose(samples[1e-9].real, samples[0.].real,atol=1e-15)

        def test_same_half_gate_irb_hash_and_legacy_unchanged(self):
            p,b=build_half_scan({**self.parameters(),'scan_axis_values':[-1e-9,-.5e-9,0.,.5e-9,1e-9],'verify_drag_beta_s':.2e-9})
            fixed=next(r for r in b['rows'] if r['condition']=='verification_candidate')
            seq,_=compile_row(fixed,self.base,p);expected=seq.segments[0]
            campaign=build_campaign({k:v for k,v in self.parameters().items() if k!='drag_repeats'})
            row=next(r for r in campaign['rows'] if r['stage']=='irb' and any(g['axis']=='x' and g['theta']==math.pi/2 for g in r['gates']))
            index=next(j for j,g in enumerate(row['gates']) if g['axis']=='x' and g['theta']==math.pi/2)
            seq,_=compile_row(row,self.base,self.parameters());actual=seq.segments[index]
            self.assertEqual(actual['basebandI']['sha256'],expected['basebandI']['sha256']);self.assertEqual(actual['basebandQ']['sha256'],expected['basebandQ']['sha256'])
            from adapters.science_v3.compiler import compile_row as frozen_compile
            old=existing.CampaignContracts().parameters();p,b=build_half_scan({**old,'scan_axis_values':[-1.,-.5,0.,.5,1.]})
            row=next(r for r in b['rows'] if r['condition']=='diagnostic')
            _,new=compile_row(row,self.base,p);_,legacy=frozen_compile(row,self.base,p)
            self.assertEqual(new,legacy)

        def test_actual_transport_select_verify_and_clipping_before_open(self):
            candidate=None
            for stage in ('measure','verify'):
                self.hw=type(self.hw)();folder=self.folder/stage;folder.mkdir();os.environ['QCAL_ARCLIGHT_RUN_DIR']=str(folder)
                p={**self.parameters(),'scan_axis_values':[-1e-9,-.5e-9,0.,.5e-9,1e-9]}
                if candidate is not None:p['verify_drag_beta_s']=candidate
                p,b=build_half_scan(p);plan={**self.plan,'procedureId':'sq_drag_half_scan','parameters':p,'campaign':b,'rows':b['rows'],'max_seconds':100}
                (folder/'plan.json').write_text(json.dumps(plan));transport=None;rng=np.random.default_rng(9 if stage=='measure' else 10)
                def factory(plan,path,cancel):
                    nonlocal transport
                    transport=NativeTransport(plan,path,cancel,native=self.native);return transport
                def read(*args,**kwargs):
                    row=plan['rows'][transport.row_id];self.hw.reads+=1
                    mean=float(row['logicalState']) if row['condition']=='reference' else .1+.3*(row['axisValue']*1e9-.2)**2
                    return (mean+rng.normal(0,.001,self.hw.shots))[:,None].tolist(),rng.normal(0,.001,self.hw.shots)[:,None].tolist()
                self.hw.read=read
                result=run_acquisition(plan,folder,transport_factory=factory,lock_acquire=lambda p,f:([],None))
                self.assertEqual(result['state'],'completed',result);self.assertTrue(result['analysis']['analysis']['accepted'],result['analysis'])
                value=result['analysis']['candidateParameters']['drag_beta_s']
                if candidate is not None:self.assertEqual(value,candidate)
                candidate=value
            self.assertAlmostEqual(candidate,.2e-9,delta=.01e-9)
            p,b=build_half_scan({**self.parameters(),'scan_axis_values':[-1e-6,-.5e-6,0.,.5e-6,1e-6]})
            row=next(r for r in b['rows'] if r['condition']=='diagnostic' and r['axisValue']==1e-6)
            with self.assertRaises(ValueError):compile_row(row,self.base,p)
    return unittest.TestSuite(Cases(name) for name in ('test_signed_beta_phase_sigma_and_archived_arrays','test_same_half_gate_irb_hash_and_legacy_unchanged','test_actual_transport_select_verify_and_clipping_before_open'))


def numeric_half_fixture():
    """Actual analysis of labelled synthetic ADC; no reusable accepted record."""
    import numpy as np
    from adapters.science_v3_irb.half_scan import build_half_scan,analyze
    from adapters.science_v3_irb.beta import coefficient_evidence,HALF_POLICY,PULSE_POLICY
    result={}
    for stage in ('selection','verification'):
        p,campaign=build_half_scan({**BetaContracts().parameters(),'capture_repeats':4,
            'scan_axis_values':[-2e-9,-1e-9,0.,1e-9,2e-9],
            **({'verify_drag_beta_s':result['selection']['analysis']['candidateParameters']['drag_beta_s']} if stage=='verification' else {})})
        rng=np.random.default_rng(314 if stage=='selection' else 315)
        means=np.array([float(row['logicalState']) if row['condition']=='reference' else .1+.05*(row['axisValue']*1e9-.2)**2 for row in campaign['rows']])
        i=means[:,None,None]+rng.normal(0,.001,(len(means),1,2000));q=rng.normal(0,.001,i.shape)
        plan={'parameters':p,'rows':campaign['rows'],'procedureId':'sq_drag_half_scan'}
        output=analyze(plan,i,q)
        result[stage]={'parameters':p,'analysis':output,'policy':PULSE_POLICY,'campaignPolicy':HALF_POLICY,
            'coefficient':coefficient_evidence(p,output['candidateParameters'].get('drag_beta_s'))}
    return {'fixtureOnly':True,'physicalAcceptanceEligible':False,'description':'Synthetic ADC through actual native numeric analysis; no device or source authentication',**result}

