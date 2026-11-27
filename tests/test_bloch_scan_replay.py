"""Pure, synthetic model contracts; no acquisitions or hardware qualification."""
import math
import unittest
from unittest.mock import patch

from backend.common import APIError
from backend import science_models as models


class BlochScanReplayContracts(unittest.TestCase):
    def inputs(self, sequence='cpmg', **changes):
        return dict(sequence=sequence, initialState='+x', durationNs=1000,
                    timesNs=[0, 250, 500, 750, 1000], piPulses=2,
                    detuningHz=1e6, t1Ns=1e16, t2Ns=1e16,
                    refocusAxis='y' if sequence == 'echo' else 'x',
                    readoutSign=-1, analysisPhaseHz=0, **changes)

    def replay(self, data):
        return models.preview('bloch', {**data, 'replayScan': True})

    def test_cpmg_scan_endpoint_is_not_longest_sequence_midpoint(self):
        p=self.inputs()
        old=models.bloch(p)
        new=self.replay({**p, 'selectedDelayNs': 250})
        self.assertAlmostEqual(old['trajectory'][1]['readoutP1'], .5, places=10)
        self.assertTrue(all(abs(v-1)<1e-10 for v in new['series'][0]['values']))
        self.assertEqual([s['startNs'] for s in new['segments']], [62.5, 187.5])
        self.assertAlmostEqual(new['trajectory'][-1]['x'], 1, places=10)
        self.assertEqual((new['selectedScanIndex'],new['selectedDelayNs']), (1,250))
        self.assertEqual(new['axes']['x']['values'], p['timesNs'])

    def test_echo_y_axis_scan_and_zero_wait_apply_the_complete_sequence(self):
        p=self.inputs('echo')
        old=models.bloch(p)
        self.assertAlmostEqual(old['trajectory'][2]['readoutP1'], 1, places=10)
        new=self.replay({**p,'selectedDelayNs':500})
        self.assertTrue(all(abs(v)<1e-10 for v in new['series'][0]['values']))
        self.assertEqual([s['startNs'] for s in new['segments']], [250])
        zero=self.replay({**p,'selectedDelayNs':0})
        self.assertEqual(len(zero['trajectory']), 1)
        self.assertEqual(zero['segments'][0]['startNs'], 0)
        self.assertAlmostEqual(zero['trajectory'][0]['x'], -1)
        self.assertAlmostEqual(zero['trajectory'][0]['readoutP1'], 0)

    def test_virtual_analysis_phase_is_not_refocused_static_detuning(self):
        out=self.replay({**self.inputs('echo'),'analysisPhaseHz':-8e5})
        for t,v in zip(out['axes']['x']['values'],out['series'][0]['values']):
            self.assertAlmostEqual(v,(1-math.cos(2*math.pi*8e5*t*1e-9))/2,places=10)

    def test_real_archive_axis_example_separates_scan_and_internal_time(self):
        p={**self.inputs(),'timesNs':[1000*i for i in range(41)],'detuningHz':250000}
        old=models.bloch(p)
        self.assertAlmostEqual(old['trajectory'][1]['readoutP1'],.5,places=10)
        out=self.replay({**p,'selectedDelayNs':1000})
        self.assertEqual([s['startNs'] for s in out['segments']],[250,750])
        mid=next(x for x in out['trajectory'] if x['t']==250)
        self.assertAlmostEqual(mid['readoutP1'],(1+math.cos(math.pi/8))/2,places=10)
        self.assertAlmostEqual(out['trajectory'][-1]['readoutP1'],1,places=10)

    def test_scan_matches_the_existing_comparison_endpoint_algorithm(self):
        for seq in ['echo','cpmg','ramsey','t1','x']:
            with self.subTest(sequence=seq):
                p={**self.inputs(seq),'initialState':'0' if seq=='x' else '1' if seq=='t1' else '+x'}
                expected=[models.bloch({**p,'timesNs':[0] if t==0 else [0,t],
                                       'durationNs':max(t,.001),'points':2})['trajectory'][-1]['readoutP1']
                          for t in p['timesNs']]
                self.assertEqual(self.replay(p)['series'][0]['values'],expected)

    def test_ordinary_bloch_contract_stays_unchanged_and_new_mode_is_explicit(self):
        p=self.inputs()
        self.assertEqual(models.preview('bloch',p),models.bloch(p))
        self.assertEqual(models.preview('bloch',{**p,'replayScan':False}),models.bloch(p))
        out=self.replay(p)
        self.assertEqual(out['selectedDelayNs'],1000)
        self.assertEqual(len(out['trajectory']),201)
        self.assertEqual(out['scanInterpretation'],'independent_scan_endpoints')
        self.assertEqual(out['modelVersion'],'qcal-bloch-scan-replay/1')
        self.assertEqual(out['dataKind'],'simulation')
        self.assertEqual(out['source'],'model')
        self.assertFalse(out['dispatched'])

    def test_axis_selection_and_mode_reject_ambiguous_values(self):
        bad=[{'timesNs':[]},{'timesNs':[1,1]},{'timesNs':[-1,1]},
             {'timesNs':[0,float('nan')]},{'timesNs':[0,1e10+1]},
             {'selectedDelayNs':250.00001},{'selectedDelayNs':True},
             {'trajectoryPoints':1},{'trajectoryPoints':models.MAX_POINTS+1},
             {'trajectoryPoints':True},{'timesNs':[0]*(models.MAX_POINTS+1)}]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(APIError):
                self.replay({**self.inputs(),**change})
        with self.assertRaises(APIError):models.preview('bloch',{**self.inputs(),'replayScan':1})
        with self.assertRaises(APIError):models.preview('bloch',{'replayScan':True})

    def test_total_numeric_budget_is_checked_before_any_trajectory(self):
        p={**self.inputs('x'),'timesNs':[0,50000,100000,150000],
           'rabiHz':1e7,'detuningHz':0,'trajectoryPoints':201}
        # Each endpoint can fit the old 200k per-call limit; their sum cannot.
        with patch.object(models,'bloch',side_effect=AssertionError('must preflight first')):
            with self.assertRaises(APIError) as caught:self.replay(p)
        self.assertEqual(caught.exception.code,'SCIENCE_MODEL_BUDGET')

    def test_maximum_refocus_count_is_bounded_and_valid_short_scan_works(self):
        out=self.replay({**self.inputs(),'piPulses':64,'trajectoryPoints':33})
        self.assertEqual(len(out['segments']),64)
        self.assertEqual(len(out['trajectory']),33)
        for change in [{'piPulses':65},{'t1Ns':1,'t2Ns':3}]:
            with self.assertRaises(APIError):self.replay({**self.inputs(),**change})


class BlochScanReplayHTTPContracts(unittest.TestCase):
    # Reuse the existing ephemeral server/session setup, never the live API.
    from tests.test_science_http import ScienceHTTPContracts as _fixture
    setUp=_fixture.setUp
    tearDown=_fixture.tearDown
    request=_fixture.request

    def database_snapshot(self):
        with self.c.store.connect() as rt:
            return list(rt.db.iterdump())

    def test_normal_preview_route_is_read_only_and_rejects_wrong_selected_point(self):
        self.assertEqual(self.request('/api/science/overview')[0],200)
        before=self.database_snapshot()
        values={**BlochScanReplayContracts().inputs(),'replayScan':True,'selectedDelayNs':250}
        with patch.object(self.adapter,'execute',side_effect=AssertionError('no device dispatch')) as execute:
            status,result=self.request('/api/science/preview',{'kind':'bloch','input':values})
            self.assertEqual(status,200,result)
            self.assertEqual(result['selectedScanIndex'],1)
            self.assertEqual([s['startNs']for s in result['segments']],[62.5,187.5])
            self.assertAlmostEqual(result['trajectory'][-1]['readoutP1'],1,places=10)
            self.assertEqual(result['dataKind'],'simulation')
            self.assertFalse(result['dispatched'])
            status,error=self.request('/api/science/preview',{'kind':'bloch','input':{**values,'selectedDelayNs':251}})
            self.assertEqual(status,400,error)
            execute.assert_not_called()
        self.assertEqual(self.adapter.calls,[])
        self.assertEqual(self.database_snapshot(),before)


if __name__=='__main__':unittest.main()
