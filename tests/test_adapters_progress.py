"""Progress uses actual native sweep axes; no device imports or execution."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_f8_progress_test',ROOT/'adapters/f8_runtime/progress.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ExactProgress(unittest.TestCase):
    def test_actual_cartesian_axes_and_capture_repeat(self):
        tracker=module.SweepProgress({'frequency':range(41),'CaptureRepeat':range(2)})
        self.assertEqual(tracker.state()['totalRows'],82)
        first=tracker.acquired()
        self.assertEqual(first['rowNumber'],1)
        self.assertAlmostEqual(first['progress'],100/82)
        for _ in range(81):tracker.acquired()
        self.assertEqual(tracker.complete()['progress'],100)
        with self.assertRaises(ValueError):tracker.acquired()

    def test_native_repeat_executor_and_ge_axis(self):
        tracker=module.SweepProgress({'ge':range(2),'probCarryScale':range(15)},native_repeats=3)
        self.assertEqual(tracker.state()['sweepRows'],30)
        self.assertEqual(tracker.state()['totalRows'],90)
        for _ in range(31):value=tracker.acquired()
        self.assertEqual(value['rowNumber'],31)
        with self.assertRaises(ValueError):tracker.complete()

    def test_rb_job_progress_is_explicit_not_a_time_estimate(self):
        tracker=module.SweepProgress({'repeat_index':[4],'gate_num':[1,2,3,4,6,8,12,16],'CaptureRepeat':[0,1]},job_index=3,job_count=62)
        for _ in range(8):value=tracker.acquired()
        self.assertEqual(value['totalRows'],16)
        self.assertEqual(value['jobProgress'],50)
        self.assertAlmostEqual(value['progress'],100*2.5/62)
        self.assertEqual(value['jobIndex'],3)
        self.assertIn('actual_acquired_rows',value['progressBasis'])
        self.assertIn('8/16',value['message'])


if __name__=='__main__':unittest.main()
