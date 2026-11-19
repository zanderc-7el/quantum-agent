import tempfile
from pathlib import Path
import unittest
import base64

from backend.server import html_report
from backend.common import APIError


class ReportHTMLTests(unittest.TestCase):
    def test_both_acquisitions_retained_and_stale_evidence_labelled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plot.png'
            path.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGZkAAAAASUVORK5CYII='))
            requested = []

            class Controller:
                root = Path(__file__).resolve().parents[1]
                def artifact(self, artifact_id):
                    requested.append(artifact_id)
                    if artifact_id == 'missing':
                        raise APIError('NOT_FOUND', 'missing <image>')
                    return path, {}

            def artifact(key):
                return {'id': key, 'kind': 'image', 'name': key + '<plot>', 'sha256': key + '-sha'}

            selection = {'metrics': {'selectionMetric': 1}, 'artifacts': [artifact('selection')],
                         'dataKind': 'real', 'parameters': {'readout_amp': .2}, 'provenance': {'operationId': 'a/measure', 'freshAcquisition': True}}
            result = {'quality': {'status': 'passed'}, 'metrics': {'verifyMetric': 2},
                      'artifacts': [artifact('verify'), artifact('missing')],
                      'dataKind': 'real', 'selectionResult': selection, 'provenance': {'operationId': 'a/verify', 'freshAcquisition': True}}
            report = {'workflow': {'id': 'fixture', 'revision': 1, 'name': 'Q11 <test>', 'mode': 'real', 'target': 'Q11 <test>'},
                      'generatedAt': '2026-11-19T00:00:00Z',
                      'workflowComplete': False, 'nodes': [{'node': {'id': 's21', 'qubits': 'Q11', 'latestAttemptId': 'a', 'result': result,
                          'label': 'S21', 'engineState': 'stale'}, 'decisions': [],
                          'attempts': [{'id': 'a', 'number': 1, 'state': 'verified', 'result': result}]}]}
            rendered = html_report(Controller(), report).decode()
            self.assertEqual(requested, ['selection', 'verify', 'missing'])
            self.assertEqual(rendered.count('<img '), 2)
            for text in ('首测', '独立复测', 'selectionMetric', 'verifyMetric',
                         'a/measure', 'a/verify', '前置参数已变化，需复测', '0 项通过',
                         'Q11 &lt;test&gt;', '该图暂时无法读取', '2026-11-19 08:00:00（北京时间）'):
                self.assertIn(text, rendered)
            self.assertNotIn('Q11 <test>', rendered)


if __name__ == '__main__':
    unittest.main()
