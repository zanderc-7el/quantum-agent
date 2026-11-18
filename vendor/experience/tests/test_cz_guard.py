from pathlib import Path
import copy,json,unittest
from quantum_agents.cz_suite import check_complete
ROOT=Path(__file__).resolve().parents[1]

class CZGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p=ROOT/'resources/twin_source/outputs/two_qubit/final'
        cls.cal=json.loads((p/'calibration.json').read_text(encoding='utf-8'))
        cls.val=json.loads((p/'validation.json').read_text(encoding='utf-8'))
        cls.tom=json.loads((p/'tomography_counts.json').read_text(encoding='utf-8'))
    def test_complete_historical_fixture(self):
        self.assertTrue(check_complete(self.cal,self.val,self.tom)['passed'])
    def test_missing_phase_fail_closed(self):
        c=copy.deepcopy(self.cal);c['calibration']['conditional_phases_rad']=[]
        self.assertFalse(check_complete(c,self.val,self.tom)['passed'])
    def test_missing_truth_table_fail_closed(self):
        c=copy.deepcopy(self.cal);c['fits']['truth_table']={}
        self.assertFalse(check_complete(c,self.val,self.tom)['passed'])
    def test_counts_and_missing_tomography(self):
        c=copy.deepcopy(self.cal);c['fits']['truth_table']['00']['counts']['00']+=1
        self.assertFalse(check_complete(c,self.val,self.tom)['passed'])
        t=copy.deepcopy(self.tom);t.pop('XX')
        self.assertFalse(check_complete(self.cal,self.val,t)['passed'])
    def test_missing_validation_even_when_passed_true(self):
        v=copy.deepcopy(self.val);v['checks']=v['checks'][:-1];v['passed']=True
        self.assertFalse(check_complete(self.cal,v,self.tom)['passed'])

if __name__=='__main__':unittest.main()
