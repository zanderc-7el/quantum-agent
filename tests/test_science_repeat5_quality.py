"""Actual native-generated numeric receipt, explicitly fixture-only."""
from copy import deepcopy
from pathlib import Path
import json,unittest
from backend.quality import evaluate,SCIENCE_REPEAT5_GATES

class FiveGateQuality(unittest.TestCase):
    def test_measurement_projection_retains_actual_five_pulse_receipt_and_no_fidelity(self):
        from backend.science_service import ScienceService
        fixture=json.loads((Path(__file__).parent/'fixtures/r23_repeat5_native_results.json').read_text('utf8'))
        raw=fixture['results'][1]['result']
        saved=ScienceService._measurement({'raw':raw,'attempt':{'procedureId':'sq_shape_rabi_amplitude_repeat5'}})
        self.assertEqual(saved['shapeRabiCalibration'],raw['provenance']['shapeRabiCalibration'])
        self.assertEqual(saved['compiledSequenceEvidence'],raw['provenance']['compiledSequenceEvidence'])
        self.assertEqual(saved['parameters'],raw['parameters'])
        self.assertNotIn('gate_fidelity',saved['metrics'])

    def test_irb_quality_requires_independent_same_gate_despite_new_amplitude_source(self):
        from tests.science_irb_quality_fixture import result_fixture
        from backend.common import digest
        result=result_fixture(self);result.update(dataKind='real',acquiredAt='synthetic-test-only')
        lineage=result['provenance']['scienceIRB']['rabiLineage']
        nodes=result['provenance']['scienceDependencies']['nodes']
        next(n for n in nodes if n['recordId']==lineage['pi01RecordId'])['procedureId']='sq_shape_rabi_amplitude_repeat5'
        proof=result['provenance']['scienceDependencies']
        proof['closureSha256']=digest({k:proof[k] for k in ('schema','policyVersion','kind','scope','inputs','rootReferences','nodes','edges')})
        first=evaluate(result,'sq_drag_irb','real',SCIENCE_REPEAT5_GATES,False)
        self.assertEqual(first['quality']['status'],'unverified');self.assertNotIn('gate_fidelity',first['metrics'])
        verified=evaluate(result,'sq_drag_irb','real',SCIENCE_REPEAT5_GATES,True)
        self.assertEqual(verified['quality']['status'],'passed',verified['quality'])
        changed=deepcopy(result);changed['targetPulseIdentity']['identity']['amplitude']*=2
        failed=evaluate(changed,'sq_drag_irb','real',SCIENCE_REPEAT5_GATES,True)
        self.assertEqual(failed['quality']['status'],'failed');self.assertNotIn('gate_fidelity',failed['metrics'])

    def test_real_native_stage_pair_cannot_change_axis_width_counts_or_candidate(self):
        from adapters.science_shape_repeat5.evidence import validate_stage_pair
        fixture=json.loads((Path(__file__).parent/'fixtures/r23_repeat5_native_results.json').read_text('utf8'))
        first,second=[item['result'] for item in fixture['results']]
        validate_stage_pair(first,second)
        for field,value in [('pulse_length_s',320e-9),('capture_repeats',3),('shots',101),
                            ('scan_axis_values',[0,.2,.4,.6,.8]),('verify_shape_pi_amp',.6)]:
            changed=deepcopy(second);changed['parameters'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_stage_pair(first,changed)
        changed=deepcopy(second);changed['targetPulseIdentity']['identity']['amplitude']/=5
        with self.assertRaises(ValueError):validate_stage_pair(first,changed)

    def test_native_numeric_selection_and_independent_only(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/r23_repeat5_native_results.json').read_text('utf8'))
        self.assertTrue(fixture['fixtureOnly']);self.assertFalse(fixture['physicalAccepted'])
        for item in fixture['results']:
            independent=item['stage']=='verification'
            result=evaluate(item['result'],'sq_shape_rabi_amplitude_repeat5','real',SCIENCE_REPEAT5_GATES,independent=independent)
            self.assertEqual(result['quality']['status'],'passed' if independent else 'unverified',result['quality'])
            self.assertNotIn('gate_fidelity',result['metrics'])

    def test_wrong_n_single_pulse_candidate_and_changed_area_hash_are_rejected(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/r23_repeat5_native_results.json').read_text('utf8'))
        for key in ('n','candidate','area','time'):
            result=deepcopy(fixture['results'][1]['result'])
            if key=='n':result['provenance']['shapeRabiCalibration']['gateRepeats']=1
            elif key=='candidate':result['candidateParameters']['shape_pi_amp']/=5
            elif key=='time':result['provenance']['compiledSequenceEvidence']['rows'][0]['totalControlDurationS']/=5
            else:result['provenance']['compiledSequenceEvidence']['rows'][0]['singlePulse']['areaISeconds']+=1e-9
            checked=evaluate(result,'sq_shape_rabi_amplitude_repeat5','real',SCIENCE_REPEAT5_GATES,independent=True)
            self.assertEqual(checked['quality']['status'],'failed',key)
