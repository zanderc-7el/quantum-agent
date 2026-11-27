"""Stored verification criteria remain reconstructible for downstream evidence."""
import unittest
from copy import deepcopy
from backend import quality


class ScienceQualityRuleContracts(unittest.TestCase):
    def test_candidate_source_expected_rules_include_independent_capture_checks(self):
        self.assertTrue(callable(getattr(quality, 'criteria_for', None)))
        base=deepcopy(quality.SCIENCE_GATES)
        expected=quality.criteria_for('sq_shape_rabi_amplitude',base,independent=True)
        self.assertEqual(expected['candidate_capture_agreement'],{'min':0,'max':1})
        self.assertEqual(expected['candidate_points_acquired'],{'min':1})
        self.assertNotIn('candidate_capture_agreement',quality.criteria_for('sq_shape_rabi_amplitude',base,independent=False))
        self.assertEqual(base,quality.SCIENCE_GATES)
        expected['r_squared']['min']=0
        self.assertEqual(base['sq_shape_rabi_amplitude']['r_squared']['min'],.8)

    def test_existing_legacy_and_non_candidate_verification_keep_their_frozen_rules(self):
        self.assertTrue(callable(getattr(quality, 'criteria_for', None)))
        self.assertEqual(quality.criteria_for('sq_rb',quality.GATES,independent=True),quality.GATES['sq_rb'])
        self.assertEqual(quality.criteria_for('sq_shape_half_verify',quality.SCIENCE_GATES,independent=True),
                         quality.SCIENCE_GATES['sq_shape_half_verify'])
