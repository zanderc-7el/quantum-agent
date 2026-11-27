import json
import tempfile
import unittest
from pathlib import Path

from adapters.progress_delivery import read_event_batch


class ProgressDeliveryTests(unittest.TestCase):
    def read(self, events, **kwargs):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'events.jsonl'
            path.write_bytes(b''.join(json.dumps(x).encode() + b'\n' for x in events))
            return read_event_batch(path, 0, **kwargs)

    def test_backlog_delivers_current_row_once_preserving_lifecycle(self):
        rows = [{'event': 'row_acquired', 'completedRows': i, 'jobIndex': 1} for i in range(122)]
        _, events = self.read([{'event': 'job_start'}, *rows, {'event': 'acquisition_complete'}])
        self.assertEqual([e['event'] for e in events], ['job_start', 'row_acquired', 'acquisition_complete'])
        self.assertEqual(events[1]['completedRows'], 121)

    def test_faults_and_job_boundaries_are_never_swallowed(self):
        events = [{'event': 'row_acquired', 'jobIndex': 1, 'completedRows': 12},
                  {'event': 'fault', 'message': 'ADC failed'},
                  {'event': 'row_acquired', 'jobIndex': 2, 'completedRows': 1},
                  {'event': 'row_acquired', 'jobIndex': 3, 'completedRows': 2}]
        self.assertEqual(self.read(events)[1], events)

    def test_batch_bound_yields_to_cancellation(self):
        events = [{'event': 'row_acquired', 'completedRows': i} for i in range(1000)]
        _, batch = self.read(events, max_lines=10)
        self.assertEqual(batch[-1]['completedRows'], 9)

    def test_partial_line_is_retained_for_next_poll(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'events.jsonl'
            first = b'{"event":"job_start"}\n'
            path.write_bytes(first + b'{"event":"row_acquired"')
            offset, batch = read_event_batch(path, 0)
            self.assertEqual(offset, len(first))
            self.assertEqual(len(batch), 1)
            with path.open('ab') as f:
                f.write(b',"completedRows":1}\n')
            self.assertEqual(read_event_batch(path, offset)[1][0]['completedRows'], 1)

    def test_file_is_not_changed_and_malformed_complete_line_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'events.jsonl'
            raw = b'not-json\n'
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                read_event_batch(path, 0)
            self.assertEqual(path.read_bytes(), raw)
