"""Chat authorization over loopback HTTP and temporary SQLite only."""
from tests import test_manual_http


class AgentManualHTTP(test_manual_http.ManualHTTPContracts):
    def test_negative_messages_never_create_one_step_permission(self):
        status,wf=self.request('/api/workflows',{'name':'单步否定验证','mode':'real','policy':'strict',
            'resourceIds':['Q11'],'manualProcedureId':'ro_resonator_survey'})
        self.assertEqual(status,201)
        status,conv=self.request('/api/conversations',{'operationId':'negative-http-chat','scope':{'kind':'workspace'}})
        self.assertEqual(status,201)
        self.controller._brain=lambda *a,**k:{'message':'提议运行。','proposals':[{'tool':'run_manual_step','arguments':{
            'workflowId':wf['id'],'nodeId':wf['currentNodeId'],'expectedRevision':wf['revision'],
            'expectedAttemptId':None,'remeasure':True}}]}
        phrases=('我不想重测任务“单步否定验证”的当前步骤。',
                 '请不要对任务“单步否定验证”的当前步骤重新测量。',
                 '暂时不打算重新测量任务“单步否定验证”的当前步骤。',
                 '重测任务“单步否定验证”的当前步骤，先不要执行。',
                 '对任务“单步否定验证”的当前步骤重新测量，不过先别执行。',
                 '命令草稿：请对任务“单步否定验证”的当前步骤重测一次。',
                 '用户原话：请对任务“单步否定验证”的当前步骤重测一次。',
                 '待确认指令：请对任务“单步否定验证”的当前步骤重测一次。',
                 '引用：请对任务“单步否定验证”的当前步骤重测一次。',
                 '请对任务“单步否定验证”的当前步骤重测一次，等我确认再执行。')
        for index,text in enumerate(phrases):
            for intent in ('auto','execute'):
                with self.subTest(text=text,intent=intent):
                    status,reply=self.request('/api/conversations/'+conv['id']+'/messages',{
                        'operationId':f'negative-http-{index}-{intent}','message':text,'intent':intent})
                    self.assertEqual(status,200,reply)
                    _,current=self.request('/api/workflows/'+wf['id'])
                    self.assertEqual((current['revision'],current['status']),(wf['revision'],'draft'))
                    self.assertNotIn('manualStep',current)
        self.assertEqual(self.adapter.calls,[])
        with self.controller.store.connect() as rt:
            for table in ('platform_attempts','platform_effects','platform_leases'):
                self.assertEqual(rt.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
