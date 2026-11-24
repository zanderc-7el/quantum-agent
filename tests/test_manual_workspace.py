"""Manual workspace authority: temporary state and fake adapters only."""
import unittest
from backend.common import APIError
from tests import test_workspace as workspace_fixtures
from tests import test_campaigns as campaign_fixtures


class ManualWorkspaceContracts(unittest.TestCase):
    setUp = workspace_fixtures.WorkspaceContracts.setUp
    tearDown = workspace_fixtures.WorkspaceContracts.tearDown

    def test_manual_start_creates_strict_draft_without_starting_or_reusing_paused_task(self):
        old = self.c.create({'name':'existing Q11','mode':'real','resourceIds':['Q11']})
        with self.c.store.transaction() as rt:
            old.update(status='paused',engineState='manual_ready')
            self.c.store.save(rt,old,'test',{})
        conv = self.w.create_conversation({'operationId':'conversation','scope':{'kind':'workspace'}})
        self.c._brain = lambda *a, **k: {'message':'建议。','proposals':[]}
        result = self.w.message(conv['id'],{'operationId':'manual-create','message':'开始Q11，人工逐步执行','intent':'auto'})
        tasks = [w for w in self.c.list() if w['id'] != old['id']]
        self.assertEqual(len(tasks),1)
        self.assertEqual((tasks[0]['policy'],tasks[0]['status']),('strict','draft'))
        self.assertEqual(self.c.get(old['id'])['status'],'paused')
        self.assertIn('在蓝图中选择步骤开始',result['messages'][-1]['content'])
        self.assertEqual(self.adapter.calls,[])

    def test_manual_wording_cannot_be_downgraded_by_model_proposal(self):
        result = self.w._execute_proposal({'tool':'start_measurement','arguments':{'resourceIds':['Q11'],'policy':'autopilot'}},
            {'operationId':'manual','message':'开始Q11人工逐步执行','intent':'execute'}, {'scope':{'kind':'workspace'}},0)
        wf = self.c.list()[0]
        self.assertEqual((wf['policy'],wf['status']),('strict','draft'))
        self.assertEqual(result['outcomes'][0]['action'],'create')
        self.assertEqual(self.adapter.calls,[])

    def test_manual_mode_wording_variants_never_start_automatic_execution(self):
        for index,text in enumerate(('创建Q11人工逐步任务','新建Q11手动任务','create Q11 manual task',
                '开始Q11，手动模式','开始Q11，每一步我确认','开始Q11人工模式',
                '开始Q11，逐步执行','start Q11 manual','start Q11 step-by-step')):
            with self.subTest(text=text):
                result = self.w._execute_proposal({'tool':'start_measurement','arguments':{'resourceIds':['Q11']}},
                    {'operationId':'variant-'+str(index),'message':text,'intent':'execute'}, {'scope':{'kind':'workspace'}},0)
                wf = self.c.get(result['outcomes'][0]['workflowId'])
                self.assertEqual((wf['policy'],wf['status']),('strict','draft'))
        self.assertEqual(self.adapter.calls,[])

    def test_selected_manual_task_cannot_be_bypassed_with_new_automatic_task(self):
        wf = self.c.create({'name':'manual','mode':'real','resourceIds':['Q11'],'policy':'strict'})
        for index,text in enumerate(('开始当前Q11任务','开始Q11')):
            with self.subTest(text=text):
                with self.assertRaises(APIError) as error:
                    self.w._execute_proposal({'tool':'start_measurement','arguments':{'resourceIds':['Q11']}},
                        {'operationId':'bypass-'+str(index),'message':text,'intent':'execute','selectedWorkflowId':wf['id']},
                        {'scope':{'kind':'workspace'}},0)
                self.assertEqual(error.exception.code,'MANUAL_STEP_REQUIRED')
        self.assertEqual(len(self.c.list()),1)
        self.assertEqual(self.c.get(wf['id'])['status'],'draft')

    def test_injected_step_or_policy_actions_cannot_reach_controller(self):
        wf = self.c.create({'name':'manual','mode':'real','resourceIds':['Q11'],'policy':'strict'})
        for action in ('run_step','approve','set_policy'):
            with self.subTest(action=action), self.assertRaises(APIError):
                self.w._execute_proposal({'tool':'workflow_action','arguments':{'workflowId':wf['id'],'action':action}},
                    {'operationId':action,'message':'开始当前Q11任务','intent':'execute','selectedWorkflowId':wf['id']},
                    {'scope':{'kind':'workspace'}},0)
        self.assertEqual(self.c.get(wf['id'])['revision'],wf['revision'])
        self.assertEqual(self.adapter.calls,[])

    def test_negated_manual_request_does_not_become_execute(self):
        conv = self.w.create_conversation({'operationId':'conversation','scope':{'kind':'workspace'}})
        self.c._brain = lambda *a, **k: {'message':'建议。','proposals':[{'tool':'start_measurement','arguments':{'resourceIds':['Q11'],'policy':'strict'}}]}
        self.w.message(conv['id'],{'operationId':'no-start','message':'不要开始Q11，人工逐步执行','intent':'auto'})
        self.assertEqual(self.c.list(),[])

    def test_context_preserves_manual_policy_and_current_revision(self):
        wf = self.c.create({'name':'manual','mode':'real','resourceIds':['Q11'],'policy':'strict'})
        context = self.w._context({'scope':{'kind':'workspace'}},{'selectedWorkflowId':wf['id']})
        row = context['workflows'][0]
        self.assertEqual(row.get('policy'),'strict')
        self.assertEqual(row.get('revision'),wf['revision'])
        self.assertTrue(context['snapshotAt'])

    def test_manual_routine_wait_is_not_exception_attention_but_failures_remain(self):
        wf = self.c.create({'name':'manual','mode':'real','resourceIds':['Q11'],'policy':'strict'})
        for state in ('awaiting_acceptance','manual_ready'):
            self.c._attention(wf['id'],state,'等待人工')
            self.assertFalse([item for item in self.w.attention_items() if item['state']=='open'])
        self.c._attention(wf['id'],'quality_attention','拟合失败')
        item = next(item for item in self.w.attention_items() if item['state']=='open')
        self.assertEqual(item['actions'][0]['id'],'open_workflow')
        self.c._attention(wf['id'],'needs_reconciliation','回执未知')
        item = next(item for item in self.w.attention_items() if item['state']=='open')
        self.assertEqual(item['actions'][0]['id'],'reconcile')


class ManualCampaignContracts(unittest.TestCase):
    setUp = campaign_fixtures.CampaignContracts.setUp
    tearDown = campaign_fixtures.CampaignContracts.tearDown
    binding = campaign_fixtures.CampaignContracts.binding
    create = campaign_fixtures.CampaignContracts.create
    action = campaign_fixtures.CampaignContracts.action

    def test_bulk_start_cannot_grant_manual_task_execution_or_hardware_confirmation(self):
        campaign = self.create(policy='strict',resourceIds=['Q11'])
        wf = self.c.get(campaign['children'][0]['workflowId'])
        with self.assertRaises(APIError) as error:
            self.action(campaign,'start','bulk-start')
        self.assertEqual(error.exception.code,'MANUAL_STEP_REQUIRED')
        after = self.c.get(wf['id'])
        self.assertEqual(after['revision'],wf['revision'])
        self.assertEqual(after['status'],'draft')
        self.assertFalse(after.get('hardwareConfirmed'))

    def test_mixed_bulk_start_keeps_manual_child_draft_and_starts_automatic_child(self):
        campaign = self.create(policy='strict',resourceIds=['Q11','Q12'])
        auto = self.c.get(campaign['children'][1]['workflowId'])
        with self.c.store.transaction() as rt:
            auto['policy'] = 'threshold'
            self.c.store.save(rt,auto,'test',{})
        result = self.action(campaign,'start','mixed-start')
        states = {c['resourceId']:c['status'] for c in result['children']}
        self.assertEqual(states,{'Q11':'draft','Q12':'queued'})
        self.assertEqual(result['permits'][-1]['resourceIds'],['Q12'])
        manual = next(row for row in result['outcomes'] if row['resourceId']=='Q11')
        self.assertEqual(manual.get('code'),'MANUAL_STEP_REQUIRED')
