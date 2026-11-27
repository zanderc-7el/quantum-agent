import json
import unittest
from unittest.mock import patch
from backend.common import APIError, file_hash
from backend.smart_q11_launch import launch
from tests import test_smart_q11_launch as fixture


AMP = 0.8593908601277258


class ShortHalfSpectrum(unittest.TestCase):
    def setUp(self):
        case = fixture.SmartQ11Launch()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.c, self.body = case.controller, case.body
        self.c.hardware_ownership = lambda *args, **kwargs: {'verified': True, 'owners': []}
        no_hardware = patch.object(self.c.adapter, 'execute', side_effect=AssertionError('physical execution prohibited'))
        self.execute = no_hardware.start()
        self.addCleanup(no_hardware.stop)

    def start(self, source_half=AMP):
        c = self.c
        src = c.create({'mode':'real', 'name':'TEST_ONLY source', 'resourceIds':['Q11'], 'policy':'strict'})
        spec = next(n for n in src['nodes'] if n['procedureId'] == 'sq_spectroscopy')
        aid = 'archived-f01'
        settings = {'readout_hz':7.00961e9, 'readout_amp':.76, 'readout_width_s':2e-6, 'drive_amp':AMP}
        if source_half is not None:
            settings['half_pi_amp'] = source_half
        with c.store.transaction() as rt:
            captures = []
            for suffix in ('measure', 'verify'):
                op = aid + '/' + suffix
                path = c.root/'data'/suffix/'raw_scan.npz'
                path.parent.mkdir()
                path.write_bytes(('TEST_ONLY '+suffix).encode())
                raw = {'id':suffix, 'name':path.name, 'kind':'raw', 'sha256':file_hash(path)}
                rt.db.execute('INSERT INTO platform_artifacts(id,path,sha256,payload) VALUES(?,?,?,?)',
                              (suffix,str(path),raw['sha256'],json.dumps(raw)))
                rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',(op,src['id'],aid,'completed','{}','{}'))
                captures.append({'artifacts':[raw]})
            result = {**captures[1], 'dataKind':'real', 'candidateParameters':{'drive_hz':4.85905e9},
                      'quality':{'status':'passed'}, 'selectionResult':captures[0]}
            attempt = {'id':aid,'nodeId':spec['id'],'number':1,'state':'verified','operationId':aid+'/measure',
                       'verificationOperationId':aid+'/verify','result':result,'parameters':settings}
            rt.db.execute('INSERT INTO platform_attempts VALUES(?,?,?,?,?)',(aid,src['id'],spec['id'],1,json.dumps(attempt)))
            src.update(status='paused', engineState='paused')
            spec.update(engineState='verified', latestAttemptId=aid)
            c.store.save(rt,src,'TEST_ONLY_archive')
        wf = launch(c,self.body)
        return wf, next(n for n in wf['nodes'] if n['procedureId']=='sq_spectroscopy')

    def effective(self,wf,node):
        return self.c.pulse_preview(wf['id'],node['id'],{'expectedRevision':wf['revision'],'parameters':{}})['effectiveParameters']

    def raw_row(self, wid):
        with self.c.store.connect() as rt:
            return rt.db.execute('SELECT payload FROM platform_workflows WHERE id=?',(wid,)).fetchone()[0]

    def test_new_launch_native_frequency_preview_pairs_amplitudes_without_accepting_calibration(self):
        wf,n = self.start()
        init = wf['smartRun']['initialization']
        self.assertEqual(init['procedureHints']['sq_spectroscopy'], {'drive_amp':AMP,'half_pi_amp':AMP})
        self.assertIs(init['acceptedCalibrationInherited'],False)
        p = self.effective(wf,n)
        self.assertEqual((p['drive_amp'],p['half_pi_amp'],p['drive_hz']),(AMP,AMP,4.85905e9))
        with self.c.store.connect() as rt:
            p,versions = self.c._parameters(rt,wf,n)
            self.c._validate_plan(wf,n,p,versions,'TEST_ONLY-admission',joint_budget=True,rt=rt)
            self.assertEqual((versions['drive_amp'], versions['half_pi_amp']),(0,0))
        self.execute.assert_not_called()

    def test_old_verified_source_missing_companion_new_seed_and_legacy_replay(self):
        from backend.smart_q11_launch import spectroscopy_initial_hints
        wf,n = self.start(source_half=None)
        init = wf['smartRun']['initialization']
        self.assertEqual(init['procedureHints']['sq_spectroscopy']['half_pi_amp'],AMP)
        del init['procedureHints']['sq_spectroscopy']['half_pi_amp']
        with self.c.store.transaction() as rt:
            self.c.store.save(rt,wf,'TEST_ONLY_preupgrade_initialization')
        before = self.raw_row(wf['id'])
        p = self.effective(self.c.get(wf['id']),n)
        self.assertEqual((p['drive_amp'],p['half_pi_amp']),(AMP,AMP))
        self.assertEqual(before,self.raw_row(wf['id']))
        base = {'pulse_compatibility':'f8_validated_short_half_dc_buff2ns_v1'}
        for unrelated in ({**n,'resourceId':'Q18'},{**n,'procedureId':'sq_t1'}):
            self.assertNotIn('half_pi_amp', spectroscopy_initial_hints(wf,unrelated,init,base))
        for foreign in ({**wf,'mode':'replay'},{**wf,'policy':'strict'},{**wf,'configurationBinding':{'chip':'other'}},
                        {**wf,'adapterProfileHash':'other'}):
            self.assertNotIn('half_pi_amp',spectroscopy_initial_hints(foreign,n,init,base))
        self.assertNotIn('half_pi_amp',spectroscopy_initial_hints(wf,n,{**init,'rawReferences':[]},base))
        self.assertNotIn('half_pi_amp',spectroscopy_initial_hints(wf,n,init,{'pulse_compatibility':'other'}))

    def test_explicit_persisted_mismatch_remains_rejected_and_current_node_is_true_admission(self):
        wf,n = self.start(source_half=.98)
        with self.c.store.transaction() as rt:
            wf['currentNodeId'] = next(v['id'] for v in wf['nodes'] if v['procedureId']=='ro_power_shift')
            self.c.store.save(rt,wf,'TEST_ONLY_prior_power_node')
        with self.assertRaisesRegex(APIError,'同幅度'):
            self.effective(wf,n)
        self.assertFalse(self.c._execute_node(wf['id'],n['id']))
        current = self.c.get(wf['id'])
        self.assertEqual((current['currentNodeId'],current['engineState']),(n['id'],'plan_rejected'))
        self.assertEqual(self.c._node(current,n['id'])['lastAdmissionError']['code'],'PULSE_POLICY_MISMATCH')
        self.assert_no_dispatch(current)

    def assert_no_dispatch(self,wf):
        self.execute.assert_not_called()
        with self.c.store.connect() as rt:
            for name in ('platform_attempts','platform_effects','platform_leases'):
                self.assertEqual(rt.db.execute('SELECT count(*) FROM '+name+' WHERE workflow_id=?',(wf['id'],)).fetchone()[0],0)

    def test_manually_submitted_conflicting_pair_and_single_explicit_override_do_not_get_normalized_away(self):
        wf,n = self.start()
        with self.assertRaisesRegex(APIError,'同幅度'):
            self.c.pulse_preview(wf['id'],n['id'],{'expectedRevision':wf['revision'],'parameters':{'drive_amp':.82,'half_pi_amp':.55}})
        n['parameterOverrides'] = {'half_pi_amp':.5}
        with self.c.store.transaction() as rt:
            self.c.store.save(rt,wf,'TEST_ONLY_override')
        with self.assertRaisesRegex(APIError,'同幅度'):
            self.effective(wf,n)
        self.assert_no_dispatch(wf)

    def test_existing_rejection_projects_true_event_target_and_stale_power_retry_cannot_change_it(self):
        wf,n = self.start(source_half=.98)
        power = next(k for k in wf['nodes'] if k['procedureId']=='ro_power_shift')
        power.update(status='completed',engineState='verified')
        with self.c.store.transaction() as rt:
            self.c.store.save(rt,wf,'TEST_ONLY_prior_verified')
        self.assertFalse(self.c._execute_node(wf['id'],n['id']))
        old = json.loads(self.raw_row(wf['id']))
        old['currentNodeId'] = power['id']
        with self.c.store.transaction() as rt:
            rt.db.execute('UPDATE platform_workflows SET payload=? WHERE id=?',(json.dumps(old),wf['id']))
        raw_before = self.raw_row(wf['id'])
        current = self.c.get(wf['id'])
        others = [w for w in self.c.store.workflows()+self.c.store.snapshot()['workflows'] if w['id']==wf['id']]
        self.assertTrue(all(w['currentNodeId']==n['id'] for w in [current,*others]))
        import backend.store as storage
        with patch.object(storage,'_read_workflow',lambda rt,payload: json.loads(payload)):
            stale = next(a for a in self.c.workspace.attention_items()
                         if a.get('workflowId')==wf['id'] and a['state']=='open')
        self.assertEqual(stale['nodeId'],power['id'])
        items = self.c.workspace.attention_items()
        self.assertEqual(next(a for a in items if a['id']==stale['id'])['state'],'resolved')
        attn = [a for a in items if a.get('workflowId')==wf['id'] and a['state']!='resolved']
        self.assertEqual([a.get('nodeId') for a in attn],[n['id']])
        self.assertEqual(self.raw_row(wf['id']),raw_before)
        with self.assertRaises(APIError) as refused:
            self.c.action(wf['id'],{'action':'retry','nodeId':power['id'],'expectedRevision':current['revision'],'operationId':'old-power-button'})
        self.assertEqual(refused.exception.code,'ADMISSION_NODE_CHANGED')
        retry = self.c.action(wf['id'],{'action':'retry','nodeId':n['id'],'expectedRevision':current['revision'],'operationId':'correct-node'})
        self.assertEqual(self.c._node(retry,n['id'])['engineState'],'retry_queued')
        self.assertEqual(self.c._node(retry,power['id'])['engineState'],'verified')
        self.assert_no_dispatch(wf)

