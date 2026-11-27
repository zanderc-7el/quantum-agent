"""Ownership refusals preserve controls and bounded evidence, never contact hardware."""
from copy import deepcopy
import json, subprocess, threading, unittest, urllib.request, urllib.error
from unittest.mock import patch
from backend.common import APIError
from backend import hardware
from tests import test_arc_controller_integration as fixture

class OwnershipDiagnostics(unittest.TestCase):
    def setUp(self):
        self.h=fixture.ArcControllerIntegration();self.h.setUp();self.addCleanup(self.h.tearDown)
        self.c=self.h.controller
        self.wf=self.h.create()
        self.execute=patch.object(type(self.h.adapter),'execute',side_effect=AssertionError('No device execution permitted'))
        self.execute.start();self.addCleanup(self.execute.stop)
    def state(self,waiting=True):
        with self.c.store.transaction() as rt:
            wf=self.c.get(self.wf['id'],rt);wf.update(status='attention' if waiting else 'queued',engineState='waiting_hardware' if waiting else 'queued',hardwareConfirmed=not waiting)
            wf['currentNodeId']=wf['nodes'][0]['id'];self.c.store.save(rt,wf,'TEST_ONLY initial state')
        return wf
    def counts(self):
        with self.c.store.connect() as rt:
            return {n:rt.db.execute('SELECT count(*) FROM '+n).fetchone()[0] for n in ('platform_commands','platform_effects','platform_attempts','platform_leases')}
    def rows(self):return self.c.store.events(self.wf['id'])
    def confirm(self,value,op='confirm-test'):
        wf=self.c.get(self.wf['id'])
        with patch.object(self.h.adapter,'hardware_ownership',return_value=value):
            with self.assertRaises(APIError) as err:self.c.action(wf['id'],{'operationId':op,'expectedRevision':wf['revision'],'action':'confirm_hardware'})
        self.assertEqual(err.exception.code,'DEVICE_IN_USE');return err.exception
    def test_confirmation_unknown_is_not_a_connection_claim_and_survives_rollback(self):
        before=self.state();counts=self.counts()
        exc=self.confirm({'verified':False,'owners':[],'reason':'secret D:/token/path timed out after 12 seconds'})
        self.assertIn('未能核实',str(exc));self.assertNotIn('超时',str(exc));self.assertNotIn('其他程序',str(exc))
        self.assertEqual(self.c.get(before['id']),before);self.assertEqual(self.counts(),counts)
        row=self.rows()[-1];self.assertEqual(row['event'],'hardware_ownership_rejected')
        evidence=row['payload']['ownershipCheck'];self.assertEqual(evidence['stage'],'confirmation')
        self.assertEqual(evidence['outcome'],'unverified');self.assertFalse(evidence['verified'])
        self.assertNotIn('secret',json.dumps(evidence));self.assertEqual(evidence,exc.ownershipCheck)
    def test_confirmation_lock_and_socket_are_distinct_and_redacted(self):
        self.state()
        for n,value in enumerate([
            {'verified':True,'owners':[{'kind':'chassis_lock','pid':123,'path':'D:/private/token.lock','operationId':'secret-token'}]},
            {'verified':True,'owners':[{'pid':456,'localPort':12345}]},
            {'verified':True,'owners':[{'unknown':'secret','pid':True}]},
        ]):
            exc=self.confirm(value,str(n));ev=exc.ownershipCheck
            self.assertEqual(ev['outcome'],('lock_records','socket_records','unknown_records')[n])
            self.assertNotIn('其他程序',str(exc));self.assertNotIn('secret',json.dumps(ev));self.assertNotIn('private',json.dumps(ev))
        self.assertEqual(len([e for e in self.rows() if e['event']=='hardware_ownership_rejected']),3)
    def test_schedule_preserves_device_fault_behavior_and_diagnostic(self):
        self.state(False)
        with patch.object(self.h.adapter,'preflight',return_value={'ready':True,'enabled':True}),patch.object(self.h.adapter,'hardware_ownership',return_value={'verified':False,'owners':[]}):
            self.c.run_workflow(self.wf['id'])
        wf=self.c.get(self.wf['id']);self.assertEqual(wf['engineState'],'waiting_hardware');self.assertTrue(wf['sharedDeviceFault'])
        node=wf['nodes'][0];self.assertIn('未能核实',node['detail']);self.assertEqual(node['ownershipCheck']['stage'],'schedule')
        self.assertEqual(self.counts(),{'platform_commands':0,'platform_effects':0,'platform_attempts':0,'platform_leases':0})
    def test_final_dispatch_refusal_is_in_attempt_and_does_not_execute(self):
        self.state(False)
        with patch.object(self.h.adapter,'hardware_ownership',return_value={'verified':True,'owners':[{'kind':'chassis_lock','pid':777,'path':'secret'}]}):
            self.assertFalse(self.c._execute_node(self.wf['id'],self.wf['nodes'][0]['id']))
        detail=self.c.detail(self.wf['id'],self.wf['nodes'][0]['id'])
        attempt=detail['attempts'][0];self.assertEqual(attempt['ownershipCheck']['stage'],'dispatch')
        self.assertEqual(attempt['ownershipCheck']['visiblePids'],[777]);self.assertIn('锁记录',attempt['error'])
        self.assertEqual(attempt['state'],'execution_failed');self.assertEqual(self.counts()['platform_leases'],0)
    def test_real_checker_timeout_is_explicit_without_command_text(self):
        with patch.object(hardware.os,'name','nt'),patch.object(hardware.subprocess,'run',side_effect=subprocess.TimeoutExpired(['secret'],12)):
            value=hardware.connection_owners({'instrument':{'address':'TCPIP::127.0.0.1::7110::SOCKET'}})
        self.assertEqual(value['check']['status'],'timeout');self.assertNotIn('secret',json.dumps(value))
    def test_unknown_shape_is_fail_closed_and_pids_are_bounded(self):
        self.state()
        exc=self.confirm({'verified':'yes','owners':[{'pid':i+1,'localPort':123} for i in range(30)],'unknownSecret':'hide'})
        self.assertFalse(exc.ownershipCheck['verified']);self.assertLessEqual(len(exc.ownershipCheck['visiblePids']),8)
        self.assertNotIn('unknownSecret',json.dumps(exc.ownershipCheck))
    def test_confirmed_release_keeps_existing_permission_transition(self):
        wf=self.state();before=self.counts()
        with patch.object(self.h.adapter,'hardware_ownership',return_value={'verified':True,'owners':[]}):
            payload={'operationId':'ok-confirm','expectedRevision':wf['revision'],'action':'confirm_hardware'}
            changed=self.c.action(wf['id'],payload)
            repeated=self.c.action(wf['id'],payload)
        self.assertTrue(changed['hardwareConfirmed']);self.assertEqual(changed['revision'],repeated['revision'])
        self.assertEqual(changed['status'],'queued');self.assertEqual(self.counts()['platform_commands'],before['platform_commands']+1)
        self.assertEqual(self.counts()['platform_effects'],0)
    def test_unreadable_adapter_result_is_failed_closed_without_exception_text(self):
        self.state()
        with patch.object(self.h.adapter,'hardware_ownership',side_effect=RuntimeError('secret filepath/token')):
            with self.assertRaises(APIError) as err:
                wf=self.c.get(self.wf['id']);self.c.action(wf['id'],{'operationId':'bad-reader','expectedRevision':wf['revision'],'action':'confirm_hardware'})
        self.assertEqual(err.exception.ownershipCheck['checkStatus'],'adapter_error');self.assertNotIn('secret',str(err.exception))
    def test_attention_confirmation_rolls_back_only_controls_and_refreshes_latest_check(self):
        self.state();attention=self.c.workspace.attention_items()[0];before=self.c.get(self.wf['id']);counts=self.counts()
        with patch.object(self.h.adapter,'hardware_ownership',return_value={'verified':True,'owners':[{'kind':'chassis_lock','pid':1234,'path':'secret'}]}):
            with self.assertRaises(APIError):self.c.workspace.attention_action(attention['id'],{'operationId':'attention-confirm','expectedRevision':attention['revision'],'action':'confirm_hardware'})
        self.assertEqual(self.c.get(self.wf['id']),before);self.assertEqual(self.counts(),counts)
        refreshed=self.c.workspace.attention_items()[0]
        self.assertGreater(refreshed['revision'],attention['revision']);self.assertEqual(refreshed['ownershipCheck']['visiblePids'],[1234])
        self.assertIn('锁记录',refreshed['message']);self.assertEqual(refreshed['actions'],attention['actions'])
    def test_legacy_attention_without_evidence_and_unknown_optional_fields_survive(self):
        wf=self.state()
        with self.c.store.transaction() as rt:
            wf=self.c.get(wf['id'],rt);wf['nodes'][0].update(detail='历史原文，原因未知',ownershipCheck={'schema':'future/unknown','path':'secret'})
            self.c.store.save(rt,wf,'TEST_ONLY legacy optional field')
        item=self.c.workspace.attention_items()[0]
        self.assertNotIn('ownershipCheck',item);self.assertEqual(item['message'],'历史原文，原因未知')
    def test_unknown_reconciliation_retains_quarantine_and_diagnostic(self):
        self.state(False)
        with patch.object(self.h.adapter,'hardware_ownership',return_value={'verified':False,'owners':[]}),patch.object(self.h.adapter,'reconcile',return_value={'status':'unknown'}):
            self.assertFalse(self.c._execute_node(self.wf['id'],self.wf['nodes'][0]['id']))
        wf=self.c.get(self.wf['id']);self.assertEqual(wf['engineState'],'needs_reconciliation');self.assertEqual(self.counts()['platform_leases'],1)
        detail=self.c.detail(wf['id'],wf['nodes'][0]['id']);self.assertEqual(detail['attempts'][0]['ownershipCheck']['outcome'],'unverified')
    def test_real_http_refusal_returns_bounded_check_and_keeps_workflow(self):
        from backend.server import Server
        wf=self.state();before=self.c.get(wf['id']);server=Server(('127.0.0.1',0),self.c)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_address[1])
            payload={'operationId':'http-confirm','expectedRevision':wf['revision'],'action':'confirm_hardware'}
            request=urllib.request.Request(base+'/api/workflows/'+wf['id']+'/actions',data=json.dumps(payload).encode(),method='POST',headers={'Origin':base,'Content-Type':'application/json','X-Qcal-Token':server.session_token})
            with patch.object(self.h.adapter,'hardware_ownership',return_value={'verified':True,'owners':[{'kind':'chassis_lock','pid':4321,'path':'secret/abc','operationId':'token'}]}):
                with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(request,timeout=5)
            self.assertEqual(err.exception.code,409);body=json.loads(err.exception.read())['error']
            self.assertEqual(body['code'],'DEVICE_IN_USE');self.assertEqual(body['ownershipCheck']['visiblePids'],[4321]);self.assertNotIn('secret',json.dumps(body))
            self.assertEqual(self.c.get(wf['id']),before)
        finally:server.shutdown();server.server_close();thread.join(2)
    def test_initial_workflow_without_selected_node_has_same_fail_closed_path(self):
        with self.c.store.transaction() as rt:
            wf=self.c.get(self.wf['id'],rt);wf.update(status='queued',engineState='queued',hardwareConfirmed=True)
            self.assertFalse(wf.get('currentNodeId'));self.c.store.save(rt,wf,'TEST_ONLY before first node')
        with patch.object(self.h.adapter,'preflight',return_value={'ready':True,'enabled':True}),patch.object(self.h.adapter,'hardware_ownership',return_value={'verified':False,'owners':[]}):
            self.c.run_workflow(wf['id'])
        waiting=self.c.get(wf['id']);self.assertEqual(waiting['engineState'],'waiting_hardware');self.assertEqual(waiting['ownershipCheck']['stage'],'schedule')
        self.confirm({'verified':True,'owners':[{'pid':123,'localPort':345}]})
        item=self.c.workspace.attention_items()[0];self.assertEqual(item['ownershipCheck']['stage'],'confirmation');self.assertIn('TCP',item['message'])
    def test_cleanup_failure_keeps_both_quarantine_and_original_check(self):
        self.state(False)
        with patch.object(self.h.adapter,'hardware_ownership',return_value={'verified':False,'owners':[]}),patch('backend.flux_execution.finalize_lease',side_effect=APIError('BIAS_LOCK_RECONCILIATION','TEST_ONLY cleanup unconfirmed')):
            self.assertFalse(self.c._execute_node(self.wf['id'],self.wf['nodes'][0]['id']))
        wf=self.c.get(self.wf['id']);self.assertEqual(wf['engineState'],'needs_reconciliation');self.assertEqual(self.counts()['platform_leases'],1)
        item=self.c.workspace.attention_items()[0]
        self.assertIn('TEST_ONLY cleanup unconfirmed',item['message']);self.assertEqual(item['ownershipCheck']['outcome'],'unverified')
    def test_real_checker_only_claims_completed_for_valid_os_records(self):
        from types import SimpleNamespace
        for content,verified,status in [('[]',True,'completed'),('[{"OwningProcess":12,"LocalPort":4000}]',True,'completed'),('[{"OwningProcess":true,"LocalPort":4000}]',False,'invalid_response'),('bad',False,'invalid_response')]:
            with patch.object(hardware.os,'name','nt'),patch.object(hardware.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=content)):
                value=hardware.connection_owners({'instrument':{'address':'TCPIP::127.0.0.1::7110::SOCKET'}})
            self.assertIs(value['verified'],verified);self.assertEqual(value['check']['status'],status)
    def test_failed_reconciliation_keeps_original_check_and_pending_effect(self):
        self.state(False)
        with patch.object(self.h.adapter,'hardware_ownership',return_value={'verified':False,'owners':[]}),patch.object(self.h.adapter,'reconcile',side_effect=RuntimeError('TEST_ONLY reconciliation unavailable')):
            self.assertFalse(self.c._execute_node(self.wf['id'],self.wf['nodes'][0]['id']))
        wf=self.c.get(self.wf['id']);self.assertEqual(wf['engineState'],'needs_reconciliation');self.assertEqual(self.counts()['platform_leases'],1)
        item=self.c.workspace.attention_items()[0];self.assertEqual(item['ownershipCheck']['stage'],'dispatch')
        self.assertIn('reconciliation unavailable',item['message'])
