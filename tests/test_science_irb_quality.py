"""Controller reportability checks, with explicitly synthetic receipt fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from backend.quality import evaluate,SCIENCE_IRB_GATES,SCIENCE_IRB_GATES_VERSION


class IndependentIRBQuality(unittest.TestCase):
    def test_independent_campaign_cannot_reuse_seed_pre_or_change_pulse(self):
        from backend.science_irb_quality import check_independent
        first=self.native_fixture();second=deepcopy(first)
        first['provenance'].update(operationId='selection',sequenceManifestSha256='first')
        second['provenance'].update(operationId='verification',sequenceManifestSha256='second')
        second['parameters']['rb_master_seed']=(first['parameters']['rb_master_seed']+0x9e3779b9)%(2**32)
        self.assertTrue(check_independent(first,second),'copied PRE must fail')
        second['provenance']['scienceIRB']['reference']['pre']['rawSha256']='fresh-fixture-raw'
        self.assertEqual(check_independent(first,second),[])
        second['parameters']['drag_lambda1']+=.1
        self.assertTrue(check_independent(first,second))

    def test_rehashed_timing_lineage_still_requires_exact_quantization_and_fit_uncertainty(self):
        from backend.common import digest
        for field,value in [('targetDurationS',20.25e-9),('sourceContinuousDurationS',20.2e-9),('calibratesShapedRotation',True)]:
            result=self.native_fixture();lineage=result['provenance']['scienceIRB']['rabiLineage'];time=lineage['time'];time[field]=value
            time['sha256']=digest({k:v for k,v in time.items() if k!='sha256'});lineage['sha256']=digest({k:v for k,v in lineage.items() if k!='sha256'})
            with self.subTest(field=field):self.assertEqual(evaluate(result,'sq_drag_irb','real',SCIENCE_IRB_GATES,True)['quality']['status'],'failed')
    def native_fixture(self):
        from tests.science_irb_quality_fixture import result_fixture
        result=result_fixture(self)
        # In-memory control-contract probe only, never saved to a source ledger.
        result.update(dataKind='real',acquiredAt='synthetic-test-only')
        return result

    def test_native_numeric_result_needs_independent_acquisition_and_retains_conditional_estimate(self):
        result=self.native_fixture()
        first=evaluate(result,'sq_drag_irb','real',SCIENCE_IRB_GATES,False)
        self.assertEqual(first['quality']['status'],'unverified');self.assertNotIn('gate_fidelity',first['metrics'])
        second=evaluate(result,'sq_drag_irb','real',SCIENCE_IRB_GATES,True)
        self.assertEqual(second['quality']['status'],'passed',second['quality'])
        self.assertIn('gate_fidelity',second['metrics'])

    def test_altered_target_fit_interval_reference_or_candidate_cannot_pass_received_flags(self):
        def target(r):r['targetPulseIdentity']['identity']['rotationRad']=3.14159
        def curve(r):r['provenance']['scienceIRB']['irb']['reference_fit']['fitted_values'][0]+=.01
        def ratio(r):r['metrics']['gate_fidelity']=.999999
        def interval(r):r['provenance']['scienceIRB']['irb']['bootstrap']['intervals_unclipped']['F_X_half_conditional']=[.999,.9999]
        def seed(r):r['parameters']['rb_master_seed']+=1
        def pre(r):r['provenance']['scienceIRB']['reference']['pre']['projection']['validation_captures']=[0,1]
        def post(r):r['provenance']['scienceIRB']['reference']['post']['changes']['ground']['postMinusPreFraction']=.2
        def candidate(r):r['candidateParameters']={'drag_lambda1':.3}
        def legacy(r):r['provenance']['scienceDependencies']['nodes'][0]['procedureId']='sq_drag_scan'
        for change in (target,curve,ratio,interval,seed,pre,post,candidate,legacy):
            result=self.native_fixture();change(result)
            with self.subTest(change=change.__name__):
                out=evaluate(result,'sq_drag_irb','real',SCIENCE_IRB_GATES,True)
                self.assertEqual(out['quality']['status'],'failed',out['quality'])
                self.assertNotIn('gate_fidelity',out['metrics'])
    def test_science_record_retains_separate_paired_fits_interval_and_pulse_links(self):
        from backend.science_service import ScienceService
        raw={'acquiredAt':'synthetic-fixture','quality':{'status':'passed'},'series':[
            {'name':'RB','x':[1,4],'y':[.9,.8]}, {'name':'RB fit','x':[1,4],'y':[.89,.81],'fit':True}],
            'targetPulseIdentity':{'sha256':'same-half'},'provenance':{'scienceIRB':{'irb':{'reportable':True,'bootstrap':{'intervals_unclipped':{'F_X_half_conditional':[.98,.99]}}},'reference':{'pre':{},'post':{}}},
                'scienceDependencies':{'rootReferences':[{'role':'drag','recordId':'half-scan'}],'nodes':[]}}}
        result=ScienceService._measurement({'raw':raw,'attempt':{'procedureId':'sq_drag_irb'}})
        self.assertEqual(len(result['sourceSeries']),2)
        self.assertEqual(result['gateCharacterization']['sourceReferences'],[{'role':'drag','recordId':'half-scan'}])
        self.assertTrue(result['gateCharacterization']['reportable'])
        self.assertEqual(result['targetPulseIdentity'],raw['targetPulseIdentity'])
    def test_received_accepted_flag_and_scalar_metrics_are_not_fidelity_evidence(self):
        result={'dataKind':'real','artifacts':[{'name':'raw_scan.npz'}],'acquiredAt':'fixture-only',
            'analysisQuality':{'accepted':True,'reasons':[]},'candidateParameters':{},
            'metrics':{'r_squared':1.,'gate_fidelity':.999,'clifford_fidelity':.998,
                'reference_drift_fraction':0.,'reference_drift_upper_diagnostic':0.,
                'independent_sequences_per_length':20,'rb_captures':2}}
        evaluated=evaluate(result,'sq_drag_irb','real',SCIENCE_IRB_GATES,independent=True,
                           rule_version=SCIENCE_IRB_GATES_VERSION)
        self.assertEqual(evaluated['quality']['status'],'failed')
        self.assertNotIn('gate_fidelity',evaluated['metrics'])

    def test_half_scan_cannot_claim_a_pi_identity_or_skip_zero_baseline_comparison(self):
        result={'dataKind':'real','artifacts':[{'name':'raw_scan.npz'}],'acquiredAt':'fixture-only',
            'analysisQuality':{'accepted':True,'reasons':[]},'candidateParameters':{'drag_lambda1':.2},
            'metrics':{'r_squared':1.,'curvature_z':10.,'diagnostic_improvement':.1,
                'candidate_agreement_fraction':0.,'candidate_signal_agreement':0.,'candidate_capture_agreement':0.,
                'candidate_points_acquired':4,'candidate_improvement_upper_5sem':.1,
                'candidate_improvement_vs_zero':.1,'candidate_improvement_sem':0.}}
        self.assertEqual(evaluate(result,'sq_drag_half_scan','real',SCIENCE_IRB_GATES,True)['quality']['status'],'failed')
