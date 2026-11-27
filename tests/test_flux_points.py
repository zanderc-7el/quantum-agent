"""Server projection tests: fixtures are synthetic and never execution eligible."""
from copy import deepcopy
import importlib
import unittest
from backend.common import APIError,digest

def cut(stage,setting=.02):
    value={'pointId':'bias-0','cutId':1,'phase':'cavity','requestedSettingV':setting,
      'frequenciesHz':[7e9+n*1e6 for n in range(5)],'responseMagnitude':[1,.9,.2,.9,1],
      'readoutFrequencyHz':7.002e9,'rawSha256':('1' if stage=='selection' else '2')*64,
      'analysis':{'accepted':True},'biasReceipt':{'setting':{'kind':'hardware_setting_register','valueV':setting},
        'measurement':{'kind':'measured_output','valueV':setting+.01,'uncertaintyV':.0001}}}
    value['cutSha256']=digest(value);return value

class FluxPointProjection(unittest.TestCase):
    def setUp(self):
        try:self.module=importlib.import_module('backend.flux_points')
        except ModuleNotFoundError:self.module=None
        self.assertIsNotNone(self.module,'server-only flux point resolver missing')
        self.originals={s:{'provenance':{'scienceFlux':{'routeSha256':'a'*64,'points':[cut(s)]}}} for s in ('selection','verification')}
        self.record={'id':'scan-record','kind':'measurement','scope':{'resourceId':'Q11'},'result':{'procedureId':'ro_flux_scan'}}
        self.inputs={'sourceRecordId':'scan-record','selectedBiasIndex':0,'bias':.02,'biasUnit':'V','purpose':'work'}
        self.operations=[{'stage':s,'operationId':s+'-operation'} for s in self.originals]
    def project(self,**kwargs):
        return self.module.project_point(self.record,self.originals,self.operations,self.inputs,**kwargs)
    def test_projection_preserves_actual_setting_measured_value_and_two_raw_origins(self):
        original=deepcopy(self.originals);value=self.project(working_point_id='point-record')
        self.assertEqual(value['schema'],'qcal-flux-point-evidence/1')
        self.assertEqual(value['requestedSettingV'],.02);self.assertEqual(value['measuredOutputV'],.03)
        self.assertEqual(value['usableReadoutBandHz'],[7e9,7.004e9])
        self.assertEqual(value['originalCut']['readoutFrequencyHz'],7.002e9)
        self.assertEqual({v['stage'] for v in value['sourceOperations']},{'selection','verification'})
        self.assertEqual(value['sha256'],digest({k:v for k,v in value.items() if k!='sha256'}))
        self.assertEqual(self.originals,original)
    def test_unknown_index_changed_source_and_baseline_cannot_become_point(self):
        for field,value in [('selectedBiasIndex',True),('selectedBiasIndex',-1),('selectedBiasIndex',1),('bias',.03),('biasUnit','A'),('sourceRecordId','other')]:
            with self.subTest(field=field),self.assertRaises(APIError):
                self.module.project_point(self.record,self.originals,self.operations,{**self.inputs,field:value},working_point_id='p')
    def test_changed_receipt_single_stage_and_unresolved_point_are_rejected(self):
        for change in ('hash','setting','failed','cache','single','same-raw'):
            with self.subTest(change=change):
                sources=deepcopy(self.originals);point=sources['verification']['provenance']['scienceFlux']['points'][0]
                if change=='hash':point['responseMagnitude'][1]=999
                elif change=='setting':point['requestedSettingV']=.03
                elif change=='failed':point['analysis']['accepted']=False
                elif change=='cache':point['biasReceipt']['measurement']['kind']='software_cache'
                elif change=='single':sources.pop('selection')
                else:point['rawSha256']='1'*64
                if change!='hash':point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
                with self.assertRaises(APIError):self.module.project_point(self.record,sources,self.operations,self.inputs,working_point_id='p')

if __name__=='__main__':unittest.main()
