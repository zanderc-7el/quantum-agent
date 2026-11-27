"""Diagnostic proof fixtures are synthetic, not physical acceptance evidence."""
import unittest
from copy import deepcopy
from pathlib import Path
import json
from backend.common import digest
from backend.quality import evaluate,science_rules

class FluxQualityContracts(unittest.TestCase):
    def fixture(self):
        value=json.loads((Path(__file__).parent/'fixtures/science_flux_numeric_result.json').read_text(encoding='utf8'))
        self.assertTrue(value['fixtureOnly']);self.assertTrue(value['neverAcquiredFromHardware'])
        return deepcopy(value['selectionResult']),deepcopy(value['verificationResult'])
    def test_actual_native_numeric_analysis_passes_and_selection_still_needs_independence(self):
        from backend.science_flux_quality import check_independent
        selection,verification=self.fixture();version,gates=science_rules({'scienceFluxPolicy':'f8_offset_only_measured_restore_v1'})
        self.assertEqual(evaluate(selection,'ro_flux_scan','real',gates,rule_version=version)['quality']['status'],'unverified')
        self.assertEqual(evaluate(verification,'ro_flux_scan','real',gates,independent=True,rule_version=version)['quality']['status'],'passed')
        self.assertEqual(check_independent(selection,verification,'ro_flux_scan'),[])
    def test_scoped_candidate_restore_readback_and_native_scalar_tampering_rejected(self):
        from backend.science_flux_quality import check
        for name in ('global-candidate','cache','restore','false-snr','provider-close','missing-row'):
            with self.subTest(name=name):
                _,value=self.fixture();flux=value['provenance']['scienceFlux'];point=flux['points'][-1]
                if name=='global-candidate':value['candidateParameters']={'readout_hz':7e9}
                elif name=='cache':point['biasReceipt']['measurement']['kind']='software_cache'
                elif name=='restore':flux['restoration']['verified']=False
                elif name=='false-snr':point['analysis']['captureCandidates'][0]['snr']=1e6
                elif name=='provider-close':flux['providerClosed']=False
                else:point['rowIds'].pop()
                point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
                self.assertTrue(check(value,'ro_flux_scan'))
    def test_empty_candidates_do_not_hide_same_point_readback_drift(self):
        from backend.science_flux_quality import check_independent,check
        selection,verification=self.fixture()
        route=verification['provenance']['scienceFlux']['route'];delta=route['toleranceV']*.55
        for value,sign in ((selection,-1),(verification,1)):
            for point in value['provenance']['scienceFlux']['points']:
                if point['phase']=='baseline':continue
                receipt=point['biasReceipt'];receipt['measurement']['valueV']+=sign*delta
                for sample in receipt['stabilitySamples']:sample['measurement']['valueV']+=sign*delta
                point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
        self.assertEqual(check(selection,'ro_flux_scan'),[])
        self.assertEqual(check(verification,'ro_flux_scan'),[])
        self.assertTrue(check_independent(selection,verification,'ro_flux_scan'))
    def test_accepted_boolean_and_scalar_metrics_cannot_replace_bias_receipts(self):
        version,gates=science_rules({'scienceFluxPolicy':'f8_offset_only_measured_restore_v1'})
        result={'dataKind':'real','acquiredAt':'2026-11-26T00:00:00+00:00','artifacts':[{'kind':'raw'}],
            'metrics':{'scan_coverage':1,'bias_readback_coverage':1,'restoration_verified':1,'resolved_bias_points':1},
            'analysisQuality':{'accepted':True},'candidateParameters':{}}
        checked=evaluate(result,'ro_flux_scan','real',gates,independent=True,rule_version=version)
        self.assertEqual(checked['quality']['status'],'failed')
        self.assertTrue(any('偏置' in reason for reason in checked['quality']['reasons']))
    def test_cavity_recheck_always_has_explicit_local_gate(self):
        _,gates=science_rules({'scienceFluxPolicy':'f8_offset_only_measured_restore_v1'})
        self.assertEqual(gates['sq_flux_point_recheck']['local_cavity_contrast_snr'],{'min':5})
        self.assertEqual(gates['ro_flux_scan']['scan_coverage'],{'min':1,'max':1})
    def test_same_points_need_two_independent_operations_even_if_global_candidates_empty(self):
        try:from backend.science_flux_quality import check_independent
        except ImportError:check_independent=None
        self.assertIsNotNone(check_independent,'same-point independent comparison is required')
        result={'candidateParameters':{},'sourceRunId':'same','provenance':{}}
        self.assertTrue(check_independent(result,deepcopy(result),'ro_flux_scan'))
    def test_flat_spectroscopy_series_cannot_be_replaced_by_claimed_peak_scalars(self):
        from backend.science_flux_quality import spectrum
        xs=[4.8e9+i*1e6 for i in range(5)]
        point={'frequenciesHz':xs,'responseMagnitude':[0]*5,'f01Hz':xs[2],
            'analysis':{'candidateHz':xs[2],'contrast_snr':100,'captureCandidates':[
                {'candidate_detected':True,'candidate_frequency_hz':xs[2],'candidate_significance':100} for _ in range(2)],
                'series':[{'x':xs,'y':[0]*5} for _ in range(2)]}}
        with self.assertRaises(ValueError):spectrum(point,2)
    def test_actual_native_point_analysis_matches_complex_on_off_evidence(self):
        from backend.science_flux_quality import check,check_independent
        fixture=json.loads((Path(__file__).parent/'fixtures/science_flux_point_numeric_result.json').read_text(encoding='utf8'))
        self.assertTrue(fixture['fixtureOnly']);self.assertTrue(fixture['neverAcquiredFromHardware'])
        first,last=fixture['selectionResult'],fixture['verificationResult']
        self.assertEqual(check(first,'sq_flux_point_recheck'),[]);self.assertEqual(check(last,'sq_flux_point_recheck'),[])
        self.assertEqual(check_independent(first,last,'sq_flux_point_recheck'),[])
        for change in ('flat','move','snr'):
            value=deepcopy(last);point=next(p for p in value['provenance']['scienceFlux']['points'] if p['phase']=='f01')
            if change=='flat':point['analysis']['series'][0]['y']=[0]*len(point['frequenciesHz'])
            elif change=='move':point['analysis']['captureCandidates'][0]['candidate_frequency_hz']=point['frequenciesHz'][1]
            else:point['analysis']['captureCandidates'][0]['candidate_significance']*=2
            point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
            with self.subTest(change=change):self.assertTrue(check(value,'sq_flux_point_recheck'))

if __name__=='__main__':unittest.main()
