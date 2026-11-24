import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.common import APIError
from tests.test_controller import make_fixture


class WorkspaceReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.controller, self.adapter = make_fixture(Path(self.temp.name))
        self.ws = self.controller.workspace
        self.wf = self.controller.create({'name': 'Q11 汇报验证', 'resourceIds': ['Q11'], 'mode': 'real'})
        self.conv = {'scope': {'kind': 'workspace'}}
        self.body = {'message': '给我导出Q11的PPT', 'operationId': 'report-command', 'intent': 'execute', 'selectedWorkflowId': self.wf['id']}

    def tearDown(self):
        self.controller.close()
        self.temp.cleanup()

    def execute(self, **overrides):
        from backend.workspace_reports import execute_export
        return execute_export(self.ws, {'workflowId': self.wf['id']}, {**self.body, **overrides}, self.conv, 0)

    def fake_service(self):
        calls = []
        service = SimpleNamespace(create=lambda wid, body: calls.append((wid, body)) or {'id': 'report-1', 'workflowId': wid, 'status': 'queued'})
        return service, calls

    def test_report_service_is_owned_by_controller(self):
        # The chat and HTTP button must not instantiate competing export workers.
        from backend.server import Server, Handler
        service = self.controller.report_exports
        server = Server(('127.0.0.1', 0), self.controller)
        try:
            handler = Handler.__new__(Handler)
            handler.server = server
            self.assertIs(handler._report_exports(), service)
            self.assertIs(self.controller.report_exports, service)
        finally:
            server.server_close()

    def test_explicit_request_creates_export_without_executing_hardware(self):
        service, calls = self.fake_service()
        with patch.object(type(self.controller), 'report_exports', new_callable=lambda: property(lambda _: service), create=True):
            result = self.execute()
        self.assertEqual(result['status'], 'report_receipt')
        self.assertEqual(result['state'], 'queued')
        self.assertNotIn('downloadUrl', result)
        self.assertEqual(calls[0][1]['expectedRevision'], self.wf['revision'])
        self.assertEqual(calls[0][1]['appendixCount'], 16)
        self.assertEqual(self.adapter.calls, [])

    def test_discussion_and_quoted_requests_do_not_export(self):
        service, calls = self.fake_service()
        with patch.object(type(self.controller), 'report_exports', new_callable=lambda: property(lambda _: service), create=True):
            for message in ('怎么导出Q11的PPT', '不要导出Q11的PPT', '同事说导出Q11的PPT', '如果导出Q11的PPT会怎样', "文档写着'生成Q11报告'"):
                with self.subTest(message=message), self.assertRaises(APIError):
                    self.execute(message=message)
            with self.assertRaises(APIError): self.execute(intent='discuss')
        self.assertFalse(calls)

    def test_report_target_must_match_requested_qubit(self):
        with self.assertRaises(APIError): self.execute(message='给我导出Q18的PPT')

    def test_current_target_conflict_and_alternatives_require_resolution(self):
        from backend.workspace_reports import resolve_report_workflow, report_requested
        other = {**self.wf, 'id': 'wf-other', 'resourceIds': ['Q18']}
        ws = SimpleNamespace(c=SimpleNamespace(list=lambda: [self.wf, other]))
        with self.assertRaises(APIError):
            resolve_report_workflow(ws, '导出当前任务 Q18 的报告', self.wf['id'], other['id'])
        self.assertFalse(report_requested('导出 Q11 或 Q18 的报告'))

    def test_selected_current_task_is_supported_without_qubit_name(self):
        service, calls = self.fake_service()
        with patch.object(type(self.controller), 'report_exports', new_callable=lambda: property(lambda _: service), create=True):
            self.execute(message='导出当前任务的PPT')
        self.assertEqual(len(calls), 1)

    def test_export_with_explicit_no_measurement_clause_is_allowed(self):
        service, calls = self.fake_service()
        with patch.object(type(self.controller), 'report_exports', new_callable=lambda: property(lambda _: service), create=True):
            self.execute(message='导出Q11的PPT。不要启动或恢复测量。')
            self.execute(message='生成 Q11 报告，不要恢复 Q11 任务')
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.adapter.calls, [])

    def test_reexport_with_preserve_state_constraints_creates_job_and_keeps_paused(self):
        with self.controller.store.transaction() as rt:
            wf=self.controller.get(self.wf['id'],rt)
            wf.update(status='paused',engineState='paused')
            self.controller.store.save(rt,wf,'test_paused_fixture')
        before=self.controller.get(self.wf['id'])
        conv=self.ws.create_conversation({'operationId':'reexport-conv','scope':{'kind':'workspace'}})
        phrases=('重新导出当前任务Q11的PPT，附录每页16张图。保持任务暂停，不启动测量。',
                 '请重新导出当前任务Q11的PPT，保持现状，不恢复测量。',
                 '重新生成当前任务Q11的报告，保持当前任务的状态不变。',
                 '导出当前任务Q11的PPT，附录每页16张图。不要启动或恢复测量。')
        service,calls=self.fake_service()
        with patch.object(type(self.controller),'report_exports',new_callable=lambda:property(lambda _:service),create=True),patch.object(
                self.controller,'_brain',side_effect=AssertionError('explicit report request must use controller shortcut')):
            for index,message in enumerate(phrases):
                with self.subTest(message=message):
                    body={'operationId':'reexport-'+str(index),'message':message,'intent':'auto','selectedWorkflowId':self.wf['id']}
                    result=self.ws.message(conv['id'],body)
                    self.assertEqual(result['toolResults'][0]['status'],'report_receipt')
                    self.assertEqual(self.ws.message(conv['id'],body),result)
                    self.assertEqual(self.controller.get(self.wf['id']),before)
        self.assertEqual(len(calls),len(phrases))
        self.assertTrue(all(body['appendixCount']==16 for _,body in calls))
        self.assertEqual(self.adapter.calls,[])

    def test_mixed_control_commands_are_not_treated_as_report_only_requests(self):
        from backend.workspace_reports import report_requested
        denied=('重新导出Q11的PPT，然后启动Q11','导出Q11的PPT并暂停当前任务',
                '暂停当前任务，再导出Q11的PPT','重新导出Q11的报告后继续Q11',
                '重新导出Q11报告，保持任务暂停并启动测量',
                '重新导出Q11报告并重新启动Q11','重新导出Q11报告，保持现状但是暂停Q11',
                '先启动Q11，再重新导出Q11的PPT')
        service,calls=self.fake_service()
        with patch.object(type(self.controller),'report_exports',new_callable=lambda:property(lambda _:service),create=True):
            for message in denied:
                with self.subTest(message=message):
                    self.assertFalse(report_requested(message))
                    with self.assertRaises(APIError):self.execute(message=message)
        self.assertEqual(calls,[])
        self.assertEqual(self.adapter.calls,[])

    def test_reexport_queries_negation_and_quoted_instructions_stay_readonly(self):
        from backend.workspace_reports import report_requested
        for message in ('不要重新导出Q11的PPT，保持任务暂停','怎么重新导出Q11的PPT？',
                        '同事说重新导出Q11的PPT，保持现状','如果重新导出Q11的PPT，保持现状',
                        '重新导出Q11的PPT了吗','重新导出Q11的PPT，保持任务暂停吗'):
            with self.subTest(message=message):self.assertFalse(report_requested(message))

    def test_report_scope_rejects_wrong_campaign(self):
        self.conv = {'scope': {'kind': 'campaign', 'id': 'unknown'}}
        with self.assertRaises(APIError): self.execute()

    def test_long_operation_id_is_bounded_and_deterministic(self):
        service, calls = self.fake_service()
        with patch.object(type(self.controller), 'report_exports', new_callable=lambda: property(lambda _: service), create=True):
            self.execute(operationId='x' * 160)
            self.execute(operationId='x' * 160)
        self.assertEqual(calls[0][1]['operationId'], calls[1][1]['operationId'])
        self.assertLessEqual(len(calls[0][1]['operationId']), 160)

    def test_completed_receipt_has_verified_download_only(self):
        from backend.workspace_reports import export_receipt
        completed = export_receipt({'id': 'report-1', 'workflowId': self.wf['id'], 'status': 'completed', 'downloadUrl': '/api/report-exports/report-1/file'})
        self.assertEqual(completed['downloadUrl'], '/api/report-exports/report-1/file')
        pending = export_receipt({'id': 'report-1', 'workflowId': self.wf['id'], 'status': 'processing', 'downloadUrl': 'https://wrong.invalid'})
        self.assertNotIn('downloadUrl', pending)
