import unittest
from copy import deepcopy

from backend.common import APIError
from backend.decision_context import build_decision_context
from backend.smart_evidence_projection import compact_block_evidence, check_shown_evidence, fit_final_input


def capture(prefix):
    def item(name, kind):
        return {'id':prefix+name,'kind':kind,'name':name,'sha256':'a'*64}
    meta = [item('raw_scan.npz','raw'),item('sampling_receipt.json','json'),item('platform_analysis.json','json')]
    blocks = [item(f'point_{i:04d}_block_000.npz','raw') for i in range(36)]
    return {'dataKind':'real','metrics':{'cycles':3.1},'artifacts':meta+blocks}


class SmartBlockCatalog(unittest.TestCase):
    def test_final_model_checkpoint_and_current_evidence_survive_large_optional_history(self):
        required = {'modelRecovery':{'requestId':'active','lastError':{'code':'BAD_PARAMETERS'}},
                    'observations':{'result':{'metrics':{'contrast_snr':4.2}}},
                    'allowedEvidenceIds':['current'],'parameterBounds':{'shots':{'max':2000}}}
        body = {**deepcopy(required),'priorFailureContext':{'cases':['a'*198100]}}
        trimmed = fit_final_input(body)
        self.assertEqual({k:trimmed[k] for k in required},required)
        self.assertEqual(trimmed['optionalContextOmitted'],['priorFailureContext'])
        self.assertIn('priorFailureContext',body)

    def test_selection_and_verification_keep_own_aggregate_evidence(self):
        raw = dict(capture('verify-'),selectionResult=capture('select-'))
        body = build_decision_context({'observations':{'result':raw},'history':[]})
        original = deepcopy(body)
        view = compact_block_evidence(body)
        self.assertEqual(body,original)
        self.assertEqual(len(view['allowedEvidenceIds']),6)
        self.assertEqual(view['modelEvidenceCatalog']['roles']['selectionResult']['individualBlockFiles'],36)
        self.assertEqual(view['observations']['result']['metrics'],raw['metrics'])
        check_shown_evidence(view,{'evidenceIds':['select-raw_scan.npz','verify-sampling_receipt.json']})
        with self.assertRaises(APIError):
            check_shown_evidence(view,{'evidenceIds':['select-point_0000_block_000.npz']})

    def test_missing_aggregate_receipt_and_unknown_names_preserve_originals(self):
        raw = capture('selection-')
        raw['artifacts'].pop(1)
        body = build_decision_context({'observations':{'result':raw},'history':[]})
        self.assertIs(compact_block_evidence(body),body)
        raw = capture('selection-')
        raw['artifacts'].append({'name':'unrecognized.npz','id':'different-evidence','kind':'raw','sha256':'b'*64})
        view = compact_block_evidence(build_decision_context({'observations':{'result':raw},'history':[]}))
        self.assertIn('different-evidence',view['allowedEvidenceIds'])
