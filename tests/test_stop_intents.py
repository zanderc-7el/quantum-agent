"""Stop intent races: temporary data and explicit fake adapter only."""
import tempfile
import threading
import unittest
from pathlib import Path

from backend.common import APIError
from tests.test_controller import make_fixture


class StopIntentContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qcal-stop-intent-')
        self.controller, self.adapter = make_fixture(Path(self.temp.name))

    def tearDown(self):
        self.controller.close()
        self.temp.cleanup()

    def create(self):
        return self.controller.create({'name':'Stop intent test','resourceIds':['Q11'],'mode':'real','policy':'threshold'})

    def action(self, wf, action, operation):
        return self.controller.action(wf['id'], {'action':action,'operationId':operation,'expectedRevision':wf['revision']})

    def test_stale_pause_and_abort_during_acquisition_wait_for_shutdown_checkpoint(self):
        for action, final in (('pause','paused'),('abort','aborted')):
            with self.subTest(action=action):
                wf = self.create()
                entered, release = threading.Event(), threading.Event()
                def hold(request, emit, cancel):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('Fake acquisition was not released')
                self.adapter.before_return = hold
                worker = threading.Thread(target=self.controller._execute_node,args=(wf['id'],wf['nodes'][0]['id']))
                worker.start()
                try:
                    self.assertTrue(entered.wait(5))
                    live = self.controller.get(wf['id'])
                    self.assertGreater(live['revision'], wf['revision'])
                    requested = self.action(wf,action,'old-screen-'+action)
                    self.assertEqual(requested['status'],'running')
                    self.assertEqual(requested['cancelRequested'],action)
                    with self.controller.store.connect() as rt:
                        self.assertIsNotNone(rt.db.execute('SELECT 1 FROM platform_leases WHERE workflow_id=?',(wf['id'],)).fetchone())
                    release.set()
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
                    stopped = self.controller.get(wf['id'])
                    self.assertEqual(stopped['status'],final)
                    self.assertIsNone(stopped['cancelRequested'])
                    self.assertTrue(self.adapter.cancel_observed)
                    self.assertEqual(len(self.controller.detail(wf['id'],wf['nodes'][0]['id'])['attempts']),1)
                finally:
                    release.set()
                    worker.join(5)

    def test_repeated_stop_is_receipt_and_later_pause_does_not_downgrade_abort(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'],rt)
            current.update(status='running',engineState='acquiring')
            self.controller.store.save(rt,current,'fake_acquisition_progress')
        requested = self.action(wf,'abort','abort-once')
        self.assertEqual(self.action(wf,'abort','abort-once'),requested)
        paused_click = self.action(wf,'pause','later-pause')
        self.assertEqual(paused_click['revision'],requested['revision'])
        self.assertEqual(paused_click['cancelRequested'],'abort')
        self.assertEqual(paused_click['engineState'],'stopping_at_checkpoint')
        self.controller._finish_cancel(wf['id'])
        stopped = self.controller.get(wf['id'])
        again = self.action(wf,'abort','new-stop-on-stopped-task')
        self.assertEqual(again['status'],'aborted')
        self.assertEqual(again['revision'],stopped['revision'])
        self.assertEqual(self.adapter.calls,[])

    def test_stale_stop_against_terminal_task_preserves_actual_terminal_result(self):
        for status in ('completed','failed','aborted'):
            wf = self.create()
            with self.controller.store.transaction() as rt:
                current = self.controller.get(wf['id'],rt)
                current.update(status=status,engineState=status,workflowComplete=status=='completed')
                self.controller.store.save(rt,current,'fake_terminal')
            before = self.controller.get(wf['id'])
            for action in ('pause','abort'):
                result = self.action(wf,action,status+'-'+action)
                self.assertEqual(result['status'],status)
                self.assertEqual(result['workflowComplete'],status=='completed')
                self.assertEqual(result['revision'],before['revision'])
                self.assertIsNone(result['cancelRequested'])
        self.assertEqual(self.adapter.calls,[])

    def test_unknown_hardware_accepts_stop_intent_without_claiming_or_releasing_stop(self):
        for action, final in (('pause','paused'),('abort','aborted')):
            wf = self.create()
            self.adapter.fail = True
            self.adapter.reconcile = lambda op: {'status':'unknown','operationId':op}
            self.controller._execute_node(wf['id'],wf['nodes'][0]['id'])
            calls = len(self.adapter.calls)
            requested = self.action(wf,action,'unknown-'+action)
            self.assertEqual(requested['status'],'attention')
            self.assertEqual(requested['engineState'],'needs_reconciliation')
            self.assertEqual(requested['cancelRequested'],action)
            with self.controller.store.connect() as rt:
                self.assertEqual(rt.db.execute('SELECT state FROM platform_leases WHERE workflow_id=?',(wf['id'],)).fetchone()[0],'quarantined')
            self.assertEqual(len(self.adapter.calls),calls)
            self.adapter.reconcile = lambda op: {'status':'cancelled','shutdownConfirmed':True,'partialArtifacts':[]}
            settled = self.action(requested,'reconcile','found-stop-'+action)
            self.assertEqual(settled['status'],final)
            self.assertIsNone(settled['cancelRequested'])
            self.assertEqual(len(self.adapter.calls),calls)
            with self.controller.store.connect() as rt:
                self.assertIsNone(rt.db.execute('SELECT 1 FROM platform_leases WHERE workflow_id=?',(wf['id'],)).fetchone())

    def test_stop_keeps_operation_dedupe_and_rejects_unknown_or_invalid_revision(self):
        wf = self.create()
        for revision in (None,0,-1,True,'1',wf['revision']+1):
            with self.subTest(revision=revision), self.assertRaises(APIError) as error:
                self.controller.action(wf['id'],{'action':'abort','operationId':'bad-'+str(revision),'expectedRevision':revision})
            self.assertEqual(error.exception.code,'STALE_REVISION')
        stopped = self.action(wf,'abort','stop-dedupe')
        with self.assertRaises(APIError) as error:
            self.action(wf,'pause','stop-dedupe')
        self.assertEqual(error.exception.code,'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.controller.get(wf['id'])['revision'],stopped['revision'])
        self.assertEqual(self.adapter.calls,[])

    def test_stale_idle_pause_does_not_allow_stale_resume_or_parameter_write(self):
        wf = self.create()
        queued = self.action(wf,'start','queue')
        paused = self.action(wf,'pause','old-queue-pause')
        self.assertEqual(paused['status'],'paused')
        self.assertEqual(self.action(queued,'pause','repeat-paused')['revision'],paused['revision'])
        with self.assertRaises(APIError) as error:
            self.action(queued,'resume','stale-resume')
        self.assertEqual(error.exception.code,'STALE_REVISION')
        with self.assertRaises(APIError) as error:
            self.controller.set_parameters(wf['id'],wf['nodes'][0]['id'],{'operationId':'stale-params','expectedRevision':queued['revision'],'parameters':{'readout_hz':6.9e9}})
        self.assertEqual(error.exception.code,'STALE_REVISION')
        self.assertEqual(self.adapter.calls,[])

    def test_stop_ignores_obsolete_node_and_attempt_binding_but_resume_does_not(self):
        for action,final in [('pause','paused'),('abort','aborted')]:
            wf=self.create()
            queued=self.action(wf,'start','queue-'+action)
            with self.controller.store.transaction() as rt:
                latest=self.controller.get(wf['id'],rt)
                latest['nodes'][0]['latestAttemptId']='new-attempt'
                self.controller.store.save(rt,latest,'attempt_changed')
            body={'action':action,'operationId':'stale-attempt-'+action,'expectedRevision':queued['revision'],
                'nodeId':queued['nodes'][0]['id'],'attemptId':'old-attempt'}
            stopped=self.controller.action(wf['id'],body)
            self.assertEqual(stopped['status'],final)
            self.assertEqual(self.controller.action(wf['id'],body),stopped)
            if action=='pause':
                with self.assertRaises(APIError) as error:
                    self.controller.action(wf['id'],{**body,'action':'resume','operationId':'strict-resume','expectedRevision':stopped['revision']})
                self.assertEqual(error.exception.code,'STALE_ATTEMPT')
            repeated=self.controller.action(wf['id'],{**body,'nodeId':'obsolete-node-id','operationId':'obsolete-node-'+action})
            self.assertEqual(repeated['status'],final)
        self.assertEqual(self.adapter.calls,[])

    def test_atomic_create_and_start_reads_the_same_transaction_and_rolls_back_together(self):
        workflow_id = None
        with self.assertRaisesRegex(RuntimeError,'rollback fixture'):
            with self.controller.store.transaction() as rt:
                wf = self.controller.create({'name':'Atomic task','resourceIds':['Q11'],'mode':'real'},_rt=rt)
                workflow_id = wf['id']
                started = self.controller.action(workflow_id,{'action':'start','operationId':'atomic-start','expectedRevision':wf['revision']},_rt=rt)
                self.assertEqual(started['status'],'queued')
                raise RuntimeError('rollback fixture')
        with self.controller.store.connect() as rt:
            self.assertIsNone(self.controller.store.get(rt,workflow_id))
            self.assertIsNone(rt.db.execute('SELECT 1 FROM platform_commands WHERE id=?',('atomic-start',)).fetchone())
        self.assertEqual(self.adapter.calls,[])


if __name__=='__main__':
    unittest.main()
