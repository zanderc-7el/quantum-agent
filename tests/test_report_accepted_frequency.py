"""Reader-facing calibration values retain selection, verification and acceptance roles."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from backend.human_reports import reader_model, html_report
from backend.report_exports import ReportExportService

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    selection = {'dataKind': 'real', 'provenance': {'freshAcquisition': True, 'operationId': 'measure'},
                 'acquiredAt': '2026-11-25T17:40:00Z', 'quality': {'status': 'unverified'},
                 'candidateParameters': {'readout_hz': 7009608000.0}, 'metrics': {}, 'artifacts': []}
    result = {'dataKind': 'real', 'provenance': {'freshAcquisition': True, 'operationId': 'verify'},
              'acquiredAt': '2026-11-25T17:44:00Z', 'quality': {'status': 'passed'},
              'candidateParameters': {'readout_hz': 7009610000.0},
              'verificationCandidateParameters': {'readout_hz': 7009620000.0},
              'selectionResult': selection, 'metrics': {'contrast_snr': 44.63654}, 'artifacts': []}
    stamp = '2026-11-25T17:45:53Z'
    node = {'id': 'Q11:readout', 'procedureId': 'ro_resonator_survey', 'label': '读取腔定频',
            'qubits': 'Q11', 'engineState': 'verified', 'latestAttemptId': 'a3', 'attempt': 3,
            'humanAcceptedAttemptId': 'a3', 'humanAcceptedAt': stamp, 'result': result}
    attempt = {'id': 'a3', 'nodeId': node['id'], 'number': 3, 'state': 'verified',
               'operationId': 'measure', 'verificationOperationId': 'verify',
               'humanAcceptedAt': stamp, 'result': copy.deepcopy(result)}
    return {'schema': 'qcal-report/1', 'generatedAt': '2026-11-25T19:51:51Z',
            'workflow': {'id': 'wf', 'target': 'Q11', 'mode': 'real', 'revision': 2149},
            'nodes': [{'node': node, 'attempts': [attempt], 'decisions': []}]}


def drive_snapshot(procedure='sq_spectroscopy'):
    """Synthetic roles using the keys recorded in report-3077.../report.json.

    Deliberately distinct stage values detect replacement by the last fit. This
    fixture is not a new measurement or a replay of the physical report itself.
    """
    report = snapshot()
    item = report['nodes'][0]
    node, attempt = item['node'], item['attempts'][0]
    amplitude = procedure == 'sq_rabi_amplitude'
    key = 'drive_amp' if amplitude else 'drive_hz'
    selected, verified, accepted_value = ((0.8655218444566908, 0.8448499519798696, 0.855)
                                           if amplitude else (4858900000., 4858950000., 4858925000.))
    node.update(id='Q11:rabi' if amplitude else 'Q11:spectroscopy', procedureId=procedure,
                label='幅度 Rabi' if amplitude else '比特定频')
    attempt['nodeId'] = node['id']
    result = node['result']
    result['candidateParameters'] = {key: accepted_value}
    result['verificationCandidateParameters'] = {key: verified}
    result['selectionResult']['candidateParameters'] = {key: selected}
    result['metrics'] = ({'contrast_snr': 12.564974744282605, 'r_squared': 0.9242364292263199}
                         if amplitude else {'peak_snr': 9.74052106917885})
    attempt['result'] = copy.deepcopy(result)
    return report


DRIVE_CASES = (
    ('sq_spectroscopy', 'drive_hz', 'valueHz', '已接受比特频率', '4.858925 GHz'),
    ('sq_rabi_amplitude', 'drive_amp', 'valueAu', '已接受 π 脉冲幅度', '0.855 a.u.'),
)


class AcceptedFrequencyReports(unittest.TestCase):
    def test_accepted_frequency_is_primary_and_preserves_three_distinct_sources(self):
        report = snapshot(); before = copy.deepcopy(report)
        row = reader_model(report)['nodes'][0]
        self.assertEqual(row['metrics'][0], {'key': 'readout_hz', 'label': '已接受读取腔频率', 'value': '7.00961 GHz'})
        self.assertEqual(row['calibration']['selection']['valueHz'], 7009608000.0)
        self.assertEqual(row['calibration']['verification']['valueHz'], 7009620000.0)
        self.assertEqual(row['calibration']['accepted']['valueHz'], 7009610000.0)
        self.assertEqual(row['calibration']['accepted']['attemptId'], 'a3')
        self.assertIn('首测候选 7.009608 GHz', row['calibrationSummary'])
        self.assertIn('独立复测候选 7.00962 GHz', row['calibrationSummary'])
        self.assertIn('人工接受 7.00961 GHz', row['calibrationSummary'])
        self.assertEqual(report, before)

    def test_html_summary_shows_frequency_without_replacing_snr(self):
        class Controller: root = ROOT
        output = html_report(Controller(), snapshot()).decode()
        self.assertIn('已接受读取腔频率 7.00961 GHz', output)
        self.assertIn('信噪比 44.64', output)
        self.assertIn('独立复测候选 7.00962 GHz', output)

    def test_quality_pass_without_bound_human_acceptance_is_only_a_candidate(self):
        for change in ('missing_stamp', 'old_attempt', 'different_result', 'different_stamp'):
            with self.subTest(change=change):
                report = snapshot(); item = report['nodes'][0]; node = item['node']; attempt = item['attempts'][0]
                if change == 'missing_stamp': node.pop('humanAcceptedAt')
                if change == 'old_attempt': node['humanAcceptedAttemptId'] = 'a2'
                if change == 'different_result': attempt['result']['candidateParameters']['readout_hz'] = 7.1e9
                if change == 'different_stamp': attempt['humanAcceptedAt'] = '2026-11-25T17:46:00Z'
                row = reader_model(report)['nodes'][0]
                self.assertIsNone(row['calibration']['accepted'])
                self.assertNotIn('已接受', row['metrics'][0]['label'])
                self.assertNotIn('人工接受 7.', row['calibrationSummary'])

    def test_stale_partial_replay_and_failed_result_never_label_frequency_accepted(self):
        for change in ('stale', 'partial', 'replay', 'failed'):
            with self.subTest(change=change):
                report = snapshot(); node = report['nodes'][0]['node']
                if change == 'stale': node['result']['stale'] = True
                if change == 'partial': node['result']['partial'] = True
                if change == 'replay': report['workflow']['mode'] = 'replay'
                if change == 'failed': node['result']['quality']['status'] = 'failed'
                row = reader_model(report)['nodes'][0]
                self.assertIsNone(row['calibration']['accepted'])
                self.assertNotIn('已接受', row['metrics'][0]['label'])

    def test_missing_or_nonfinite_verification_value_is_not_inferred_from_final_candidate(self):
        for value in (None, float('nan'), float('inf'), True, -1):
            with self.subTest(value=value):
                report = snapshot(); result = report['nodes'][0]['node']['result']
                result['verificationCandidateParameters']['readout_hz'] = value
                row = reader_model(report)['nodes'][0]
                self.assertIsNone(row['calibration']['verification'])
                self.assertIsNone(row['calibration']['accepted'])
                self.assertIn('独立复测候选未记录', row['calibrationSummary'])

    def test_incomplete_or_unbound_stage_metadata_cannot_gain_an_accepted_label(self):
        for change in ('selection_partial', 'attempt_partial', 'same_operation', 'invalid_acceptance_time'):
            with self.subTest(change=change):
                report = snapshot(); item = report['nodes'][0]; node = item['node']
                if change == 'selection_partial': node['result']['selectionResult']['partial'] = True
                if change == 'attempt_partial': item['attempts'][0]['result']['partial'] = True
                if change == 'same_operation': node['result']['selectionResult']['provenance']['operationId'] = 'verify'
                if change == 'invalid_acceptance_time': node['humanAcceptedAt'] = 'not-a-date'
                self.assertIsNone(reader_model(report)['nodes'][0]['calibration']['accepted'])


class AcceptedDriveCalibrationReports(unittest.TestCase):
    def test_drive_calibration_has_distinct_selection_verification_and_accepted_roles(self):
        for procedure, key, value_key, label, display in DRIVE_CASES:
            with self.subTest(procedure=procedure):
                report = drive_snapshot(procedure); before = copy.deepcopy(report)
                result = report['nodes'][0]['node']['result']
                row = reader_model(report)['nodes'][0]
                self.assertEqual(row['metrics'][0], {'key': key, 'label': label, 'value': display})
                calibration = row['calibration']
                for role, parameters, source in (
                    ('selection', result['selectionResult']['candidateParameters'],
                     'node.result.selectionResult.candidateParameters.'),
                    ('verification', result['verificationCandidateParameters'],
                     'node.result.verificationCandidateParameters.'),
                    ('accepted', result['candidateParameters'], 'node.result.candidateParameters.'),
                ):
                    self.assertEqual(calibration[role][value_key], parameters[key])
                    self.assertEqual(calibration[role]['sourceField'], source + key)
                self.assertEqual(calibration['selection']['operationId'], 'measure')
                self.assertEqual(calibration['verification']['operationId'], 'verify')
                self.assertEqual(calibration['accepted']['attemptId'], 'a3')
                self.assertEqual(calibration['accepted']['acceptedAt'], report['nodes'][0]['node']['humanAcceptedAt'])
                self.assertIn('首测候选 ', row['calibrationSummary'])
                self.assertIn('独立复测候选 ', row['calibrationSummary'])
                self.assertIn('人工接受 ' + display, row['calibrationSummary'])
                self.assertEqual(report, before)

    def test_frequency_and_amplitude_do_not_exchange_units_or_parameter_roles(self):
        for procedure, key, value_key, label, display in DRIVE_CASES:
            with self.subTest(procedure=procedure):
                report = drive_snapshot(procedure)
                result = report['nodes'][0]['node']['result']
                # A simultaneous, plausible but unrelated parameter is not this
                # method's calibrated result, nor is a generic fit metric.
                wrong_key = 'drive_hz' if key == 'drive_amp' else 'drive_amp'
                for params in (result['candidateParameters'], result['verificationCandidateParameters'],
                               result['selectionResult']['candidateParameters']):
                    params[wrong_key] = 4.95e9 if wrong_key == 'drive_hz' else 0.99
                    params['readout_hz'] = 7.1e9
                result['metrics']['frequency_hz'] = 1234567890.
                report['nodes'][0]['attempts'][0]['result'] = copy.deepcopy(result)
                row = reader_model(report)['nodes'][0]
                self.assertEqual(row['metrics'][0], {'key': key, 'label': label, 'value': display})
                other_unit = 'valueHz' if value_key == 'valueAu' else 'valueAu'
                for role in ('selection', 'verification', 'accepted'):
                    self.assertNotIn(other_unit, row['calibration'][role])

    def test_quality_pass_without_human_acceptance_remains_a_candidate_for_both_methods(self):
        for procedure, key, value_key, _label, _display in DRIVE_CASES:
            with self.subTest(procedure=procedure):
                report = drive_snapshot(procedure); node = report['nodes'][0]['node']
                node.pop('humanAcceptedAt'); node.pop('humanAcceptedAttemptId')
                row = reader_model(report)['nodes'][0]
                self.assertIsNone(row['calibration']['accepted'])
                self.assertIsNotNone(row['calibration']['selection'])
                self.assertIsNotNone(row['calibration']['verification'])
                self.assertNotIn('已接受', row['metrics'][0]['label'])
                self.assertIn('独立复测候选', row['metrics'][0]['label'])
                self.assertNotIn('人工接受 ', row['calibrationSummary'])

    def test_wrong_attempt_node_number_operation_and_stamp_cannot_borrow_acceptance(self):
        changes = ('accepted_attempt', 'latest_attempt', 'node_identity', 'attempt_number',
                   'selection_operation', 'verification_operation', 'same_operation',
                   'acceptance_stamp', 'accepted_value', 'selection_value', 'verification_value')
        for procedure, key, value_key, _label, _display in DRIVE_CASES:
            for change in changes:
                with self.subTest(procedure=procedure, change=change):
                    report = drive_snapshot(procedure); item = report['nodes'][0]
                    node, attempt = item['node'], item['attempts'][0]
                    if change == 'accepted_attempt': node['humanAcceptedAttemptId'] = 'a2'
                    if change == 'latest_attempt': node['latestAttemptId'] = 'missing'
                    if change == 'node_identity': attempt['nodeId'] = 'Q18:other'
                    if change == 'attempt_number': attempt['number'] = 2
                    if change == 'selection_operation': attempt['operationId'] = 'other/measure'
                    if change == 'verification_operation': attempt['verificationOperationId'] = 'other/verify'
                    if change == 'same_operation': node['result']['selectionResult']['provenance']['operationId'] = 'verify'
                    if change == 'acceptance_stamp': attempt['humanAcceptedAt'] = '2026-11-25T17:46:00Z'
                    if change == 'accepted_value': attempt['result']['candidateParameters'][key] *= 1.01
                    if change == 'selection_value': attempt['result']['selectionResult']['candidateParameters'][key] *= 1.01
                    if change == 'verification_value': attempt['result']['verificationCandidateParameters'][key] *= 1.01
                    row = reader_model(report)['nodes'][0]
                    self.assertIsNone(row['calibration']['accepted'])
                    self.assertNotIn('已接受', row['metrics'][0]['label'])
                    self.assertNotIn('人工接受 ', row['calibrationSummary'])

    def test_failed_stale_partial_replay_and_pending_results_are_not_accepted(self):
        for procedure, _key, _value_key, _label, _display in DRIVE_CASES:
            for change in ('failed', 'stale', 'partial', 'pending', 'replay', 'not_fresh', 'selection_partial'):
                with self.subTest(procedure=procedure, change=change):
                    report = drive_snapshot(procedure); node = report['nodes'][0]['node']
                    if change == 'failed': node['result']['quality']['status'] = 'failed'
                    if change in ('stale', 'partial'): node['result'][change] = True
                    if change == 'pending': node['result']['verificationPending'] = True
                    if change == 'replay': report['workflow']['mode'] = 'replay'
                    if change == 'not_fresh': node['result']['provenance']['freshAcquisition'] = False
                    if change == 'selection_partial': node['result']['selectionResult']['partial'] = True
                    row = reader_model(report)['nodes'][0]
                    self.assertIsNone(row['calibration']['accepted'])
                    self.assertNotIn('已接受', row['metrics'][0]['label'])

    def test_zero_nonfinite_bool_and_missing_values_cannot_be_calibration_values(self):
        for procedure, key, value_key, _label, _display in DRIVE_CASES:
            for role in ('selection', 'verification', 'accepted'):
                for invalid in (None, 0, -1, float('nan'), float('inf'), -float('inf'), True, '0.8'):
                    with self.subTest(procedure=procedure, role=role, invalid=invalid):
                        report = drive_snapshot(procedure); item = report['nodes'][0]
                        result = item['node']['result']
                        parameters = (result['selectionResult']['candidateParameters'] if role == 'selection' else
                                      result['verificationCandidateParameters'] if role == 'verification' else
                                      result['candidateParameters'])
                        parameters[key] = invalid
                        item['attempts'][0]['result'] = copy.deepcopy(result)
                        row = reader_model(report)['nodes'][0]
                        self.assertIsNone(row['calibration'][role])
                        self.assertIsNone(row['calibration']['accepted'])
                        self.assertFalse(any(metric['label'].startswith('已接受') for metric in row['metrics']))

    def test_no_fallback_from_other_parameter_or_fit_when_method_candidate_is_missing(self):
        for procedure, key, _value_key, _label, _display in DRIVE_CASES:
            with self.subTest(procedure=procedure):
                report = drive_snapshot(procedure); item = report['nodes'][0]; result = item['node']['result']
                for parameters in (result['candidateParameters'], result['verificationCandidateParameters'],
                                   result['selectionResult']['candidateParameters']):
                    parameters.pop(key)
                    parameters.update(readout_hz=7.00961e9, half_pi_amp=0.4)
                result['metrics'].update(frequency_hz=4.85e9, resonance_hz=4.85e9, pi_amplitude=0.85)
                item['attempts'][0]['result'] = copy.deepcopy(result)
                row = reader_model(report)['nodes'][0]
                self.assertTrue(all(row['calibration'][role] is None for role in ('selection', 'verification', 'accepted')))
                self.assertFalse(any(metric['key'] == key for metric in row['metrics']))
                self.assertNotIn('人工接受 ', row['calibrationSummary'])

    def test_single_acquisition_is_selection_only_not_an_independent_repeat(self):
        for procedure, key, value_key, _label, _display in DRIVE_CASES:
            with self.subTest(procedure=procedure):
                report = drive_snapshot(procedure); item = report['nodes'][0]
                result = item['node']['result']; result.pop('selectionResult')
                # Even an unbound verification field cannot manufacture the
                # absent independently acquired selection/verification pair.
                item['attempts'][0]['result'] = copy.deepcopy(result)
                row = reader_model(report)['nodes'][0]
                self.assertEqual(row['calibration']['selection'][value_key], result['candidateParameters'][key])
                self.assertIsNone(row['calibration']['verification'])
                self.assertIsNone(row['calibration']['accepted'])
                self.assertIn('首测候选', row['metrics'][0]['label'])

    def test_html_and_ppt_job_share_the_same_reader_roles_and_human_primary_values(self):
        # The runner is an isolated fixture: this validates the shared reader
        # handed to the PPT exporter, not physical measurement or PPT layout.
        for procedure, _key, _value_key, label, display in DRIVE_CASES:
            with self.subTest(procedure=procedure), tempfile.TemporaryDirectory() as folder:
                report = drive_snapshot(procedure); before = copy.deepcopy(report)
                expected = reader_model(report)
                html = html_report(SimpleNamespace(root=ROOT), report).decode()
                self.assertIn(label + ' ' + display, html)
                for role in ('首测候选 ', '独立复测候选 ', '人工接受 ' + display):
                    self.assertIn(role, html)
                root = Path(folder); (root / 'data').mkdir(); db_path = root / 'data/platform.sqlite'
                with closing(sqlite3.connect(db_path)) as db, db:
                    db.execute('CREATE TABLE platform_workflows(id TEXT PRIMARY KEY,payload TEXT)')
                    db.execute('INSERT INTO platform_workflows VALUES(?,?)', ('wf', json.dumps(report['workflow'])))
                captured = []
                def run(directory, _job):
                    captured.append(json.loads((directory / 'reader.json').read_text(encoding='utf-8')))
                    output = directory / 'fixture.pptx'; output.write_bytes(b'isolated-ppt-runner-fixture')
                    return output
                controller = SimpleNamespace(root=root, store=SimpleNamespace(path=db_path))
                with patch('backend.report_exports.snapshot', return_value=copy.deepcopy(report)), \
                     patch('backend.report_exports.writing_audit', return_value={'policyVersion':'test-fixture'}), \
                     patch('backend.result_archive.register_report_archive', return_value='fixture-archive'):
                    service = ReportExportService(controller, runner=run)
                    try:
                        job = service.create('wf', {'operationId':'test-op', 'expectedRevision':2149,
                                                    'format':'pptx', 'appendixCount':16})
                        service.shutdown()
                        self.assertEqual(service.get(job['id'])['status'], 'completed')
                    finally:
                        service.shutdown()
                self.assertEqual(captured, [expected])
                self.assertEqual(captured[0]['nodes'][0]['metrics'][0]['label'], label)
                self.assertEqual(captured[0]['nodes'][0]['metrics'][0]['value'], display)
                self.assertEqual(report, before)


if __name__ == '__main__': unittest.main()
