"""Numerical analysis fixtures only; these arrays are not acquisition evidence."""
import sys
import importlib.util
import json
import subprocess
from pathlib import Path
import unittest
from adapters.science_v3.plans import build_rows
if importlib.util.find_spec('scipy') is not None and importlib.util.find_spec('numpy') is not None:
    import numpy as np
    from adapters.science_v3.analysis import analyze

ROOT=Path(__file__).resolve().parents[1]

def load_tests(loader,tests,pattern):
    if importlib.util.find_spec('scipy') is not None and importlib.util.find_spec('numpy') is not None:return tests
    class PinnedNativeAnalysis(unittest.TestCase):
        def runTest(self):
            profile=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
            code='import sys,unittest;sys.path.insert(0,sys.argv[1]);r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName(sys.argv[2]));raise SystemExit(not r.wasSuccessful())'
            result=subprocess.run([profile['python'],'-I','-B','-c',code,str(ROOT),'tests.test_science_v3_analysis'],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
    return unittest.TestSuite([PinnedNativeAnalysis()])

class AnalysisContracts(unittest.TestCase):
    def setUp(self):
        self.p={'drive_hz':4.86e9,'drive_amp':.3,'drive_length_s':20e-9,'readout_hz':7.01e9,'readout_amp':.4,
            'readout_width_s':2e-6,'xy_delay_s':100e-9,'xy_filter_mhz':4700,'xy_attenuation_db':3.75,
            'pulse_shape':'CosPulse','pulse_length_s':20e-9,'shape_pi_amp':.2,
            'probe12_shape':'CosPulse','probe12_length_s':24e-9,'probe12_amplitude':.2,'f12_hz':4.66e9,
            'shots':100,'capture_repeats':4,'acquisitions_per_point':1,'anharmonicity_hz':-200e6}
        self.rng=np.random.default_rng(42)
    def data(self,method,changes,signal):
        p,rows=build_rows(method,{**self.p,**changes});means=np.array([signal(r) for r in rows])
        i=means[:,None,None]+self.rng.normal(0,.01,(len(rows),1,100));q=self.rng.normal(0,.01,i.shape)
        return {'procedureId':method,'parameters':p,'rows':rows},i,q
    def test_bad_fixed_candidate_cannot_pass_good_independent_rabi_axis(self):
        path=str(ROOT/'adapters/arclight_configurable_runtime');sys.path.insert(0,path)
        self.addCleanup(lambda:sys.path.remove(path))
        plan,i,q=self.data('sq_shape_rabi_amplitude',{'scan_axis_values':np.linspace(0,.98,61).tolist(),'verify_shape_pi_amp':.2},
            lambda r:0. if r['condition']=='verification_candidate' else np.sin(np.pi*r['axisValue']/.4)**2)
        result=analyze(plan,i,q)
        self.assertFalse(result['analysis']['accepted'],result)
        self.assertTrue(any('candidate' in x.lower() for x in result['analysis']['reasons']))
    def test_drag_noise_is_not_accepted_optimum(self):
        plan,i,q=self.data('sq_drag_scan',{'scan_axis_values':np.linspace(-1,1,21).tolist()},
            lambda r:float(r['logicalState']) if r['condition']=='reference' else .5)
        result=analyze(plan,i,q)
        self.assertFalse(result['analysis']['accepted'],result)
    def test_candidate_capture_errors_cannot_cancel_in_the_mean(self):
        plan,i,q=self.data('sq_drag_scan',{'scan_axis_values':np.linspace(-1,1,21).tolist(),'verify_drag_lambda1':.2},
            lambda r:float(r['logicalState']) if r['condition']=='reference' else .1+.3*(r['axisValue']-.2)**2)
        for j,r in enumerate(plan['rows']):
            if r['condition']=='verification_candidate':i[j]+=.4 if r['capture']%2 else -.4
        result=analyze(plan,i,q)
        self.assertFalse(result['analysis']['accepted'],result)
        self.assertGreater(result['metrics']['candidate_capture_agreement'],1)
    def test_spectrum_dip_finds_conditional_resonance(self):
        center=4.66e9
        plan,i,q=self.data('sq_shelve_spectrum12',{'scan_axis_values':np.linspace(4.60e9,4.72e9,61).tolist()},
            lambda r:0 if r['condition']=='prep_off' else 1-.6*np.exp(-.5*((r['axisValue']-center)/1e7)**2))
        result=analyze(plan,i,q)
        self.assertTrue(result['analysis']['accepted'],result)
        self.assertLess(abs(result['candidateParameters']['f12_hz']-center),2e6)
    def test_drag_resolved_minimum_and_fixed_point_signal(self):
        def good(r):
            if r['condition']=='reference':return float(r['logicalState'])
            return .1+.3*(r['axisValue']-.2)**2
        plan,i,q=self.data('sq_drag_scan',{'scan_axis_values':np.linspace(-1,1,21).tolist(),'verify_drag_lambda1':.2},good)
        accepted=analyze(plan,i,q)
        self.assertTrue(accepted['analysis']['accepted'],accepted)
        self.assertGreater(accepted['metrics']['curvature_z'],3)
        self.assertLessEqual(accepted['metrics']['candidate_signal_agreement'],1)
        for j,r in enumerate(plan['rows']):
            if r['condition']=='verification_candidate':i[j]=.9
        rejected=analyze(plan,i,q)
        self.assertFalse(rejected['analysis']['accepted']);self.assertGreater(rejected['metrics']['candidate_signal_agreement'],1)
    def test_spectrum_fixed_point_requires_actual_on_off_contrast(self):
        def response(r):
            condition=r.get('baseCondition',r['condition'])
            return 0 if condition=='prep_off' else 1-.6*np.exp(-.5*((r['axisValue']-4.66e9)/1e7)**2)
        plan,i,q=self.data('sq_shelve_spectrum12',{'scan_axis_values':np.linspace(4.60e9,4.72e9,61).tolist(),'verify_f12_hz':4.66e9},response)
        self.assertTrue(analyze(plan,i,q)['analysis']['accepted'])
        for j,r in enumerate(plan['rows']):
            if r['condition']=='verification_candidate' and r['baseCondition']=='prep_on':i[j]=1
        rejected=analyze(plan,i,q);self.assertFalse(rejected['analysis']['accepted'])
        self.assertGreater(rejected['metrics']['candidate_signal_agreement'],1)
    def test_counts_are_held_out_and_ground_disturbance_rejects(self):
        plan,i,q=self.data('sq_shelve_readout_compare',{},
            lambda r:float(r['logicalState'])*(.4 if r['condition']=='off' else .8))
        result=analyze(plan,i,q)
        self.assertTrue(result['analysis']['accepted'],result)
        self.assertEqual(result['comparison']['on']['n0'],200)
        self.assertEqual(result['comparison']['on']['n1'],200)
        self.assertEqual(result['classifierProtocol']['trainingCaptures'],[0,1])
        self.assertEqual(result['classifierProtocol']['validationCaptures'],[2,3])
        for j,r in enumerate(plan['rows']):
            if r['condition']=='on' and r['logicalState']==0:i[j]+=.5
        rejected=analyze(plan,i,q);self.assertFalse(rejected['analysis']['accepted'])
        self.assertGreater(rejected['metrics']['ground_disturbance_ratio'],.2)

if __name__=='__main__':unittest.main()
