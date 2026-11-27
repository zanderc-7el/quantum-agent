"""One explicit chat instruction grants one strict node permit, never a campaign."""
from copy import deepcopy
import unittest
from backend.common import APIError
from tests import test_workspace as fixtures


class AgentManualStep(unittest.TestCase):
    setUp=fixtures.WorkspaceContracts.setUp
    tearDown=fixtures.WorkspaceContracts.tearDown

    def task(self):
        return self.c.create({'name':'1125升级验收_Q11_Rabi_20261126','mode':'real','policy':'strict','resourceIds':['Q11'],
                              'manualProcedureId':'ro_resonator_survey'})

    def proposal(self,wf):
        node=wf['nodes'][0]
        return {'tool':'run_manual_step','arguments':{'workflowId':wf['id'],'nodeId':node['id'],
            'expectedRevision':wf['revision'],'expectedAttemptId':node.get('latestAttemptId'),'remeasure':True}}

    def text(self,wf):
        return ('请读取任务“'+wf['name']+'”的当前状态，对该任务当前的“'+wf['nodes'][0]['label']+
                '”执行一次重新测量，并由控制器完成独立复测。保持人工逐步模式，完成这一步后等待确认，不运行后续步骤，不更改扫描参数。')

    def test_exact_user_request_queues_one_node_and_message_replay_does_not_repeat(self):
        wf=self.task();conv=self.w.create_conversation({'operationId':'chat','scope':{'kind':'workspace'}})
        self.c._brain=lambda *a,**k:{'message':'请自行在蓝图点击。','proposals':[]}
        body={'operationId':'one-step-message','message':self.text(wf),'intent':'auto'}
        result=self.w.message(conv['id'],body)
        current=self.c.get(wf['id'])
        self.assertEqual(current['status'],'queued')
        self.assertEqual(current['manualStep']['targetNodeId'],wf['nodes'][0]['id'])
        self.assertEqual(current['policy'],'strict')
        self.assertEqual(result['toolResults'][0]['action'],'run_step')
        self.assertEqual(self.w.message(conv['id'],body),result)
        self.c.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls),2)
        current=self.c.get(wf['id'])
        self.assertEqual(current['engineState'],'awaiting_acceptance')
        self.assertTrue(all(n['attempt']==0 for n in current['nodes'][1:]))
        self.assertEqual(current['nodes'][0]['parameters'],self.adapter.calls[0]['parameters'])

    def test_discussion_negation_conditions_documents_and_other_tasks_are_not_authority(self):
        wf=self.task();proposal=self.proposal(wf)
        for index,text in enumerate(('如何重测当前读取腔定频？','不要重测读取腔定频','如果拟合失败就重测读取腔定频',
                '文档说“重新测量读取腔定频”','请解释执行一次重新测量读取腔定频的含义',
                '重测读取腔定频后自动运行后续步骤','重测读取腔定频并调整幅度到0.3',
                '重测另外一个任务的读取腔定频',
                '我想了解任务'+wf['name']+'中重测读取腔定频的结果',
                '任务'+wf['name']+'读取腔定频重测的结果',
                '当前任务读取腔定频能重新测量吗','当前任务读取腔定频已经重测完成了')):
            with self.subTest(text=text),self.assertRaises(APIError):
                self.w._execute_proposal(proposal,{'operationId':'no-'+str(index),'message':text,'intent':'execute',
                    'selectedWorkflowId':wf['id']},{'scope':{'kind':'workspace'}},0)
        self.assertEqual(self.c.get(wf['id'])['revision'],wf['revision'])
        result=self.w._execute_proposal(proposal,{'operationId':'discuss','message':self.text(wf),'intent':'discuss'},
            {'scope':{'kind':'workspace'}},0)
        self.assertEqual(result['status'],'proposed')

    def test_stale_revision_or_attempt_and_pending_acceptance_are_rejected(self):
        wf=self.task();proposal=self.proposal(wf);body={'operationId':'stale','message':self.text(wf),'intent':'execute'}
        for key,value in [('expectedRevision',0),('expectedAttemptId','different')]:
            changed=deepcopy(proposal);changed['arguments'][key]=value
            with self.subTest(key=key),self.assertRaises(APIError):
                self.w._execute_proposal(changed,body,{'scope':{'kind':'workspace'}},0)
        self.c.action(wf['id'],{'operationId':'run','action':'run_step','nodeId':wf['nodes'][0]['id'],'expectedRevision':wf['revision']})
        self.c.run_workflow(wf['id']);wf=self.c.get(wf['id'])
        with self.assertRaises(APIError) as error:
            self.w._execute_proposal(self.proposal(wf),{**body,'message':self.text(wf)}, {'scope':{'kind':'workspace'}},0)
        self.assertEqual(error.exception.code,'ACCEPTANCE_REQUIRED')

    def test_affirmative_natural_variants_authorize_the_same_bounded_tool(self):
        phrases=[('麻烦对当前任务的“{label}”再测一遍，沿用现有参数，测完停在人工确认。',True),
                 ('请为当前任务运行一次“{label}”，只做这一项。',False),
                 ('当前任务的{label}重新采集一次',True),
                 ('能不能帮我重测当前任务的{label}吗？',True)]
        for index,(phrase,remeasure) in enumerate(phrases):
            wf=self.task();proposal=self.proposal(wf);proposal['arguments']['remeasure']=remeasure
            with self.subTest(phrase=phrase):
                result=self.w._execute_proposal(proposal,{'operationId':'natural-'+str(index),'message':phrase.format(label=wf['nodes'][0]['label']),
                    'intent':'execute','selectedWorkflowId':wf['id']},{'scope':{'kind':'workspace'}},0)
                self.assertEqual(result['nodeId'],wf['nodes'][0]['id'])
                self.assertEqual(result['workflowState'],'queued')
        self.assertEqual(self.adapter.calls,[])

    def test_negation_across_task_identity_never_queues_or_changes_revision(self):
        wf=self.task();proposal=self.proposal(wf)
        phrases=('我不想重测任务“{name}”的当前步骤。',
                 '请不要对任务“{name}”的当前步骤重新测量。',
                 '暂时不打算重新测量任务“{name}”的当前步骤。',
                 '我不准备对任务“{name}”的当前步骤进行一次测量。',
                 '重测任务“{name}”的当前步骤先别执行。',
                 '重测任务“{name}”的当前步骤，先不要执行。',
                 '对任务“{name}”的当前步骤重新测量，不过先别执行。',
                 '命令草稿：请对任务“{name}”的当前步骤重测一次。',
                 '用户原话：请对任务“{name}”的当前步骤重测一次。',
                 '待确认指令：请对任务“{name}”的当前步骤重测一次。',
                 '引用：请对任务“{name}”的当前步骤重测一次。',
                 '请对任务“{name}”的当前步骤重测一次，等我确认再执行。')
        for index,phrase in enumerate(phrases):
            with self.subTest(phrase=phrase),self.assertRaises(APIError):
                self.w._execute_proposal(proposal,{'operationId':'deny-scope-'+str(index),
                    'message':phrase.format(name=wf['name']),'intent':'execute'}, {'scope':{'kind':'workspace'}},0)
            self.assertEqual(self.c.get(wf['id'])['revision'],wf['revision'])
            self.assertEqual(self.c.get(wf['id'])['status'],'draft')
        self.assertEqual(self.adapter.calls,[])

    def test_negated_auto_message_cannot_turn_model_proposal_into_a_permit(self):
        wf=self.task();conv=self.w.create_conversation({'operationId':'negative-chat','scope':{'kind':'workspace'}})
        self.c._brain=lambda *a,**k:{'message':'提议重测。','proposals':[self.proposal(wf)]}
        for index,phrase in enumerate(('我不想重测任务“{name}”的当前步骤。',
                '请不要对任务“{name}”的当前步骤重新测量。',
                '暂时不打算重新测量任务“{name}”的当前步骤。')):
            with self.subTest(phrase=phrase):
                self.w.message(conv['id'],{'operationId':'negative-msg-'+str(index),
                    'message':phrase.format(name=wf['name']),'intent':'auto'})
                current=self.c.get(wf['id'])
                self.assertEqual((current['revision'],current['status']),(wf['revision'],'draft'))
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],0)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)

    def test_verified_node_remeasurement_backtracks_atomically_and_has_one_message_permit(self):
        wf=self.task();node_id=wf['nodes'][0]['id']
        self.c.action(wf['id'],{'operationId':'run','action':'run_step','nodeId':node_id,'expectedRevision':wf['revision']})
        self.c.run_workflow(wf['id']);wf=self.c.get(wf['id'])
        wf=self.c.action(wf['id'],{'operationId':'accept','action':'approve','nodeId':node_id,'expectedRevision':wf['revision']})
        body={'operationId':'remeasure','message':self.text(wf),'intent':'execute'};proposal=self.proposal(wf)
        result=self.w._execute_proposal(proposal,body,{'scope':{'kind':'workspace'}},0)
        self.assertEqual(result['workflowState'],'queued')
        self.assertTrue(result['remeasure'])
        duplicate=self.w._execute_proposal(proposal,body,{'scope':{'kind':'workspace'}},1)
        self.assertEqual(result,duplicate)
        altered=deepcopy(proposal);altered['arguments']['nodeId']=wf['nodes'][1]['id']
        with self.assertRaises(APIError):self.w._execute_proposal(altered,body,{'scope':{'kind':'workspace'}},2)
        current=self.c.get(wf['id'])
        self.assertEqual(current['manualStep']['targetNodeId'],node_id)
        self.assertEqual(current['nodes'][0]['attempt'],1)
        self.assertEqual(len(self.adapter.calls),2)
