"""Explicit synthetic statistical fixtures, not acquired calibration evidence."""
import unittest
import importlib.util
import json
import subprocess
from pathlib import Path
if importlib.util.find_spec('scipy') is not None and importlib.util.find_spec('numpy') is not None:
    import numpy as np

def load_tests(loader,tests,pattern):
    if importlib.util.find_spec('scipy') is not None and importlib.util.find_spec('numpy') is not None:return tests
    class PinnedNativeAnalysis(unittest.TestCase):
        def runTest(self):
            root=Path(__file__).resolve().parents[1]
            profile=json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf-8'))
            code='import sys,unittest;sys.path.insert(0,sys.argv[1]);r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName(sys.argv[2]));raise SystemExit(not r.wasSuccessful())'
            result=subprocess.run([profile['python'],'-I','-B','-c',code,str(root),'tests.test_science_v3_irb_analysis'],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
    return unittest.TestSuite([PinnedNativeAnalysis()])

class ReferenceContracts(unittest.TestCase):
    def test_half_candidate_comparison_uses_actual_zero_and_fixed_candidate_rows(self):
        from tests.test_science_v3_irb import CampaignContracts
        from adapters.science_v3_irb.half_scan import build_half_scan,analyze
        p,bundle=build_half_scan({**CampaignContracts().parameters(),'verify_drag_lambda1':.2})
        plan={'procedureId':'sq_drag_half_scan','parameters':p,'rows':bundle['rows']};rng=np.random.default_rng(87)
        means=[float(r['logicalState']) if r['condition']=='reference' else .1+.3*(r['axisValue']-.2)**2 for r in plan['rows']]
        i=np.array(means)[:,None,None]+rng.normal(0,.01,(len(means),1,200));q=rng.normal(0,.01,i.shape)
        result=analyze(plan,i,q);self.assertTrue(result['analysis']['accepted'],result)
        self.assertAlmostEqual(result['metrics']['candidate_improvement_vs_zero'],.012,delta=.004)
        for j,r in enumerate(plan['rows']):
            if r['condition']=='verification_candidate':i[j]=.118;q[j]=0
        result=analyze(plan,i,q)
        self.assertLess(result['metrics']['candidate_signal_agreement'],1)
        self.assertLess(result['metrics']['candidate_improvement_upper_5sem'],0)
        self.assertFalse(result['analysis']['accepted'])
    def fixture(self):
        rows=[{'id':2*c+s,'stage':'pre','capture':c,'logicalState':s} for c in range(4) for s in (0,1)]
        rng=np.random.default_rng(819);z=np.array([r['logicalState']+rng.normal(0,.03,400)+1j*rng.normal(0,.03,400) for r in rows])
        return rows,z
    def test_reference_freezes_training_and_requires_held_out_capture_contrast(self):
        from adapters.science_v3_irb.analysis import freeze_pre
        rows,z=self.fixture();result=freeze_pre(rows,z,shots=200,source='synthetic-fixture')
        self.assertEqual(result['projection']['training_captures'],[0,1]);self.assertEqual(result['projection']['validation_captures'],[2,3])
        self.assertTrue(result['accepted'])
        altered=z.copy();altered[5]=altered[4];altered[7]=altered[6]
        with self.assertRaisesRegex(ValueError,'held-out'):freeze_pre(rows,altered,shots=200,source='synthetic-fixture')
        self.assertEqual(result['projection']['ground_iq'],freeze_pre(rows,z,shots=200,source='synthetic-fixture')['projection']['ground_iq'])
    def test_post_drift_uses_fixed_pre_scale_and_rejects_offset(self):
        from adapters.science_v3_irb.analysis import freeze_pre,check_post
        rows,z=self.fixture();frozen=freeze_pre(rows,z,shots=200,source='synthetic-fixture')
        post=[{**r,'stage':'post'} for r in rows]
        self.assertTrue(check_post(post,z,frozen,shots=200)['accepted'])
        drift=check_post(post,z+.25,frozen,shots=200)
        self.assertFalse(drift['accepted']);self.assertGreater(drift['reference_drift_fraction'],.1)
    def test_held_out_capture_offsets_cannot_cancel_before_branch_admission(self):
        from adapters.science_v3_irb.analysis import freeze_pre
        rows,z=self.fixture();z[4:6]+=.25;z[6:8]-=.25
        with self.assertRaisesRegex(ValueError,'held-out'):freeze_pre(rows,z,shots=200,source='synthetic-fixture')

if __name__=='__main__':unittest.main()
