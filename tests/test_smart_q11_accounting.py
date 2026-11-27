"""Q11 accounting through the local HTTP API and a strictly offline native planner."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from adapters.arclight import ArcLightAdapter
from backend import shot_accounting, workflow_budget
from backend.acquisition_budget import selection_lookahead, verify_admission
from backend.backtrack_admission import admit_backtrack
from backend.common import APIError, ROOT
from backend.controller import Controller
from backend.server import Server
import test_http as http_fixtures


class AccountingHTTP(unittest.TestCase):
    request, session = http_fixtures.HTTPContracts.request, http_fixtures.HTTPContracts.session

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='r32-account-offline-')
        self.root = Path(self.temp.name)
        imports = self.root / 'data/imports'
        imports.mkdir(parents=True)
        (imports/'history.json').write_text(json.dumps({'version':1,'resources':[{'id':'Q11','label':'Q11'}],'records':[]}),encoding='utf8')
        self.adapter = ArcLightAdapter(ROOT,ROOT/'adapters/arclight_profile_v3.json')
        self.controller = Controller(self.root, adapter=self.adapter,start_worker=False)
        self.server = Server(('127.0.0.1',0),self.controller)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True)
        self.thread.start()
        self.headers = {'X-Qcal-Token':self.session()}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.controller.close()
        self.temp.cleanup()

    def create(self,**kwargs):
        return self.request('POST','/api/workflows',{'name':'Q11 offline','mode':'real','policy':'autopilot',
            'resourceIds':['Q11'],'operationId':'r32-offline-create',**kwargs},self.headers)

    def test_create_query_replay_and_finite_default(self):
        status,_,w=self.create(budget={'mode':'accounting_only'})
        self.assertEqual((status,w.get('status')),(201,'draft'),w)
        self.assertEqual(w['budget']['mode'],'accounting_only')
        self.assertTrue(all(w[k] is None for k in workflow_budget.CEILINGS))
        self.assertEqual(w['shotAccounting']['confirmedShots'],0)
        self.assertEqual(self.create(budget={'mode':'accounting_only'})[2]['id'],w['id'])
        self.assertEqual(self.request('GET','/api/workflows/'+w['id'])[2]['budget'],w['budget'])
        with self.controller.store.connect() as rt:
            specs=[v for k,v in rt.steps.items() if k.startswith(w['id']+'/')]
            self.assertTrue(specs and all(v.budget.accounting_only and v.budget.max_attempts is None for v in specs))
            with self.assertRaisesRegex(ValueError, '平台控制器'):
                rt.run_step(specs[0].id, None, 'forbidden-direct-run')
            self.assertEqual(rt.db.execute('SELECT count(1) FROM platform_effects').fetchone()[0],0)
        status,_,legacy=self.create(operationId='ordinary')
        self.assertEqual(status,201)
        self.assertGreater(legacy['maxTotalShots'],0)
        self.assertNotIn('shotAccounting',legacy)

    def test_scope_and_parent_field_rejected(self):
        for i,extra in enumerate([{'mode':'replay'},{'policy':'strict'},{'policy':'threshold'},
                {'accountingParent':'forged'},{'_budget_parent':'forged'},{'budget':{'mode':'accounting_only','maxTotalShots':1}}]):
            with self.subTest(extra=extra):
                status,_,_=self.create(**{'operationId':'bad-'+str(i),'budget':{'mode':'accounting_only'},**extra})
                self.assertIn(status,[400,409])
        with self.controller.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT COUNT(1) FROM platform_workflows').fetchone()[0],0)

    def test_explicit_conversion_is_idempotent_preserves_charges_and_stop(self):
        _,_,w=self.create()
        with self.controller.store.transaction() as rt:
            prior=self.controller.get(w['id'],rt)
            prior.update(reservedShots=4000,iterations=41)
            self.controller.store.save(rt,prior,'budget_reserved',{'operationId':'old-pending','shots':4000})
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',
                          ('old-pending',w['id'],'archive','not_started','{}','{}'))
        path='/api/workflows/'+w['id']
        change={'operationId':'set-account','expectedRevision':prior['revision'],'reason':'operator explicitly selected',
                'budget':{'mode':'accounting_only'}}
        status,_,changed=self.request('PATCH',path+'/budget',change,self.headers)
        self.assertEqual(status,200,changed)
        self.assertEqual((changed['reservedShots'],changed['iterations'],changed['status']),(4000,41,'draft'))
        self.assertEqual(changed['shotAccounting']['confirmedShots'],0)
        self.assertEqual(self.request('PATCH',path+'/budget',change,self.headers)[2]['revision'],changed['revision'])
        with self.controller.store.transaction() as rt:
            with self.assertRaises(APIError):shot_accounting.reserve(rt.db,w['id'],'old-pending',4000)
            self.assertTrue(all(s.budget.accounting_only for k,s in rt.steps.items() if k.startswith(w['id']+'/')))
        status,_,_=self.request('PATCH',path+'/policy',{'policy':'strict','operationId':'not-this-scope',
                              'expectedRevision':changed['revision']},self.headers)
        self.assertEqual(status,409)
        status,_,stopped=self.request('POST',path+'/actions',{'action':'abort','operationId':'abort-now',
            'expectedRevision':changed['revision']},self.headers)
        self.assertEqual((status,stopped['status']),(200,'aborted'))
        restarted=Controller(self.root,adapter=self.adapter,start_worker=False)
        try:
            self.assertEqual((restarted.get(w['id'])['status'],restarted.get(w['id'])['reservedShots']),('aborted',4000))
            with restarted.store.connect() as rt:
                self.assertEqual(rt.db.execute('SELECT COUNT(1) FROM platform_shot_usage').fetchone()[0],1)
        finally:
            restarted.close()

    def test_active_parent_public_name_cannot_replace_server_binding(self):
        _,_,w=self.create(budget={'mode':'accounting_only'})
        with self.controller.store.transaction() as rt:
            current=self.controller.get(w['id'],rt)
            current.update(status='running',smartRun={'schema':'q11-full-same-gate/1'})
            self.controller.store.save(rt,current,'test_inert_smart_parent')
            data={'mode':'real','resourceIds':['Q11'],'experiment':'single','policy':'strict','executionProfile':'science-shape-repeat5'}
            observed={'sourceWorkflowId':w['id'],'resourceId':'Q11','sourceAdapterProfileHash':w['adapterProfileHash']}
            self.assertEqual(workflow_budget.inherit_smart_budget(self.controller,rt,w['id'],data,{'observedResourceBinding':observed})['parentWorkflowId'],w['id'])
            for bad in [None,{'observedResourceBinding':{**observed,'sourceWorkflowId':'elsewhere'}}]:
                with self.assertRaises(APIError):
                    workflow_budget.inherit_smart_budget(self.controller,rt,w['id'],data,bad)


class AccountingSemantics(unittest.TestCase):
    def test_exceed_old_limits_with_per_operation_guards(self):
        wf={'budget':{'mode':'accounting_only'},**dict.fromkeys(workflow_budget.CEILINGS),
            'iterations':220,'reservedShots':150_000_000}
        self.assertFalse(workflow_budget.exhausted(wf,'maxAttemptsPerNode',11))
        self.assertIsNone(admit_backtrack(wf,{'id':'target','attempt':11})['remainingWorkflowAttempts'])
        look=selection_lookahead(wf,{'estimated_total_shots':300},{'estimated_total_shots':400},basis='native')
        self.assertIsNone(look['remainingShotsAtCheck'])
        self.assertEqual(verify_admission(wf,{'estimated_total_shots':400},look)['actualVerificationShots'],400)
        with self.assertRaises(APIError):verify_admission(wf,{'estimated_total_shots':401},look)
        with self.assertRaises(APIError):selection_lookahead(dict(wf,budget={}),{'estimated_total_shots':1},{'estimated_total_shots':1},basis='x')
        with self.assertRaises(APIError):selection_lookahead(wf,{'estimated_total_shots':False},{'estimated_total_shots':1},basis='x')

    def test_completed_partial_and_missing_receipt_do_not_turn_reservations_into_measurements(self):
        with tempfile.TemporaryDirectory(prefix='r32-count-') as tmp:
            root=Path(tmp);op='inert/measure';folder=root/'data/runs/arclight_operations'/hashlib.sha256(op.encode()).hexdigest()
            folder.mkdir(parents=True)
            def save(name,value):
                path=folder/name;path.write_text(json.dumps(value),encoding='utf8')
                return {'name':name,'sourcePath':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            plan=save('plan.json',{'operationId':op,'estimated_total_shots':200,'expectedJobRows':[20],
                      'jobs':[{'name':'s','spec':{'meta':{'shots':5}},'acquisitions_per_point':2}]})
            protocol={'job':'s','points_expected':20,'shots_per_acquisition':5,'acquisitions_per_point':2,'effective_shots_per_point':10,'shutdown_ok':True}
            partial=save('status.json',{**protocol,'points_completed':9})
            self.assertEqual(shot_accounting.confirmed_count(root,op,{'partialArtifacts':[plan,partial]},'cancelled',200),(90,'lower_bound'))
            done=save('status.json',{**protocol,'points_completed':20})
            self.assertEqual(shot_accounting.confirmed_count(root,op,{'artifacts':[plan,done]},'completed',200),(200,'exact'))
            self.assertEqual(shot_accounting.confirmed_count(root,op,{'artifacts':[plan,partial]},'completed',200),(None,'unconfirmed'))
            self.assertEqual(shot_accounting.confirmed_count(root,op,{},'unknown',200),(None,'unconfirmed'))

    def test_science_child_counts_blocks_and_parent_totals_only_from_registered_child(self):
        import sqlite3
        from backend.result_archive import ensure_schema
        with tempfile.TemporaryDirectory(prefix='r32-science-ledger-') as tmp:
            root=Path(tmp);op='science/verify'
            folder=root/'data/runs/arclight_operations'/hashlib.sha256(op.encode()).hexdigest()
            folder.mkdir(parents=True)
            def save(name,value):
                path=folder/name;raw=json.dumps(value).encode();path.write_bytes(raw)
                return {'name':name,'sourcePath':str(path),'sha256':hashlib.sha256(raw).hexdigest()}
            plan=save('plan.json',{'operationId':op,'rows':[{},{}],'estimated_total_shots':600,'totalRows':2,
                                   'parameters':{'shots':100,'acquisitions_per_point':3}})
            status=save('status.json',{'state':'cancelled','operationId':op,'pointsCompleted':1,'shutdown_ok':True})
            raw={'name':'partial_raw_scan.npz','kind':'raw'}
            result={'partialArtifacts':[plan,status,raw]}
            self.assertEqual(shot_accounting.confirmed_count(root,op,result,'cancelled',600),(300,'lower_bound'))
            db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.row_factory=sqlite3.Row
            ensure_schema(db)
            db.execute('CREATE TABLE platform_workflows(id TEXT PRIMARY KEY,payload TEXT)')
            db.execute('CREATE TABLE platform_effects(operation_id TEXT PRIMARY KEY,state TEXT,result TEXT)')
            parent={'id':'p','budget':{'mode':'accounting_only'},'reservedShots':0,'iterations':0}
            for key,parent_id in [('c','p'),('other','foreign')]:
                db.execute('INSERT INTO platform_accounting_workflows VALUES(?,?)',(key,parent_id))
                db.execute('INSERT INTO platform_workflows VALUES(?,?)',(key,json.dumps({'id':key,'reservedShots':600,'iterations':1})))
            shot_accounting.reserve(db,'c',op,600)
            db.execute('INSERT INTO platform_effects VALUES(?,?,?)',(op,'cancelled',json.dumps(result)))
            shot_accounting.capture(db,op,root)
            self.assertEqual(shot_accounting.view(db,parent)['shotAccounting'],{'reservedShots':600,'attempts':1,'scienceChildren':1,
                           'confirmedShots':300,'operations':1,'unresolvedOperations':1,'unattributedReservedShots':0})
