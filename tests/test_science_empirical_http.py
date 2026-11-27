"""Real loopback HTTP, temporary DB, no physical or asynchronous executor."""
import threading
import unittest
from backend.server import Server
from tests import test_manual_http, test_science_empirical_drag


class EmpiricalHTTP(unittest.TestCase):
    request=test_manual_http.ManualHTTPContracts.request

    def setUp(self):
        self.fixture=test_science_empirical_drag.EmpiricalDragController()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.controller=self.fixture.c;self.adapter=self.fixture.fake
        self.server=Server(('127.0.0.1',0),self.controller)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.base=f'http://127.0.0.1:{self.server.server_address[1]}'
        self.token=self.request('/api/session')[1]['token']

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def test_create_beta_draft_and_reject_mixed_units_without_execution(self):
        data=self.fixture.data()
        status,wf=self.request('/api/workflows',data)
        self.assertEqual(status,201,wf)
        node=wf['nodes'][0]
        self.assertEqual(node['parameterOverrides']['beta_start_s'],-1e-9)
        self.assertNotIn('anharmonicity',node['scienceSourceRoles'])
        status,detail=self.request('/api/workflows/'+wf['id']+'/nodes/'+node['id'])
        self.assertEqual(status,200,detail)
        self.assertEqual(detail['parameters']['drag_mode'],'empirical_beta')
        data['manualParameters']['drag_lambda1']=.2
        status,error=self.request('/api/workflows',data)
        self.assertEqual(status,400,error)
        self.assertEqual(error['error']['code'],'SCIENCE_DRAG_MODE_PARAMETERS')
        self.assertEqual(len(self.controller.list()),1)
        self.assertEqual(self.adapter.calls,[])
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],0)
