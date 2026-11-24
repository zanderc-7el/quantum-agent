"""Campaign isolation, transactions and restart contracts with no hardware or model."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from backend.common import APIError, dumps
from backend.controller import Controller
from backend.workspace import Workspace, device_key
from tests.test_controller import FakeAdapter, make_fixture


class BoundFakeAdapter(FakeAdapter):
    physical_hardware = True
    def __init__(self, root, profile):
        super().__init__(root)
        self.profile = deepcopy(profile)
    def capabilities(self):
        return {**super().capabilities(), 'resources':list(self.profile['resources'])}
    def with_execution_enabled(self):
        return type(self)(self.root,{**self.profile,'enabled':True})
    def hardware_ownership(self,refresh=False):
        return {'verified':True,'owners':[]}
    def build_plan(self,*args,**kwargs):
        raise AssertionError('Campaign control tests must never dispatch an acquisition')
    def execute(self,*args,**kwargs):
        raise AssertionError('Campaign control tests must never execute hardware')


class CampaignContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='qcal-campaign-nohardware-')
        self.root=Path(self.tmp.name)
        self.c,self.adapter=make_fixture(self.root)
        self.w=Workspace(self.c,initialize_catalog=False)
        self.c._workspace_service=self.w
        self.binding_patch=patch('adapters.arclight_dynamic.DynamicArcLightAdapter',BoundFakeAdapter)
        self.binding_patch.start()
        self.w.chips=SimpleNamespace(resolve_binding=self.binding,list_chips=lambda:[],list_deployments=lambda:[],
            get_chip=lambda id:{'id':id,'entityId':'chip','qubits':[{'id':'Q'+str(q),'label':'Q'+str(q)} for q in range(11,20)]},
            get_deployment=lambda id:{'id':id,'entityId':'deployment'})

    def tearDown(self):
        self.binding_patch.stop()
        self.c.close()
        self.tmp.cleanup()

    def binding(self,chip,dep,resources):
        profile=deepcopy(self.adapter.profile)
        profile.update(id='fake-bound-'+chip+'-'+dep+'-'+resources[0],enabled=False)
        profile['resources']={q:{'q':q,'readout_hz':6.8e9,'readout_amp':.2,'drive_hz':4.4e9+int(q[1:])*1e6} for q in resources}
        profile['instrument']={'host':'127.0.0.99','port':7110,'resourceGroup':'deliberately-changing-'+resources[0]}
        return {'chipVersionId':chip,'deploymentVersionId':dep,
            'resources':[{'id':q,'label':q,'engineKey':q} for q in resources],
            'profile':profile,'source':{'explicit':'synthetic-fixture'}}

    def create(self,op='create-nine',**changes):
        return self.w.create_campaign({'operationId':op,'name':'nine resources','chipVersionId':'chip-v1',
            'deploymentVersionId':'deployment-v1','resourceIds':['Q'+str(q) for q in range(11,20)],'coverage':'full','policy':'threshold',**changes})

    def action(self,campaign,action,op,**extra):
        return self.w.campaign_action(campaign['id'],{'operationId':op,'expectedRevision':campaign['revision'],'action':action,**extra})

    def assertCode(self,code,fn):
        with self.assertRaises(APIError) as caught:fn()
        self.assertEqual(caught.exception.code,code)

    def test_nine_children_have_independent_frozen_bindings_and_repeat_create_is_identical(self):
        campaign=self.create()
        self.assertEqual(len(campaign['children']),9)
        self.assertEqual(len({ch['workflowId'] for ch in campaign['children']}),9)
        for child in campaign['children']:
            wf=self.c.get(child['workflowId'])
            self.assertEqual(wf['resourceIds'],[child['resourceId']])
            self.assertEqual(wf['campaignId'],campaign['id'])
            self.assertEqual(wf['coverage'],'full')
            self.assertEqual(wf['status'],'draft')
            self.assertFalse(wf['hardwareConfirmed'])
            self.assertFalse(wf['configurationBinding']['profile']['enabled'])
            self.assertTrue(all(n['resourceId']==child['resourceId'] for n in wf['nodes']))
            self.assertGreaterEqual(len(wf['nodes']),13)
        self.assertEqual(self.create(),campaign)
        self.assertEqual(len(self.c.list()),9)
        self.assertCode('IDEMPOTENCY_CONFLICT',lambda:self.create(name='different'))
        self.assertEqual(self.adapter.calls,[])

    def test_mid_creation_exception_rolls_back_campaign_children_steps_and_command(self):
        original=Controller.create;calls=[]
        def fail_fifth(controller,data,**kwargs):
            calls.append(data)
            if len(calls)==5:raise APIError('TEST_ATOMIC_FAILURE','explicit test interruption')
            return original(controller,data,**kwargs)
        with patch.object(Controller,'create',fail_fifth):
            self.assertCode('TEST_ATOMIC_FAILURE',lambda:self.create())
        self.assertEqual(self.c.list(),[])
        with self.c.store.connect() as rt:
            for table in ('platform_campaigns','platform_workflows','steps','workspace_commands'):
                self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0,table)
        self.assertEqual(len(self.create()['children']),9)

    def test_scoped_start_pause_resume_skip_and_repeated_operation(self):
        campaign=self.create();first=campaign['children'][0];second=campaign['children'][1]
        result=self.action(campaign,'start','start-first',resourceIds=[first['resourceId']])
        self.assertEqual(self.action(campaign,'start','start-first',resourceIds=[first['resourceId']]),result)
        self.assertEqual(self.c.get(first['workflowId'])['status'],'queued')
        self.assertEqual(self.c.get(second['workflowId'])['status'],'draft')
        self.assertTrue(self.c.get(first['workflowId'])['hardwareConfirmed'])
        self.assertTrue(self.adapter.profile['enabled'])
        paused=self.action(result,'pause','pause-first',resourceIds=[first['resourceId']])
        self.assertEqual(self.c.get(first['workflowId'])['status'],'paused')
        resumed=self.action(paused,'resume','resume-first',resourceIds=[first['resourceId']])
        self.assertEqual(self.c.get(first['workflowId'])['status'],'queued')
        skipped=self.action(resumed,'skip','skip-second',resourceIds=[second['resourceId']])
        self.assertEqual(next(c for c in skipped['children'] if c['resourceId']==second['resourceId'])['status'],'skipped')
        self.assertEqual(self.c.get(first['workflowId'])['status'],'queued')
        self.assertCode('STALE_REVISION',lambda:self.action(campaign,'start','stale'))
        self.assertCode('INVALID_SCOPE',lambda:self.action(skipped,'start','bad-scope',resourceIds=['Q1']))

    def test_batch_start_rolls_back_prior_children_when_device_blocker_appears(self):
        campaign=self.create();before=[self.c.get(ch['workflowId']) for ch in campaign['children']]
        calls=0
        def blocker(*args,**kwargs):
            nonlocal calls;calls+=1
            return {'id':'blocker'} if calls==5 else None
        with patch.object(self.w,'queue_blocker',blocker):
            self.assertCode('DEVICE_QUEUE_PAUSED',lambda:self.action(campaign,'start','start-all'))
        for old in before:
            wf=self.c.get(old['id'])
            self.assertEqual(wf['revision'],old['revision'])
            self.assertEqual(wf['status'],'draft')
            self.assertFalse(wf['hardwareConfirmed'])
        self.assertEqual(self.w.get_campaign(campaign['id'])['revision'],campaign['revision'])

    def test_binding_clone_preserves_root_and_resource_seeds(self):
        binding=self.binding('chip-v1','deployment-v1',['Q19'])
        bound=self.c.with_binding(binding)
        self.assertIs(self.c.adapter,self.adapter)
        self.assertNotEqual(bound.adapter_id,self.c.adapter_id)
        self.assertTrue(bound._physical_adapter)
        self.assertEqual(bound.adapter_profile_hash,bound._adapter_fingerprint())
        binding['profile']['resources']['Q19']['drive_hz']=1
        self.assertNotEqual(bound.profile['resources']['Q19']['drive_hz'],1)
        self.assertEqual(bound.records,[])

    def test_same_chassis_legacy_lease_blocks_dynamic_resource_despite_different_groups(self):
        campaign=self.create();one,two=campaign['children'][:2]
        wf=self.c.get(two['workflowId']);bound=self.c.for_workflow(wf['id'])
        bound._validate_plan=Mock(side_effect=AssertionError('lease must block before planning'))
        with self.c.store.transaction() as rt:
            rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',('old-custom-group',one['workflowId'],'old-attempt','quarantined','old-token'))
        self.assertFalse(bound._execute_node(wf['id'],wf['nodes'][0]['id']))
        bound._validate_plan.assert_not_called()
        self.assertEqual(self.c.get(wf['id'])['engineState'],'resource_busy')

    def test_resource_failure_continues_peer_but_device_failure_blocks_same_host(self):
        campaign=self.create();one,two=campaign['children'][:2]
        self.c._attention(one['workflowId'],'quality_attention','synthetic quality issue')
        self.assertIsNone(self.w.queue_blocker(self.c.get(two['workflowId'])))
        self.c._attention(one['workflowId'],'needs_reconciliation','synthetic receipt unknown')
        self.assertEqual(self.w.queue_blocker(self.c.get(two['workflowId']))['id'],one['workflowId'])
        other=self.binding('chip-v1','elsewhere',['Q19'])
        other['profile']['instrument']['host']='127.0.0.100'
        self.assertIsNone(self.w.queue_blocker({'id':'other','configurationBinding':other}))

    def test_restart_quarantines_pending_acquisition_without_dispatch(self):
        campaign=self.create();child=campaign['children'][0]
        with self.c.store.transaction() as rt:
            wf=self.c.get(child['workflowId'],rt)
            wf.update(status='running',engineState='measuring')
            self.c.store.save(rt,wf,'test_fixture')
            rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',(device_key(wf['configurationBinding']['profile']),wf['id'],'attempt','active','token'))
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',('attempt/measure',wf['id'],'attempt','pending',dumps({'resourceId':child['resourceId']}),None))
        self.c._recover_interrupted()
        current=self.c.get(child['workflowId'])
        self.assertEqual((current['status'],current['engineState']),('attention','needs_reconciliation'))
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT state FROM platform_leases').fetchone()[0],'quarantined')
            self.assertEqual(rt.db.execute('SELECT state FROM platform_effects').fetchone()[0],'unknown')
        self.assertEqual(self.adapter.calls,[])
        self.assertEqual(len(self.w.get_campaign(campaign['id'])['children']),9)

    def test_restart_clears_pending_conversation_without_replaying_proposals(self):
        conv=self.w.create_conversation({'operationId':'new-conv','scope':{'kind':'workspace'}})
        body={'operationId':'interrupted-message','message':'start Q11'}
        with self.c.store.transaction() as rt:
            old,fingerprint=self.w._command(rt,body,'message:'+conv['id'])
            conv['pending']=body['operationId']
            self.w._put(rt,'conversations',conv)
            self.w._remember(rt,body,fingerprint,conv)
        brain=Mock(side_effect=AssertionError('interrupted model proposal must not replay'))
        self.c._brain=brain
        reopened=Workspace(self.c,initialize_catalog=False)
        result=reopened.get_conversation(conv['id'])
        self.assertNotIn('pending',result)
        self.assertIn('中断',result['error'])
        self.assertEqual(reopened.message(conv['id'],body),result)
        brain.assert_not_called()
        self.assertEqual(self.w.list_campaigns(),[])

    def test_conversation_scope_rejects_cross_campaign_actions(self):
        first=self.create();second=self.create('create-second',name='second campaign',resourceIds=['Q19'])
        conv=self.w.create_conversation({'operationId':'campaign-conv','scope':{'kind':'campaign','id':first['id']}})
        self.assertCode('INVALID_SCOPE',lambda:self.w._execute_proposal({'tool':'campaign_action','arguments':{
            'campaignId':second['id'],'action':'start','resourceIds':['Q19']}},
            {'operationId':'cross-scope','message':'启动全部','intent':'execute'},conv,0))
        self.assertEqual(self.w.get_campaign(second['id'])['state'],'draft')
        proposed=self.w._execute_proposal({'tool':'campaign_action','arguments':{'campaignId':first['id'],'action':'start'}},
            {'operationId':'discuss','message':'讨论是否启动全部','intent':'discuss'},conv,0)
        self.assertEqual(proposed,{'status':'proposed'})
        self.assertEqual(self.w.get_campaign(first['id'])['state'],'draft')

    def test_blocked_resource_keeps_nine_child_campaign_and_cannot_bypass_gate(self):
        resolve=self.w.chips.resolve_binding
        def unresolved(chip,dep,resources):
            if resources==['Q15']:raise APIError('RESOURCE_NOT_READY','Q15 identity unresolved',409)
            return resolve(chip,dep,resources)
        self.w.chips.resolve_binding=unresolved
        campaign=self.create()
        blocked=next(c for c in campaign['children'] if c['resourceId']=='Q15')
        wf=self.c.get(blocked['workflowId'])
        self.assertEqual(wf['engineState'],'configuration_required')
        self.assertNotIn('configurationBinding',wf)
        result=self.action(campaign,'start','start-including-unresolved')
        self.assertEqual(next(v for v in result['outcomes'] if v['resourceId']=='Q15')['status'],'blocked')
        for action in ('start','resume','confirm_hardware'):
            current=self.c.get(wf['id'])
            with self.assertRaises(APIError):
                self.c.action(wf['id'],{'operationId':'bypass-'+action,'expectedRevision':current['revision'],'action':action})
        self.assertEqual(self.c.get(wf['id'])['engineState'],'configuration_required')
        self.assertEqual(sum(c['status']=='queued' for c in result['children']),8)

    def test_negated_user_request_cannot_authorize_start(self):
        campaign=self.create()
        conv=self.w.create_conversation({'operationId':'negative-conv','scope':{'kind':'campaign','id':campaign['id']}})
        with self.assertRaises(APIError):
            self.w._execute_proposal({'tool':'campaign_action','arguments':{'campaignId':campaign['id'],'action':'start'}},
                {'operationId':'negative-start','message':'不要启动整批，先解释计划','intent':'execute'},conv,0)
        self.assertEqual(self.w.get_campaign(campaign['id'])['state'],'draft')

    def test_chinese_adjacent_resource_names_preserve_explicit_scope(self):
        self.assertEqual(self.w._requested_resources('请给Q11新建任务'),{'Q11'})
        self.assertEqual(self.w._requested_resources('启动Q12和Q19'),{'Q12','Q19'})
        self.assertEqual(self.w._requested_resources('测Q11到Q19'),{'Q'+str(q) for q in range(11,20)})
        self.assertEqual(self.w._requested_resources('请使用otherQ19label'),set())

    def test_terminal_batch_aggregate_distinguishes_aborted_failed_partial(self):
        campaign=self.create()
        aborted=self.action(campaign,'abort','abort-entire-batch')
        self.assertEqual(aborted['state'],'aborted')
        second=self.create('create-partial',name='partly complete',resourceIds=['Q11','Q12'])
        with self.c.store.transaction() as rt:
            for child,state in zip(second['children'],('completed','aborted')):
                wf=self.c.get(child['workflowId'],rt);wf.update(status=state,engineState=state)
                self.c.store.save(rt,wf,'synthetic_terminal_state')
        self.assertEqual(self.w.get_campaign(second['id'])['state'],'partial')
        with self.c.store.transaction() as rt:
            wf=self.c.get(second['children'][1]['workflowId'],rt);wf.update(status='failed',engineState='failed')
            self.c.store.save(rt,wf,'synthetic_terminal_state')
        self.assertEqual(self.w.get_campaign(second['id'])['state'],'failed')

    def test_shared_ownership_fault_pauses_peer_until_explicit_fake_handover(self):
        campaign=self.create();one,two=campaign['children'][:2]
        started=self.action(campaign,'start','start-peer',resourceIds=[two['resourceId']])
        self.c._attention(one['workflowId'],'waiting_hardware','synthetic shared ownership conflict',shared_device_fault=True)
        peer=self.c.get(two['workflowId'])
        self.assertEqual(self.w.queue_blocker(peer)['id'],one['workflowId'])
        self.c.run_workflow(peer['id'])
        peer=self.c.get(peer['id'])
        self.assertEqual((peer['status'],peer['engineState']),('paused','device_queue_paused'))
        self.assertEqual(peer['deviceBlockerId'],one['workflowId'])
        item=next(i for i in self.w.attention_items() if i['workflowId']==one['workflowId'])
        self.assertEqual(item['scope'],'device')
        owner=self.c.get(one['workflowId'])
        changed=self.c.action(owner['id'],{'operationId':'fake-confirm-handover','expectedRevision':owner['revision'],'action':'confirm_hardware'})
        self.assertNotIn('sharedDeviceFault',changed)
        self.assertIsNone(self.w.queue_blocker(peer))
        self.assertEqual(self.adapter.calls,[])

    def test_resume_never_starts_drafts_and_no_eligible_action_is_rejected(self):
        campaign=self.create(resourceIds=['Q11','Q12'])
        self.assertCode('INVALID_STATE',lambda:self.action(campaign,'resume','resume-fresh-drafts'))
        self.assertEqual([c['status'] for c in self.w.get_campaign(campaign['id'])['children']],['draft','draft'])
        self.assertEqual(self.w.get_campaign(campaign['id'])['revision'],campaign['revision'])
        started=self.action(campaign,'start','start-one-to-pause',resourceIds=['Q11'])
        paused=self.action(started,'pause','pause-one',resourceIds=['Q11'])
        resumed=self.action(paused,'resume','resume-mixed')
        byid={c['resourceId']:c for c in resumed['children']}
        self.assertEqual((byid['Q11']['status'],byid['Q12']['status']),('queued','draft'))
        self.assertEqual(resumed['permits'][-1]['resourceIds'],['Q11'])
        self.assertEqual(self.adapter.calls,[])

    def test_start_all_applies_only_to_drafts_when_peer_is_paused(self):
        campaign=self.create(resourceIds=['Q11','Q12'])
        started=self.action(campaign,'start','start-q11',resourceIds=['Q11'])
        paused=self.action(started,'pause','pause-q11',resourceIds=['Q11'])
        result=self.action(paused,'start','start-mixed-batch')
        byid={c['resourceId']:c for c in result['children']}
        self.assertEqual((byid['Q11']['status'],byid['Q12']['status']),('paused','queued'))
        self.assertEqual(result['permits'][-1]['resourceIds'],['Q12'])
        self.assertEqual(self.adapter.calls,[])

    def test_running_skip_remains_running_until_real_cancellation_checkpoint(self):
        campaign=self.create(resourceIds=['Q11'])
        wf=self.c.get(campaign['children'][0]['workflowId'])
        with self.c.store.transaction() as rt:
            wf.update(status='running',engineState='acquiring')
            self.c.store.save(rt,wf,'fixture_running')
        requested=self.action(campaign,'skip','skip-active-measurement')
        active=self.c.get(wf['id'])
        self.assertEqual((requested['state'],requested['children'][0]['status']),('running','running'))
        self.assertEqual(active['engineState'],'stopping_at_checkpoint')
        self.assertTrue(active['skipRequested'])
        self.assertFalse(active['skipped'])
        self.assertCode('INVALID_STATE',lambda:self.action(requested,'pause','cannot-replace-skip-with-pause'))
        self.c._finish_cancel(wf['id'])
        finished=self.w.get_campaign(campaign['id'])
        self.assertEqual(self.c.get(wf['id'])['status'],'aborted')
        self.assertEqual(finished['children'][0]['status'],'skipped')
        self.assertEqual(self.adapter.calls,[])

    def test_skip_with_uncertain_receipt_stays_attention_until_reconciled(self):
        campaign=self.create(resourceIds=['Q11'])
        wf=self.c.get(campaign['children'][0]['workflowId'])
        with self.c.store.transaction() as rt:
            wf.update(status='running',engineState='acquiring')
            self.c.store.save(rt,wf,'fixture_running')
        self.action(campaign,'skip','skip-before-unknown-receipt')
        with self.c.store.transaction() as rt:
            rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',('fixture-bus',wf['id'],'fixture-attempt','quarantined','fixture-token'))
        self.c._finish_cancel(wf['id'])
        current=self.w.get_campaign(campaign['id'])
        self.assertEqual(current['children'][0]['status'],'attention')
        self.assertEqual(current['children'][0]['engineState'],'needs_reconciliation')
        self.assertCode('INVALID_STATE',lambda:self.action(current,'resume','no-resume-unknown-skip'))
        self.assertEqual(self.adapter.calls,[])

if __name__=='__main__':unittest.main()
