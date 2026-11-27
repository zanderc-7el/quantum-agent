"""Offline provenance and renderer contracts for a linked one-start Q11 report."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.smart_q11_report import fidelity_measurement
from backend.smart_q11_report_jobs import enqueue, tick
from backend.common import ROOT, file_hash
from backend.human_reports import html_report
from scripts.export_report_snapshot import snapshot
from tests.test_controller import make_fixture


def irb_capture(v):
    return {'dataKind':'real','quality':{'status':'passed'},'metrics':{'gate_fidelity':v},
            'provenance':{'freshAcquisition':True,'scienceIRB':{'irb':{'reportable':True,'target_gate':'X/2','reasons':[],
            'F_X_half_conditional':v,'bootstrap':{'confidence':.95,'systematic_model_error_included':False,
            'intervals_unclipped':{'F_X_half_conditional':[.96,.99]}}}}}}


def child_report():
    result = {**irb_capture(.98),'selectionResult':irb_capture(.97)}
    return {'workflow':{'workflowComplete':True,'id':'child'},'nodes':[{'node':{'latestAttemptId':'test','result':result},
            'attempts':[{'id':'test','state':'verified','operationId':'sel','verificationOperationId':'ver','result':result}]}]}


class SmartGateReport(unittest.TestCase):
    def test_html_keeps_early_child_and_its_technical_source_on_same_page(self):
        from tests.test_human_reports import fixture
        report = fixture()
        parent = report['workflow']
        parent['smartRun'] = {'schema':'q11-full-same-gate/1','gate':'pending'}
        child = fixture()
        child['workflow']['id'] = 'test-linked-draft'
        child['workflow']['target'] = parent['target'] = 'Q11'
        method = 'sq_shape_rabi_amplitude_repeat5'
        node = child['nodes'][0]['node']
        node.update(id='Q11:'+method,procedureId=method,engineState='pending',attempt=0)
        node.pop('latestAttemptId',None);node.pop('result',None)
        child['nodes'][0].update(attempts=[],decisions=[])
        report['smartGateReports'] = [{'procedureId':method,'current':True,'report':child}]
        rendered = html_report(SimpleNamespace(root=ROOT),report).decode()
        self.assertIn('五脉冲整形 Rabi',rendered)
        self.assertIn('sq_shape_rabi_amplitude_repeat5',rendered)
        self.assertNotIn('95% 条件置信区间',rendered)

    def test_two_distinct_estimates_and_exclusions(self):
        doc = child_report()
        self.assertEqual([m['value'] for m in fidelity_measurement(doc,{'accepted':True})],[.97,.98])
        self.assertIsNone(fidelity_measurement(doc,{'accepted':False}))
        for key,value in [('reportable',False),('target_gate','Clifford'),('F_X_half_conditional',1.2),('reasons',['drift'])]:
            other = deepcopy(doc)
            other['nodes'][0]['node']['result']['provenance']['scienceIRB']['irb'][key] = value
            self.assertIsNone(fidelity_measurement(other,{'accepted':True}))
        doc['nodes'][0]['attempts'][0]['verificationOperationId'] = 'sel'
        self.assertIsNone(fidelity_measurement(doc,{'accepted':True}))

    def test_snapshot_requires_both_directions_and_same_physical_source(self):
        with TemporaryDirectory() as target:
            root, path = Path(target), Path(target)/'offline.sqlite'
            method = 'sq_drag_irb'
            parent = {'id':'parent','nodes':[],'resourceIds':['Q11'],'adapterProfileHash':'fixed',
                      'smartRun':{'schema':'q11-full-same-gate/1','children':{method:'child'}}}
            child = {'id':'child','mode':'real','resourceIds':['Q11'],'nodes':[{'id':'gate','procedureId':method}],
                     'smartParent':{'workflowId':'parent','procedureId':method},'budget':{'parent':{'parentWorkflowId':'parent'}},
                     'observedResourceBinding':{'sourceWorkflowId':'parent','resourceId':'Q11','sourceAdapterProfileHash':'fixed'}}
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript('CREATE TABLE platform_workflows(id,payload); CREATE TABLE platform_attempts(workflow_id,node_id,number,payload); CREATE TABLE platform_decisions(workflow_id,node_id,payload);')
                db.executemany('insert into platform_workflows values(?,?)',[(w['id'],json.dumps(w)) for w in (child,parent)])
            self.assertEqual(snapshot(path,'parent',root)['smartGateReports'][0]['report']['workflow']['id'],'child')
            child['observedResourceBinding']['sourceWorkflowId'] = 'other'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('update platform_workflows set payload=? where id=?',(json.dumps(child),'child'))
            with self.assertRaisesRegex(ValueError,'physical configuration'):
                snapshot(path,'parent',root)

    def test_restart_reuses_report_operation_pause_never_publishes_then_resume(self):
        with TemporaryDirectory() as target:
            root=Path(target)
            controller,_ = make_fixture(root)
            self.addCleanup(controller.close)
            wf=controller.create({'name':'offline report','resourceIds':['Q11'],'mode':'real','policy':'autopilot'})
            smart={'schema':'q11-full-same-gate/1','gate':'verified','main':'verified','children':{'sq_drag_irb':'gate'},
                   'gateEvidence':{'childWorkflowId':'gate','attemptId':'fixture'},'report':'pending'}
            with controller.store.transaction() as rt:
                wf.update(smartRun=smart,status='running',engineState='report_pending')
                enqueue(rt,wf)
                enqueue(rt,wf)
                controller.store.save(rt,wf,'offline')
            calls=[]
            def create(wid,payload):
                calls.append(payload)
                return {'id':'job','operationId':payload['operationId'],'workflowId':wid,'status':'processing'}
            def get(jobid):
                return {'id':jobid,'operationId':calls[0]['operationId'],'workflowId':wf['id'],'status':'completed',
                        'revision':wf['revision'],'sha256':'digest','archiveId':'archive','bytes':1,'downloadUrl':'/file',
                        'snapshotSha256':file_hash(job_dir/'report.json')}
            job_dir=root/'export'/'job'; (job_dir/'build').mkdir(parents=True)
            (job_dir/'report.json').write_text('{}','utf-8')
            (job_dir/'reader.json').write_text(json.dumps({'workflowId':wf['id'],'revision':wf['revision'],
                'smartGate':{'verified':True,'fidelity':[{'sourceWorkflowId':'gate','attemptId':'fixture'}]*2}}),'utf-8')
            (job_dir/'build'/'validation.json').write_text(json.dumps({'passed':True,'sha256':'digest'}),'utf-8')
            exports=SimpleNamespace(create=create,get=get,file=lambda _:None,output_root=job_dir.parent)
            with patch.object(type(controller),'report_exports',property(lambda _:exports)):
                tick(controller)
                with controller.store.transaction() as rt:
                    w=controller.get(wf['id'],rt);w['status']='paused';controller.store.save(rt,w,'offline_pause')
                    rt.db.execute('update platform_smart_reports set due=0')
                tick(controller)
                self.assertEqual(controller.get(wf['id'])['status'],'paused')
                with controller.store.transaction() as rt:
                    w=controller.get(wf['id'],rt);w['status']='running';controller.store.save(rt,w,'offline_resume')
                    rt.db.execute('update platform_smart_reports set due=0')
                tick(controller);tick(controller)
            self.assertEqual(len(calls),1)
            saved=controller.get(wf['id'])
            self.assertEqual((saved['status'],saved['smartRun']['reportEvidence']['id']),('completed','job'))
