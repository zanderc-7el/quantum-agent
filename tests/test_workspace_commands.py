import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend.common import APIError
from backend.workspace_actions import command_action
from tests import test_workspace as fixtures

class OperatorCommands(unittest.TestCase):
    setUp=fixtures.WorkspaceContracts.setUp
    tearDown=fixtures.WorkspaceContracts.tearDown
    def send(self,text,operation='message',**extra):
        conv=self.w.create_conversation({'operationId':'c-'+operation,'scope':{'kind':'workspace'}})
        self.c._brain=lambda *a,**k:{'message':'只讨论。','proposals':[]}
        return self.w.message(conv['id'],{'operationId':operation,'message':text,'intent':'auto',**extra})

    def test_exact_operator_phrase_starts_native_q11_without_published_draft(self):
        result=self.send('你直接跑一边八号机Q11的测控吧')
        self.assertEqual(len(self.c.list()),1)
        wf=self.c.list()[0]
        self.assertEqual(wf['status'],'queued')
        self.assertEqual(wf['resourceIds'],['Q11'])
        self.assertEqual(wf['policy'],'autopilot')
        self.assertFalse(any(n['engineState']=='verified' for n in wf['nodes']))
        self.assertIn('测量队列',result['messages'][-1]['content'])
        self.assertEqual(self.adapter.calls,[])

    def test_spoken_affirmative_variants_start_the_same_native_q11(self):
        phrases=('直接开始做QQ11','把Q11测一下','Q11开始测','QQ11 开始测',
                 '那你开始测Q11吧','那就开始测Q11吧','好的，开始测Q11吧',
                 '好，那你直接开始做QQ11吧','那八号机Q11开始测吧')
        for index,text in enumerate(phrases):
            with self.subTest(text=text):
                self.assertEqual(command_action(text),'start')
                result=self.send(text,operation='spoken-'+str(index))
                self.assertEqual(result['toolResults'][0]['status'],'task_receipt')
                self.assertEqual([(w['resourceIds'],w['status']) for w in self.c.list()],[(['Q11'],'queued')])
        self.assertEqual(self.adapter.calls,[])

    def test_no_import_clause_does_not_negate_explicit_measurement(self):
        for index,text in enumerate(('先测Q11，不用导入文件','不用导入文件，直接开始做QQ11',
                '好的，不需要再导入芯片资料，那就开始测Q11吧','Q11开始测；无需上传交付文档',
                '请开始测Q11，不必重新导入文件了','先测Q11不用导入文件',
                '直接开始做QQ11不需要再导入芯片资料')):
            with self.subTest(text=text):
                self.assertEqual(command_action(text),'start')
                result=self.send(text,operation='no-import-'+str(index))
                self.assertEqual(result['toolResults'][0]['status'],'task_receipt')
                self.assertEqual(len(self.c.list()),1)
        self.assertEqual(self.adapter.calls,[])

    def test_duration_questions_and_alternative_targets_never_start_measurement(self):
        denied=('开始测Q11需要多久','开始测Q11要多长时间','开始测Q11要多长','开始Q11大约需要几分钟',
                '开始Q11要几个小时','能不能帮我开始测Q11需要多久',
                '开始Q11或Q18','开始Q11或者Q18','开始Q11还是Q18','start Q11 or Q18',
                '先测Q11不用导入文件但不要开始','先测Q11不用导入Q18文件',
                '不要先测Q11不用导入文件')
        conv=self.w.create_conversation({'operationId':'duration-c','scope':{'kind':'workspace'}})
        self.c._brain=lambda *a,**k:{'message':'仅讨论。','proposals':[
            {'tool':'start_measurement','arguments':{'resourceIds':['Q11']}}]}
        for index,text in enumerate(denied):
            with self.subTest(text=text):
                self.assertIsNone(command_action(text))
                self.w.message(conv['id'],{'operationId':'duration-'+str(index),'message':text,'intent':'auto'})
                self.assertEqual(len(self.c.list()),0)
                with self.assertRaises(APIError):
                    self.w._execute_proposal({'tool':'start_measurement','arguments':{'resourceIds':['Q11']}},
                        {'operationId':'forced-duration-'+str(index),'message':text,'intent':'execute'},conv,0)
        self.assertEqual(self.adapter.calls,[])

    def test_clause_authorization_does_not_accept_negated_or_unrelated_targets(self):
        proposal={'tool':'start_measurement','arguments':{'resourceIds':['Q11']}}
        denied=('好的，不要开始Q11','那你别测Q11','Q11先不开始测','不用导入文件，也不要开始Q11',
                '不测Q11，开始Q18','开始Q11，暂停Q18','开始Q11，终止Q11',
                'Q11开始测并暂停Q18','Q11开始测并暂停','开始Q11后终止','Q11开始测Q18',
                '不用导入Q18文件，开始Q11','开始Q11，Q18稍后处理',
                '如果不用导入文件，开始Q11','好的，"开始Q11"','技术员说Q11开始测',
                'Q11开始测了吗','好的，Q11测控结果在哪里')
        for index,text in enumerate(denied):
            with self.subTest(text=text):
                self.assertIsNone(command_action(text))
                with self.assertRaises(APIError):
                    self.w._execute_proposal(proposal,{'operationId':'denied-clause-'+str(index),
                        'message':text,'intent':'execute'},{'scope':{'kind':'workspace'}},0)
        self.assertEqual(self.c.list(),[])
        self.assertEqual(self.adapter.calls,[])

    def test_spoken_current_pause_resume_and_abort_keep_the_selected_task(self):
        self.send('开始Q11',operation='control-start')
        wf=self.c.list()[0]
        for index,(text,state) in enumerate((('好的，请暂停当前任务','paused'),
                ('那就继续当前任务吧','queued'),('那你终止当前任务吧','aborted'))):
            result=self.send(text,operation='control-'+str(index),selectedWorkflowId=wf['id'])
            self.assertEqual(result['toolResults'][0]['workflowId'],wf['id'])
            self.assertEqual(self.c.get(wf['id'])['status'],state)
        self.assertEqual(len(self.c.list()),1)
        self.assertEqual(self.adapter.calls,[])

    def test_polite_help_request_wraps_an_explicit_operator_command(self):
        for index,text in enumerate(('能不能帮我开始测QQ11','可以帮我开始测Q11吗',
                '能否帮我把Q11测一下？','能不能帮我开始测Q11吗？')):
            with self.subTest(text=text):
                result=self.send(text,operation='polite-start-'+str(index))
                self.assertEqual(result['toolResults'][0]['status'],'task_receipt')
                self.assertEqual([(w['resourceIds'],w['status']) for w in self.c.list()],[(['Q11'],'queued')])
        wf=self.c.list()[0]
        for index,(text,state) in enumerate((('可以帮我暂停当前任务吗','paused'),
                ('能否帮我继续当前任务？','queued'),('可以帮我终止当前任务吗？','aborted'))):
            result=self.send(text,operation='polite-control-'+str(index),selectedWorkflowId=wf['id'])
            self.assertEqual(result['toolResults'][0]['workflowId'],wf['id'])
            self.assertEqual(self.c.get(wf['id'])['status'],state)
        self.assertEqual(len(self.c.list()),1)
        self.assertEqual(self.adapter.calls,[])

    def test_polite_wrapper_does_not_authorize_questions_negation_or_other_targets(self):
        denied=('为什么不能开始Q11','Q11现在可以测吗','你能开始测Q11吗','请问能不能开始测Q11',
                '可以帮我解释为什么不能开始Q11吗','可以帮我不要开始Q11吗',
                '能不能帮我如果就绪就开始Q11','可以帮我"开始Q11"吗',
                '能否帮我开始Q11并暂停Q18','能不能帮我Q11开始测Q18',
                '可以帮我开始Q11吗，为什么不能开始Q18','技术员说可以帮我开始Q11吗')
        proposal={'tool':'start_measurement','arguments':{'resourceIds':['Q11']}}
        for index,text in enumerate(denied):
            with self.subTest(text=text):
                self.assertIsNone(command_action(text))
                with self.assertRaises(APIError):
                    self.w._execute_proposal(proposal,{'operationId':'denied-polite-'+str(index),
                        'message':text,'intent':'execute'},{'scope':{'kind':'workspace'}},0)
        self.assertEqual(self.c.list(),[])
        self.assertEqual(self.adapter.calls,[])

    def test_second_start_reuses_running_queue_and_message_replay_never_duplicates(self):
        result=self.send('开始测控 QQ11',operation='start')
        conv=self.w.get_conversation(result['id'])
        same=self.w.message(conv['id'],{'operationId':'start','message':'开始测控 QQ11','intent':'auto'})
        self.assertEqual(result,same)
        self.send('开始测控 Q11',operation='second')
        self.assertEqual(len(self.c.list()),1)

    def test_questions_and_negative_requests_remain_discussion(self):
        for i,text in enumerate(['为什么不能启动Q11','不要开始测Q11','如何测Q11','如果启动Q11会怎样','启动Q11？',
                '不测 Q11','勿启动Q11','Q11测量结果在哪里','Q11测控结果出来了没有',
                '技术员说启动Q11','"启动Q11"',"'启动Q11'",'Q11测控结果','请告诉我Q11测控进度',
                '开始Q11了吗','先暂停Q11，然后开始Q18','开始Q11并暂停Q18','启动Q11的结果']):
            with self.subTest(text=text):
                self.send(text,operation='d'+str(i))
                self.assertEqual(self.c.list(),[])
        self.assertEqual(self.c.list(),[])

    def test_model_proposal_does_not_upgrade_discussion_to_execution(self):
        conv=self.w.create_conversation({'operationId':'discussion','scope':{'kind':'workspace'}})
        self.c._brain=lambda *a,**k:{'message':'建议。','proposals':[
            {'tool':'start_measurement','arguments':{'resourceIds':['Q11']}}]}
        for i,text in enumerate(('不测Q11','Q11测量结果在哪里','技术员说启动Q11')):
            self.w.message(conv['id'],{'operationId':'proposed-'+str(i),'message':text,'intent':'auto'})
        self.assertEqual(self.c.list(),[])

    def test_current_selection_must_agree_with_explicit_resource(self):
        self.send('开始测Q11')
        wf=self.c.list()[0]
        for i,text in enumerate(('暂停当前 Q18 任务','终止当前 Q18 任务','继续当前 Q18 任务','开始当前 Q18 任务')):
            result=self.send(text,operation='conflict-'+str(i),selectedWorkflowId=wf['id'])
            self.assertEqual(self.c.get(wf['id'])['status'],'queued')
            self.assertIn('不一致',result['messages'][-1]['content'])

    def test_start_creation_and_permit_roll_back_together(self):
        with patch.object(self.c,'action',side_effect=APIError('TEST_FAILURE','启动测试故障')):
            result=self.send('开始测Q11')
        self.assertIn('启动测试故障',result['messages'][-1]['content'])
        self.assertEqual(self.c.list(),[])
        with self.w.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM platform_commands').fetchone()[0],0)
            self.assertEqual(rt.db.execute("SELECT COUNT(*) FROM workspace_commands WHERE id LIKE '%:tool:%'").fetchone()[0],0)

    def test_latest_published_version_of_same_chip_is_the_only_candidate(self):
        calls=[]
        binding={'chipVersionId':'v2','deploymentVersionId':'d1'}
        self.w.chips=SimpleNamespace(
            list_chips=lambda:[{'id':'chip','versions':[{'id':'v1','qubits':[{'id':'Q18'}]},
                                                       {'id':'v2','qubits':[{'id':'Q18'}]}]}],
            list_deployments=lambda:[{'versions':[{'id':'d1'}]}],
            resolve_binding=lambda cv,dv,q:calls.append((cv,dv,q)) or binding)
        # Stop before constructing any native adapter; verify configuration selection only.
        with patch.object(self.c,'with_binding',side_effect=APIError('SELECTED','selected latest')) as selected:
            result=self.send('开始测Q18')
        self.assertEqual(calls,[('v2','d1',['Q18'])])
        selected.assert_called_once_with(binding)
        self.assertIn('selected latest',result['messages'][-1]['content'])

    def test_same_alias_across_distinct_chips_still_requires_selection(self):
        self.w.chips=SimpleNamespace(
            list_chips=lambda:[{'id':'one','versions':[{'id':'v1','qubits':[{'id':'Q18'}]}]},
                               {'id':'two','versions':[{'id':'v2','qubits':[{'id':'Q18'}]}]}],
            list_deployments=lambda:[{'versions':[{'id':'d1'}]}],
            resolve_binding=lambda cv,dv,q:{'chipVersionId':cv,'deploymentVersionId':dv})
        with patch.object(self.c,'with_binding') as selected:
            result=self.send('开始测Q18')
        selected.assert_not_called()
        self.assertIn('唯一可用配置',result['messages'][-1]['content'])
        self.assertEqual(self.c.list(),[])

    def test_unknown_target_is_not_mapped_to_q11(self):
        result=self.send('直接开始测 Q19')
        self.assertEqual(self.c.list(),[])
        self.assertIn('Q19',result['messages'][-1]['content'])

    def test_pause_current_selected_task_and_abort_naturally(self):
        self.send('开始测 Q11')
        wf=self.c.list()[0]
        paused=self.send('暂停当前任务',operation='pause',selectedWorkflowId=wf['id'])
        self.assertEqual(self.c.get(wf['id'])['status'],'paused')
        stopped=self.send('终止当前任务',operation='stop',selectedWorkflowId=wf['id'])
        self.assertEqual(self.c.get(wf['id'])['status'],'aborted')
        self.assertIn('已终止',stopped['messages'][-1]['content'])

    def test_reuses_only_matching_recorded_hardware_handover(self):
        old=self.c.create({'name':'old','mode':'real','resourceIds':['Q11']})
        with self.c.store.transaction() as rt:
            old.update(status='completed',hardwareConfirmed=True,hardwareConfirmedAt='2026-11-19T00:00:00Z')
            self.c.store.save(rt,old,'test',{})
        self.send('开始测Q11')
        new=next(w for w in self.c.list() if w['id']!=old['id'])
        self.assertTrue(new['hardwareConfirmed'])
        self.assertEqual(new['hardwareConfirmationSource'],old['id'])

    def test_different_profile_handover_is_not_reused(self):
        old=self.c.create({'name':'old','mode':'real','resourceIds':['Q11']})
        with self.c.store.transaction() as rt:
            old.update(status='completed',hardwareConfirmed=True,adapterProfileHash='different-profile')
            self.c.store.save(rt,old,'test',{})
        self.send('开始测Q11')
        new=next(w for w in self.c.list() if w['id']!=old['id'])
        self.assertFalse(new['hardwareConfirmed'])
        self.assertNotIn('hardwareConfirmationSource',new)

if __name__=='__main__':unittest.main()
