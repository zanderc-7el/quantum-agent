import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from backend.report_exports import ReportExportService
from backend.common import APIError

ROOT=Path(__file__).resolve().parents[1]

class ExportsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'data').mkdir()
        shutil.copytree(ROOT/'knowledge/report-writing',self.root/'knowledge/report-writing')
        self.db=self.root/'data/platform.sqlite'
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript('CREATE TABLE platform_workflows(id TEXT PRIMARY KEY,payload TEXT);CREATE TABLE platform_attempts(workflow_id TEXT,node_id TEXT,number INTEGER,payload TEXT);CREATE TABLE platform_decisions(workflow_id TEXT,node_id TEXT,payload TEXT);CREATE TABLE platform_artifacts(id TEXT,path TEXT,sha256 TEXT,payload TEXT);')
            wf={'id':'wf-1','revision':7,'name':'测试','target':'Q19','mode':'real','nodes':[]}
            db.execute('INSERT INTO platform_workflows VALUES(?,?)',('wf-1',json.dumps(wf)))
        self.control=SimpleNamespace(root=self.root,store=SimpleNamespace(path=self.db));self.services=[]

    def tearDown(self):
        for s in self.services:s.shutdown()
        self.temp.cleanup()

    def service(self,runner):
        s=ReportExportService(self.control,runner=runner);self.services.append(s);return s

    def body(self):return {'operationId':'op1','expectedRevision':7,'format':'pptx','appendixCount':16}

    def test_idempotent_frozen_output_and_hash_check(self):
        enter=threading.Event();finish=threading.Event();calls=[]
        def run(directory,job):
            calls.append(job['id']);enter.set();finish.wait(5)
            out=directory/'report.pptx';out.write_bytes(b'test-ppt');return out
        with patch('backend.result_archive.register_report_archive',return_value='archive1'):
            s=self.service(run);one=s.create('wf-1',self.body());enter.wait(5)
            self.assertTrue(s.has_active())
            two=s.create('wf-1',self.body());self.assertEqual(one['id'],two['id'])
            with closing(sqlite3.connect(self.db)) as db, db:db.execute('UPDATE platform_workflows SET payload=?',(json.dumps({'id':'wf-1','revision':8,'nodes':[]}),))
            self.assertEqual(s.create('wf-1',self.body())['id'],one['id'])
            finish.set();s.shutdown();self.services.remove(s)
            job=s.get(one['id']);self.assertEqual(job['status'],'completed');self.assertEqual(job['revision'],7);self.assertEqual(len(calls),1)
            output,mime=s.file(one['id']);self.assertIn('presentationml',mime)
            output.write_bytes(b'tampered')
            with self.assertRaises(APIError):s.file(one['id'])

    def test_conflict_and_bad_format(self):
        s=self.service(lambda *_:None)
        for body in [dict(self.body(),expectedRevision=8),dict(self.body(),format='json'),dict(self.body(),appendixCount=True)]:
            with self.assertRaises(APIError):s.create('wf-1',body)

    def test_failure_is_durable_and_retry_id_does_not_rerun(self):
        calls=[]
        def run(*a):calls.append(1);raise RuntimeError('no runtime')
        s=self.service(run);job=s.create('wf-1',self.body());s.shutdown();self.services.remove(s)
        self.assertEqual(s.get(job['id'])['status'],'failed');self.assertEqual(s.create('wf-1',self.body())['id'],job['id']);self.assertEqual(calls,[1])
        with self.assertRaises(APIError):s.file(job['id'])

    def test_restart_interrupted_job_never_reexecutes(self):
        s=self.service(lambda *_:None);s.shutdown();self.services.remove(s)
        with closing(sqlite3.connect(self.db)) as db, db:db.execute('INSERT INTO platform_report_exports VALUES(?,?,?,?)',('job1','old','hash',json.dumps({'id':'job1','status':'processing','workflowId':'wf-1'})))
        second=self.service(lambda *_:self.fail('must not restart job'))
        self.assertEqual(second.get('job1')['status'],'interrupted')

if __name__=='__main__':unittest.main()
