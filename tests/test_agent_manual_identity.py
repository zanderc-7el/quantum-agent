"""Regression for production prefix-name ambiguity; no device operations."""
import unittest
from backend.common import APIError
from backend.workspace_manual import resolve,_named_targets
from tests import test_workspace as fixtures
from tests import test_manual_http


class ManualIdentity(unittest.TestCase):
    setUp=fixtures.WorkspaceContracts.setUp
    tearDown=fixtures.WorkspaceContracts.tearDown

    def task(self,name):
        return self.c.create({'name':name,'mode':'real','policy':'strict','resourceIds':['Q11'],
                              'manualProcedureId':'ro_resonator_survey'})

    def target(self,text,selected=None):
        return resolve(self.w,{'message':text,'selectedWorkflowId':selected},{'kind':'workspace'})[0]['id']

    def test_quoted_and_unquoted_long_name_do_not_match_contained_prefix(self):
        short=self.task('1125');long=self.task('1125升级验收_Q11_Rabi_20261126')
        for name in ('“'+long['name']+'”',long['name'], '"'+long['name']+'"'):
            self.assertEqual(self.target('请重测任务'+name+'的当前步骤。'),long['id'])
        self.assertEqual(self.c.get(short['id'])['status'],'draft')

    def test_two_separate_names_are_ambiguous_even_if_one_is_longer(self):
        short=self.task('1125');long=self.task('1125升级验收_Q11_Rabi_20261126')
        for text in ('请重测任务“'+long['name']+'”和“1125”的当前步骤。',
                     '请重测任务'+long['name']+'以及任务1125的当前步骤。'):
            with self.assertRaises(APIError) as error:self.target(text)
            self.assertEqual(error.exception.code,'WORKFLOW_TARGET_REQUIRED')

    def test_duplicate_full_names_require_an_explicit_unique_id(self):
        first=self.task('重复名称');second=self.task('重复名称')
        with self.assertRaises(APIError):self.target('请重测任务“重复名称”的当前步骤。')
        self.assertEqual(self.target('请重测任务“重复名称”（'+first['id']+'）的当前步骤。'),first['id'])
        with self.assertRaises(APIError):self.target('请重测'+first['id']+'和'+second['id']+'的当前步骤。')
        with self.assertRaises(APIError):self.target('请重测任务“重复名称”（'+first['id']+'）的当前步骤。',second['id'])

    def test_node_names_use_the_same_exact_occurrence_rules(self):
        nodes=[{'id':'node1','label':'DRAG 系数扫描'},{'id':'node2','label':'X/2 DRAG 系数扫描'}]
        for label in ('X/2 DRAG 系数扫描','“X/2 DRAG 系数扫描”'):
            self.assertEqual(_named_targets('请重测'+label,nodes,'label','NODE_TARGET_REQUIRED','ambiguous'),[nodes[1]])
        with self.assertRaises(APIError):
            _named_targets('请重测“X/2 DRAG 系数扫描”和“DRAG 系数扫描”',nodes,'label','NODE_TARGET_REQUIRED','ambiguous')

    def test_unknown_explicit_task_or_node_cannot_fall_back_to_selected(self):
        wf=self.task('1125')
        for text in ('请重测任务“1125升级验收_Q11_Rabi_不存在”的当前步骤。',
                     '请重测任务1125升级验收_Q11_Rabi_不存在的当前步骤。',
                     '请重测“未知任务”的当前步骤。',
                     '请重测“未知任务”，执行当前步骤。',
                     '请重测任务“另外一个不存在任务”的当前步骤。',
                     '请重测当前任务“未知任务”的当前步骤。',
                     '请重测当前任务的测项“未知测项”，就是当前步骤。',
                     '请重测当前任务的节点“未知测项”，就是当前步骤。',
                     '请对任务“1125”当前的“未知测项”执行一次重新测量，完成这一步后等待确认。'):
            with self.subTest(text=text),self.assertRaises(APIError):self.target(text,wf['id'])
        self.assertEqual(self.c.get(wf['id'])['revision'],wf['revision'])

    def test_action_word_that_is_a_task_name_does_not_invent_a_target(self):
        wf=self.task('测量')
        with self.assertRaises(APIError):self.target('请重新测量当前步骤。')
        self.assertEqual(self.target('请重新测量当前步骤。',wf['id']),wf['id'])
        self.assertEqual(self.target('请重测任务测量的当前步骤。'),wf['id'])


class ManualIdentityHTTP(test_manual_http.ManualHTTPContracts):
    def test_actual_request_with_prefix_task_queues_exactly_named_task(self):
        tasks=[]
        for name in ('1125','1125升级验收_Q11_Rabi_20261126'):
            status,wf=self.request('/api/workflows',{'name':name,'mode':'real','policy':'strict',
                'resourceIds':['Q11'],'manualProcedureId':'ro_resonator_survey'})
            self.assertEqual(status,201);tasks.append(wf)
        status,conv=self.request('/api/conversations',{'operationId':'identity-chat','scope':{'kind':'workspace'}})
        self.assertEqual(status,201)
        self.controller._brain=lambda *a,**k:{'message':'请自行点击。','proposals':[]}
        for index,text in enumerate(('请重测任务“1125升级验收_Q11_Rabi_不存在”的当前步骤。',
                '请重测“未知任务”，执行当前步骤。',
                '请对任务“1125”当前的“未知测项”执行一次重新测量，完成这一步后等待确认。')):
            status,unknown=self.request('/api/conversations/'+conv['id']+'/messages',{
                'operationId':'unknown-identity-command-'+str(index),'intent':'auto',
                'selectedWorkflowId':tasks[0]['id'],'message':text})
            self.assertEqual(status,200,unknown)
            self.assertEqual(unknown['toolResults'],[])
            for wf in tasks:
                _,current=self.request('/api/workflows/'+wf['id'])
                self.assertEqual((current['revision'],current['status']),(wf['revision'],'draft'))
        status,ambiguous=self.request('/api/conversations/'+conv['id']+'/messages',{
            'operationId':'ambiguous-identity-command','intent':'auto',
            'message':'请重测任务“1125升级验收_Q11_Rabi_20261126”和“1125”的当前步骤。'})
        self.assertEqual(status,200,ambiguous)
        self.assertEqual(ambiguous['toolResults'],[])
        for wf in tasks:
            _,current=self.request('/api/workflows/'+wf['id'])
            self.assertEqual((current['revision'],current['status']),(wf['revision'],'draft'))
        body={'operationId':'identity-command','intent':'auto','message':
              '请读取任务“1125升级验收_Q11_Rabi_20261126”的当前状态，对该任务当前的“读取腔定频”执行一次重新测量，并由控制器完成独立复测。保持人工逐步模式，完成这一步后等待确认，不运行后续步骤，不更改扫描参数。'}
        status,reply=self.request('/api/conversations/'+conv['id']+'/messages',body)
        self.assertEqual(status,200,reply)
        self.assertEqual(reply['toolResults'][0]['workflowId'],tasks[1]['id'])
        self.assertEqual(reply['toolResults'][0]['action'],'run_step')
        _,short=self.request('/api/workflows/'+tasks[0]['id'])
        _,long=self.request('/api/workflows/'+tasks[1]['id'])
        self.assertEqual((short['revision'],short['status']),(tasks[0]['revision'],'draft'))
        self.assertEqual(long['status'],'queued')
        status,replay=self.request('/api/conversations/'+conv['id']+'/messages',body)
        self.assertEqual(replay,reply)
        self.assertEqual(self.adapter.calls,[])
