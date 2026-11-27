"""Actual loopback HTTP/session, temporary store, explicitly fake acquisition."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from backend.server import Server
from tests.test_controller import make_fixture
from tests.test_science_channel_comparison import fixture


class ChannelHTTPContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='r05-http-')
        self.c,self.adapter=make_fixture(Path(self.temp.name))
        self.wf=self.c.create({'name':'TEST ONLY HTTP external channel','mode':'real','resourceIds':['Q11']})
        self.scope={'workflowId':self.wf['id'],'resourceId':'Q11'}
        self.server=Server(('127.0.0.1',0),self.c)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_address[1])
        self.token=self.request('/api/session')[1]['token']

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.c.close();self.temp.cleanup()

    def request(self,path,body=None,authorized=True):
        headers={'Origin':self.base}
        if body is not None:headers.update({'Content-Type':'application/json','X-Qcal-Token':self.token if authorized else 'invalid'})
        req=urllib.request.Request(self.base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=5) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as response:return response.code,json.load(response)

    def body(self,doc=None):
        original=b'\xef\xbb\xbf'+json.dumps(doc or fixture(self.scope),ensure_ascii=False).encode()
        return {'operationId':'http-channel','kind':'predistortion_comparison','scope':self.scope,
                'input':{'fileName':'TEST_ONLY_BOM.json','documentBase64':base64.b64encode(original).decode()}}

    def test_actual_http_auth_idempotence_preview_list_restore_and_no_device_effect(self):
        status,template=self.request('/api/science/channel-comparison-template',{'scope':self.scope})
        self.assertEqual(status,200,template);self.assertEqual(template['scope'],self.scope)
        body=self.body()
        self.assertEqual(self.request('/api/science/records',body,False)[0],401)
        status,preview=self.request('/api/science/channel-comparison-preview',{'scope':self.scope,'input':body['input']})
        self.assertEqual(status,200,preview)
        status,saved=self.request('/api/science/records',body);self.assertEqual(status,200,saved)
        self.assertEqual(saved['result'],preview)
        self.assertEqual(self.request('/api/science/records',body),(200,saved))
        page=self.request('/api/science/records?summary=true&kind=predistortion_comparison&workflowId='+self.wf['id'])[1]
        self.assertEqual(len(page['records']),1);self.assertTrue(page['records'][0]['summary'])
        detail=self.request('/api/science/records/'+saved['id'])[1]
        self.assertEqual(detail['result'],saved['result']);self.assertEqual(detail['input'],body['input'])
        self.assertFalse(detail['physicalAccepted']);self.assertEqual(self.adapter.calls,[])
        wf=self.c.get(self.wf['id']);self.assertEqual((wf['status'],wf['revision']),('draft',self.wf['revision']))
        with self.c.store.connect() as rt:
            for table in ('platform_attempts','platform_effects','platform_leases'):
                self.assertEqual(rt.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)

    def test_real_request_rejects_mismatch_scope_escalation_and_oversize_atomically(self):
        doc=fixture(self.scope);doc['compensated']['context']['measurementPoint']='wrong plane'
        self.assertEqual(self.request('/api/science/records',self.body(doc))[0],400)
        body=self.body();body['physicalAccepted']=True
        self.assertEqual(self.request('/api/science/records',body)[0],400)
        body=self.body();body['scope']={'workflowId':self.wf['id'],'resourceId':'Q99'}
        self.assertEqual(self.request('/api/science/records',body)[0],400)
        body=self.body();body['input']['documentBase64']=base64.b64encode(b' '*524289).decode()
        self.assertEqual(self.request('/api/science/records',body)[0],400)
        self.assertEqual(self.request('/api/science/records')[1]['records'],[])
        self.assertEqual(self.adapter.calls,[])


if __name__=='__main__':unittest.main()
