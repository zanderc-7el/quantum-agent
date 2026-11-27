"""Empirical-mode reportability and controller lifecycle, synthetic data only.

The existing native numerical fixture is reused to test API contracts; replacing
its pulse metadata is not a new physical observation or a valid native receipt.
Source authentication/transport remain explicitly injected in the lifecycle test.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.common import digest
from backend.quality import evaluate, SCIENCE_IRB_GATES
from tests.science_irb_quality_fixture import result_fixture


def empirical_fixture(test):
    from adapters.science_v3_irb.plans import normalize_parameters, identity
    from adapters.science_v3_irb.beta import policy_for, pulse_policy
    result=result_fixture(test)
    p=result['parameters']
    p.pop('drag_lambda1',None);p.pop('anharmonicity_hz',None)
    p.update(drag_mode='empirical_beta',drag_beta_s=-.3e-9)
    p,_=normalize_parameters(p);result['parameters']=p
    result['targetPulseIdentity']=identity(p)
    result['provenance'].update(policy=pulse_policy(p),campaignPolicy=policy_for('sq_drag_irb',p))
    proof=result['provenance']['scienceDependencies']
    drag_id=next(r['recordId'] for r in proof['rootReferences'] if r['role']=='drag')
    next(n for n in proof['nodes'] if n['recordId']==drag_id)['targetPulseIdentity']=identity(p)
    proof['closureSha256']=digest({k:proof[k] for k in ('schema','policyVersion','kind','scope','inputs','rootReferences','nodes','edges')})
    return result


def half_fixture(test,stage):
    """Actual native synthetic ADC analysis plus separately injected lineage."""
    envelope=json.loads((Path(__file__).parent/'fixtures/r19_beta_half_numeric.json').read_text(encoding='utf8'))
    test.assertTrue(envelope['fixtureOnly']);test.assertFalse(envelope['physicalAcceptanceEligible'])
    numeric=envelope[stage];analysis=numeric['analysis'];base=result_fixture(test)
    return {'dataKind':'real','acquiredAt':'TEST_ONLY','parameters':deepcopy(numeric['parameters']),
        'metrics':deepcopy(analysis['metrics']),'candidateParameters':deepcopy(analysis['candidateParameters']),
        'targetPulseIdentity':deepcopy(analysis['targetPulseIdentity']),'series':deepcopy(analysis['series']),
        'analysisQuality':deepcopy(analysis['analysis']),'artifacts':[{'name':'raw_scan.npz','kind':'raw'}],
        'provenance':{'freshAcquisition':True,'policy':numeric['policy'],'campaignPolicy':numeric['campaignPolicy'],
            'scienceDependencies':deepcopy(base['provenance']['scienceDependencies']),
            'scienceHalfDrag':{'rabiLineage':deepcopy(base['provenance']['scienceIRB']['rabiLineage']),
                'targetRotation':'X/2','zeroBaselineAcquired':True,
                'candidateComparison':deepcopy(analysis.get('candidateComparison'))}}}


class EmpiricalIRBContracts(unittest.TestCase):
    def test_actual_numeric_beta_half_requires_seconds_and_independent_candidate(self):
        first=half_fixture(self,'selection');second=half_fixture(self,'verification')
        initial=evaluate(first,'sq_drag_half_scan','real',SCIENCE_IRB_GATES,False)
        self.assertEqual(initial['quality']['status'],'unverified',initial['quality'])
        final=evaluate(second,'sq_drag_half_scan','real',SCIENCE_IRB_GATES,True)
        self.assertEqual(final['quality']['status'],'passed',final['quality'])
        self.assertNotIn('gate_fidelity',final['metrics'])
        for key,value in (('coefficientUnit','ns'),('coefficientMode','normalized_alpha'),
                          ('candidateBetaSeconds',second['candidateParameters']['drag_beta_s']+1e-11)):
            invalid=deepcopy(second);invalid['provenance']['scienceHalfDrag']['candidateComparison'][key]=value
            with self.subTest(key=key):
                self.assertEqual(evaluate(invalid,'sq_drag_half_scan','real',SCIENCE_IRB_GATES,True)['quality']['status'],'failed')
        invalid=deepcopy(second);invalid['parameters']['verify_drag_beta_s']+=1e-11
        self.assertEqual(evaluate(invalid,'sq_drag_half_scan','real',SCIENCE_IRB_GATES,True)['quality']['status'],'failed')

    def test_numeric_fixture_requires_independent_same_mode_target(self):
        result=empirical_fixture(self);result.update(dataKind='real',acquiredAt='TEST_ONLY')
        first=evaluate(result,'sq_drag_irb','real',SCIENCE_IRB_GATES,False)
        self.assertEqual(first['quality']['status'],'unverified')
        self.assertNotIn('gate_fidelity',first['metrics'])
        verified=evaluate(result,'sq_drag_irb','real',SCIENCE_IRB_GATES,True)
        self.assertEqual(verified['quality']['status'],'passed',verified['quality'])
        self.assertIn('gate_fidelity',verified['metrics'])
        for mutation in ('wrong_mode','wrong_beta','wrong_source','publishes_coefficient'):
            invalid=deepcopy(result)
            if mutation=='wrong_mode':invalid['parameters']['drag_mode']='normalized_alpha'
            elif mutation=='wrong_beta':invalid['parameters']['drag_beta_s']=.3e-9
            elif mutation=='publishes_coefficient':invalid['candidateParameters']={'drag_beta_s':-.3e-9}
            else:
                source=invalid['provenance']['scienceDependencies']
                node=next(n for n in source['nodes'] if n.get('procedureId')=='sq_drag_half_scan')
                node['targetPulseIdentity']['identity']['betaSeconds']=.1e-9
                source['closureSha256']=digest({k:source[k] for k in ('schema','policyVersion','kind','scope','inputs','rootReferences','nodes','edges')})
            with self.subTest(mutation=mutation):
                out=evaluate(invalid,'sq_drag_irb','real',SCIENCE_IRB_GATES,True)
                self.assertEqual(out['quality']['status'],'failed')
                self.assertNotIn('gate_fidelity',out['metrics'])

    def test_controller_still_uses_one_lease_two_effects_and_fresh_seed(self):
        from tests.test_science_irb_controller import ScienceIRBControllerContracts
        contract=ScienceIRBControllerContracts('test_exact_adapter_two_effects_one_lease_fresh_seed_and_human_acceptance')
        contract.setUp();self.addCleanup(contract.doCleanups)
        with patch('tests.science_irb_quality_fixture.result_fixture',side_effect=empirical_fixture):
            contract.test_exact_adapter_two_effects_one_lease_fresh_seed_and_human_acceptance()
