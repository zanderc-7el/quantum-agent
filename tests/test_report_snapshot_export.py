import hashlib
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('export_report_snapshot',Path(__file__).resolve().parents[1]/'scripts/export_report_snapshot.py')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReportSnapshotTests(unittest.TestCase):
    def test_readonly_snapshot_preserves_attempts_and_rejects_changed_or_outside_images(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); image=root/'figure.png'; image.write_bytes(b'actual-image-fixture')
            db=root/'platform.sqlite'; expected=hashlib.sha256(image.read_bytes()).hexdigest()
            artifact={'id':'image','kind':'image','sha256':expected}
            with closing(sqlite3.connect(db)) as connection:
                connection.executescript('CREATE TABLE platform_workflows(id TEXT,payload TEXT); CREATE TABLE platform_attempts(workflow_id TEXT,node_id TEXT,number INTEGER,payload TEXT); CREATE TABLE platform_decisions(workflow_id TEXT,node_id TEXT,payload TEXT); CREATE TABLE platform_artifacts(id TEXT,path TEXT,sha256 TEXT,payload TEXT);')
                connection.execute('INSERT INTO platform_workflows VALUES (?,?)',('wf',json.dumps({'id':'wf','nodes':[{'id':'n'}],'workflowComplete':False})))
                connection.execute('INSERT INTO platform_attempts VALUES (?,?,?,?)',('wf','n',3,json.dumps({'state':'execution_failed','result':{'quality':{'status':'failed'},'artifacts':[artifact]}})))
                connection.execute('INSERT INTO platform_artifacts VALUES (?,?,?,?)',('image',str(image),expected,json.dumps(artifact)))
                connection.commit()
            before=db.read_bytes(); report=MODULE.snapshot(db,'wf',root)
            self.assertEqual(db.read_bytes(),before)
            self.assertFalse(report['workflowComplete'])
            self.assertEqual(report['nodes'][0]['attempts'][0]['result']['quality']['status'],'failed')
            self.assertEqual(report['artifactIndex']['image']['sha256'],expected)
            image.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'integrity'): MODULE.snapshot(db,'wf',root)
            with self.assertRaisesRegex(ValueError,'outside'): MODULE.snapshot(db,'wf',root/'unrelated')

    def test_named_protected_workspace_rejected_before_open(self):
        with self.assertRaisesRegex(ValueError,'Protected'): MODULE.checked('D:/qoskit-workspace/data/a.png')


if __name__=='__main__': unittest.main()
