"""Scoped natural commands: temporary ledger and fake adapter, no live I/O."""
import unittest

from backend.common import APIError
from tests import test_workspace as fixtures


class ScopedWorkflowCommands(unittest.TestCase):
    setUp = fixtures.WorkspaceContracts.setUp
    tearDown = fixtures.WorkspaceContracts.tearDown

    def paused(self, name='八号机 Q11 · 单比特测控', policy='threshold'):
        workflow = self.c.create({'name': name, 'mode': 'real', 'resourceIds': ['Q11'], 'policy': policy})
        with self.c.store.transaction() as rt:
            workflow.update(status='paused', engineState='paused', currentNodeId='Q11:s02_qubit_frequency')
            self.c.store.save(rt, workflow, 'test_paused', {})
        return workflow

    def long_command(self, workflow):
        return (f'继续已暂停的「{workflow["name"]}」真机任务，任务ID {workflow["id"]}。'
                '沿用该任务已确认的八号机接线和参数，恢复当前比特定频采集。'
                '此次明确授权实际测量，请执行并返回任务状态，不要新建或恢复其他任务。')

    def send(self, text, operation, selected=None, intent='auto'):
        conversation = self.w.create_conversation({'operationId': 'c-' + operation, 'scope': {'kind': 'workspace'}})
        self.c._brain = lambda *a, **k: {'message': '仅讨论。', 'proposals': []}
        return self.w.message(conversation['id'], {'operationId': operation, 'message': text,
            'intent': intent, **({'selectedWorkflowId': selected} if selected else {})})

    def test_actual_ui_long_request_resumes_only_exact_named_id(self):
        target = self.paused()
        other = self.paused()
        result = self.send(self.long_command(target), 'long')
        self.assertEqual(self.c.get(target['id'])['status'], 'queued')
        self.assertEqual(self.c.get(other['id'])['status'], 'paused')
        self.assertEqual(result['toolResults'][0]['workflowId'], target['id'])
        self.assertTrue(result['actionTrace'][0]['executed'])
        self.assertEqual(len(self.c.list()), 2)
        self.assertEqual(self.adapter.calls, [])

    def test_exact_id_or_quoted_label_can_name_one_existing_task(self):
        for index, template in enumerate(('继续任务ID {id}', '恢复任务 {id}',
                '继续「{name}」', '恢复"{name}"，沿用当前任务的接线和参数，不要操作其他任务')):
            target = self.paused(name='Q11 task ' + str(index))
            result = self.send(template.format(**target), 'target-' + str(index))
            self.assertEqual(self.c.get(target['id'])['status'], 'queued')
            self.assertEqual(result['toolResults'][0]['workflowId'], target['id'])
        self.assertEqual(self.adapter.calls, [])

    def test_duplicate_label_needs_id_or_matching_selection(self):
        one, two = self.paused(), self.paused()
        text = f'继续「{one["name"]}」，不要操作其他任务'
        self.send(text, 'ambiguous')
        self.assertEqual(self.c.get(one['id'])['status'], 'paused')
        self.assertEqual(self.c.get(two['id'])['status'], 'paused')
        result = self.send(text, 'selected', selected=two['id'])
        self.assertEqual(self.c.get(one['id'])['status'], 'paused')
        self.assertEqual(self.c.get(two['id'])['status'], 'queued')
        self.assertEqual(result['toolResults'][0]['workflowId'], two['id'])

    def test_explicit_target_must_agree_with_id_label_resources_and_selection(self):
        one = self.paused()
        other = self.paused(name='Q11 another task')
        texts = (f'继续「{one["name"]}」，任务ID {other["id"]}',
                 f'继续任务ID {one["id"]}，恢复Q18',
                 f'继续任务ID {one["id"]}，任务ID {other["id"]}',
                 '继续任务ID wf-' + 'f' * 32)
        for index, text in enumerate(texts):
            self.send(text, 'mismatch-' + str(index), selected=one['id'])
            self.assertEqual(self.c.get(one['id'])['status'], 'paused')
            self.assertEqual(self.c.get(other['id'])['status'], 'paused')
        self.send(f'继续「{one["name"]}」', 'wrong-selected', selected=other['id'])
        self.assertEqual(self.c.get(one['id'])['status'], 'paused')
        self.assertEqual(self.c.get(other['id'])['status'], 'paused')

    def test_quotes_conditions_negation_and_extra_actions_never_grant_permission(self):
        target = self.paused()
        command = self.long_command(target)
        denied = ('「' + command + '」', '如果设备已就绪，' + command, '技术员说：' + command,
                  '不要' + command, command + '可以吗？', command + '再启动Q18',
                  command.replace('不要新建或恢复其他任务', '不要恢复当前任务'),
                  command.replace('不要新建或恢复其他任务', '不要恢复Q18'),
                  command.replace('沿用该任务已确认的八号机接线和参数', '将驱动频率改为4.9GHz'),
                  command.replace('恢复当前比特定频采集', '恢复当前时间Rabi采集'))
        proposal = {'tool': 'workflow_action', 'arguments': {'workflowId': target['id'], 'action': 'resume'}}
        for index, text in enumerate(denied):
            with self.subTest(text=text):
                self.send(text, 'denied-' + str(index), selected=target['id'])
                with self.assertRaises(APIError):
                    self.w._execute_proposal(proposal, {'operationId': 'forced-' + str(index),
                        'message': text, 'intent': 'execute', 'selectedWorkflowId': target['id']},
                        {'scope': {'kind': 'workspace'}}, 0)
                self.assertEqual(self.c.get(target['id'])['status'], 'paused')
        self.assertEqual(self.adapter.calls, [])

    def test_model_cannot_redirect_exact_target_to_another_task(self):
        target, other = self.paused(), self.paused()
        with self.assertRaises(APIError):
            self.w._execute_proposal({'tool': 'workflow_action', 'arguments': {
                'workflowId': other['id'], 'action': 'resume'}}, {'operationId': 'redirect',
                'message': self.long_command(target), 'intent': 'execute'},
                {'scope': {'kind': 'workspace'}}, 0)
        self.assertEqual(self.c.get(target['id'])['status'], 'paused')
        self.assertEqual(self.c.get(other['id'])['status'], 'paused')

    def test_discussion_and_manual_policy_keep_existing_execution_gates(self):
        target = self.paused()
        self.send(self.long_command(target), 'discussion', intent='discuss')
        self.assertEqual(self.c.get(target['id'])['status'], 'paused')
        manual = self.paused(name='Q11 manual', policy='strict')
        self.send(self.long_command(manual), 'manual', selected=manual['id'])
        self.assertEqual(self.c.get(manual['id'])['status'], 'paused')
        self.assertNotIn('manualStep', self.c.get(manual['id']))
        self.assertEqual(self.adapter.calls, [])


if __name__ == '__main__':
    unittest.main()
