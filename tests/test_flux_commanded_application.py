"""Application contracts using native numeric/command fixtures, never hardware."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from backend.common import APIError,digest
from backend.quality import evaluate,science_rules
from backend.science_flux_quality import check,check_independent
from backend.flux_points import project_point


def fixture():
    value=json.loads((Path(__file__).parent/'fixtures/science_flux_commanded_numeric_result.json').read_text(encoding='utf8'))
    assert value['fixtureOnly'] and value['neverAcquiredFromHardware']
    return value['selectionResult'],value['verificationResult']


class CommandedApplication(unittest.TestCase):
    def test_native_command_result_has_separate_gates_and_no_measured_claim(self):
        a,b=fixture();route=a['provenance']['scienceFlux']['route']
        version,gates=science_rules({'scienceFluxPolicy':'f8_offset_only_measured_restore_v1','biasRoutes':{'Qfixture':route}})
        self.assertEqual(version,'science-flux-commanded-return-gates/1')
        self.assertNotIn('bias_readback_coverage',gates['ro_flux_scan'])
        self.assertEqual(check(a,'ro_flux_scan'),[]);self.assertEqual(check_independent(a,b,'ro_flux_scan'),[])
        self.assertEqual(evaluate(a,'ro_flux_scan','real',gates,rule_version=version)['quality']['status'],'unverified')
        self.assertEqual(evaluate(b,'ro_flux_scan','real',gates,independent=True,rule_version=version)['quality']['status'],'passed')

    def test_command_evidence_must_match_operation_reply_and_declared_end(self):
        for case in ('other-operation','measured-claim','wrong-end','failed-end','unknown-reply','false-snr','context'):
            with self.subTest(case=case):
                _,b=fixture();f=b['provenance']['scienceFlux'];point=f['points'][-1];r=point['biasReceipt']
                if case=='other-operation':r['command']['operationId']='other'
                elif case=='measured-claim':r['measurement']={'kind':'measured_output','valueV':.2}
                elif case=='wrong-end':f['restoration']['setting']['valueV']=.3
                elif case=='failed-end':f['restoration']['commandEndVerified']=False
                elif case=='unknown-reply':r['command']['reply']['replies'][-1]='UNKNOWN'
                elif case=='false-snr':point['analysis']['captureCandidates'][0]['snr']*=2
                else:
                    f['biasContext']['requiresReapplicationAndMeasuredReadback']=True
                    f['biasContext']['sha256']=digest({k:v for k,v in f['biasContext'].items() if k!='sha256'})
                r['command']['sha256']=digest({k:v for k,v in r['command'].items() if k!='sha256'})
                point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
                self.assertTrue(check(b,'ro_flux_scan'))

    def project(self,a,b):
        record={'id':'fixture-record','kind':'measurement','scope':{'resourceId':'Qfixture'},'result':{'procedureId':'ro_flux_scan'}}
        return project_point(record,{'selection':a,'verification':b},[{'stage':s,'operationId':s} for s in ('selection','verification')],
            {'sourceRecordId':'fixture-record','selectedBiasIndex':0,'purpose':'work'})

    def test_point_preserves_two_actual_cuts_and_null_measured_output(self):
        a,b=fixture();point=self.project(a,b)
        self.assertEqual(point['schema'],'qcal-flux-point-evidence/2')
        self.assertEqual(point['requestedSettingV'],.2)
        self.assertIsNone(point['measuredOutputV']);self.assertIsNone(point['measurementUncertaintyV'])
        self.assertEqual(len(point['sourceOperations']),2)
        self.assertEqual(point['originalCut']['frequenciesHz'],b['provenance']['scienceFlux']['points'][-1]['frequenciesHz'])
        self.assertEqual(point['sha256'],digest({k:v for k,v in point.items() if k!='sha256'}))
        b['provenance']['scienceFlux']['points'][-1]['biasReceipt']['command']['operationId']='selection'
        p=b['provenance']['scienceFlux']['points'][-1];p['biasReceipt']['command']['sha256']=digest({k:v for k,v in p['biasReceipt']['command'].items() if k!='sha256'})
        p['cutSha256']=digest({k:v for k,v in p.items() if k!='cutSha256'})
        with self.assertRaises(APIError):self.project(a,b)

if __name__=='__main__':unittest.main()
