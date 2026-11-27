import copy
import unittest

from backend.human_reports import reader_model
from tests.test_report_accepted_frequency import snapshot


def smart_report():
    report = snapshot()
    wf = report['workflow']
    wf.update(smartRun={'schema':'q11-full-same-gate/1'}, policy='autopilot')
    item = report['nodes'][0]
    node = item['node']
    attempt = item['attempts'][0]
    node.pop('humanAcceptedAt')
    node.pop('humanAcceptedAttemptId')
    attempt.pop('humanAcceptedAt')
    node.update(resourceId='Q11', parameterVersions={'readout_hz': 3})
    value = node['result']['candidateParameters']['readout_hz']
    reference = {'id':'verified-artifact','kind':'json','name':'platform_analysis.json'}
    node['result']['artifacts'] = [reference]
    attempt['result'] = copy.deepcopy(node['result'])
    item['decisions'] = [{'source':'rule', 'action':'commit', 'createdAt':'2026-11-25T17:45:53Z',
                          'id':'d1','attemptId':attempt['id'],'nodeId':node['id'],
                          'evidenceIds':[reference['id']], 'parameterChanges':{'readout_hz':value}}]
    report['committedParameters'] = {'schema':'qcal-report-committed-parameters/1',
                                      'resources':{'Q11':{'readout_hz':{'value':value,'revision':3}}}}
    return report


class ReportControllerCommit(unittest.TestCase):
    def test_primary_metric_identifies_the_current_committed_value(self):
        report = smart_report()
        original = copy.deepcopy(report)
        row = reader_model(report)['nodes'][0]
        self.assertIn('控制器已提交 7.00961 GHz', row['calibrationSummary'])
        self.assertNotIn('人工', row['calibrationSummary'])
        self.assertEqual(row['metrics'][0]['label'], '已提交读取腔频率')
        self.assertIsNone(row['calibration']['accepted'])
        self.assertEqual(row['calibration']['controllerCommit']['decisionId'], 'd1')
        self.assertEqual(report, original)

    def test_requires_the_current_resource_version_and_two_linked_captures(self):
        for change in ('other-node','other-attempt','another-revision','other-value','other-artifacts',
                       'no-decision','no-snapshot','partial','same-operation','other-resource','wrong-policy','ambiguous'):
            with self.subTest(change=change):
                report = smart_report()
                item = report['nodes'][0]
                d = item['decisions'][0]
                version = report['committedParameters']['resources']['Q11']['readout_hz']
                if change=='other-node': d['nodeId']='Q18:readout'
                if change=='other-attempt': d['attemptId']='a4'
                if change=='another-revision': version['revision']+=1
                if change=='other-value': version['value']+=50000
                if change=='other-artifacts': d['evidenceIds']=['old-artifact']
                if change=='no-decision': item['decisions']=[]
                if change=='no-snapshot': report.pop('committedParameters')
                if change=='partial': item['node']['result']['selectionResult']['partial']=True
                if change=='same-operation': item['attempts'][0]['verificationOperationId']='measure'
                if change=='other-resource': item['node']['resourceId']='Q18'
                if change=='wrong-policy': report['workflow']['policy']='manual'
                if change=='ambiguous': item['decisions'].append(dict(d,id='d2'))
                row = reader_model(report)['nodes'][0]
                self.assertNotIn('控制器已提交', row['calibrationSummary'])
                self.assertFalse(row['calibration'].get('controllerCommit'))
