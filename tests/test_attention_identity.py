"""Attention identity is display-only; temporary DB, no execution."""
from pathlib import Path
import json,tempfile,unittest
from backend.common import dumps
from tests.test_controller import make_fixture

class AttentionIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='r28-attention-');self.addCleanup(self.tmp.cleanup)
        self.c,self.adapter=make_fixture(Path(self.tmp.name));self.addCleanup(self.c.close)
    def waiting(self,name):
        wf=self.c.create({'name':name,'mode':'real','policy':'autopilot','resourceIds':['Q11']})
        with self.c.store.transaction() as rt:
            current=self.c.get(wf['id'],rt);current.update(status='attention',engineState='waiting_hardware')
            self.c.store.save(rt,current,'TEST_ONLY attention')
        return current
    def test_queue_projection_has_actual_workflow_identity_and_preserves_target(self):
        first=self.waiting('第一项历史任务');second=self.waiting('本轮验收任务')
        rows=self.c.workspace.attention_items()
        self.assertEqual(len(rows),2)
        for row in rows:
            wf=self.c.get(row['workflowId'])
            self.assertEqual(row['workflowName'],wf['name']);self.assertEqual(row['workflowCreatedAt'],wf['createdAt'])
            self.assertEqual(row['title'],'Q11 · 设备准备')
        row=next(r for r in rows if r['workflowId']==first['id'])
        self.c.workspace.attention_action(row['id'],{'operationId':'defer-only-first','expectedRevision':row['revision'],'action':'defer'})
        rows=self.c.workspace.attention_items()
        self.assertEqual(next(r for r in rows if r['workflowId']==first['id'])['state'],'deferred')
        self.assertEqual(next(r for r in rows if r['workflowId']==second['id'])['state'],'open')
        self.assertEqual(self.adapter.calls,[])
    def test_existing_deferred_item_metadata_upgrade_does_not_reopen_or_bump_revision(self):
        wf=self.waiting('旧版同名任务');row=self.c.workspace.attention_items()[0]
        self.c.workspace.attention_action(row['id'],{'operationId':'defer-before-upgrade','expectedRevision':row['revision'],'action':'defer'})
        with self.c.store.transaction() as rt:
            stored=json.loads(rt.db.execute('SELECT payload FROM platform_attention WHERE id=?',(row['id'],)).fetchone()[0])
            stored.pop('workflowName',None);stored.pop('workflowCreatedAt',None)
            rt.db.execute('UPDATE platform_attention SET payload=? WHERE id=?',(dumps(stored),row['id']))
        before=self.c.get(wf['id']);updated=self.c.workspace.attention_items()[0]
        self.assertEqual(updated['revision'],stored['revision']);self.assertEqual(updated['state'],'deferred')
        self.assertEqual(updated['workflowName'],wf['name']);self.assertEqual(updated['workflowId'],wf['id'])
        self.assertEqual(self.c.get(wf['id']),before)
        self.assertEqual(self.c.workspace.attention_items()[0],updated)
