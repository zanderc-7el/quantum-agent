"""V3 contracts: no real station is contacted."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

class Plans(unittest.TestCase):
    def setUp(self):
        from adapters.science_v3.plans import build_rows
        self.build=build_rows
        self.p={'drive_hz':4.86e9,'drive_amp':.3,'drive_length_s':20e-9,'readout_hz':7.01e9,'readout_amp':.4,
                'readout_width_s':2e-6,'xy_delay_s':100e-9,'xy_filter_mhz':4700,'xy_attenuation_db':3.75,
                'pulse_shape':'Gaussian','pulse_length_s':20e-9,'pulse_sigma_s':4e-9,'shape_pi_amp':.3,
                'probe12_shape':'CosPulse','probe12_length_s':24e-9,'probe12_amplitude':.2,'f12_hz':4.66e9,
                'shots':100,'capture_repeats':4,'acquisitions_per_point':1,'scan_axis_values':[.1,.2,.3]}
    def test_shape_rabi_is_exact_fixed_shape_and_actual_axis(self):
        p,rows=self.build('sq_shape_rabi_amplitude',self.p)
        pulse=[r['gates'][-1]['pulse'] for r in rows]
        self.assertEqual(len(rows),12)
        self.assertEqual([x['amplitude'] for x in pulse[:3]],[.1,.2,.3])
        self.assertTrue(all(x['sigmaNs']==4 and x['durationNs']==20 for x in pulse))
    def test_shelve_frequency_only_changes_probe(self):
        p,rows=self.build('sq_shelve_spectrum12',{**self.p,'scan_axis_values':[4.6e9,4.65e9,4.7e9]})
        on=[r for r in rows if r['condition']=='prep_on']
        self.assertTrue(all(r['gates'][0]['pulse']['frequencyHz']==4.86e9 for r in on))
        self.assertEqual([r['gates'][1]['pulse']['frequencyHz'] for r in on[:3]],[4.6e9,4.65e9,4.7e9])
    def test_spectrum_can_find_unknown_f12_without_circular_prerequisite(self):
        supplied={k:v for k,v in self.p.items() if k!='f12_hz'}
        p,rows=self.build('sq_shelve_spectrum12',{**supplied,'scan_axis_values':[4.6e9,4.65e9,4.7e9]})
        self.assertNotIn('f12_hz',p)
        self.assertEqual(rows[0]['gates'][1]['pulse']['frequencyHz'],4.6e9)
    def test_verification_reacquires_frozen_candidate_and_preserves_baseline(self):
        p,rows=self.build('sq_drag_scan',{**self.p,'drag_lambda1':.2,'anharmonicity_hz':-200e6,
                                         'scan_axis_values':[-.5,0,.5],'verify_drag_lambda1':.2})
        actual=[r for r in rows if r['condition']=='verification_candidate']
        self.assertEqual(len(actual),4)
        self.assertTrue(all(g['pulse']['lambda1']==.2 for r in actual for g in r['gates']))
        self.assertTrue(all(g['pulse']['lambda1']==0 for r in rows if r['condition']=='reference' for g in r['gates']))
    def test_compare_four_conditions_matched_timing(self):
        p,rows=self.build('sq_shelve_readout_compare',{k:v for k,v in self.p.items() if k!='scan_axis_values'})
        self.assertEqual(len(rows),16)
        self.assertEqual({(r['condition'],r['logicalState']) for r in rows},{('off',0),('off',1),('on',0),('on',1)})
        self.assertEqual(len({tuple(g['pulse']['durationNs'] for g in r['gates']) for r in rows}),1)
        self.assertEqual([r['gates'][1]['pulse']['amplitude'] for r in rows[:4]],[0,0,.2,.2])
    def test_bad_grid_axis_unknown_and_unsafe_parameters(self):
        for change in ({'pulse_length_s':20.1e-9},{'scan_axis_values':[.3,.1]},
                       {'scan_axis_values':[.2]*300},{'shape_pi_amp':1.1},{'execute':'code'}):
            with self.subTest(change=change),self.assertRaises(ValueError):self.build('sq_shape_rabi_amplitude',{**self.p,**change})

class Transport(unittest.TestCase):
    def test_explicit_parameters_are_method_scoped_before_dependencies(self):
        from adapters.science_v3_adapter import ScienceV3Adapter,build_science_profile
        from adapters.f8 import AdapterError
        base=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        adapter=ScienceV3Adapter(ROOT,build_science_profile(ROOT,base))
        request={'operationId':'test/measure','resourceId':next(iter(base['resources'])),
                 'procedureId':'sq_shape_rabi_amplitude','parameters':{'probe12_amplitude':.1},
                 'explicitParameterKeys':['probe12_amplitude']}
        with self.assertRaisesRegex(AdapterError,'Explicit parameters do not belong'):
            adapter.build_plan(request)

    def test_import_and_disabled_profile_never_load_native_driver(self):
        from adapters.science_v3_adapter import ScienceV3Adapter,build_science_profile
        base=json.loads((ROOT/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
        profile=build_science_profile(ROOT,base)
        self.assertFalse(profile['enabled'])
        self.assertEqual(profile['hardwareLocks'],base['hardwareLocks'])
        with patch('subprocess.Popen',side_effect=AssertionError('spawn')):
            with self.assertRaises(Exception):ScienceV3Adapter(ROOT,profile).execute({},lambda e:None,lambda:False)

if __name__=='__main__':unittest.main()
