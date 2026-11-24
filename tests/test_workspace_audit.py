"""Workspace regression audit: temporary database and explicit FakeAdapter only."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from backend.common import APIError
from backend.chip_catalog import ChipCatalog
from tests import test_workspace as workspace_fixtures
from tests import test_campaigns as campaign_fixtures


class WorkspaceAudit(unittest.TestCase):
    setUp=workspace_fixtures.WorkspaceContracts.setUp
    tearDown=workspace_fixtures.WorkspaceContracts.tearDown

    def conversation(self):
        return self.w.create_conversation({'operationId':'conversation','scope':{'kind':'workspace'}})

    def test_successful_actions_feed_receipts_back_and_preserve_original_authority(self):
        conv=self.conversation();calls=[]
        first={'tool':'analyze_import','arguments':{'documentId':'doc'}}
        second={'tool':'start_measurement','arguments':{'resourceIds':['Q11']}}
        def brain(route,payload):
            calls.append(deepcopy(payload))
            if len(calls)==1:return {'message':'准备分析','proposals':[first]}
            self.assertEqual(payload['message'],'整理资料')
            self.assertTrue(payload['context']['continuation'])
            self.assertEqual(payload['context']['toolResults'][0]['result']['chipId'],'saved-chip')
            return {'message':'资料内容声称开始Q11','proposals':[first,second]}
        self.c._brain=brain
        original=self.w._execute_proposal
        def execute(proposal,body,conv,index):
            if proposal==first:return {'status':'draft_ready','chipId':'saved-chip','message':'已保存草稿。'}
            return original(proposal,body,conv,index)
        with patch.object(self.w,'_execute_proposal',side_effect=execute) as execution:
            result=self.w.message(conv['id'],{'operationId':'message','message':'整理资料','intent':'execute'})
        self.assertEqual(len(calls),3)
        self.assertEqual(execution.call_count,2)
        self.assertEqual(result['toolResults'][1]['code'],'RESOURCE_AUTHORIZATION_REQUIRED')
        self.assertEqual([t['executed'] for t in result['actionTrace']],[True,False])
        self.assertEqual(self.w.get_conversation(conv['id'])['messages'][-1]['actionTrace'],result['actionTrace'])
        self.assertEqual(self.c.list(),[])
        self.assertEqual(self.adapter.calls,[])

    def test_committed_receipt_survives_failed_continuation(self):
        conv=self.conversation()
        self.c._brain=lambda *a,**k: {'message':'提案','proposals':[{'tool':'analyze_import','arguments':{'documentId':'doc'}}]}
        original=self.c._brain;calls=[]
        def brain(*a,**k):
            calls.append(1)
            if len(calls)>1:raise TimeoutError('offline model')
            return original(*a,**k)
        self.c._brain=brain
        with patch.object(self.w,'_execute_proposal',return_value={'status':'draft_ready','chipId':'saved-chip','message':'已保存草稿。'}):
            result=self.w.message(conv['id'],{'operationId':'message','message':'整理资料','intent':'execute'})
        self.assertEqual(len(result['toolResults']),1)
        self.assertIn('已保存草稿',result['messages'][-1]['content'])
        self.assertEqual(result['messages'][-1]['analysisMessage'],'后续分析暂未完成，已执行操作保留。')

    def test_committed_receipt_survives_context_refresh_failure(self):
        conv=self.conversation()
        self.c._brain=lambda *a,**k:{'message':'提案','proposals':[{'tool':'analyze_import','arguments':{'documentId':'doc'}}]}
        with patch.object(self.w,'_context',side_effect=[{},RuntimeError('catalog temporarily unavailable')]),patch.object(
                self.w,'_execute_proposal',return_value={'status':'draft_ready','chipId':'saved-chip','message':'已保存草稿。'}):
            result=self.w.message(conv['id'],{'operationId':'message','message':'整理资料','intent':'execute'})
        self.assertEqual(result['toolResults'][0]['chipId'],'saved-chip')
        self.assertEqual(result['messages'][-1]['analysisMessage'],'后续分析暂未完成，已执行操作保留。')

    def test_unavailable_continuation_does_not_override_committed_receipt_or_execute_proposals(self):
        conv=self.conversation()
        for index,status in enumerate(('waiting_model','model_invalid_continuation','error')):
            with self.subTest(status=status):
                replies=[{'status':'ok','message':'保存提案','proposals':[{'tool':'analyze_import','arguments':{'documentId':'doc'}}]},
                         {'status':status,'message':'实验状态未改变','proposals':[{'tool':'start_measurement','arguments':{'resourceIds':['Q11']}}]}]
                self.c._brain=lambda *a,**k:replies.pop(0)
                with patch.object(self.w,'_execute_proposal',return_value={'status':'draft_ready','chipId':'saved-chip','message':'已保存草稿。'}) as execution:
                    result=self.w.message(conv['id'],{'operationId':'status-message-'+str(index),'message':'整理资料','intent':'execute'})
                self.assertEqual(execution.call_count,1)
                self.assertEqual(len(result['toolResults']),1)
                self.assertEqual(result['toolResults'][0]['chipId'],'saved-chip')
                self.assertIn('已保存草稿',result['messages'][-1]['content'])
                self.assertEqual(result['messages'][-1]['analysisMessage'],'后续分析暂未完成，已执行操作保留。')
                self.assertEqual(self.w.get_conversation(conv['id'])['messages'][-1],result['messages'][-1])
                self.assertEqual(self.adapter.calls,[])

    def test_conversation_revision_progress_cannot_block_explicit_stop(self):
        conv=self.conversation()
        self.w.message(conv['id'],{'operationId':'start','message':'开始Q11','intent':'auto'})
        result=self.w.message(conv['id'],{'operationId':'pause','message':'暂停Q11','intent':'auto','expectedRevision':conv['revision']})
        self.assertEqual(self.c.list()[0]['status'],'paused')
        self.assertEqual(result['toolResults'][0]['workflowState'],'paused')

    def test_continuations_have_three_round_limit_and_ten_total_actions(self):
        conv=self.conversation();calls=[]
        def brain(route,payload):
            calls.append(payload)
            return {'message':'提案','proposals':[{'tool':'fake','arguments':{'index':len(calls)*10+i}} for i in range(4)]}
        self.c._brain=brain
        with patch.object(self.w,'_execute_proposal',return_value={'status':'configuration_receipt','message':'保存测试草稿'}):
            result=self.w.message(conv['id'],{'operationId':'message','message':'保存Q19配置','intent':'execute'})
        self.assertEqual(len(calls),3)
        self.assertEqual(len(result['toolResults']),10)

    def test_discussion_never_continues_or_mutates(self):
        conv=self.conversation();calls=[]
        self.c._brain=lambda route,payload: calls.append(payload) or {'message':'建议','proposals':[{'tool':'save_chip_draft','arguments':{}}]}
        result=self.w.message(conv['id'],{'operationId':'message','message':'怎么保存Q19配置？','intent':'auto'})
        self.assertEqual(len(calls),1)
        self.assertEqual(result['toolResults'],[{'status':'proposed'}])
        self.assertIn('建议尚未执行。',result['messages'][-1]['content'])
        self.assertEqual(self.c.list(),[])

    def test_unexecuted_discussion_proposals_keep_model_text_and_show_notice_once(self):
        conv=self.conversation()
        for index,intent in enumerate(('discuss','auto')):
            for present in (False,True):
                model_text='可以按16张图一页导出。'+('建议尚未执行。' if present else '')
                self.c._brain=lambda *a,**k:{'message':model_text,'proposals':[{'tool':'export_report','arguments':{'workflowId':'unexecuted'}}]}
                result=self.w.message(conv['id'],{'operationId':'discussion-'+str(index)+'-'+str(present),
                    'message':'如何导出报告？','intent':intent})
                content=result['messages'][-1]['content']
                self.assertIn(model_text,content)
                self.assertEqual(content.count('建议尚未执行。'),1)
                self.assertEqual(result['toolResults'],[{'status':'proposed'}])
        self.assertEqual(self.c.list(),[])
        self.assertEqual(self.adapter.calls,[])

    def test_direct_task_receipt_is_available_in_next_model_context(self):
        conv=self.conversation()
        result=self.w.message(conv['id'],{'operationId':'start','message':'开始Q11','intent':'auto'})
        calls=[]
        self.c._brain=lambda route,payload:calls.append(payload) or {'message':'读取当前配置。','proposals':[]}
        self.w.message(conv['id'],{'operationId':'inspect','message':'你自己看看，应该已经配置好了吧','intent':'auto'})
        history=calls[0]['context']['conversationHistory']
        self.assertTrue(any(m['role']=='assistant' and '测量队列' in m['content'] for m in history))
        self.assertEqual(calls[0]['context']['workflows'][0]['status'],'queued')
        self.assertEqual(len(self.c.list()),1)
        self.assertEqual(self.adapter.calls,[])

    def test_unknown_peer_does_not_block_configured_resource_and_is_recorded(self):
        conv=self.conversation()
        body={'operationId':'mixed-start','message':'开始Q11和Q19','intent':'auto'}
        result=self.w.message(conv['id'],body)
        self.assertEqual([(w['resourceIds'],w['status']) for w in self.c.list()],[(['Q11'],'queued')])
        outcomes=result['toolResults'][0]['outcomes']
        self.assertTrue(any(o.get('resourceId')=='Q19' and o['status']=='blocked' for o in outcomes))
        self.assertIn('Q19',result['messages'][-1]['content'])
        self.assertEqual(self.w.message(conv['id'],body),result)
        self.assertEqual(len(self.c.list()),1)
        self.assertEqual(self.adapter.calls,[])

    def test_start_after_pause_explicitly_reports_new_task(self):
        conv=self.conversation()
        self.w.message(conv['id'],{'operationId':'start','message':'开始Q11','intent':'auto'})
        self.w.message(conv['id'],{'operationId':'pause','message':'暂停Q11','intent':'auto'})
        result=self.w.message(conv['id'],{'operationId':'new-start','message':'开始Q11','intent':'auto'})
        self.assertEqual(sorted(w['status'] for w in self.c.list()),['paused','queued'])
        self.assertIn('已创建新任务',result['messages'][-1]['content'])

    def test_rejected_proposal_receipt_can_be_repaired_without_duplicate_execution(self):
        conv=self.conversation();calls=[]
        def brain(route,payload):
            calls.append(payload)
            if len(calls)==1:return {'message':'检查','proposals':[{'tool':'not_allowed','arguments':{}}]}
            self.assertEqual(payload['context']['toolResults'][0]['status'],'rejected')
            return {'message':'当前请求无需新任务。','proposals':[]}
        self.c._brain=brain
        result=self.w.message(conv['id'],{'operationId':'inspect','message':'整理资料','intent':'execute'})
        self.assertEqual(len(calls),2)
        self.assertEqual(result['messages'][-1]['analysisMessage'],'当前请求无需新任务。')
        self.assertEqual(self.c.list(),[])


class CampaignStopAudit(unittest.TestCase):
    setUp=campaign_fixtures.CampaignContracts.setUp
    tearDown=campaign_fixtures.CampaignContracts.tearDown
    binding=campaign_fixtures.CampaignContracts.binding
    create=campaign_fixtures.CampaignContracts.create
    action=campaign_fixtures.CampaignContracts.action

    def test_stale_batch_stop_succeeds_and_replay_cannot_dispatch(self):
        campaign=self.create(resourceIds=['Q11','Q12'])
        self.action(campaign,'start','start')
        paused=self.action(campaign,'pause','old-pause',resourceIds=['Q11'])
        self.assertEqual([c['status'] for c in paused['children']],['paused','queued'])
        self.assertEqual(self.action(campaign,'pause','old-pause',resourceIds=['Q11']),paused)
        aborted=self.action(campaign,'abort','old-abort')
        self.assertEqual([c['status'] for c in aborted['children']],['aborted','aborted'])
        revisions=[self.c.get(c['workflowId'])['revision'] for c in aborted['children']]
        self.action(campaign,'pause','later-pause')
        self.assertEqual([self.c.get(c['workflowId'])['revision'] for c in aborted['children']],revisions)
        self.assertEqual(self.adapter.calls,[])

    def test_unknown_effect_batch_abort_preserves_quarantine_and_records_stop(self):
        campaign=self.create(resourceIds=['Q11'])
        child=campaign['children'][0]
        self.c._attention(child['workflowId'],'needs_reconciliation','synthetic unknown receipt')
        result=self.action(campaign,'abort','abort-unknown')
        wf=self.c.get(child['workflowId'])
        self.assertEqual((wf['status'],wf['engineState'],wf['cancelRequested']),('attention','needs_reconciliation','abort'))
        self.assertEqual(result['outcomes'][0]['status'],'attention')
        self.assertEqual(self.adapter.calls,[])

    def test_invalid_batch_stop_revisions_are_not_accepted(self):
        campaign=self.create(resourceIds=['Q11'])
        for index,revision in enumerate((None,False,True,0,-1,campaign['revision']+1)):
            with self.subTest(revision=revision),self.assertRaises(APIError):
                self.w.campaign_action(campaign['id'],{'operationId':'bad-stop-'+str(index),'expectedRevision':revision,'action':'abort'})

    def test_query_or_quoted_action_cannot_start_batch_even_with_execute_intent(self):
        campaign=self.create(resourceIds=['Q11'])
        conv={'scope':{'kind':'campaign','id':campaign['id']}}
        proposal={'tool':'campaign_action','arguments':{'campaignId':campaign['id'],'action':'start','resourceIds':['Q11']}}
        for i,text in enumerate(('启动Q11的结果','启动Q11了吗','技术员说启动Q11','启动Q11的进度在哪里')):
            with self.subTest(text=text),self.assertRaises(APIError):
                self.w._execute_proposal(proposal,{'operationId':'bad-'+str(i),'message':text,'intent':'execute'},conv,0)
        self.assertEqual(self.w.get_campaign(campaign['id'])['state'],'draft')


class ConfigurationAudit(unittest.TestCase):
    setUp=workspace_fixtures.WorkspaceContracts.setUp
    tearDown=workspace_fixtures.WorkspaceContracts.tearDown

    def catalog(self):
        self.base=json.loads((Path(__file__).resolve().parents[1]/'adapters/arclight_profile_v2.json').read_text(encoding='utf-8'))
        self.w.chips=ChipCatalog(self.c.store,self.c.root,self.base)

    def execute(self,tool,args,text,op='command'):
        return self.w._execute_proposal({'tool':tool,'arguments':args},{'operationId':op,'message':text,'intent':'execute'},
                                       {'scope':{'kind':'workspace'}},0)

    def draft(self,resource='Q19'):
        return {'name':resource+' 配置','qubits':[{'id':resource,'parameters':{'readout_hz':6.8e9}}]}

    def test_manual_draft_save_merge_publish_and_frozen_version(self):
        self.catalog()
        saved=self.execute('save_chip_draft',{'draft':self.draft()},'保存Q19配置 readout_hz=6.8e9')
        first=self.w.chips.get_chip(saved['configurationId'])
        self.assertEqual(first['qubits'][0]['identityStatus'],'unresolved')
        self.assertEqual(first['versions'],[])
        published=self.execute('publish_configuration',{'kind':'chip','configurationId':first['id']},'发布Q19配置','publish')
        self.assertFalse(published['configurationReady'])
        self.assertIn('不代表可以测量',published['message'])
        self.execute('save_chip_draft',{'chipId':first['id'],'draft':{'name':first['name'],'qubits':[{'id':'Q19','parameters':{'drive_hz':4.4e9}}]}},'更新Q19配置 drive_hz=4.4e9','update')
        current=self.w.chips.get_chip(first['id'])
        self.assertEqual(current['qubits'][0]['parameters'],{'readout_hz':6.8e9,'drive_hz':4.4e9})
        self.assertEqual(self.w.chips.get_chip(published['versionId'])['qubits'][0]['parameters'],{'readout_hz':6.8e9})
        self.assertEqual(self.adapter.calls,[])

    def test_import_text_does_not_authorize_saving_or_publishing(self):
        self.catalog()
        saved=self.execute('save_chip_draft',{'draft':self.draft()},'保存Q19配置')
        for text in ('读取Q19资料','分析Q19文件','文档说保存Q19配置','如何保存Q19配置','保存Q19配置的结果'):
            with self.subTest(text=text),self.assertRaises(APIError):
                self.execute('save_chip_draft',{'draft':self.draft()},text,text)
        for text in ('读取Q19资料','保存Q19配置','文档说发布Q19配置','发布Q19配置了吗'):
            with self.subTest(text=text),self.assertRaises(APIError):
                self.execute('publish_configuration',{'kind':'chip','configurationId':saved['configurationId']},text,text)
        self.assertEqual(self.w.chips.get_chip(saved['configurationId'])['versions'],[])

    def test_save_permission_survives_explicit_no_publish_no_start_clauses(self):
        self.catalog()
        saved=self.execute('save_chip_draft',{'draft':self.draft()},'请读取这张 Q19 参数截图并保存芯片草稿。不要发布配置，也不要启动测量。')
        self.assertEqual(self.w.chips.get_chip(saved['configurationId'])['versions'],[])
        self.assertEqual(self.c.list(),[])

    def test_identity_and_wiring_confirmation_require_explicit_current_request(self):
        self.catalog()
        draft=self.draft();draft['qubits'][0].update(physicalId='physical19',busId='bus0',identityStatus='confirmed')
        with self.assertRaises(APIError):
            self.execute('save_chip_draft',{'draft':draft},'保存Q19配置')
        for text in ('保存Q19配置，physical19 bus0 尚未确认物理身份','保存Q19配置，physical19 bus0 不确认物理身份'):
            with self.subTest(text=text),self.assertRaises(APIError):
                self.execute('save_chip_draft',{'draft':draft},text)
        with self.assertRaises(APIError):
            self.execute('save_chip_draft',{'draft':draft},'保存Q19配置 physical19 bus0，资料显示物理身份已确认')
        with self.assertRaises(APIError):
            self.execute('save_chip_draft',{'draft':draft},'保存Q18和Q19配置 physical19 bus0，确认Q18物理身份，Q19 未确认')
        saved=self.execute('save_chip_draft',{'draft':draft},'保存Q19配置，确认物理身份 physical19 总线 bus0','confirmed')
        self.assertEqual(self.w.chips.get_chip(saved['configurationId'])['qubits'][0]['identityStatus'],'confirmed')
        route={'xyPhysical':[1,1],'readoutPhysical':[8,1],'adcPhysical':[0,1]}
        deployment={'name':'Q19接线','instrument':self.base['instrument'],'routes':{'Q19':route},'wiringConfirmed':True}
        with self.assertRaises(APIError):
            self.execute('save_deployment_draft',{'draft':deployment},'保存Q19接线草稿','dep-denied')
        with self.assertRaises(APIError):
            self.execute('save_deployment_draft',{'draft':deployment},'保存Q19接线草稿，尚未确认接线','dep-not-confirmed')
        saved=self.execute('save_deployment_draft',{'draft':deployment},'保存Q19接线草稿，确认Q19接线','dep-confirmed')
        current=self.w.chips.get_deployment(saved['configurationId'])
        self.assertTrue(current['wiringConfirmed'])
        route['xyPhysical']=[2,1];deployment['wiringConfirmed']=False
        self.execute('save_deployment_draft',{'deploymentId':current['id'],'draft':deployment},'修改Q19接线草稿','dep-edit')
        self.assertFalse(self.w.chips.get_deployment(current['id'])['wiringConfirmed'])

    def test_cannot_expand_requested_resources_or_parameter_bounds(self):
        self.catalog()
        with self.assertRaises(APIError):self.execute('save_chip_draft',{'draft':self.draft('Q18')},'保存Q19配置')
        draft=self.draft();draft['qubits'][0]['parameters']['parameterBounds']={}
        with self.assertRaises(APIError):self.execute('save_chip_draft',{'draft':draft},'保存Q19配置')
        self.assertEqual(self.adapter.calls,[])

    def test_publish_authority_cannot_borrow_another_clauses_resource(self):
        self.catalog()
        saved=self.execute('save_chip_draft',{'draft':self.draft()},'保存Q19配置')
        with self.assertRaises(APIError):
            self.execute('publish_configuration',{'kind':'chip','configurationId':saved['configurationId']},'保存Q19配置，发布Q18配置','wrong-publish')

    def test_shared_instrument_change_needs_all_existing_resources_in_scope(self):
        self.catalog()
        route={'xyPhysical':[1,1],'readoutPhysical':[8,1],'adcPhysical':[0,1]}
        deployment={'name':'shared','instrument':self.base['instrument'],'routes':{'Q18':route,'Q19':route}}
        saved=self.execute('save_deployment_draft',{'draft':deployment},'保存Q18和Q19接线草稿')
        instrument=deepcopy(self.base['instrument']);instrument.update(host='127.0.0.99',address='TCPIP::127.0.0.99::'+str(instrument['port'])+'::SOCKET')
        edit={'name':'shared','instrument':instrument,'routes':{'Q19':route}}
        with self.assertRaises(APIError):
            self.execute('save_deployment_draft',{'deploymentId':saved['configurationId'],'draft':edit},'修改Q19接线配置','cross-scope-device')
        self.assertEqual(self.w.chips.get_deployment(saved['configurationId'])['instrument'],self.base['instrument'])

    def test_save_then_publish_uses_real_draft_id_from_feedback(self):
        self.catalog()
        conv=self.w.create_conversation({'operationId':'conversation','scope':{'kind':'workspace'}})
        calls=[]
        def brain(route,payload):
            calls.append(payload)
            if len(calls)==1:return {'message':'先保存','proposals':[{'tool':'save_chip_draft','arguments':{'draft':self.draft()}}]}
            if len(calls)==2:
                saved=payload['context']['toolResults'][0]['result']
                return {'message':'发布刚保存草稿','proposals':[{'tool':'publish_configuration','arguments':{'kind':'chip','configurationId':saved['configurationId']}}]}
            self.assertIn('versionId',payload['context']['toolResults'][1]['result'])
            return {'message':'发布后仍需补齐参数和身份。','proposals':[]}
        self.c._brain=brain
        result=self.w.message(conv['id'],{'operationId':'save-publish','message':'保存Q19配置并发布Q19配置','intent':'auto'})
        self.assertEqual(len(calls),3)
        self.assertEqual(len(result['toolResults']),2)
        self.assertTrue(all(t['executed'] for t in result['actionTrace']))
        self.assertEqual(self.c.list(),[])
        self.assertEqual(self.adapter.calls,[])

    def test_saved_and_published_complete_configuration_binds_and_queues_only_fake_adapter(self):
        self.catalog()
        params={key:value for key,value in self.base['resources']['Q11'].items() if key in self.base['parameterBounds']}
        draft={'name':'Q19 完整配置','qubits':[{'id':'Q19','physicalId':'P19','busId':'B0','identityStatus':'confirmed','parameters':params}]}
        chip=self.execute('save_chip_draft',{'draft':draft},'保存Q19配置，确认Q19物理身份 P19 总线 B0')
        cv=self.execute('publish_configuration',{'kind':'chip','configurationId':chip['configurationId']},'发布Q19配置','publish-chip')
        self.assertTrue(cv['configurationReady'])
        dep={'name':'Q19 接线','instrument':self.base['instrument'],
             'routes':{'Q19':{'xyPhysical':[1,1],'readoutPhysical':[8,1],'adcPhysical':[0,1]}},'wiringConfirmed':True}
        deployment=self.execute('save_deployment_draft',{'draft':dep},'保存Q19接线，确认Q19接线','save-deployment')
        dv=self.execute('publish_configuration',{'kind':'deployment','configurationId':deployment['configurationId']},'发布Q19接线','publish-deployment')
        # This pure validator reads the checked-in runner hash, with no driver or
        # instrument connection. All writes and workflow state remain temporary.
        self.w.chips.root=Path(__file__).resolve().parents[1]
        binding=self.w.chips.resolve_binding(cv['versionId'],dv['versionId'],['Q19'])
        self.assertEqual(binding['profile']['parameterBounds'],self.base['parameterBounds'])
        self.assertEqual(binding['profile']['resources']['Q19']['physical_id'],'P19')
        manifest=binding['profile']['sourceManifest']
        fixture_manifest=self.c.root/manifest
        fixture_manifest.parent.mkdir(parents=True,exist_ok=True)
        fixture_manifest.write_bytes((self.w.chips.root/manifest).read_bytes())
        conv=self.w.create_conversation({'operationId':'conversation','scope':{'kind':'workspace'}})
        with patch.object(self.w.chips,'resolve_binding',return_value=binding),patch(
                'adapters.arclight_dynamic.DynamicArcLightAdapter',campaign_fixtures.BoundFakeAdapter):
            result=self.w.message(conv['id'],{'operationId':'start','message':'开始Q19','intent':'auto'})
        self.assertTrue(self.c.list(),result['messages'][-1]['content'])
        self.assertEqual(self.c.list()[0]['status'],'queued')
        self.assertEqual(self.c.list()[0]['resourceIds'],['Q19'])
        self.assertEqual(result['toolResults'][0]['outcomes'][0]['workflowState'],'queued')
        self.assertEqual(self.adapter.calls,[])


if __name__=='__main__':unittest.main()
