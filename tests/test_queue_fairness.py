"""Same-device fairness at completed-attempt boundaries; temporary DB only."""
from pathlib import Path
import tempfile
import unittest
from tests.test_controller import make_fixture


class QueueFairness(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.c,self.adapter=make_fixture(Path(self.tmp.name))
        procedures=self.adapter.capabilities()['procedures']
        self.adapter.capabilities=lambda:{'procedures':procedures+['ro_power_shift']}
        self.serial=0

    def tearDown(self):
        self.c.close();self.tmp.cleanup()

    def command(self,w,action,**extra):
        self.serial+=1
        return self.c.action(w['id'],{'operationId':'queue-'+str(self.serial),
            'expectedRevision':w['revision'],'action':action,**extra})

    def create(self,policy='autopilot'):
        return self.c.create({'name':'TEST_ONLY queue '+policy,'mode':'real','resourceIds':['Q11'],'policy':policy})

    def queued_peer_during_first_operation(self,*,failed=False):
        auto=self.command(self.create(),'start');peer=self.create('strict')
        original=self.adapter.execute;queued=[]
        def execute(request,emit,cancel):
            if not queued:
                queued.append(self.command(self.c.get(peer['id']),'run_step',nodeId=peer['nodes'][0]['id']))
            return original(request,emit,cancel)
        self.adapter.execute=execute
        self.adapter.same_bytes=failed
        return auto,peer

    def test_waiting_manual_gets_turn_after_one_full_independent_attempt(self):
        auto,peer=self.queued_peer_during_first_operation()
        self.c.run_workflow(auto['id'])
        self.assertEqual(len(self.adapter.calls),2,'autopilot must not enter a second node ahead of a queued peer')
        self.assertEqual([r['workflow_id'] for r in self.effect_rows()],[auto['id'],auto['id']])
        self.assertEqual([r['state'] for r in self.effect_rows()],['completed','completed'])
        self.assertEqual(self.adapter.calls[0]['leaseToken'],self.adapter.calls[1]['leaseToken'])
        self.assertEqual(self.c.get(auto['id'])['status'],'queued')
        self.assertEqual(self.c._queued_workflows()[0]['id'],peer['id'])
        self.c.run_workflow(peer['id'])
        self.assertEqual(len(self.adapter.calls),4)
        self.assertEqual(self.c.get(peer['id'])['engineState'],'awaiting_acceptance')
        self.assertEqual(self.c.get(auto['id'])['nodes'][0]['engineState'],'verified')

    def effect_rows(self):
        with self.c.store.connect() as rt:return [dict(r) for r in rt.db.execute('SELECT * FROM platform_effects ORDER BY rowid')]

    def test_failed_node_defers_model_until_waiting_peer_has_turn(self):
        auto,peer=self.queued_peer_during_first_operation(failed=True)
        calls=[]
        self.c._brain=lambda *a,**kw:calls.append(a) or {'status':'waiting_model','message':'TEST_ONLY'}
        self.c.run_workflow(auto['id'])
        self.assertEqual(calls,[],'slow model decisions must not precede an already waiting physical operation')
        self.assertEqual(self.c.get(auto['id'])['engineState'],'decision_queued')
        self.assertEqual(self.c._queued_workflows()[0]['id'],peer['id'])
        self.assertEqual(len(self.adapter.calls),2)

    def test_queue_order_uses_authorized_request_time_not_workflow_creation(self):
        older=self.create('strict');newer=self.create('strict')
        newer=self.command(newer,'run_step',nodeId=newer['nodes'][0]['id'])
        older=self.command(older,'run_step',nodeId=older['nodes'][0]['id'])
        self.assertEqual([w['id'] for w in self.c._queued_workflows()],[newer['id'],older['id']])

    def test_unknown_or_active_receipt_never_yields_or_releases_lease(self):
        auto=self.command(self.create(),'start');peer=self.create('strict')
        self.command(peer,'run_step',nodeId=peer['nodes'][0]['id'])
        with self.c.store.transaction() as rt:
            rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',('fixture-device',auto['id'],'a','quarantined','test-token'))
        before=self.c.get(auto['id'])
        self.assertFalse(self.c._yield_to_waiter(auto['id']))
        self.assertEqual(self.c.get(auto['id']),before)
        with self.c.store.connect() as rt:self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],1)

    def test_no_peer_keeps_existing_autopilot_path(self):
        auto=self.command(self.create(),'start')
        before=self.c.get(auto['id'])
        self.assertFalse(self.c._yield_to_waiter(auto['id']))
        self.assertEqual(self.c.get(auto['id']),before)

    def test_paused_cancelled_or_missing_manual_permit_is_not_a_waiter(self):
        auto=self.command(self.create(),'start');peer=self.create('strict')
        for state in ('paused','cancelled','missing_permit','configuration_missing'):
            with self.subTest(state=state):
                with self.c.store.transaction() as rt:
                    current=self.c.get(peer['id'],rt)
                    current.update(status='paused' if state=='paused' else 'queued',cancelRequested='abort' if state=='cancelled' else None)
                    current['manualStep']={'state':'finished' if state=='missing_permit' else 'queued','createdAt':current['createdAt']}
                    if state=='configuration_missing':current['configurationIssue']={'message':'TEST_ONLY missing configuration'}
                    else:current.pop('configurationIssue',None)
                    self.c.store.save(rt,current,'fixture_only',{})
                self.assertFalse(self.c._yield_to_waiter(auto['id']))

    def test_shared_fault_does_not_become_a_free_device_handoff(self):
        auto=self.command(self.create(),'start');peer=self.create('strict')
        self.command(peer,'run_step',nodeId=peer['nodes'][0]['id'])
        fault=self.create()
        self.c._attention(fault['id'],'needs_reconciliation','TEST_ONLY unknown receipt')
        self.assertFalse(self.c._yield_to_waiter(auto['id']))

    def test_pausing_yielded_task_removes_it_from_queue_and_resuming_gets_new_turn(self):
        auto,peer=self.queued_peer_during_first_operation()
        self.c.run_workflow(auto['id'])
        yielded=self.c.get(auto['id']);old=yielded['queueEnteredAt']
        paused=self.command(yielded,'pause')
        self.assertNotIn(auto['id'],[w['id'] for w in self.c._queued_workflows()])
        resumed=self.command(paused,'resume')
        self.assertGreater(resumed['queueEnteredAt'],old)
        self.assertEqual(self.c._queued_workflows()[0]['id'],peer['id'])
