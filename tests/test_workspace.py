"""Workspace orchestration contracts; temporary database, no instrument or model."""
from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from backend.common import APIError
from tests.test_controller import make_fixture


class WorkspaceContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c, self.adapter = make_fixture(Path(self.tmp.name))
        from backend.workspace import Workspace
        self.w = Workspace(self.c, initialize_catalog=False)
        self.c._workspace_service = self.w

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_global_conversation_without_task_recovers_and_scope_is_checked(self):
        conv = self.w.create_conversation({'operationId':'conversation', 'scope':{'kind':'workspace'}})
        self.c._brain = lambda *a, **k: {'status':'ok','message':'请选择芯片配置。','proposals':[]}
        result = self.w.message(conv['id'], {'operationId':'message','message':'如何安排实验？','intent':'discuss'})
        self.assertEqual(len(result['messages']), 2)
        self.assertEqual(self.w.message(conv['id'], {'operationId':'message','message':'如何安排实验？','intent':'discuss'}), result)
        with self.assertRaises(APIError):
            self.w.message(conv['id'], {'operationId':'bad','message':'x','scope':{'kind':'workflow','id':'missing'}})
        self.assertEqual(self.w.get_conversation(conv['id'])['messages'], result['messages'])

    def test_readonly_inspection_evidence_is_attached_to_each_reply_and_survives_reload(self):
        wf = self.c.create({'name':'selected','mode':'real','resourceIds':['Q11']})
        conv = self.w.create_conversation({'operationId':'inspect-c','scope':{'kind':'workspace'}})
        evidence = [{'tool':'read_source','arguments':{'path':'backend/controller.py','startLine':10},
            'source':{'path':'backend/controller.py','startLine':10,'sha256':'a'*64},'ok':True,'checkedAt':'2026-11-19T00:00:00Z'}]
        def brain(route,payload):
            self.assertEqual(payload['context']['selectedWorkflowId'],wf['id'])
            return {'status':'ok','message':'已查到对应实现。','proposals':[],'inspections':evidence}
        self.c._brain=brain
        result=self.w.message(conv['id'],{'operationId':'inspect-m','message':'为什么这个任务未启动，查下代码','intent':'auto','selectedWorkflowId':wf['id']})
        self.assertEqual(result['messages'][-1]['inspections'],evidence)
        self.assertEqual(self.w.get_conversation(conv['id'])['messages'][-1]['inspections'],evidence)
        self.assertEqual(self.c.get(wf['id'])['status'],'draft')
        self.assertEqual(self.adapter.calls,[])

    def test_campaign_initial_context_does_not_include_other_workflows_or_selected_target(self):
        own=self.c.create({'name':'own','mode':'real','resourceIds':['Q11']})
        other=self.c.create({'name':'foreign','mode':'real','resourceIds':['Q11']})
        campaign={'id':'campaign-own','children':[{'workflowId':own['id']}],'chipVersionId':'cv-one','deploymentVersionId':'dv-one'}
        self.w.get_campaign=lambda id:campaign
        context=self.w._context({'scope':{'kind':'campaign','id':campaign['id']}},{'selectedWorkflowId':other['id']})
        self.assertEqual([w['id'] for w in context['workflows']],[own['id']])
        self.assertEqual([c['id'] for c in context['campaigns']],['campaign-own'])
        self.assertNotIn('selectedWorkflowId',context)
        self.assertEqual(context['execution']['existingNativeResources'],[])

    def test_discussion_cannot_execute_model_tools(self):
        conv = self.w.create_conversation({'operationId':'c','scope':{'kind':'workspace'}})
        self.c._brain = lambda *a, **k: {'status':'ok','message':'建议创建批次。','proposals':[{'tool':'create_campaign','arguments':{'name':'x'}}]}
        result = self.w.message(conv['id'], {'operationId':'m','message':'讨论下怎么测','intent':'discuss'})
        self.assertEqual(self.w.list_campaigns(), [])
        self.assertEqual(result['proposals'][0]['tool'], 'create_campaign')

    def test_attention_defer_is_durable_and_stale_action_rejected(self):
        wf = self.c.create({'name':'test','mode':'real','resourceIds':['Q11']})
        self.c._attention(wf['id'], 'waiting_hardware', '等待接线')
        item = self.w.attention_items()[0]
        self.w.attention_action(item['id'], {'operationId':'defer','expectedRevision':item['revision'],'action':'defer'})
        self.assertEqual(self.w.attention_items()[0]['state'], 'deferred')
        self.assertEqual(self.c.get(wf['id'])['status'], 'attention')
        self.c._attention(wf['id'], 'waiting_hardware', '接线状态已更新')
        with self.assertRaises(APIError):
            self.w.attention_action(item['id'], {'operationId':'stale','expectedRevision':item['revision'],'action':'confirm_hardware'})

    def test_resource_failure_does_not_block_peer_but_shared_failure_does(self):
        one = self.c.create({'name':'one','mode':'real','resourceIds':['Q11']})
        two = self.c.create({'name':'two','mode':'real','resourceIds':['Q11']})
        self.c._attention(one['id'], 'quality_attention', '拟合未通过')
        self.assertIsNone(self.w.queue_blocker(two))
        self.c._attention(one['id'], 'needs_reconciliation', '回执未知')
        self.assertEqual(self.w.queue_blocker(two)['id'], one['id'])

    def test_agent_saves_only_attached_document_as_unpublished_chip_draft(self):
        parsed={'id':'doc-one','revision':2,'draft':{'name':'交付资料','qubits':[]}}
        calls=[]
        self.w.imports=SimpleNamespace(get=lambda id: {'id':id,'revision':1},
            analyze=lambda id,body: calls.append((id,body)) or parsed)
        self.w.chips=SimpleNamespace(save_chip=lambda body: {'id':'new-chip','name':body['draft']['name']})
        conv={'scope':{'kind':'workspace'},'documentIds':['doc-one']}
        proposal={'tool':'analyze_import','arguments':{'documentId':'doc-one'}}
        result=self.w._execute_proposal(proposal,{'operationId':'analyze','message':'请读取资料并整理配置，不要启动测量','intent':'execute'},conv,0)
        self.assertEqual(result['status'],'draft_ready')
        self.assertEqual(calls[0][0],'doc-one')
        self.assertEqual(self.c.list(),[])
        with self.assertRaises(APIError):
            self.w._execute_proposal({'tool':'analyze_import','arguments':{'documentId':'unattached'}},
                {'operationId':'bad','message':'请读取资料','intent':'execute'},conv,0)
        self.w._execute_proposal(proposal,{'operationId':'discuss','message':'讨论下资料','intent':'discuss'},conv,0)
        self.assertEqual(len(calls),1)

    def test_negated_start_clause_does_not_deny_requested_attachment_analysis(self):
        calls=[]
        self.w.imports=SimpleNamespace(get=lambda id:{'id':id,'revision':1},
            analyze=lambda id,body:calls.append((id,body)) or {'draft':{'name':'资料草稿','qubits':[]}})
        self.w.chips=SimpleNamespace(save_chip=lambda body:{'id':'draft-only','name':body['draft']['name']})
        conv={'scope':{'kind':'workspace'},'documentIds':['doc-one']}
        proposal={'tool':'analyze_import','arguments':{'documentId':'doc-one'}}
        for index,message in enumerate(('不要启动，只分析资料','不要启动；读取资料','暂不启动。整理配置',
                '不要启动!分析资料','不要启动\n读取资料','不要启动,只分析资料','请读取资料并整理配置，不要启动测量')):
            with self.subTest(message=message):
                result=self.w._execute_proposal(proposal,{'operationId':'analysis-'+str(index),'message':message,'intent':'execute'},conv,0)
                self.assertEqual(result['status'],'draft_ready')
        self.assertEqual(len(calls),7)
        self.assertEqual(self.c.list(),[])

    def test_negated_analysis_stays_denied_in_its_own_clause(self):
        calls=[]
        self.w.imports=SimpleNamespace(get=lambda id:calls.append('get'),analyze=lambda *a:calls.append('analyze'))
        self.w.chips=SimpleNamespace(save_chip=lambda body:calls.append('save'))
        conv={'scope':{'kind':'workspace'},'documentIds':['doc-one']}
        for message in ('不要分析资料，只启动Q11','先不读取资料','不要帮我整理配置','Do not read the document'):
            with self.subTest(message=message),self.assertRaises(APIError) as caught:
                self.w._execute_proposal({'tool':'analyze_import','arguments':{'documentId':'doc-one'}},
                    {'operationId':'denied-analysis','message':message,'intent':'execute'},conv,0)
            self.assertEqual(caught.exception.code,'EXPLICIT_REQUEST_REQUIRED')
        self.assertEqual(calls,[])

    def test_start_authorization_obeys_negation_within_clause(self):
        calls=[]
        self.w.get_campaign=lambda id:{'id':id,'name':'test batch','revision':1,'resourceIds':['Q11']}
        self.w.campaign_action=lambda id,body:calls.append((id,body)) or {'status':'queued'}
        conv={'scope':{'kind':'campaign','id':'batch'}}
        proposal={'tool':'campaign_action','arguments':{'campaignId':'batch','action':'start','resourceIds':['Q11']}}
        for message in ('不要启动Q11，只分析资料','请不要立即启动Q11','暂不启动Q11','Do not start Q11'):
            with self.subTest(message=message),self.assertRaises(APIError) as caught:
                self.w._execute_proposal(proposal,{'operationId':'denied-start','message':message,'intent':'execute'},conv,0)
            self.assertEqual(caught.exception.code,'EXPLICIT_REQUEST_REQUIRED')
        self.assertEqual(calls,[])
        for index,message in enumerate(('启动Q11','不要分析资料，只启动Q11','不要读，只启动Q11')):
            with self.subTest(message=message):
                result=self.w._execute_proposal(proposal,{'operationId':'start-'+str(index),'message':message,'intent':'execute'},conv,0)
                self.assertEqual(result['status'],'queued')
        self.assertEqual(len(calls),3)

    def test_large_attachment_context_is_explicit_preview_and_full_analysis_stays_available(self):
        original={'id':'large-ppt','filename':'交付资料.pptx','revision':1,'status':'parsed','sha256':'a'*64,
            'blocks':[{'id':'block-'+str(i),'location':'slide '+str(i//20+1),'text':'量子交付资料 '+str(i)+' x'*150} for i in range(4122)],
            'draft':{'name':'芯片草稿','qubits':[{'id':'Q19','label':'Q19','parameters':{'drive_hz':5.1e9}}]},'issues':['物理身份待核对']}
        before=deepcopy(original);calls=[]
        def analyze(id,body):
            self.assertEqual(len(original['blocks']),4122)
            calls.append((id,body))
            return {'draft':original['draft']}
        self.w.imports=SimpleNamespace(get=lambda id:original,analyze=analyze)
        self.w.chips=SimpleNamespace(list_chips=lambda:[],list_deployments=lambda:[],
            save_chip=lambda body:{'id':'draft-only','name':body['draft']['name']})
        conv={'scope':{'kind':'workspace'},'documentIds':['large-ppt']}
        context=self.w._context(conv,{'intent':'execute'})
        document=context['documents'][0]
        self.assertEqual(document['documentId'],'large-ppt')
        self.assertEqual(document['totalBlockCount'],4122)
        self.assertTrue(document['preview'])
        self.assertEqual(document['contentScope'],'parsed_block_preview_only')
        self.assertLess(document['previewBlockCount'],4122)
        self.assertEqual(document['sourceRetention'],'complete_original_retained')
        self.assertEqual(document['analysisSource'],'complete_stored_document')
        self.assertEqual(document['draftSummary']['totalQubitCount'],1)
        self.assertNotIn('draft',document)
        request={'sessionId':'test','message':'请分析附件'+('资料'*9990),'context':context}
        encoded=json.dumps(request,ensure_ascii=False,separators=(',',':'))
        self.assertLess(len(encoded.encode('utf-8')),250000)
        self.assertLess(len(encoded.encode('utf-16-le'))//2,200000)
        self.assertLessEqual(len(json.dumps(context['documents'],ensure_ascii=False,separators=(',',':')).encode()),60000)
        result=self.w._execute_proposal({'tool':'analyze_import','arguments':{'documentId':'large-ppt'}},
            {'operationId':'full-analysis','message':'分析资料','intent':'execute'},conv,0)
        self.assertEqual(result['status'],'draft_ready')
        self.assertEqual(calls[0][0],'large-ppt')
        self.assertEqual(original,before)
        self.assertEqual(self.c.list(),[])

    def test_small_attachment_keeps_complete_parsed_blocks_and_source_locations(self):
        blocks=[{'id':'b1','location':'sheet Data A1','text':'Q19 frequency 5.1 GHz'},
                {'id':'b2','location':'page 2','text':'物理接线需要现场核对'}]
        original={'id':'small-doc','revision':1,'filename':'small.pdf','blocks':blocks,'draft':{'name':'small','qubits':[]}}
        self.w.imports=SimpleNamespace(get=lambda id:original)
        context=self.w._context({'scope':{'kind':'workspace'},'documentIds':['small-doc']},{})
        document=context['documents'][0]
        self.assertEqual(document['blocks'],blocks)
        self.assertEqual((document['totalBlockCount'],document['previewBlockCount']),(2,2))
        self.assertFalse(document['preview'])
        self.assertEqual(document['contentScope'],'complete_parsed_blocks')
        self.assertFalse(context['documentSummary']['preview'])
        self.assertIsNot(document['blocks'][0],blocks[0])

    def test_multiple_attachment_and_configuration_context_budgets_are_truthful(self):
        originals={str(i):{'id':str(i),'blocks':[{'id':'b','location':'page 1','text':'文档内容'*50000}],
            'draft':{'name':'草稿','qubits':[]}} for i in range(6)}
        self.w.imports=SimpleNamespace(get=lambda id:originals[id])
        chips=[{'id':'chip-'+str(i),'name':'芯片','qubits':[{'id':'Q19','parameters':{'blob':'x'*20000}}],
            'versions':[{'id':'chip-version-'+str(i),'entityId':'chip-'+str(i),'name':'published','qubits':[{'id':'Q19','provenance':{'blob':'x'*20000}}]}]} for i in range(100)]
        self.w.chips=SimpleNamespace(list_chips=lambda:chips,list_deployments=lambda:[])
        context=self.w._context({'scope':{'kind':'workspace'},'documentIds':list(originals)},{})
        self.assertEqual(context['documentSummary']['totalCount'],6)
        self.assertEqual(context['documentSummary']['returnedCount'],6)
        self.assertTrue(context['documentSummary']['preview'])
        self.assertTrue(all(d['preview'] and d['blocks'][0]['preview'] for d in context['documents']))
        self.assertLessEqual(len(json.dumps(context['documents'],ensure_ascii=False,separators=(',',':')).encode()),60000)
        self.assertEqual(context['collectionSummaries']['chips']['totalCount'],100)
        self.assertTrue(context['collectionSummaries']['chips']['preview'])
        self.assertTrue(context['chips'])
        self.assertEqual(context['chips'][0]['versions'][0]['id'],'chip-version-0')
        self.assertLess(len(json.dumps(context,ensure_ascii=False).encode()),140000)
        self.assertEqual(len(originals['0']['blocks'][0]['text']),200000)
        self.assertEqual(chips[0]['qubits'][0]['parameters']['blob'],'x'*20000)

    def test_executed_import_receipt_replaces_pre_execution_model_disclaimers(self):
        from unittest.mock import patch
        self.w.chips=SimpleNamespace(list_chips=lambda:[],list_deployments=lambda:[],
            get_chip=lambda id:{'id':id,'name':'Q19资料草稿','qubits':[{'id':'Q19','identityStatus':'unresolved'}]})
        conv=self.w.create_conversation({'operationId':'receipt-conv','scope':{'kind':'workspace'}})
        model_text='先说结论：我不会自己分析或写入，以上只是提案。'
        self.c._brain=lambda *a,**k:{'message':model_text,'proposals':[{'tool':'analyze_import','arguments':{'documentId':'doc'}}]}
        receipt={'status':'draft_ready','chipId':'draft','message':'已保存芯片草稿“Q19资料草稿”，可在“芯片与接线”中核对缺项和接线。'}
        with patch.object(self.w,'_execute_proposal',return_value=receipt):
            result=self.w.message(conv['id'],{'operationId':'receipt-message','message':'整理资料','intent':'execute'})
        content=result['messages'][-1]['content']
        self.assertIn('已保存芯片草稿“Q19资料草稿”',content)
        self.assertIn('物理比特身份',content)
        self.assertIn('当前接线',content)
        self.assertNotIn('先说结论',content)
        self.assertNotIn('不会',content)
        self.assertNotIn('提案',content)
        self.assertEqual(result['toolResults'],[receipt])
        self.assertEqual(self.w.get_conversation(conv['id'])['messages'][-1]['content'],content)

    def test_discussion_preserves_model_text_and_does_not_invent_operation_receipt(self):
        conv=self.w.create_conversation({'operationId':'discussion-receipt-conv','scope':{'kind':'workspace'}})
        self.c._brain=lambda *a,**k:{'message':'建议先核对物理比特身份。','proposals':[{'tool':'analyze_import','arguments':{'documentId':'doc'}}]}
        result=self.w.message(conv['id'],{'operationId':'discussion-receipt','message':'讨论下配置','intent':'discuss'})
        self.assertEqual(result['messages'][-1]['content'],'建议先核对物理比特身份。\n\n建议尚未执行。')
        self.assertEqual(result['toolResults'],[{'status':'proposed'}])

    def test_campaign_receipts_distinguish_created_draft_from_actual_queued_start(self):
        from unittest.mock import patch
        for action,state,status in (('create_campaign','draft','draft'),('campaign_action','running','queued')):
            with self.subTest(action=action):
                conv=self.w.create_conversation({'operationId':'receipt-'+action,'scope':{'kind':'workspace'}})
                self.c._brain=lambda *a,**k:{'message':'先说结论：测量已经完成。','proposals':[{'tool':action,'arguments':{'action':'start'}}]}
                receipt={'id':'campaign','name':'Q19批次','state':state,'resourceIds':['Q19'],
                    'children':[{'resourceId':'Q19','status':status}],
                    **({'outcomes':[{'resourceId':'Q19','status':status}]} if action=='campaign_action' else {})}
                with patch.object(self.w,'_execute_proposal',return_value=receipt):
                    result=self.w.message(conv['id'],{'operationId':'message-'+action,'message':'创建或启动Q19批次','intent':'execute'})
                content=result['messages'][-1]['content']
                self.assertIn('Q19批次',content)
                self.assertNotIn('已经完成',content)
                self.assertNotIn('先说结论',content)
                self.assertIn('草稿' if state=='draft' else '排队',content)
                if state=='draft':self.assertNotIn('已启动',content)

    def test_mixed_tool_success_and_rejection_keep_truthful_receipts_without_model_claims(self):
        from unittest.mock import patch
        conv=self.w.create_conversation({'operationId':'mixed-receipts','scope':{'kind':'workspace'}})
        self.c._brain=lambda *a,**k:{'message':'已全部完成。','proposals':[
            {'tool':'campaign_action','arguments':{'action':'skip'}},{'tool':'campaign_action','arguments':{'action':'start'}}]}
        applied={'id':'batch','name':'验收批次','state':'running','children':[{'status':'running'}],
                 'outcomes':[{'resourceId':'Q19','status':'running'}]}
        rejected={'status':'rejected','message':'接线版本待核对','code':'BLOCKED'}
        with patch.object(self.w,'_execute_proposal',side_effect=[applied,rejected]):
            result=self.w.message(conv['id'],{'operationId':'mixed-receipt-message','message':'跳过Q19再启动整批','intent':'execute'})
        content=result['messages'][-1]['content']
        self.assertIn('跳过',content)
        self.assertIn('检查点',content)
        self.assertIn('接线版本待核对',content)
        self.assertNotIn('已全部完成',content)
        self.assertNotIn('已跳过',content)

    def test_clipboard_import_gets_readable_single_qubit_chip_name_without_renaming_source(self):
        generated='codex-clipboard-e1a70000-1234-5678-9abc-123456789abc'
        for original_name, expected in ((generated,'Q19 参数草稿'),('交付芯片参数','交付芯片参数')):
            with self.subTest(name=original_name):
                parsed={'draft':{'name':original_name,'qubits':[{'id':'Q19','label':'Q19','parameters':{'drive_hz':5.1e9}}]}}
                before=deepcopy(parsed);saved=[]
                self.w.imports=SimpleNamespace(get=lambda id:{'id':id,'revision':1,'filename':original_name+'.png'},analyze=lambda *a:parsed)
                self.w.chips=SimpleNamespace(save_chip=lambda body:saved.append(deepcopy(body)) or {'id':'draft','name':body['draft']['name']})
                result=self.w._execute_proposal({'tool':'analyze_import','arguments':{'documentId':'doc'}},
                    {'operationId':'readable-name','message':'整理资料','intent':'execute'},
                    {'scope':{'kind':'workspace'},'documentIds':['doc']},0)
                self.assertEqual(saved[0]['draft']['name'],expected)
                self.assertEqual(saved[0]['draft']['qubits'],parsed['draft']['qubits'])
                self.assertEqual(parsed,before)
                self.assertIn(expected,result['message'])
                self.assertEqual(self.w.imports.get('doc')['filename'],original_name+'.png')

    def test_rejected_operation_reports_the_actual_reason_without_model_success_claim(self):
        from unittest.mock import patch
        conv=self.w.create_conversation({'operationId':'rejected-receipt-conv','scope':{'kind':'workspace'}})
        self.c._brain=lambda *a,**k:{'message':'已启动测量。','proposals':[{'tool':'campaign_action','arguments':{'action':'start'}}]}
        with patch.object(self.w,'_execute_proposal',return_value={'status':'rejected','message':'尚未确认接线','code':'BLOCKED'}):
            result=self.w.message(conv['id'],{'operationId':'rejected-receipt-message','message':'启动整批','intent':'execute'})
        self.assertEqual(result['messages'][-1]['content'],'未执行：尚未确认接线')

if __name__ == '__main__': unittest.main()
