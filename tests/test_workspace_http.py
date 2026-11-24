"""New workspace HTTP routing with temporary state and explicit nohardware stubs."""
from copy import deepcopy
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from backend.server import Server, Handler
from backend.workspace import Workspace
from tests.test_controller import make_fixture
from tests.test_campaigns import BoundFakeAdapter
from tests import test_campaigns as campaign_fixtures

class WorkspaceHTTPContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='qcal-workspace-http-nohardware-')
        self.root=Path(self.tmp.name)
        self.controller,self.adapter=make_fixture(self.root)
        self.workspace=Workspace(self.controller,initialize_catalog=False)
        self.controller._workspace_service=self.workspace
        self.binding_patch=patch('adapters.arclight_dynamic.DynamicArcLightAdapter',BoundFakeAdapter)
        self.binding_patch.start()
        self.workspace.chips=SimpleNamespace(resolve_binding=lambda c,d,r:campaign_fixtures.CampaignContracts.binding(self,c,d,r),
            list_chips=lambda:[],get_chip=lambda id:{'id':id,'entityId':'chip','qubits':[{'id':'Q'+str(q),'label':'Q'+str(q)} for q in range(11,20)]},
            list_deployments=lambda:[],get_deployment=lambda id:{'id':id,'entityId':'deployment'},resources=lambda:[],
            save_chip=lambda body:{'id':'chip'},save_deployment=lambda body:{'id':'dep'},
            publish_chip=lambda id,body:{'id':id},publish_deployment=lambda id,body:{'id':id})
        self.import_calls=[]
        def upload(body):
            self.import_calls.append(deepcopy(body));return {'id':'document-stub','status':'uploaded','size':len(body.get('contentBase64',''))}
        self.workspace.imports=SimpleNamespace(list=lambda:[],get=lambda id:{'id':id,'blocks':[]},create=upload,update=lambda id,b:{'id':id},analyze=lambda id,b:{'id':id})
        self.server=Server(('127.0.0.1',0),self.controller)
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True)
        self.thread.start()
        self.token=self.request('GET','/api/session')[1]['token']

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
        self.controller.close();self.binding_patch.stop();self.tmp.cleanup()

    def request(self,method,path,body=None,headers=None,auth=False):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=4)
        request_headers={'Connection':'close',**({'X-Qcal-Token':self.token} if auth else {}),**(headers or {})}
        if body is not None:
            body=json.dumps(body).encode();request_headers['Content-Type']='application/json'
        try:
            connection.request(method,path,body=body,headers=request_headers)
            response=connection.getresponse();result=json.loads(response.read())
            return response.status,result
        finally:connection.close()

    def reject_declared_length(self,method,path,length):
        # Framing rejection must happen before payload delivery. Header-only
        # requests avoid a Windows TCP reset racing the intentional early close.
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        try:
            connection.putrequest(method,path)
            connection.putheader('X-Qcal-Token',self.token)
            connection.putheader('Content-Type','application/json')
            connection.putheader('Content-Length',str(length))
            connection.endheaders()
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:connection.close()

    def campaign_body(self,op='http-create-nine'):
        return {'operationId':op,'name':'HTTP nine','chipVersionId':'chip-v1','deploymentVersionId':'deployment-v1',
            'resourceIds':['Q'+str(q) for q in range(11,20)],'coverage':'full','policy':'threshold'}

    def test_new_writes_require_same_session_origin_and_host_boundary(self):
        for path in ('/api/chips','/api/deployments','/api/chip-imports','/api/campaigns','/api/conversations','/api/attention-items/missing/actions'):
            with self.subTest(path=path):
                status,error=self.request('POST',path,{'operationId':'denied'})
                self.assertEqual((status,error['error']['code']),(401,'INVALID_SESSION'))
                status,error=self.request('POST',path,{'operationId':'denied'},auth=True,headers={'Origin':'http://untrusted.invalid'})
                self.assertEqual((status,error['error']['code']),(403,'CROSS_ORIGIN'))
        self.assertEqual(self.workspace.list_campaigns(),[])
        self.assertEqual(self.workspace.list_conversations(),[])
        self.assertEqual(self.import_calls,[])
        self.assertEqual(self.adapter.calls,[])

    def test_campaign_create_actions_get_and_idempotent_responses(self):
        body=self.campaign_body()
        status,campaign=self.request('POST','/api/campaigns',body,auth=True)
        self.assertEqual(status,201,campaign)
        self.assertEqual(len(campaign['children']),9)
        self.assertEqual(self.request('POST','/api/campaigns',body,auth=True),(201,campaign))
        status,conflict=self.request('POST','/api/campaigns',{**body,'name':'conflicting'},auth=True)
        self.assertEqual((status,conflict['error']['code']),(409,'IDEMPOTENCY_CONFLICT'))
        endpoint='/api/campaigns/'+campaign['id']
        self.assertEqual(self.request('GET',endpoint),(200,campaign))
        self.assertEqual(len(self.request('GET','/api/campaigns')[1]),1)
        command={'operationId':'start-Q19','expectedRevision':campaign['revision'],'action':'start','resourceIds':['Q19']}
        status,started=self.request('POST',endpoint+'/actions',command,auth=True)
        self.assertEqual(status,200,started)
        self.assertEqual(self.request('POST',endpoint+'/actions',command,auth=True),(200,started))
        statuses={c['resourceId']:c['status'] for c in started['children']}
        self.assertEqual(statuses['Q19'],'queued')
        self.assertTrue(all(statuses[q]=='draft' for q in statuses if q!='Q19'))
        stop={**command,'operationId':'stale-stop','action':'pause'}
        status,paused=self.request('POST',endpoint+'/actions',stop,auth=True)
        self.assertEqual(status,200,paused)
        self.assertEqual(next(c['status'] for c in paused['children'] if c['resourceId']=='Q19'),'paused')
        self.assertEqual(self.request('POST',endpoint+'/actions',stop,auth=True),(200,paused))
        status,error=self.request('POST',endpoint+'/actions',{**stop,'operationId':'future-stop','expectedRevision':paused['revision']+1},auth=True)
        self.assertEqual((status,error['error']['code']),(409,'STALE_REVISION'))
        status,error=self.request('POST',endpoint+'/actions',{**command,'operationId':'outside','expectedRevision':paused['revision'],'resourceIds':['Q99']},auth=True)
        self.assertEqual((status,error['error']['code']),(400,'INVALID_SCOPE'))
        self.assertEqual(len(self.controller.list()),9)
        self.assertEqual(self.adapter.calls,[])

    def test_conversation_route_is_durable_idempotent_and_scope_cannot_change(self):
        body={'operationId':'new-conversation','scope':{'kind':'workspace'}}
        status,conv=self.request('POST','/api/conversations',body,auth=True)
        self.assertEqual(status,201,conv)
        self.assertEqual(self.request('POST','/api/conversations',body,auth=True),(201,conv))
        endpoint='/api/conversations/'+conv['id']
        self.controller._brain=lambda *a,**k:{'status':'ok','message':'只讨论，不执行。','proposals':[]}
        message={'operationId':'message','expectedRevision':conv['revision'],'message':'讨论Q19的测量方案','intent':'discuss'}
        status,reply=self.request('POST',endpoint+'/messages',message,auth=True)
        self.assertEqual(status,200,reply)
        self.assertEqual(len(reply['messages']),2)
        self.assertEqual(self.request('POST',endpoint+'/messages',message,auth=True),(200,reply))
        self.assertEqual(self.request('GET',endpoint),(200,reply))
        status,error=self.request('POST',endpoint+'/messages',{'operationId':'scope-change','message':'x','scope':{'kind':'workflow','id':'other'}},auth=True)
        self.assertEqual((status,error['error']['code']),(409,'INVALID_SCOPE'))
        self.assertEqual(self.request('GET',endpoint)[1]['messages'],reply['messages'])
        self.assertEqual(self.workspace.list_campaigns(),[])

    def test_large_import_has_specific_cap_and_general_writes_remain_one_mib(self):
        body={'operationId':'big-document','name':'synthetic-test.png','contentBase64':'a'*(Handler.maximum_body_bytes+128)}
        self.assertGreater(len(json.dumps(body).encode()),1024*1024)
        status,result=self.request('POST','/api/chip-imports',body,auth=True)
        self.assertEqual(status,201,result)
        self.assertEqual(len(self.import_calls),1)
        self.assertGreater(result['size'],1024*1024)
        for method,path in [('POST','/api/conversations'),('POST','/api/campaigns'),('PATCH','/api/chip-imports/document-stub'),('POST','/api/chip-imports/document-stub/analyze')]:
            status,error=self.reject_declared_length(method,path,len(json.dumps(body).encode()))
            self.assertEqual((status,error['error']['code']),(413,'INVALID_BODY'),path)
        self.assertEqual(len(self.import_calls),1)
        status,error=self.reject_declared_length('POST','/api/chip-imports',96*1024*1024+1)
        self.assertEqual((status,error['error']['code']),(413,'INVALID_BODY'))
        self.assertEqual(self.adapter.calls,[])

if __name__=='__main__':unittest.main()
