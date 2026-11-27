"""Exact workflow/attempt signal views; synthetic measurements, private database."""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

from backend.common import APIError
from backend.science_service import ScienceService
from backend.science_dependencies import ScienceDependencyResolver
from backend.store import Store


class AttemptSignal(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.row_factory=sqlite3.Row;self.db.executescript('''
        CREATE TABLE platform_workflows(id TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE platform_attempts(id TEXT PRIMARY KEY,workflow_id TEXT,payload TEXT);
        CREATE TABLE platform_effects(workflow_id TEXT,attempt_id TEXT,operation_id TEXT,state TEXT,request TEXT,result TEXT);
        CREATE TABLE platform_artifacts(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,payload TEXT);''')
        db=self.db
        class Archive:
            get=staticmethod(Store.get)
            @contextmanager
            def connect(self):yield SimpleNamespace(db=db)
        self.c=SimpleNamespace(store=Archive(),root=self.root)
        self.s=object.__new__(ScienceService);self.s.c=self.c;self.s.store=self.c.store;self.s.dependencies=ScienceDependencyResolver(self.c)
        self.wf={'id':'w','mode':'real','resourceIds':['Q11'],'nodes':[{'id':'n','resourceId':'Q11','procedureId':'sq_rabi_amplitude'}]}
        self.db.execute('insert into platform_workflows values(?,?)',('w',json.dumps(self.wf)))
        self.query={'workflowId':'w','nodeId':'n','attemptId':'b'}
        path=self.root/'raw_scan.npz'
        def array_bytes(values):
            h=repr({'descr':'<f8','fortran_order':False,'shape':(2,2,3)}).encode('ascii')
            return b'\x93NUMPY\x01\x00'+struct.pack('<H',len(h)+1)+h+b'\n'+struct.pack('<12d',*values)
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('i.npy',array_bytes(range(12)));z.writestr('q.npy',array_bytes(range(-12,0)))
        digest=sha256(path.read_bytes()).hexdigest()
        self.item={'id':'file','kind':'raw','name':'raw_scan.npz','sha256':digest}
        self.db.execute('insert into platform_artifacts values(?,?,?,?)',('file',str(path),digest,json.dumps(self.item)))
        self.raw={'dataKind':'real','acquiredAt':'2026-11-27T00:00:01Z','sourceRunId':'b/measure',
            'parameters':{'drive_amp':.4},'quality':{'status':'failed'},'metrics':{},
            'series':[{'name':'trace','x':[0,.5],'y':[.02,.03],'xUnit':'a.u.','yUnit':'IQ a.u.'}],'artifacts':[self.item]}
        self.req=dict(runId='w',nodeId='n',resourceId='Q11',procedureId='sq_rabi_amplitude',attemptId='b',operationId='b/measure')
        self.at=dict(id='b',mode='real',state='attention',endedAt='time',nodeId='n',number=2,operationId='b/measure',procedureId='sq_rabi_amplitude',result=self.raw)
        self._store()
    def _store(self):
        self.db.execute("insert or replace into platform_attempts values('b','w',?)",(json.dumps(self.at),))
        self.db.execute("delete from platform_effects where operation_id='b/measure'")
        self.db.execute("insert into platform_effects values('w','b','b/measure','completed',?,?)",(json.dumps(self.req),json.dumps(self.raw)))
    def get(self,ep='attempt-signals',**q):return self.s.get('/'+ep,self.query|q)
    def test_failed_selection_only_curve_adc_tones_no_writes(self):
        before=list(self.db.iterdump());r=self.get()
        self.assertEqual(r['result']['sourceSeries'][0]['values'],[.02,.03]);self.assertFalse(r['provenance']['independentlyVerified'])
        d=self.get('attempt-signal-adc',row='1',tone='1');self.assertEqual(d['i'],[9,10,11]);self.assertEqual(d['q'],[-3,-2,-1])
        self.assertEqual(d['recordId'],r['id']);self.assertEqual(d['sha256'],self.item['sha256'])
        self.assertEqual(self.get('attempt-signal-pulse')['status'],'unavailable')
        self.assertEqual(list(self.db.iterdump()),before)
    def test_no_cross_workflow_node_attempt_pending_unfinished_or_misbound_effect(self):
        for changed in ({'workflowId':'other'},{'nodeId':'n2'},{'attemptId':'other'},{'workflowId':['w','w']}):
            with self.subTest(changed=changed),self.assertRaises(APIError):self.get(**changed)
        self.req['nodeId']='different';self._store()
        with self.assertRaises(APIError):self.get()
        self.req['nodeId']='n';self.at.pop('endedAt');self._store()
        with self.assertRaises(APIError):self.get()
        self.at['endedAt']='end';self._store()
        self.db.execute("update platform_effects set state='unknown'")
        with self.assertRaises(APIError):self.get()
    def test_edited_series_effect_and_tampered_raw_reject(self):
        xf=deepcopy(self.raw);xf['series'][0]['y'][0]=.8
        self.db.execute('update platform_effects set result=?',(json.dumps(xf),))
        with self.assertRaises(APIError):self.get()
        self._store();(self.root/'raw_scan.npz').write_bytes(b'replaced')
        with self.assertRaisesRegex(APIError,'摘要'):self.get('attempt-signal-adc')
        for field,value in [('row','-1'),('tone','3'),('row',['0','1'])]:
            with self.assertRaises(APIError):self.get('attempt-signal-adc',**{field:value})
    def test_native_repeat_stage_not_selection_and_both_effects_must_match(self):
        initial=deepcopy(self.raw)
        current=deepcopy(self.raw);current.update(sourceRunId='b/verify',acquiredAt='time2',quality={'status':'passed'},selectionResult=initial)
        self.at.update(state='verified',verificationOperationId='b/verify',result=current)
        self._store()
        self.db.execute("insert into platform_effects values('w','b','b/verify','completed',?,?)",(json.dumps(self.req|{'operationId':'b/verify'}),json.dumps(current)))
        result=self.get();self.assertEqual(result['readOnlyAttempt']['stage'],'verification')
        self.assertTrue(result['provenance']['independentlyVerified'])
        self.assertEqual(self.get('attempt-signal-pulse',stage='selection')['operationId'],'b/measure')
        self.db.execute("update platform_effects set state='pending' where operation_id='b/measure'")
        with self.assertRaises(APIError):self.get()
