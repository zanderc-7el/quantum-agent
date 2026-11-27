"""Real local HTTP, isolated SQLite, explicitly fake adapter; no hardware calls."""
import json
import threading
from pathlib import Path
import tempfile
import urllib.request
import urllib.error
import unittest
from backend.server import Server
from tests.test_controller import make_fixture


class ScienceHTTPContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.c,self.adapter=make_fixture(Path(self.temp.name))
        self.server=Server(('127.0.0.1',0),self.c);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_address[1]);self.token=self.request('/api/session')[1]['token']

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.c.close();self.temp.cleanup()

    def request(self,path,body=None,authorized=True):
        headers={'Origin':self.base}
        if body is not None:headers.update({'Content-Type':'application/json','X-Qcal-Token':self.token if authorized else 'invalid'})
        req=urllib.request.Request(self.base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as r:return r.code,json.load(r)

    def test_preview_save_reload_and_auth_boundary(self):
        status,overview=self.request('/api/science/overview');self.assertEqual(status,200)
        self.assertEqual(overview['temperature']['status'],'not_connected')
        status,result=self.request('/api/science/preview',{'kind':'pulse','input':{'durationNs':20}})
        self.assertEqual(status,200,result);self.assertFalse(result['dispatched'])
        body={'operationId':'http-save','kind':'pulse','input':{'durationNs':20},'scope':{}}
        self.assertEqual(self.request('/api/science/records',body,False)[0],401)
        status,saved=self.request('/api/science/records',body);self.assertEqual(status,200,saved)
        self.assertEqual(self.request('/api/science/records',body),(status,saved))
        self.assertEqual(self.request('/api/science/records/'+saved['id'])[1]['input'],body['input'])
        self.assertEqual(len(self.request('/api/science/records')[1]['records']),1)
        self.assertEqual(self.adapter.calls,[])
