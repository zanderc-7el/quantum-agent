import unittest
from backend.science_models import pulse
from backend.common import APIError

class MissingPhaseTests(unittest.TestCase):
    def test_missing_phases_keep_timing_and_null_metadata(self):
        data=dict(durationNs=20,xyGapNs=2,readoutDurationNs=2000,acquisitionDurationNs=2000,
                  acquisitionDelayNs=70,phaseDeg=None,readoutPhaseDeg=None)
        out=pulse(data)
        self.assertEqual([(s['startNs'],s['endNs']) for s in out['segments'] if s['id'] in ('xy-1','xy-2','read-in','read-out')],[(0,20),(22,42),(42,2042),(112,2112)])
        self.assertIsNone(out['segments'][0]['phaseDeg'])
        self.assertIsNone(out['segments'][-1]['phaseDeg'])
        self.assertEqual(out['phaseReference']['xy'],'unknown_zero_reference_model')
        self.assertIn('零参考相位',out['series'][0]['label'])
        self.assertEqual(out['dataKind'],'simulation')
        self.assertFalse(out['dispatched'])

    def test_explicit_phase_is_preserved_and_invalid_values_still_rejected(self):
        out=pulse({'phaseDeg':90,'readoutPhaseDeg':30})
        self.assertEqual(out['segments'][0]['phaseDeg'],90)
        self.assertEqual(out['segments'][-1]['phaseDeg'],30)
        self.assertAlmostEqual(max(abs(v) for v in out['series'][0]['values']),0,places=10)
        for value in ('unknown',float('nan'),True):
            with self.assertRaises(APIError):pulse({'phaseDeg':value})
