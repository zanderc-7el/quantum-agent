"""Attempt version admission uses an isolated DB; never starts a device."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
import threading
import urllib.request
import urllib.error
import shutil

from adapters.arclight import ArcLightAdapter
from backend.arclight_versions import fingerprint
from backend.common import APIError
from tests.test_controller import make_fixture
from backend.common import digest

ROOT = Path(__file__).resolve().parents[1]


class AttemptVersionContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c, self.fake = make_fixture(Path(self.tmp.name))
        self.c.adapter = ArcLightAdapter(ROOT, ROOT/'adapters/arclight_profile_v3.json')
        self.c.profile = self.c.adapter.profile
        self.c.adapter_id = self.c.profile['id']
        self.c.adapter_profile_hash = fingerprint(ROOT, self.c.profile)
        self.c._physical_adapter = True
        self.w = self.c.create({'name':'TEST ONLY attempt version', 'mode':'real', 'policy':'strict',
            'coverage':'full', 'resourceIds':['Q11'], 'budget':{'maxAttemptsPerNode':4}})
        self.n = next(n for n in self.w['nodes'] if n['procedureId']=='sq_rabi_amplitude')
        # Admission fixture: no accepted calibration or acquisition is invented.
        with self.c.store.transaction() as rt:
            w=self.c.get(self.w['id'],rt);n=self.c._node(w,self.n['id'])
            n['parameterOverrides']={'drive_length_s':375e-9,'points':41,'capture_repeats':2,
                'shots':2000,'acquisitions_per_point':8,'amp_start':.02,'amp_stop':.98}
            n['attempt']=3;w.update(status='attention',engineState='quality_attention',currentNodeId=n['id'])
            self.c.store.save(rt,w,'test_setup')
        self.w=self.c.get(self.w['id'])

    def tearDown(self):
        self.c.close();self.tmp.cleanup()

    def select(self, **extra):
        return self.c.select_execution_version(self.w['id'],self.n['id'],{
            'operationId':'select-version','expectedRevision':self.w['revision'],
            'executionVersion':'rabi16-v1','reason':'Explicit TEST ONLY 16 block authorization',**extra})

    def test_explicit_selection_keeps_workflow_binding_and_no_effect_or_lease(self):
        before=deepcopy(self.w)
        after=self.select()
        self.assertEqual(after['adapterId'],before['adapterId'])
        self.assertEqual(after['adapterProfileHash'],before['adapterProfileHash'])
        d=self.c.detail(after['id'],self.n['id'])
        self.assertEqual(d['parameterBounds']['acquisitions_per_point']['max'],16)
        self.assertEqual(d['parameters']['acquisitions_per_point'],16)
        self.assertEqual(d['resolvedExecutionPlan']['estimatedTotalShots'],2624000)
        self.assertEqual(d['attemptExecutionVersion']['nextAttemptNumber'],4)
        self.assertEqual(self.select(),after)
        with self.c.store.connect() as rt:
            for table in ('platform_effects','platform_leases','platform_attempts','parameters'):
                self.assertEqual(rt.db.execute('select count(*) from '+table).fetchone()[0],0)

    def test_scope_revision_and_arbitrary_profile_fail_closed(self):
        for extra in ({'expectedRevision':0},{'profile':'adapters/arclight_profile_v3.json'},
                      {'executionVersion':'foreign'},{'executionVersion':True}):
            with self.subTest(extra=extra),self.assertRaises(APIError):self.select(**extra)
        with self.assertRaises(APIError):
            self.c.select_execution_version(self.w['id'],self.w['nodes'][0]['id'],{
                'operationId':'wrong-method','expectedRevision':self.w['revision'],
                'executionVersion':'rabi16-v1','reason':'TEST ONLY'})
        self.assertEqual(self.c.get(self.w['id']),self.w)

    def test_changed_parameters_require_a_new_explicit_selection(self):
        w=self.select()
        with self.assertRaises(APIError) as caught:
            self.c.set_parameters(w['id'],self.n['id'],{'operationId':'change-after-version',
                'expectedRevision':w['revision'],'parameters':{'drive_length_s':400e-9}})
        self.assertEqual(caught.exception.code,'EXECUTION_VERSION_PARAMETERS_CHANGED')
        d=self.c.detail(w['id'],self.w['nodes'][0]['id'])
        self.assertEqual(d['parameterBounds']['acquisitions_per_point']['max'],8)

    def _ready(self):
        # Lifecycle-only fixture: a single isolated node, no physical calibration
        # claim. Real planner/binding/budget stay in use; transport is injected.
        with self.c.store.transaction() as rt:
            w=self.c.get(self.w['id'],rt);n=self.c._node(w,self.n['id'])
            w['nodes']=[n];w['edges']=[];n['dependsOn']=[]
            # Context gate compares a non-output gate length with the ledger.
            rt.db.execute('INSERT INTO parameters VALUES(?,?,?)',(w['id']+':Q11:drive_length_s','3.75e-7',1))
            w['hardwareConfirmed']=True
            self.c.store.save(rt,w,'isolated_lifecycle_setup')
        self.w=self.c.get(self.w['id'])

    def test_one_attempt_uses_one_version_for_both_effects_then_cannot_reuse(self):
        from adapters.arclight_rabi16_adapter import ArcLightRabi16Adapter
        self._ready();w=self.select();self.fake.candidate={'drive_amp':.8}
        def execute(adapter,request,*args):
            result=self.fake.execute(request,*args)
            result['provenance']={'executionBinding':deepcopy(request['executionBinding'])}
            return result
        with patch.object(ArcLightAdapter,'hardware_ownership',return_value={'verified':True,'owners':[]}),\
             patch.object(ArcLightAdapter,'preflight',return_value={'ready':True,'enabled':False}),\
             patch.object(ArcLightRabi16Adapter,'execute',execute):
            queued=self.c.action(w['id'],{'action':'run_step','nodeId':self.n['id'],
                'operationId':'one-step','expectedRevision':w['revision']})
            self.assertEqual(len(self.fake.calls),0)
            self.c.run_workflow(w['id'])
        final=self.c.get(w['id']);node=self.c._node(final,self.n['id'])
        self.assertEqual(len(self.fake.calls),2,node.get('detail'))
        first,second=self.fake.calls
        self.assertEqual(first['executionBinding'],second['executionBinding'])
        self.assertEqual(first['executionBinding']['attemptNumber'],4)
        self.assertEqual(first['parameters']['acquisitions_per_point'],16)
        self.assertEqual(first['parameters']['shots'],2000)
        self.assertEqual(final['reservedShots'],5248000)
        self.assertEqual(final['adapterProfileHash'],self.w['adapterProfileHash'])
        self.assertEqual(node['engineState'],'awaiting_acceptance',node.get('detail'))
        # Cross the real bridge/worker admission boundary for both controller
        # requests. Popen is stopped before any native process or connection.
        from adapters.arclight_rabi16_runtime import policy
        from adapters.arclight_rabi16_runtime.execute_worker import validate_admission,verify_sources
        from adapters.f8 import AdapterError
        isolated=Path(self.tmp.name)
        entries=json.loads((ROOT/policy.MANIFEST).read_text(encoding='utf-8'))
        for name in {r['path'] for r in entries}|{policy.MANIFEST}:
            target=isolated/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,target)
        verify_sources(isolated)
        bridge=ArcLightRabi16Adapter(isolated,policy.load_profile(isolated)).with_execution_enabled()
        checked=[]
        def stop_at_process(*args,**kwargs):
            request=self.fake.calls[len(checked)]
            folder=bridge._operation_dir(request['operationId'])
            plan=json.loads((folder/'plan.json').read_text(encoding='utf-8'))
            accepted=validate_admission(isolated,folder,plan)
            self.assertEqual(plan['estimated_total_shots'],2624000)
            self.assertEqual(plan['totalRows'],82)
            self.assertEqual(accepted,{k:v for k,v in request.items() if k!='outputDir'})
            checked.append(plan['executionBinding'])
            raise OSError('TEST ONLY: stop before any native process')
        with patch('adapters.arclight.subprocess.Popen',side_effect=stop_at_process):
            for request in (first,second):
                with self.assertRaises(AdapterError) as error:bridge.execute(request,lambda e:None,lambda:False)
                self.assertEqual(error.exception.code,'FAILED_BEFORE_START')
        self.assertEqual(checked,[first['executionBinding'],second['executionBinding']])
        with self.c.store.connect() as rt:
            attempts,_=self.c.store.detail(w['id'],node['id'])
            actual=attempts[-1]
            scoped=self.c.for_attempt(w['id'],actual,rt)
            self.assertIs(type(scoped.adapter),ArcLightRabi16Adapter)
            from backend.attempt_execution import validate_request
            self.assertIs(type(validate_request(self.c,rt,first).adapter),ArcLightRabi16Adapter)
            with self.assertRaises(APIError):validate_request(self.c,rt,{**first,'attemptId':'foreign'})
            self.assertEqual(rt.db.execute('select count(*) from platform_effects').fetchone()[0],2)
            self.assertEqual(rt.db.execute('select count(*) from platform_leases').fetchone()[0],0)
            self.assertIs(scoped.for_node(w['id'], node['id'], rt, purpose='view'), scoped)
        detail = self.c.detail(w['id'], node['id'])
        self.assertEqual(detail['attemptExecutionVersion']['state'], 'consumed')
        self.assertEqual(detail['workflowRevision'], final['revision'])
        self.assertEqual(detail['parameters']['acquisitions_per_point'], 16)
        approved=self.c.action(w['id'],{'action':'approve','nodeId':node['id'],
            'operationId':'accept-test-only','expectedRevision':final['revision'],'attemptId':actual['id']})
        self.assertEqual(self.c._node(approved,node['id'])['engineState'],'verified')
        self.c.run_workflow(w['id']);self.assertEqual(len(self.fake.calls),2)

    def test_selected_authorization_rejects_upstream_version_change_and_restart_does_not_start(self):
        self._ready();w=self.select()
        self.c._recover_interrupted()
        self.assertEqual(self.c.get(w['id'])['status'],'paused')
        with self.c.store.transaction() as rt:
            rt.db.execute('INSERT INTO parameters VALUES(?,?,?)',(w['id']+':Q11:readout_hz','7020000000.0',1))
        detail=self.c.detail(w['id'],self.n['id'])
        self.assertEqual(detail['resolvedExecutionPlan']['status'],'unavailable')
        self.assertEqual(detail['attemptExecutionVersion']['state'],'selected')
        with self.assertRaises(APIError) as error:self.c.for_node(w['id'],self.n['id'])
        self.assertEqual(error.exception.code,'EXECUTION_VERSION_PARAMETERS_CHANGED')
        current=self.c.get(w['id'])
        restored=self.c.select_execution_version(w['id'],self.n['id'],{'operationId':'cancel-stale',
            'expectedRevision':current['revision'],'executionVersion':'default','reason':'TEST ONLY stale cancellation'})
        self.assertIsNone(self.c.detail(w['id'],self.n['id'])['attemptExecutionVersion'])
        self.assertEqual(self.c.detail(w['id'],self.n['id'])['parameters']['acquisitions_per_point'],8)
        self.assertEqual(self.fake.calls,[])

    def test_unknown_outcome_keeps_quarantine_and_consumes_only_one_attempt(self):
        from adapters.arclight_rabi16_adapter import ArcLightRabi16Adapter
        self._ready();w=self.select()
        def lost(adapter,request,*args):
            self.fake.calls.append(deepcopy(request));raise RuntimeError('TEST ONLY lost receipt')
        with patch.object(ArcLightAdapter,'hardware_ownership',return_value={'verified':True,'owners':[]}),\
             patch.object(ArcLightAdapter,'preflight',return_value={'ready':True,'enabled':False}),\
             patch.object(ArcLightRabi16Adapter,'execute',lost),\
             patch.object(ArcLightRabi16Adapter,'reconcile',return_value={'status':'unknown'}):
            self.c.action(w['id'],{'action':'run_step','nodeId':self.n['id'],
                'operationId':'unknown-step','expectedRevision':w['revision']})
            self.c.run_workflow(w['id'])
        current=self.c.get(w['id']);self.assertEqual(current['engineState'],'needs_reconciliation')
        self.assertEqual(len(self.fake.calls),1)
        self.assertEqual(self.c._node(current,self.n['id'])['attempt'],4)
        self.w=current
        with self.assertRaises(APIError):self.select(operationId='no-new-version')
        self.c._recover_interrupted()
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('select state from platform_leases').fetchone()[0],'quarantined')
            self.assertEqual(rt.db.execute('select state from platform_effects').fetchone()[0],'unknown')
        self.assertEqual(len(self.fake.calls),1)

    def test_pause_before_claim_requires_fresh_manual_permission_and_never_auto_executes(self):
        self._ready();w=self.select()
        with patch.object(ArcLightAdapter,'hardware_ownership',return_value={'verified':True,'owners':[]}):
            w=self.c.action(w['id'],{'action':'run_step','nodeId':self.n['id'],
                'operationId':'pause-queue','expectedRevision':w['revision']})
        w=self.c.action(w['id'],{'action':'pause','operationId':'pause-before-claim','expectedRevision':w['revision']})
        self.c._recover_interrupted();self.c.run_workflow(w['id'])
        self.assertEqual(self.c.get(w['id'])['status'],'paused')
        self.assertEqual(self.fake.calls,[])
        with self.c.store.connect() as rt:self.assertEqual(rt.db.execute('select count(*) from platform_effects').fetchone()[0],0)


class AttemptVersionHTTP(unittest.TestCase):
    def test_normal_version_detail_preview_endpoints_are_scoped_and_no_dispatch(self):
        from backend.server import Server
        fixture=AttemptVersionContracts();fixture.setUp()
        server=Server(('127.0.0.1',0),fixture.c)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            def request(path,body=None):
                req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
                    headers={'Content-Type':'application/json','X-Qcal-Token':server.session_token,'Origin':base})
                try:
                    with urllib.request.urlopen(req) as response:return response.status,json.load(response)
                except urllib.error.HTTPError as error:return error.code,json.load(error)
            path=f"/api/workflows/{fixture.w['id']}/nodes/{fixture.n['id']}"
            body={'operationId':'http-version','expectedRevision':fixture.w['revision'],
                'executionVersion':'rabi16-v1','reason':'TEST ONLY explicit version'}
            status,w=request(path+'/execution-version',body)
            self.assertEqual(status,200,w)
            self.assertEqual(request(path+'/execution-version',body),(200,w))
            status,d=request(path);self.assertEqual(status,200,d)
            self.assertEqual(d['parameterBounds']['acquisitions_per_point']['max'],16)
            status,p=request(path+'/pulse-preview',{'expectedRevision':w['revision'],'parameters':{}})
            self.assertEqual(status,200,p);self.assertEqual(p['status'],'ready')
            self.assertFalse(p['dispatched'])
            status,rejected=request(path+'/execution-version',{**body,'operationId':'evil','profile':'external.json'})
            self.assertGreaterEqual(status,400)
            with fixture.c.store.connect() as rt:
                self.assertEqual(rt.db.execute('select count(*) from platform_effects').fetchone()[0],0)
                self.assertEqual(rt.db.execute('select count(*) from platform_leases').fetchone()[0],0)
        finally:
            server.shutdown();server.server_close();worker.join(5);fixture.tearDown()


if __name__=='__main__':unittest.main()
