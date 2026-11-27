import importlib
import math
import unittest

from backend.common import APIError


class ScientificModelContracts(unittest.TestCase):
    def setUp(self):
        try:
            self.models = importlib.import_module('backend.science_models')
        except ModuleNotFoundError:
            self.models = None
        self.assertIsNotNone(self.models, 'scientific model implementation is missing')

    def preview(self, kind, **values):
        return self.models.preview(kind, values)

    def test_pulse_edges_and_phase_do_not_move_other_channels(self):
        args = dict(durationNs=20, xyGapNs=2, xyCount=2, readoutDurationNs=2000,
                    acquisitionDelayNs=50, acquisitionDurationNs=400, points=101)
        a = self.preview('pulse', **args)
        b = self.preview('pulse', **{**args, 'acquisitionDelayNs': 70, 'phaseDeg': 90})
        xy = [s for s in a['segments'] if s['channel'] == 'XY']
        self.assertEqual([(s['startNs'], s['endNs']) for s in xy], [(0, 20), (22, 42)])
        rows = {s['channel']: s for s in a['segments'] if s['channel'] != 'XY'}
        rows2 = {s['channel']: s for s in b['segments'] if s['channel'] != 'XY'}
        self.assertEqual(rows['Read in']['endNs'] - rows['Read in']['startNs'], 2000)
        self.assertEqual(rows2['Read out']['startNs'] - rows['Read out']['startNs'], 20)
        self.assertEqual(rows['Read in'], rows2['Read in'])
        self.assertLess(max(abs(x) for x in b['series'][0]['values']), 1e-12)
        self.assertGreater(max(b['series'][1]['values']), .1)
        self.assertFalse(a['dispatched'])

    def test_drag_derivative_has_correct_units_sign_and_zero_phase_rotation(self):
        a = self.preview('drag', amplitude=.5, durationNs=40, shape='cosine', alphaNs=2, points=401)
        i, q = (s['values'] for s in a['series'][:2])
        self.assertAlmostEqual(q[100], -2*.5*math.pi/40, places=12)
        self.assertAlmostEqual(q[300], 2*.5*math.pi/40, places=12)
        self.assertEqual(a['metrics']['gateFidelity'], None)
        self.assertAlmostEqual(i[200], .5)
        with self.assertRaises(APIError):
            self.preview('drag', shape='square')

    def test_ideal_x_and_t1_have_independent_analytic_endpoints(self):
        x = self.preview('bloch', sequence='x', durationNs=50, rabiHz=10e6,
                         t1Ns=1e15, t2Ns=1e15, points=101)
        self.assertAlmostEqual(x['trajectory'][0]['z'], 1)
        self.assertAlmostEqual(x['trajectory'][-1]['z'], -1, places=9)
        t1 = self.preview('bloch', sequence='t1', durationNs=50000, t1Ns=50000, t2Ns=40000, points=31)
        self.assertAlmostEqual(t1['trajectory'][-1]['p1'], math.exp(-1), places=12)
        for t in x['trajectory'] + t1['trajectory']:
            self.assertLessEqual(t['x']**2+t['y']**2+t['z']**2, 1+1e-9)

    def test_echo_refocuses_static_detuning_and_cpmg_declares_pulses(self):
        args = dict(durationNs=1300, detuningHz=1e6, t1Ns=1e15, t2Ns=1e15, points=201)
        r = self.preview('bloch', sequence='ramsey', **args)
        e = self.preview('bloch', sequence='echo', **args)
        c = self.preview('bloch', sequence='cpmg', piPulses=4, **args)
        self.assertAlmostEqual(e['trajectory'][-1]['x'], 1, places=9)
        self.assertAlmostEqual(c['trajectory'][-1]['x'], 1, places=9)
        self.assertLess(r['trajectory'][-1]['x'], .5)
        self.assertEqual(len([s for s in c['segments'] if s['kind']=='refocus']), 4)

    def test_inverse_filter_is_regularized_and_gain_bounded(self):
        result = self.preview('predistortion', target=[0,1,0,0], impulseResponse=[.01],
                              regularization=1e-8, maxGain=3, sampleIntervalNs=1)
        self.assertLessEqual(result['metrics']['maxAppliedGain'], 3+1e-12)
        self.assertAlmostEqual(result['series'][1]['values'][1], 3, places=8)
        self.assertAlmostEqual(result['series'][2]['values'][1], .03, places=8)
        self.assertEqual(result['dataKind'], 'simulation')

    def test_shelve_uses_logical_labels_not_probability_of_level_two(self):
        result = self.preview('shelve', f01Hz=5e9, twoPhotonFrequencyHz=4.875e9,
                              counts=[[90,10],[20,80]], shelvedCounts=[[95,5],[10,90]])
        self.assertEqual(result['metrics']['f12EstimateHz'], 4.75e9)
        self.assertAlmostEqual(result['metrics']['withoutShelve']['fidelity'], .85)
        self.assertAlmostEqual(result['metrics']['withShelve']['fidelity'], .925)
        self.assertTrue(result['requiresIndependentCalibration'])
        self.assertEqual(result['calibrationStatus'], 'not_verified')

    def test_flux_preserves_missing_samples_and_selected_original_slice(self):
        result = self.preview('flux', biasValues=[-.1,.1], frequencyHz=[6e9,6.1e9,6.2e9],
                              amplitude=[[1,None,2],[3,1,2]], selectedBiasIndex=1)
        self.assertEqual(result['series'][0]['values'], [3,1,2])
        self.assertEqual(result['grid']['values'][0][1], None)
        self.assertEqual(result['metrics']['selectedMinimumHz'], 6.1e9)
        self.assertNotEqual(result['dataKind'], 'real')

    def test_invalid_grids_and_nonphysical_inputs_fail_before_computation(self):
        for kind, args in [('pulse', {'points':1000000}), ('pulse', {'durationNs':float('nan')}),
                           ('bloch', {'t1Ns':10,'t2Ns':21}), ('shelve', {'counts':[[0,0],[1,2]]}),
                           ('flux', {'biasValues':[0,0], 'frequencyHz':[1,2], 'amplitude':[[1,2],[1,2]]}),
                           ('predistortion', {'target':[1]*10000, 'impulseResponse':[1]})]:
            with self.subTest(kind=kind, args=str(args)[:70]), self.assertRaises(APIError):
                self.preview(kind, **args)

    def test_unbounded_waveform_values_are_rejected_not_overflowed(self):
        with self.assertRaises(APIError):
            self.preview('predistortion',target=[0,1e308],impulseResponse=[1e308])

    def test_shelve_without_counts_cannot_invent_observations(self):
        with self.assertRaises(APIError):self.preview('shelve',f01Hz=5e9)


if __name__ == '__main__':
    unittest.main()
