from copy import deepcopy
import unittest
from tests.test_science_flux import route

def parameters():
    return {'bias_values_v':[-.2,.2],'readout_frequencies_hz':[6.99e9+i*1e6 for i in range(21)],
        'readout_hz':7e9,'readout_amp':.3,'readout_width_s':2e-6,'xy_delay_s':1e-7,
        'xy_attenuation_db':3.75,'xy_filter_mhz':4700,'shots':16,'capture_repeats':2,'acquisitions_per_point':1}

class Plans(unittest.TestCase):
    def test_explicit_axes_baseline_and_full_budget(self):
        from adapters.science_flux.plans import build_campaign
        c=build_campaign('ro_flux_scan',parameters(),route())
        self.assertEqual(c['totalRows'],126)
        self.assertEqual(c['cuts'][0]['phase'],'baseline')
        self.assertIsNone(c['cuts'][0]['requestedSettingV'])
        self.assertEqual(c['estimated_total_shots'],126*16)
        self.assertGreater(c['maximumRampCommands'],0)
        for key in ('bias_values_v','readout_frequencies_hz'):
            p=parameters();del p[key]
            with self.assertRaises(ValueError):build_campaign('ro_flux_scan',p,route())

    def test_bounded_rows_and_voltage(self):
        from adapters.science_flux.plans import build_campaign
        for updates in ({'bias_values_v':[.6]}, {'bias_values_v':[0]*22}, {'shots':True},
                {'readout_frequencies_hz':[7e9,6.9e9]}, {'capture_repeats':4,'bias_values_v':[i*.01 for i in range(21)]}):
            with self.subTest(updates=updates),self.assertRaises(ValueError):build_campaign('ro_flux_scan',{**parameters(),**updates},route())

    def test_point_requires_current_server_source_and_in_band_local_axis(self):
        from adapters.science_flux.plans import build_campaign
        p=parameters();p.pop('bias_values_v');p['local_readout_frequencies_hz']=p.pop('readout_frequencies_hz')
        source={'resourceId':'Qfixture','requestedSettingV':.2,'usableReadoutBandHz':[6.98e9,7.02e9],
            'pointId':'point-1','recordId':'real-source','routeSha256':__import__('adapters.science_runtime.policy',fromlist=['digest']).digest(route())}
        with self.assertRaises(ValueError):build_campaign('sq_flux_point_recheck',p,route())
        c=build_campaign('sq_flux_point_recheck',p,route(),point=source)
        self.assertEqual(c['totalRows'],42);self.assertEqual(c['cuts'][0]['requestedSettingV'],.2)
        with self.assertRaises(ValueError):build_campaign('sq_flux_point_recheck',p,route(),point={**source,'resourceId':'other'})
        with self.assertRaises(ValueError):build_campaign('sq_flux_point_recheck',p,route(),point={**source,'usableReadoutBandHz':[7.1e9,7.2e9]})
        p.update(drive_frequencies_hz=[4.8e9+i*1e6 for i in range(11)],drive_amp=.1,spectroscopy_width_s=2e-6)
        c=build_campaign('sq_flux_point_recheck',p,route(),point=source)
        self.assertEqual(c['totalRows'],86);self.assertEqual(c['cuts'][1]['phase'],'f01')
        self.assertEqual(c['cuts'][1]['readoutPolicy'],'fresh_accepted_local_cavity')

if __name__=='__main__':unittest.main()
