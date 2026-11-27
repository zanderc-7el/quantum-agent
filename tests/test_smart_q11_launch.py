import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
from backend.common import APIError, ROOT, file_hash
from backend.controller import Controller
from backend.smart_q11_launch import attach_rabi_history, launch, rabi_grid_hint, PROFILE, START_SCOPE


class SmartQ11Launch(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        directory = root / 'data/imports'
        directory.mkdir(parents=True)
        (directory / 'history.json').write_text(json.dumps({'version': 1, 'resources': [{'id': 'Q11', 'label': 'Q11'}], 'records': []}))
        adapter = ArcLightAdapter(ROOT, ROOT / 'adapters/arclight_profile_v3.json')
        self.controller = Controller(root, adapter=adapter, start_worker=False)
        self.addCleanup(self.controller.close)
        self.body = {'name':'Q11', 'resourceId':'Q11', 'operationId':'desktop-start',
                     'adapterId':PROFILE, 'scope':START_SCOPE, 'stationConfirmed':True}

    def test_historical_scan_grid_is_only_a_hint_and_obeys_actual_base_profile_schema(self):
        source = {'id':'source','nodes':[{'procedureId':'sq_rabi_amplitude','id':'amp','engineState':'quality_attention','latestAttemptId':'try'}]}
        initial = {'readout_amp':0.76,'drive_hz':4.85905e9}
        p = {**initial,'acquisitions_per_point':16,'shots':2000,'points':41,'capture_repeats':2,
             'amp_start':.02,'amp_stop':.98,'drive_length_s':375e-9}
        path = self.controller.root/'data'/'raw_scan.npz';path.write_bytes(b'fixture only')
        raw = {'id':'raw','name':'raw_scan.npz','kind':'raw','sha256':file_hash(path)}
        analysis_path = path.with_name('platform_analysis.json')
        repeats = [{'repeat':1,'fit':None},{'repeat':2,'fit':{'cycles_in_window':3.1}}]
        analysis_path.write_text(json.dumps({'analysis':{'kind':'rabi_amplitude','accepted':False,
                                  'groups':[{'repeats':repeats}]}}))
        analysis = {'id':'analysis','name':analysis_path.name,'kind':'json','sha256':file_hash(analysis_path)}
        a = {'id':'try','nodeId':'amp','number':4,'operationId':'sel','parameters':p,
             'result':{'dataKind':'real','quality':{'status':'failed'},'provenance':{'freshAcquisition':True},'artifacts':[raw,analysis]}}
        with self.controller.store.transaction() as rt:
            self.controller.store.attempt(rt,'source',a)
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',('sel','source','try','completed','{}','{}'))
            rt.db.execute('INSERT INTO platform_artifacts(id,path,sha256,payload) VALUES(?,?,?,?)',('raw',str(path),raw['sha256'],json.dumps(raw)))
            rt.db.execute('INSERT INTO platform_artifacts(id,path,sha256,payload) VALUES(?,?,?,?)',('analysis',str(analysis_path),analysis['sha256'],json.dumps(analysis)))
            hint = rabi_grid_hint(self.controller,rt,source,initial)
            self.assertEqual((hint['sourceQuality'],hint['sourceBlocks'],hint['grid']['acquisitions_per_point']),('failed',16,8))
            self.assertNotIn('drive_hz',hint['grid'])
            self.assertIsNone(rabi_grid_hint(self.controller,rt,source,{**initial,'readout_amp':.8}))
            wf = {'smartRun':{'schema':START_SCOPE,'initialization':{'rabiGridSource':hint}}}
            context = attach_rabi_history({'allowedEvidenceIds':['current']},wf,'sq_rabi_amplitude')
            self.assertEqual(context['allowedEvidenceIds'],['current'])
            self.assertEqual(context['historicalRabiGrid']['native']['captures'],
                             [{'capture':1,'fitAvailable':False},{'capture':2,'fitAvailable':True,'cyclesInWindow':3.1}])
            self.assertFalse(context['historicalRabiGrid']['native']['nativeAccepted'])
            self.assertEqual(attach_rabi_history({},wf,'sq_t1'),{})
            analysis_path.write_text('{}')
            self.assertNotIn('previousAnalysis',rabi_grid_hint(self.controller,rt,source,initial))

    def test_atomic_start_replay_and_stopped_status(self):
        with patch.object(self.controller.adapter, 'execute', side_effect=AssertionError('offline')):
            run = launch(self.controller, self.body)
            self.assertEqual((run['status'],run['policy'],run['budget']['mode']), ('queued','autopilot','accounting_only'))
            self.assertEqual(len(run['nodes']),13)
            self.assertTrue(all(n['attempt'] == 0 for n in run['nodes']))
            self.assertTrue(run['hardwareConfirmed'])
            self.assertEqual(run['smartRun']['gate'],'pending')
            self.assertEqual(launch(self.controller,self.body)['id'],run['id'])
            self.controller.action(run['id'],{'action':'pause','operationId':'pause','expectedRevision':run['revision']})
            self.assertEqual(launch(self.controller,self.body)['status'],'paused')
            with self.assertRaises(APIError):
                launch(self.controller, {**self.body, 'name':'different'})
            self.assertEqual(len(self.controller.list()),1)

    def test_scope_and_transaction_failure_leave_no_launch_or_permit(self):
        for bad in [{'resourceId':'Q18'},{'stationConfirmed':False},{'scope':'other'},{'budget':{}}]:
            with self.subTest(bad=bad), self.assertRaises(APIError):
                launch(self.controller,{**self.body,**bad})
        with patch.object(self.controller, 'action', side_effect=RuntimeError('check after confirmation')), self.assertRaises(RuntimeError):
            launch(self.controller,self.body)
        self.assertEqual(self.controller.list(),[])
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('select count(*) from platform_commands').fetchone()[0],0)
            self.assertEqual(rt.db.execute('select count(*) from platform_effects').fetchone()[0],0)

    def test_workspace_agent_start_uses_same_smart_workflow_and_actual_recorded_handover(self):
        from backend.workspace_actions import execute
        ws = self.controller.workspace
        proposal = {'tool':'start_measurement','arguments':{'resourceIds':['Q11']}}
        conversation = {'scope':{'kind':'workspace'}}
        command = {'message':'开始测Q11','operationId':'workspace-start'}
        blocked = execute(ws,proposal,command,conversation,0)
        self.assertEqual(blocked['outcomes'][0]['code'],'SMART_HANDOVER_REQUIRED')
        prior = self.controller.create({'mode':'real','name':'已交接的固定机台','resourceIds':['Q11'],'policy':'strict'})
        with self.controller.store.transaction() as rt:
            prior.update(hardwareConfirmed=True,hardwareConfirmedAt='2026-11-26T00:00:00Z',status='paused')
            self.controller.store.save(rt,prior,'offline_handover_fixture')
        command = {**command,'operationId':'workspace-next-start'}
        response = execute(ws,proposal,command,conversation,0)
        new = self.controller.get(response['outcomes'][0]['workflowId'])
        self.assertEqual(new['hardwareConfirmationSource'],prior['id'])
        self.assertEqual(new['smartRun']['schema'],START_SCOPE)
        self.assertEqual(new['status'],'queued')
        self.assertEqual(execute(ws,proposal,command,conversation,0),response)
        self.assertEqual(len(self.controller.list()),2)
