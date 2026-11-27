"""Human declarations are scoped to one command; no devices or physical claims."""
from copy import deepcopy
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.common import APIError,digest
from tests.test_controller import make_fixture
from tests.test_science_flux_commanded import route


class BiasStartConfirmation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.c,self.fake=make_fixture(Path(self.tmp.name));self.addCleanup(self.c.close)
        from tests.test_flux_controller import FluxControllerContracts
        helper=FluxControllerContracts();helper.c,helper.fake=self.c,self.fake
        body=helper.fixture();self.route=route();self.route['resourceId']='Q11'
        self.fake.profile['biasRoutes']={'Q11':self.route};self.c.adapter_profile_hash=self.c._adapter_fingerprint()
        self.wf=self.c.create(body);self.node=self.wf['nodes'][0]
        self.body={'action':'run_step','nodeId':self.node['id'],'operationId':'human-command','expectedRevision':self.wf['revision']}
        self.confirmation={'confirmed':True,'routeSha256':digest(self.route),'startSettingV':self.route['startSettingV'],'endSettingV':self.route['endSettingV']}

    def test_missing_false_wrong_route_or_values_cannot_queue_or_lease(self):
        for confirmation in (None,{**self.confirmation,'confirmed':False},{**self.confirmation,'routeSha256':'0'*64},{**self.confirmation,'startSettingV':.923},{**self.confirmation,'confirmed':1}):
            with self.subTest(confirmation=confirmation),self.assertRaises(APIError) as caught:
                self.c.action(self.wf['id'],{**self.body,**({'biasStartConfirmation':confirmation} if confirmation is not None else {})})
            self.assertIn(caught.exception.code,('BIAS_START_CONFIRMATION_REQUIRED','BIAS_START_CONFIRMATION_CHANGED'))
            self.assertEqual(self.c.get(self.wf['id'])['revision'],self.wf['revision'])
        with self.c.store.connect() as rt:
            for table in ('platform_effects','platform_leases','platform_attempts','platform_commands'):
                # Task creation itself may have a command; no failed action is persisted.
                if table=='platform_commands':continue
                self.assertEqual(rt.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_confirmation_frozen_to_unique_command_revision_and_actual_attempt(self):
        with patch.object(self.c,'_validate_plan',return_value={}):
            queued=self.c.action(self.wf['id'],{**self.body,'biasStartConfirmation':self.confirmation})
            replay=self.c.action(self.wf['id'],{**self.body,'biasStartConfirmation':self.confirmation})
        self.assertEqual(queued['revision'],replay['revision'])
        from backend.flux_execution import selection_confirmation
        queued['manualStep'].update(state='running',attemptId='attempt-fixture')
        request={'operationId':'attempt-fixture/measure','attemptId':'attempt-fixture','runId':queued['id'],'nodeId':self.node['id'],'resourceId':'Q11'}
        value=selection_confirmation(queued,self.node,request,self.route)
        self.assertEqual(value['commandOperationId'],self.body['operationId']);self.assertEqual(value['confirmedRevision'],self.wf['revision'])
        self.assertEqual(value['operationId'],request['operationId']);self.assertEqual(value['attemptId'],request['attemptId'])
        self.assertEqual(value['confirmationId'],digest({k:v for k,v in value.items() if k!='confirmationId'}))
        with self.assertRaises(APIError):selection_confirmation(queued,self.node,{**request,'attemptId':'another'},self.route)
        changed=deepcopy(self.route);changed['endSettingV']=.9
        with self.assertRaises(APIError):selection_confirmation(queued,self.node,request,changed)

    def test_read_presentation_shows_line_and_settings_not_measurement(self):
        presented=self.c.present_workflows([self.wf])[0]
        requirement=presented['biasStartRequirements'][self.node['id']]
        self.assertEqual(requirement,{'resourceId':'Q11','nodeId':self.node['id'],'lineId':self.route['lineId'],'routeSha256':digest(self.route),'startSettingV':self.route['startSettingV'],'endSettingV':self.route['endSettingV'],'unit':'V'})
        self.assertNotIn('biasStartRequirements',self.c.get(self.wf['id']))

    def test_agent_single_step_cannot_supply_or_infer_operator_confirmation(self):
        from backend.workspace_manual import execute
        args={'workflowId':self.wf['id'],'nodeId':self.node['id'],'expectedRevision':self.wf['revision'],
            'expectedAttemptId':self.node.get('latestAttemptId'),'remeasure':False}
        body={'operationId':'agent-command','selectedWorkflowId':self.wf['id'],
            'message':f'请对任务“{self.wf["name"]}”的“{self.node["label"]}”执行一次测量，完成本步后等待确认，不运行后续步骤。'}
        with self.assertRaises(APIError) as caught:execute(self.c.workspace,args,body,{'scope':{'kind':'workspace'}})
        self.assertEqual(caught.exception.code,'BIAS_START_CONFIRMATION_REQUIRED')
        self.assertEqual(self.c.get(self.wf['id'])['revision'],self.wf['revision'])
        self.assertNotIn('manualStep',self.c.get(self.wf['id']))

if __name__=='__main__':unittest.main()
