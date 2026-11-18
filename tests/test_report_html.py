import tempfile
from pathlib import Path
import unittest

from backend.server import html_report
from backend.common import APIError


class ReportHTMLTests(unittest.TestCase):
    def test_both_acquisitions_retained_and_stale_evidence_labelled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plot.png'
            path.write_bytes(b'test image bytes')
            requested = []

            class Controller:
                def artifact(self, artifact_id):
                    requested.append(artifact_id)
                    if artifact_id == 'missing':
                        raise APIError('NOT_FOUND', 'missing <image>')
                    return path, {}

            def artifact(key):
                return {'id': key, 'kind': 'image', 'name': key + '<plot>', 'sha256': key + '-sha'}

            selection = {'metrics': {'selectionMetric': 1}, 'artifacts': [artifact('selection')],
                         'parameters': {'readout_amp': .2}, 'provenance': {'operationId': 'a/measure'}}
            result = {'quality': {'status': 'passed'}, 'metrics': {'verifyMetric': 2},
                      'artifacts': [artifact('verify'), artifact('missing')],
                      'selectionResult': selection, 'provenance': {'operationId': 'a/verify'}}
            report = {'workflow': {'name': 'Q11 <test>', 'mode': 'real', 'target': 'Q11'},
                      'workflowComplete': False, 'nodes': [{'node': {'qubits': 'Q11',
                          'label': 'S21', 'engineState': 'stale'}, 'decisions': [],
                          'attempts': [{'number': 1, 'state': 'verified', 'result': result}]}]}
            rendered = html_report(Controller(), report).decode()
            self.assertEqual(requested, ['selection', 'verify', 'missing'])
            self.assertEqual(rendered.count('<img '), 2)
            for text in ('候选测量', '独立复测', 'selectionMetric', 'verifyMetric',
                         'a/measure', 'a/verify', '当前节点需复测', '尚未全部通过',
                         'Q11 &lt;test&gt;', '产物校验失败'):
                self.assertIn(text, rendered)
            self.assertNotIn('Q11 <test>', rendered)


if __name__ == '__main__':
    unittest.main()
