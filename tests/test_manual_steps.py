"""Manual single-step contracts: temporary SQLite and FakeAdapter only."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from backend.common import APIError
from backend.controller import Controller
from tests.test_controller import make_fixture


class ManualStepContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qcal-manual-test-')
        self.root = Path(self.directory.name)
        self.controller, self.adapter = make_fixture(self.root)
        self.counter = 0

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def create(self, **extra):
        return self.controller.create({'name': 'Manual test', 'resourceIds': ['Q11'],
                                       'mode': 'real', 'policy': 'strict', **extra})

    def command(self, wf, action, **extra):
        self.counter += 1
        return self.controller.action(wf['id'], {'action': action,
            'operationId': 'manual-' + str(self.counter), 'expectedRevision': wf['revision'], **extra})

    def assert_code(self, code, fn):
        with self.assertRaises(APIError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def execute(self, wf, target=None):
        queued = self.command(wf, 'run_step', nodeId=target or wf['nodes'][0]['id'])
        self.controller.run_workflow(wf['id'])
        return self.controller.get(wf['id']), queued

    def test_one_target_waits_for_acceptance_then_pauses_without_commit_or_dependent_run(self):
        wf = self.create()
        target = wf['nodes'][0]['id']
        self.adapter.candidate = {'readout_hz': 6.91e9}
        wf, _ = self.execute(wf, target)
        self.assertEqual([c['nodeId'] for c in self.adapter.calls], [target, target])
        self.assertEqual((wf['status'], wf['engineState']), ('attention', 'awaiting_acceptance'))
        node = self.controller._node(wf, target)
        self.assertEqual(node['engineState'], 'awaiting_acceptance')
        self.assertFalse(node['mainProcedureComplete'])
        self.assertFalse(wf['approved'])
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM parameters').fetchone()[0], 0)
        self.assert_code('ACCEPTANCE_REQUIRED', lambda: self.command(wf, 'run_step', nodeId=target))
        wf = self.command(wf, 'approve', nodeId=target, attemptId=node['latestAttemptId'])
        self.assertEqual((wf['status'], wf['engineState']), ('paused', 'manual_ready'))
        self.assertEqual(self.controller._node(wf, target)['humanAcceptedAttemptId'], node['latestAttemptId'])
        self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 2)
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT value FROM parameters').fetchone()[0], '6910000000.0')

    def test_target_dependency_and_scope_are_enforced(self):
        wf = self.create()
        blocked = next(n for n in wf['nodes'] if n['dependsOn'])
        self.assert_code('PREREQUISITES_MISSING', lambda: self.command(wf, 'run_step', nodeId=blocked['id']))
        self.assert_code('NODE_ID_REQUIRED', lambda: self.command(wf, 'run_step'))
        self.assert_code('MANUAL_STEP_REQUIRED', lambda: self.controller._execute_node(wf['id'], wf['nodes'][0]['id']))
        queued = self.command(wf, 'run_step', nodeId=wf['nodes'][0]['id'])
        self.assert_code('MANUAL_STEP_SCOPE', lambda: self.controller._execute_node(wf['id'], blocked['id']))
        self.assertEqual(self.adapter.calls, [])
        self.controller.run_workflow(queued['id'])
        self.assertEqual(len(self.adapter.calls), 2)

    def test_legacy_run_commands_do_not_start_manual_hardware(self):
        for action in ('start', 'resume', 'retry'):
            with self.subTest(action=action):
                wf = self.create()
                self.assert_code('MANUAL_STEP_REQUIRED', lambda: self.command(wf, action, nodeId=wf['nodes'][0]['id']))
                self.controller.run_workflow(wf['id'])
        self.assertEqual(self.adapter.calls, [])

    def test_failed_quality_never_calls_model_or_accepts_and_retry_is_explicit(self):
        wf = self.create(budget={'maxAttemptsPerNode': 1})
        self.adapter.same_bytes = True
        def unexpected_model(*args, **kwargs):
            self.fail('Manual run must not request a model decision')
        self.controller._brain = unexpected_model
        wf, _ = self.execute(wf)
        self.assertEqual((wf['status'], wf['engineState']), ('attention', 'quality_attention'))
        self.assertEqual(len(self.adapter.calls), 2)
        self.assert_code('GATE_NOT_PASSED', lambda: self.command(wf, 'approve', nodeId=wf['currentNodeId']))
        self.assert_code('ATTEMPT_BUDGET', lambda: self.command(wf, 'run_step', nodeId=wf['currentNodeId']))

    def test_approval_binds_current_attempt_and_verified_requires_explicit_backtrack(self):
        wf, _ = self.execute(self.create())
        target = wf['currentNodeId']
        another = next(n for n in wf['nodes'] if n['id'] != target)
        self.assert_code('CURRENT_ACCEPTANCE_REQUIRED', lambda: self.command(wf, 'approve', nodeId=another['id']))
        wf = self.command(wf, 'approve', nodeId=target)
        self.assert_code('BACKTRACK_REQUIRED', lambda: self.command(wf, 'run_step', nodeId=target))
        wf = self.command(wf, 'backtrack', targetNodeId=target)
        self.assertEqual((wf['status'], wf['engineState']), ('paused', 'manual_ready'))
        self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 2)
        wf, _ = self.execute(wf, target)
        self.assertEqual(len(self.adapter.calls), 4)
        self.assertEqual(wf['engineState'], 'awaiting_acceptance')

    def test_duplicate_and_stale_run_step_cannot_expand_scope(self):
        wf = self.create()
        payload = {'action': 'run_step', 'nodeId': wf['nodes'][0]['id'],
                   'operationId': 'same-command', 'expectedRevision': wf['revision']}
        queued = self.controller.action(wf['id'], payload)
        self.assertEqual(self.controller.action(wf['id'], payload), queued)
        self.assert_code('STALE_REVISION', lambda: self.command(wf, 'run_step', nodeId=wf['nodes'][0]['id']))
        self.controller.run_workflow(wf['id'])
        self.controller.action(wf['id'], payload)
        self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 2)

    def test_policy_switch_never_bypasses_queued_or_unaccepted_gate(self):
        wf = self.create()
        wf = self.command(wf, 'run_step', nodeId=wf['nodes'][0]['id'])
        def switch(current):
            return self.controller.set_policy(current['id'], {'policy': 'autopilot',
                'operationId': 'switch-policy', 'expectedRevision': current['revision']})
        self.assert_code('INVALID_STATE', lambda: switch(wf))
        self.controller.run_workflow(wf['id'])
        wf = self.controller.get(wf['id'])
        self.assert_code('ACCEPTANCE_REQUIRED', lambda: switch(wf))
        wf = self.command(wf, 'approve', nodeId=wf['currentNodeId'])
        wf = switch(wf)
        self.assertEqual((wf['status'], wf['engineState']), ('paused', 'manual_ready'))
        self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 2)

    def test_restart_keeps_pass_unaccepted_and_does_not_reissue_queued_permit(self):
        wf, _ = self.execute(self.create())
        self.controller.close()
        self.controller = Controller(self.root, adapter=self.adapter, start_worker=False)
        current = self.controller.get(wf['id'])
        self.assertEqual(current['engineState'], 'awaiting_acceptance')
        self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 2)
        current = self.command(current, 'approve', nodeId=current['currentNodeId'])
        current = self.command(current, 'backtrack', targetNodeId=current['currentNodeId'])
        current = self.command(current, 'run_step', nodeId=current['currentNodeId'])
        self.controller.close()
        self.controller = Controller(self.root, adapter=self.adapter, start_worker=False)
        current = self.controller.get(wf['id'])
        self.assertEqual((current['status'], current['engineState']), ('paused', 'manual_ready'))
        self.assertEqual(current['manualStep']['state'], 'interrupted')
        self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 2)

    def test_unknown_effect_blocks_new_permission_and_approval(self):
        wf = self.create()
        self.adapter.fail = True
        wf, _ = self.execute(wf)
        self.assertEqual(wf['engineState'], 'needs_reconciliation')
        self.assert_code('RECONCILIATION_REQUIRED', lambda: self.command(wf, 'run_step', nodeId=wf['currentNodeId']))
        self.assertEqual(len(self.adapter.calls), 1)

    def test_last_human_approval_completes_without_running_again(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            current['nodes'] = current['nodes'][:1]
            self.controller.store.save(rt, current, 'single-node-fixture')
        wf, _ = self.execute(self.controller.get(wf['id']))
        self.assertFalse(wf['workflowComplete'])
        wf = self.command(wf, 'approve', nodeId=wf['currentNodeId'])
        self.assertEqual(wf['status'], 'completed')
        self.assertTrue(wf['workflowComplete'])
        self.assertEqual(len(self.adapter.calls), 2)

    def test_replay_and_smart_paths_keep_existing_start_semantics(self):
        for args in ({'mode': 'replay'}, {'policy': 'threshold'}):
            wf = self.create(**args)
            wf = self.command(wf, 'start')
            self.assertEqual(wf['status'], 'queued')

    def test_rejecting_pass_discards_candidate_and_invalidates_acceptance_marker(self):
        wf = self.create()
        self.adapter.candidate = {'readout_hz': 6.91e9}
        wf, _ = self.execute(wf)
        target = wf['currentNodeId']
        wf = self.command(wf, 'backtrack', targetNodeId=target)
        self.assertTrue(self.controller._node(wf, target)['result']['stale'])
        self.assert_code('GATE_NOT_PASSED', lambda: self.command(wf, 'approve', nodeId=target))
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM parameters').fetchone()[0], 0)
        wf, _ = self.execute(wf, target)
        wf = self.command(wf, 'approve', nodeId=target)
        wf = self.command(wf, 'backtrack', targetNodeId=target)
        self.assertNotIn('humanAcceptedAttemptId', self.controller._node(wf, target))

    def test_parameter_edits_do_not_hide_unaccepted_pass(self):
        wf, _ = self.execute(self.create())
        other = next(n for n in wf['nodes'] if n['id'] != wf['currentNodeId'])
        self.assert_code('ACCEPTANCE_REQUIRED', lambda: self.controller.set_parameters(wf['id'], other['id'], {
            'parameters': {'shots': 100}, 'operationId': 'edit-other', 'expectedRevision': wf['revision']}))
        self.assert_code('ACCEPTANCE_REQUIRED', lambda: self.command(wf, 'backtrack', targetNodeId=other['id']))
        self.assertEqual(self.controller.get(wf['id'])['engineState'], 'awaiting_acceptance')

    def test_operator_can_select_ready_branch_instead_of_first_ready_node(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            target = current['nodes'][1]
            target['dependsOn'] = []
            self.controller.store.save(rt, current, 'independent-branch-fixture')
        wf, _ = self.execute(self.controller.get(wf['id']), target['id'])
        self.assertEqual([c['nodeId'] for c in self.adapter.calls], [target['id'], target['id']])
        self.assertEqual(wf['nodes'][0]['attempt'], 0)

    def test_confirm_hardware_requires_new_explicit_step_permission(self):
        wf = self.create()
        self.adapter.profile['enabled'] = False
        self.controller.profile['enabled'] = False
        # Adapter profile is frozen when the workflow is created.
        wf = self.create()
        wf, _ = self.execute(wf)
        self.assertEqual(wf['engineState'], 'waiting_hardware')
        wf = self.command(wf, 'confirm_hardware')
        self.assertEqual((wf['status'], wf['engineState']), ('paused', 'manual_ready'))
        current = self.controller._node(wf, wf['currentNodeId'])
        self.assertEqual((current['status'],current['engineState']),('pending','pending'))
        self.assertNotIn('等待接线',current['detail'])
        self.controller.run_workflow(wf['id'])
        self.assertEqual(self.adapter.calls, [])
        wf, _ = self.execute(wf)
        self.assertEqual(len(self.adapter.calls), 2)

    def test_restart_repairs_confirmed_manual_handover_node_without_reissuing_step(self):
        wf=self.create()
        target=wf['nodes'][0]['id']
        with self.controller.store.transaction() as rt:
            wf=self.controller.get(wf['id'],rt)
            wf.update(status='paused',engineState='manual_ready',hardwareConfirmed=True,currentNodeId=target,
                      manualStep={'state':'interrupted','targetNodeId':target,'operationId':'old-permit'})
            self.controller._node(wf,target).update(status='attention',engineState='waiting_hardware',detail='等待接线')
            self.controller.store.save(rt,wf,'legacy_confirmed_handover_fixture')
        self.controller.close()
        self.controller=Controller(self.root,adapter=self.adapter,start_worker=False)
        restored=self.controller.get(wf['id'])
        self.assertEqual((restored['status'],restored['engineState']),('paused','manual_ready'))
        self.assertEqual(self.controller._node(restored,target)['engineState'],'pending')
        self.assertEqual(restored['manualStep']['state'],'interrupted')
        self.controller.run_workflow(wf['id'])
        self.assertEqual(self.adapter.calls,[])
        revision=restored['revision']
        self.controller._recover_interrupted()
        self.assertEqual(self.controller.get(wf['id'])['revision'],revision)

    def test_handover_repair_preserves_unconfirmed_and_uncertain_device_states(self):
        for guard in ('unconfirmed','effect','lease','unknown_node'):
            with self.subTest(guard=guard):
                wf=self.create();target=wf['nodes'][0]['id']
                with self.controller.store.transaction() as rt:
                    wf=self.controller.get(wf['id'],rt)
                    wf.update(status='paused',engineState='manual_ready',hardwareConfirmed=guard!='unconfirmed',currentNodeId=target)
                    self.controller._node(wf,target).update(status='attention',engineState='waiting_hardware')
                    if guard=='effect':
                        rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',('effect-guard',wf['id'],'attempt','unknown','{}',None))
                    elif guard=='lease':
                        rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)',('lease-guard',wf['id'],'attempt','quarantined','token'))
                    elif guard=='unknown_node':
                        wf['nodes'][1]['engineState']='unknown'
                    self.controller.store.save(rt,wf,'guard-fixture')
                self.controller._recover_interrupted()
                current=self.controller.get(wf['id'])
                self.assertEqual(self.controller._node(current,target)['engineState'],'waiting_hardware')
                self.assertEqual(current['revision'],wf['revision'])
        self.assertEqual(self.adapter.calls,[])

    def test_restart_during_unknown_running_attempt_reconciles_without_resend(self):
        wf = self.create()
        queued = self.command(wf, 'run_step', nodeId=wf['nodes'][0]['id'])
        def stop_after_dispatch(request, emit, cancel):
            raise KeyboardInterrupt('simulate process interruption')
        self.adapter.before_return = stop_after_dispatch
        with self.assertRaises(KeyboardInterrupt):
            self.controller._execute_node(wf['id'], wf['nodes'][0]['id'])
        self.controller.close()
        self.controller = Controller(self.root, adapter=self.adapter, start_worker=False)
        current = self.controller.get(wf['id'])
        self.assertEqual(current['engineState'], 'needs_reconciliation')
        self.assertEqual(current['manualStep']['state'], 'interrupted')
        self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 1)
        self.assert_code('RECONCILIATION_REQUIRED', lambda: self.command(current, 'run_step', nodeId=current['currentNodeId']))

    def test_pause_during_verification_preserves_pass_acceptance_gate(self):
        wf = self.create()
        def pause_on_verify(request, emit, cancel):
            if request['operationId'].endswith('/verify'):
                self.command(self.controller.get(wf['id']), 'pause')
        self.adapter.before_return = pause_on_verify
        wf, _ = self.execute(wf)
        self.assertEqual((wf['status'], wf['engineState']), ('paused', 'awaiting_acceptance'))
        self.assert_code('ACCEPTANCE_REQUIRED', lambda: self.command(wf, 'run_step', nodeId=wf['currentNodeId']))
        wf = self.command(wf, 'approve', nodeId=wf['currentNodeId'])
        self.assertEqual(wf['engineState'], 'manual_ready')

    def test_readout_change_waits_for_approval_before_invalidating_and_rebuilding(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            for node in current['nodes']:
                node.update(engineState='verified', status='success', mainProcedureComplete=True,
                            result={'dataKind': 'real', 'quality': {'status': 'passed'}, 'artifacts': []})
            optimizer = next(n for n in current['nodes'] if n['procedureId'] == 'ro_optimize')
            optimizer.update(engineState='pending', status='pending')
            self.controller.store.save(rt, current, 'readout-fixture')
        self.adapter.candidate = {'readout_amp': .4}
        wf, _ = self.execute(self.controller.get(wf['id']), optimizer['id'])
        self.assertEqual(wf['engineState'], 'awaiting_acceptance')
        self.assertNotIn('readoutRevalidations', wf)
        wf = self.command(wf, 'approve', nodeId=optimizer['id'])
        marker = wf['readoutRevalidations']['Q11']
        self.assertEqual((wf['status'], wf['engineState']), ('paused', 'manual_ready'))
        self.assertEqual(wf['currentNodeId'], marker['rabiNodeId'])
        self.assertEqual(self.controller._node(wf, optimizer['id'])['engineState'], 'stale')
        self.controller.run_workflow(wf['id'])
        self.assertEqual(len(self.adapter.calls), 2)
        attempt = self.controller.detail(wf['id'], optimizer['id'])['attempts'][-1]
        self.assertFalse(attempt['result'].get('stale', False), 'Accepted attempt evidence remains immutable')

    def test_changed_raw_evidence_cannot_be_human_accepted(self):
        wf, _ = self.execute(self.create())
        (self.root / 'data' / 'fake-raw' / 'acquisition-2.json').write_text('changed evidence')
        self.assert_code('ARTIFACT_HASH_MISMATCH', lambda: self.command(wf, 'approve', nodeId=wf['currentNodeId']))
        self.assertEqual(self.controller.get(wf['id'])['engineState'], 'awaiting_acceptance')

    def test_manual_rb_precheck_does_not_backtrack_automatically(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            for node in current['nodes']:
                node.update(engineState='verified', status='success',
                    result={'dataKind': 'real', 'quality': {'status': 'passed'}, 'parameters': {}})
            target = next(n for n in current['nodes'] if n['procedureId'] == 'sq_rb')
            current.update(currentNodeId=target['id'], status='queued')
            self.controller.store.save(rt, current, 'mismatched-rb-fixture')
        self.controller._physical_adapter = True
        self.assertFalse(self.controller._rb_prerequisites_current(wf['id'], target['id']))
        current = self.controller.get(wf['id'])
        self.assertEqual(current['currentNodeId'], target['id'])
        self.assertEqual(current['engineState'], 'prerequisites_missing')
        self.assertFalse(any(n['engineState'] == 'stale' for n in current['nodes']))
        self.assertEqual(self.adapter.calls, [])

    def test_fixed_readout_is_not_complete_before_human_acceptance(self):
        wf = self.create()
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            for node in current['nodes']:
                node.update(engineState='verified', status='success', result={'dataKind': 'real',
                            'quality': {'status': 'passed'}, 'artifacts': []})
            optimizer = next(n for n in current['nodes'] if n['procedureId'] == 'ro_optimize')
            optimizer.update(engineState='pending', status='pending')
            self.controller.store.save(rt, current, 'readout-fixture')
        self.adapter.candidate = {'readout_amp': .4}
        wf, _ = self.execute(self.controller.get(wf['id']), optimizer['id'])
        wf = self.command(wf, 'approve', nodeId=optimizer['id'])
        self.adapter.candidate = {}
        for procedure in ('sq_rabi_amplitude', 'sq_rabi_time', 'ro_iq01'):
            target = next(n for n in wf['nodes'] if n['procedureId'] == procedure)
            wf, _ = self.execute(wf, target['id'])
            wf = self.command(wf, 'approve', nodeId=target['id'])
        self.adapter.candidate = {'readout_amp': .4}
        wf, _ = self.execute(wf, optimizer['id'])
        self.assertEqual(wf['readoutRevalidations']['Q11']['state'], 'active')
        self.assertEqual(wf['readoutRevalidations']['Q11']['validationStatus'], 'awaiting_acceptance')
        wf = self.command(wf, 'approve', nodeId=optimizer['id'])
        self.assertEqual(wf['readoutRevalidations']['Q11']['state'], 'completed')
        self.assertEqual(wf['readoutRevalidations']['Q11']['validationStatus'], 'verified')

    def test_concurrent_dispatch_claims_one_permit_without_closing_active_attempt(self):
        import threading
        wf = self.create()
        wf = self.command(wf, 'run_step', nodeId=wf['nodes'][0]['id'])
        original = self.controller._execute_node
        rendezvous = threading.Barrier(2)
        competing_dispatch_done = threading.Event()
        failures = []
        def dispatch(*args, **kwargs):
            rendezvous.wait(timeout=5)
            try:
                return original(*args, **kwargs)
            except APIError:
                competing_dispatch_done.set()
                raise
        def held_acquisition(request, emit, cancel):
            self.assertTrue(competing_dispatch_done.wait(timeout=5))
        def worker():
            try:
                self.controller._run_manual_step(wf['id'])
            except BaseException as error:
                failures.append(error)
        self.controller._execute_node = dispatch
        self.adapter.before_return = held_acquisition
        workers = [threading.Thread(target=worker) for _ in range(2)]
        for worker_thread in workers:
            worker_thread.start()
        for worker_thread in workers:
            worker_thread.join(timeout=10)
        self.assertFalse(any(worker_thread.is_alive() for worker_thread in workers))
        self.assertEqual(failures, [])
        self.assertEqual(self.controller.get(wf['id'])['engineState'], 'awaiting_acceptance')
        self.assertEqual(len(self.adapter.calls), 2)

    def test_stale_attempt_id_is_rejected_with_current_workflow_revision(self):
        wf, _ = self.execute(self.create())
        self.assert_code('STALE_ATTEMPT', lambda: self.command(wf, 'approve',
            nodeId=wf['currentNodeId'], attemptId='a-different-attempt'))
        self.assertEqual(self.controller.get(wf['id'])['engineState'], 'awaiting_acceptance')

    def test_model_retry_experience_is_observed_only_after_human_acceptance(self):
        wf = self.create()
        self.adapter.same_bytes = True
        wf, _ = self.execute(wf)
        target = wf['currentNodeId']
        with self.controller.store.transaction() as rt:
            current = self.controller.get(wf['id'], rt)
            node = self.controller._node(current, target)
            self.controller._record_decision(rt, current, target, {'action': 'retry',
                'reason': 'Prior model suggested amplitude before switching to manual',
                'evidenceIds': [a['id'] for a in node['result']['artifacts']],
                'parameterChanges': {'drive_amp': .25}}, 'model')
            node['parameterOverrides'] = {'drive_amp': .25}
            self.controller.store.save(rt, current, 'prior-model-fixture')
        self.adapter.same_bytes = False
        wf, _ = self.execute(self.controller.get(wf['id']), target)
        with self.controller.store.connect() as rt:
            self.assertEqual(self.controller.experience.list(rt), [])
        wf = self.command(wf, 'approve', nodeId=target)
        with self.controller.store.connect() as rt:
            candidates = self.controller.experience.list(rt)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['provenance']['attemptId'], self.controller._node(wf, target)['latestAttemptId'])
