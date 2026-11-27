"""Pure contracts for the authorized same-half-gate child chain; no hardware."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
from backend.common import APIError
from backend import smart_science as subject


class SmartScienceContracts(unittest.TestCase):
    def test_four_exact_versioned_methods_and_roles(self):
        self.assertEqual(subject.METHODS,('sq_shape_rabi_amplitude_repeat5','sq_shape_half_verify','sq_drag_half_scan','sq_drag_irb'))
        self.assertEqual(subject.PROFILE,'science-shape-repeat5')
        self.assertEqual(subject.references(2,{'f01':'f','readout':'r','rabi_time':'t'},['pi','half']),[
            {'role':'pi01','recordId':'pi'},{'role':'half01','recordId':'half'},
            {'role':'rabi_amplitude','recordId':'pi'},{'role':'rabi_time','recordId':'t'},
            {'role':'readout','recordId':'r'}])
        self.assertNotIn('anharmonicity',str(subject.references(3,{'readout':'r'},['pi','half','beta'])))

    def test_initial_window_uses_continuous_time_fit_not_legacy_rounded_commit(self):
        p={'drive_length_s':342.5e-9,'drive_amp':.8,'readout_amp':.76}
        timing={'selectionResult':{'candidateParameters':{'drive_length_s':342.37e-9}}}
        result=subject.initial_parameters(p,timing)
        self.assertAlmostEqual(result['pulse_length_s'],342.25e-9)
        self.assertEqual(result['pulse_shape'],'Gaussian')
        self.assertEqual(result['pulse_sigma_s'],result['pulse_length_s']/2)
        self.assertEqual(result['points'],41)
        self.assertNotIn('anharmonicity_hz',result)
        with self.assertRaises(APIError):subject.initial_parameters(p,{})

    def test_half_and_beta_inherit_measured_shape_pi_and_irb_exact_empirical_target(self):
        record={'result':{'parameters':{'pulse_shape':'Gaussian','pulse_length_s':40e-9,'pulse_sigma_s':10e-9,
            'pulse_phase_rad':.1,'shape_pi_amp':.6,'drive_hz':4.8e9,'readout_hz':7e9,'readout_amp':.3,
            'readout_width_s':2e-6,'xy_delay_s':0,'xy_filter_mhz':1000,'xy_attenuation_db':0,
            'drive_amp':.8,'drive_length_s':40e-9},'targetPulseIdentity':{'identity':{'betaSeconds':-.8e-9,'dragMode':'empirical_beta'}}},
            'dependencyProof':{'nodes':[{'recordId':'pi','resolvedOutputs':{'candidateParameters':{'shape_pi_amp':.61}}}]},'id':'pi'}
        half=subject.followup_parameters(1,record)
        self.assertEqual(half['shape_pi_amp'],.61)
        beta=subject.followup_parameters(2,record)
        self.assertEqual(beta['drag_mode'],'empirical_beta');self.assertEqual(beta['beta_start_s'],-1e-9)
        irb=subject.followup_parameters(3,record)
        self.assertEqual(irb['drag_beta_s'],-.8e-9);self.assertEqual(irb['shape_pi_amp'],.6)
        self.assertEqual(irb['rb_lengths'],[1,2,4,8,16,32])
        self.assertNotIn('anharmonicity_hz',irb)

    def test_reportable_requires_real_independent_current_gate_result_not_scan_or_model(self):
        value={'kind':'measurement','dataKind':'real','evidenceCurrent':True,'evidenceIntact':True,
            'provenance':{'independentlyVerified':True},'result':{'procedureId':'sq_drag_irb',
            'quality':{'status':'passed'},'metrics':{'gate_fidelity':.99},
            'gateCharacterization':{'reportable':True,'irb':{'reportable':True}},
            'targetPulseIdentity':{'sha256':'a'*64}}}
        self.assertTrue(subject.reportable(value))
        for key,bad in [('dataKind','model'),('evidenceCurrent',False),('evidenceIntact',False)]:
            self.assertFalse(subject.reportable({**value,key:bad}))
        self.assertFalse(subject.reportable({**value,'result':{**value['result'],'procedureId':'sq_drag_half_scan'}}))

    def test_window_adaptation_validates_both_seeds_and_never_suppresses_other_failure(self):
        from tests.test_science_repeat5 import parameters
        p={**parameters(),'drag_mode':'empirical_beta','drag_beta_s':0.,'rb_lengths':[1,2,4,8,16,32],
           'rb_sequences':20,'rb_captures':2,'reference_captures':4,'rb_master_seed':72,
           'rb_bootstrap_replicates':100,'rb_target':'X/2'}
        from adapters.science_v3_irb.plans import FIELDS,DEFAULTS
        p={k:v for k,v in p.items() if k in FIELDS|set(DEFAULTS)}
        calls=[]
        def planner(value):
            calls.append(value)
            if value['rb_lengths'][-1]>16:raise ValueError('Native IRB sequence exceeds fixed control window')
            return {'sha256':'f'*64,'budget':{'rows':416},'rows':[]}
        result,receipt=subject.bounded_irb_parameters(p,planner)
        self.assertEqual(result['rb_lengths'],[1,2,4,8,16]);self.assertEqual(len(calls),3)
        self.assertNotEqual(receipt['captures'][0]['seed'],receipt['captures'][1]['seed'])
        self.assertTrue(receipt['nativePlanningOnly']);self.assertFalse(receipt['hardwareDispatched'])
        with self.assertRaisesRegex(APIError,'source changed'):
            subject.bounded_irb_parameters(p,lambda _p:(_ for _ in ()).throw(ValueError('source changed')))

    def test_readonly_planner_is_explicit_utf8_without_environment_mutation(self):
        import os
        environment=dict(os.environ)
        with (patch('adapters.science_v3_irb.policy.verify_sources'),
              patch('adapters.science_v3_irb.plans.normalize_parameters',return_value=({'fixture':1},{})),
              patch('subprocess.run',return_value=SimpleNamespace(returncode=1,stdout=b'',stderr='中文路径：控制窗超出'.encode('utf8'))) as run):
            with self.assertRaisesRegex(ValueError,'中文路径：控制窗超出'):
                subject.native_initial_planner(Path('fixture-root'),'fixture-python',{})
            args,kwargs=run.call_args
            self.assertEqual(args[0][1:5],['-I','-B','-X','utf8'])
            self.assertIsInstance(kwargs['input'],bytes)
            self.assertNotIn('text',kwargs);self.assertNotIn('env',kwargs)
        self.assertEqual(dict(os.environ),environment)


if __name__=='__main__':unittest.main()
