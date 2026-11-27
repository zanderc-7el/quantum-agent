"""Numerical-only cavity boundary tests; no devices or acquisition dispatch."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NUMERIC = importlib.util.find_spec('numpy') and importlib.util.find_spec('scipy')
if NUMERIC:
    import numpy as np
    from adapters.arclight_analysis_v3 import platform_analysis as revised
    from adapters.arclight_runtime import platform_analysis as legacy


def load_tests(loader, tests, pattern):
    if NUMERIC:
        return tests
    class PinnedCavityAnalysis(unittest.TestCase):
        def runTest(self):
            profile = json.loads((ROOT / 'adapters/arclight_profile.json').read_text('utf-8'))
            code = 'import sys,unittest;sys.path.insert(0,sys.argv[1]);r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName(sys.argv[2]));raise SystemExit(not r.wasSuccessful())'
            run = subprocess.run([profile['python'], '-I', '-B', '-c', code, str(ROOT),
                                  'tests.test_cavity_repeat_boundary'], capture_output=True,
                                 text=True, encoding='utf-8', errors='replace', timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + '\n' + run.stderr)
    return unittest.TestSuite([PinnedCavityAnalysis()])


class CavityRepeatBoundary(unittest.TestCase):
    def data(self, axis, peaks):
        records, means = [], []
        for capture, peak in enumerate(peaks):
            for index, frequency in enumerate(axis):
                records.append({'repeat_index': capture, 'cfg.Q1.para.probFreq': float(frequency),
                                'cfg.Q1.para.probCarryScale': .1})
                means.append(1 - .2 * np.exp(-((index - peak) / .5) ** 2))
        iq = np.asarray(means)[:, None] + np.tile([-1e-5, 1e-5], 32)[None, :] + 0j
        return records, iq, None, {'kind': 's21_power', 'acquisitions_per_point': 1}

    def result(self, axis, peaks, module=None):
        return (module or revised)._cavity(*self.data(axis, peaks))

    def test_exact_two_grid_steps_at_7ghz_are_not_rejected_by_one_ulp(self):
        # Same arithmetic grid as the archived Q11 31-point power scan.
        axis = 7007576666.666666 + np.arange(31) * (4000000 / 30)
        spread = axis[17] - axis[15]
        threshold = 2 * np.median(np.diff(axis))
        self.assertGreater(spread, threshold)
        self.assertLessEqual(spread - threshold, np.spacing(axis[-1]))
        old = self.result(axis, [17, 15], legacy)
        new = self.result(axis, [17, 15])
        self.assertFalse(old[2]['accepted'])
        self.assertTrue(new[2]['accepted'], new[2])
        self.assertEqual(new[0], old[0])
        self.assertEqual(new[1], old[1])
        self.assertEqual(new[2]['repeat_centers_hz'], old[2]['repeat_centers_hz'])
        for a, b in zip(new[3], old[3]):
            for key in ('x', 'y', 'sem'):
                np.testing.assert_array_equal(a[key], b[key])

    def test_three_actual_grid_steps_still_fail(self):
        axis = 7007576666.666666 + np.arange(31) * (4000000 / 30)
        self.assertFalse(self.result(axis, [18, 15])[2]['accepted'])

    def test_nonuniform_two_indices_may_exceed_the_physical_hz_limit(self):
        axis = 7e9 + np.array([0, 1e5, 2e5, 3e5, 4e5, 9e5, 10e5, 11e5, 12e5])
        self.assertFalse(self.result(axis, [3, 5])[2]['accepted'])
        self.assertTrue(self.result(axis, [1, 3])[2]['accepted'])

    def test_nonuniform_axis_has_no_relative_frequency_tolerance(self):
        axis = 7e9 + np.array([0, 1e5, 2e5, 3e5 + .001, 4e5 + .001, 5e5 + .001, 6e5 + .001])
        self.assertFalse(self.result(axis, [1, 3])[2]['accepted'])

    def test_existing_50khz_floor_is_preserved_but_real_excess_rejected(self):
        axis = 7e9 + np.arange(11) * 10000.
        self.assertTrue(self.result(axis, [2, 7])[2]['accepted'])
        axis[7] += 1.
        self.assertFalse(self.result(axis, [2, 7])[2]['accepted'])

    def test_boundary_and_single_capture_are_still_rejected(self):
        axis = np.linspace(7e9, 7e9 + 1e6, 11)
        self.assertFalse(self.result(axis, [0, 1])[2]['accepted'])
        self.assertFalse(self.result(axis, [5])[2]['accepted'])
        records, iq, blocks, job = self.data(axis, [5, 5])
        iq[:] = 1 + 0j
        self.assertFalse(revised._cavity(records, iq, blocks, job)[2]['accepted'])

    def test_other_accepted_archive_semantics_do_not_change(self):
        axis = np.linspace(7e9, 7e9 + 1e6, 11)
        for peaks in ([4, 4], [4, 5], [4, 6], [3, 6]):
            with self.subTest(peaks=peaks):
                old = self.result(axis, peaks, legacy)
                new = self.result(axis, peaks)
                self.assertEqual(new[:3], old[:3])


if __name__ == '__main__':
    unittest.main()
