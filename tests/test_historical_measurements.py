"""Synthetic receipt contracts, temporary databases only; never hardware proof."""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import tempfile
from types import SimpleNamespace
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from backend.common import ROOT, APIError, dumps
from backend.historical_measurements import search, capability
from backend.prior_failure_context import load_audited_bindings, IMPLEMENTATION
from backend.server import Server, Handler
from tests import test_prior_failure_context as prior_fixture
from tests import test_controller as controller_fixture


class HistoricalMeasurementsTests(unittest.TestCase):
    def setUp(self):
        self.builder=prior_fixture.PriorFailureContextTests();self.builder.setUp();self.addCleanup(self.builder.tearDown)
        self.root=self.builder.root;self.path=self.root/'test.sqlite'
        self.bindings=load_audited_bindings(ROOT,IMPLEMENTATION)
        for binding in self.bindings.values():
            for item in binding['sourceFiles']:
                src=ROOT/item['path'];dest=self.root/item['path'];dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(src,dest)
        for name in ('adapters/arclight_profile_v3.json','adapters/arclight_analysis_v3/source_manifest.json'):
            dest=self.root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
        self.record=self.builder.fixture(independent=True)
        self.source=self.record['workflow'];self.attempt=self.record['attempt'];binding=self.bindings[self.source['adapterId']]
        self.source.update(revision=1,gates={'ro_power_shift':{'contrast_snr':{'min':5}}},gatesVersion='TEST_ONLY/1')
        self.attempt.update(state='verified',endedAt='2026-11-18T00:02:00Z')
        for index,effect in enumerate(self.record['effects']):
            result=effect['result'];result['analysisQuality'].update(accepted=True,reasons=[])
            result['acquiredAt']=f'2026-11-18T00:0{index}:00Z'
            result['candidateParameters']={'readout_hz':7e9+index}
            result['quality']={'status':'passed','ruleVersion':'TEST_ONLY/1','criteria':self.source['gates']['ro_power_shift']}
            opdir=self.root/'data/runs/arclight_operations'/hashlib.sha256(effect['operation_id'].encode()).hexdigest()
            for artifact in result['artifacts']:
                path=Path(artifact['sourcePath'])
                if artifact['name']=='plan.json':
                    plan=json.loads(path.read_text());plan.update(instrument=binding['profile']['instrument'],hardwareLocks=binding['profile']['hardwareLocks'])
                    path.write_text(dumps(plan),encoding='utf8')
                if artifact['name']=='platform_analysis.json':
                    path.write_text(dumps({'analysis':result['analysisQuality'],'metrics':result['metrics'],'series':[],
                        'candidateParameters':result['candidateParameters']}),encoding='utf8')
                artifact['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();artifact['id']='real-'+artifact['sha256'][:24]
            axes=opdir/'results/offline-fixture/axes.json';axes.write_text(dumps({'TEST_ONLY':True}),encoding='utf8')
            sha=hashlib.sha256(axes.read_bytes()).hexdigest()
            result['artifacts'].append({'id':'real-'+sha[:24],'sha256':sha,'name':'axes.json','kind':'json','sourcePath':str(axes)})
            request=effect['request'];rh=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            (opdir/'adapter_intent.json').write_text(dumps({'operationId':effect['operation_id'],'request':request,'requestHash':rh,
                'profileHash':binding['executionProfileHashes'][1]}),encoding='utf8')
            (opdir/'adapter_receipt.json').write_text(dumps({'status':'completed','operationId':effect['operation_id'],
                'requestHash':rh,'result':result}),encoding='utf8')
            (opdir/'completion.json').write_text(dumps({'state':'completed','shutdown_ok':True,'acquiredAt':result['acquiredAt'],
                'files':[a['sourcePath'] for a in result['artifacts']],
                'analysis':{'analysis':result['analysisQuality'],'metrics':result['metrics'],'series':result['series'],
                    'candidateParameters':result['candidateParameters']}}),encoding='utf8')
        final=deepcopy(self.record['effects'][1]['result']);final['selectionResult']=deepcopy(self.record['effects'][0]['result'])
        final['verificationCandidateParameters']=deepcopy(final['candidateParameters']);final['candidateParameters']=deepcopy(final['selectionResult']['candidateParameters'])
        self.attempt['result']=final;self.source['nodes'][0].update(engineState='verified',latestAttemptId=self.attempt['id'],result=deepcopy(final))
        self.current=deepcopy(self.builder.current);self.current.update(revision=7,engineState='deciding',currentNodeId=self.current['nodes'][0]['id'])
        self.current['nodes'][0]['result']={'parameters':{'readout_hz':7.1e9,'readout_amp':.76}}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''CREATE TABLE platform_workflows(id TEXT PRIMARY KEY,revision INTEGER,payload TEXT);
              CREATE TABLE platform_attempts(id TEXT PRIMARY KEY,workflow_id TEXT,node_id TEXT,payload TEXT);
              CREATE TABLE platform_effects(operation_id TEXT,workflow_id TEXT,attempt_id TEXT,state TEXT,request TEXT,result TEXT);
              CREATE TABLE platform_artifacts(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,payload TEXT);''')
        self.save()

    def save(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            for wf in (self.source,self.current):db.execute('INSERT OR REPLACE INTO platform_workflows VALUES(?,?,?)',(wf['id'],wf['revision'],dumps(wf)))
            a=self.attempt;db.execute('INSERT OR REPLACE INTO platform_attempts VALUES(?,?,?,?)',(a['id'],self.source['id'],a['nodeId'],dumps(a)))
            db.execute('DELETE FROM platform_effects')
            for effect in self.record['effects']:
                db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',tuple(dumps(effect[k]) if k in ('request','result') else effect[k]
                    for k in ('operation_id','workflow_id','attempt_id','state','request','result')))
                for a in effect['result']['artifacts']:
                    db.execute('INSERT OR REPLACE INTO platform_artifacts VALUES(?,?,?,?)',(a['id'],a['sourcePath'],a['sha256'],dumps(a)))

    def query(self,**kw):return search(self.root,self.path,self.current['id'],self.current['currentNodeId'],7,**kw)

    def test_complete_pair_is_reference_not_current_evidence(self):
        before=self.path.read_bytes();out=self.query();self.assertEqual(len(out['cases']),1,out)
        case=out['cases'][0];self.assertEqual(case['operations'][0]['measuredCandidates']['readout_hz'],7e9)
        self.assertEqual(case['operations'][1]['measuredCandidates']['readout_hz'],7e9+1)
        self.assertEqual(case['submittedCandidates']['readout_hz'],7e9)
        self.assertFalse(case['currentCalibration']);self.assertFalse(case['executionAuthorized'])
        self.assertFalse(case['physicalIdentity']['physicalDesignIdentityConfirmed'])
        self.assertEqual(case['physicalIdentity']['classification'],'same_frozen_route_reference')
        self.assertIn('readout_amp',case['parameterDifferences']);self.assertEqual(before,self.path.read_bytes())

    def test_missing_failed_stale_and_wrong_profile_rejected(self):
        for change in ('stale','latest','failed','profile','method','special'):
            with self.subTest(change=change):
                source,attempt=deepcopy(self.source),deepcopy(self.attempt)
                if change=='stale':self.attempt['result']['stale']=True;self.source['nodes'][0]['result']['stale']=True
                elif change=='latest':self.source['nodes'][0]['latestAttemptId']='different'
                elif change=='failed':self.attempt['state']='failed'
                elif change=='profile':self.source['adapterProfileHash']='0'*64
                elif change=='method':self.attempt['procedureId']='sq_rb'
                else:self.attempt['executionBinding']={'unreviewed':True}
                self.save();self.assertEqual(self.query()['cases'],[])
                self.source,self.attempt=source,attempt
        self.save()

    def test_archive_tamper_rejected(self):
        effect=self.record['effects'][0];opdir=self.root/'data/runs/arclight_operations'/hashlib.sha256(effect['operation_id'].encode()).hexdigest()
        for name in ('adapter_intent.json','adapter_receipt.json','completion.json','results/offline-fixture/raw_scan.npz','plan.json'):
            path=opdir/name;old=path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(b'{}');self.assertEqual(self.query()['cases'],[])
            path.write_bytes(old)

    def test_current_scope_and_limits_fail_closed(self):
        for key,value in (('engineState','paused'),('currentNodeId','other'),('cancelRequested',True),('revision',8)):
            original=deepcopy(self.current);self.current[key]=value;self.save()
            with self.assertRaises(APIError):self.query()
            self.current=original
        self.save()
        for limit in (0,4,True):
            with self.assertRaises(APIError):self.query(limit=limit)
        self.assertIsNone(capability({'mode':'replay'},{'procedureId':'ro_power_shift'}))

    def test_independent_raw_reuse_is_rejected_after_full_receipt_checks(self):
        first,last=self.record['effects'];a=next(a for a in first['result']['artifacts'] if a['kind']=='raw')
        raw=next(a for a in last['result']['artifacts'] if a['kind']=='raw');Path(raw['sourcePath']).write_bytes(Path(a['sourcePath']).read_bytes())
        raw.update(id=a['id'],sha256=a['sha256']);last['result']['analysisQuality']['raw_sha256']=a['sha256']
        document=next(a for a in last['result']['artifacts'] if a['name']=='platform_analysis.json');path=Path(document['sourcePath'])
        analysis=json.loads(path.read_text());analysis['analysis']=last['result']['analysisQuality'];path.write_text(dumps(analysis),encoding='utf8')
        document['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();document['id']='real-'+document['sha256'][:24]
        opdir=path.parent.parent.parent
        receipt=json.loads((opdir/'adapter_receipt.json').read_text());receipt['result']=last['result']
        (opdir/'adapter_receipt.json').write_text(dumps(receipt),encoding='utf8')
        completion=json.loads((opdir/'completion.json').read_text());completion['analysis']['analysis']=last['result']['analysisQuality']
        (opdir/'completion.json').write_text(dumps(completion),encoding='utf8')
        final=deepcopy(last['result']);final['selectionResult']=deepcopy(first['result'])
        final['verificationCandidateParameters']=deepcopy(final['candidateParameters']);final['candidateParameters']=deepcopy(first['result']['candidateParameters'])
        self.attempt['result']=final;self.source['nodes'][0]['result']=deepcopy(final);self.save()
        out=self.query();self.assertEqual(out['cases'],[]);self.assertEqual(out['excluded'][0]['reasonCode'],'independent_raw_reused')

    def test_numeric_quality_is_rechecked_not_accepted_boolean(self):
        self.source['gates']['ro_power_shift']['contrast_snr']['min']=20
        self.attempt['result']['quality']['criteria']=deepcopy(self.source['gates']['ro_power_shift'])
        self.source['nodes'][0]['result']=deepcopy(self.attempt['result']);self.save()
        out=self.query();self.assertEqual(out['cases'],[]);self.assertEqual(out['excluded'][0]['reasonCode'],'native_or_numeric_quality_failed')

    def test_bounded_read_budget_and_nonobject_metadata(self):
        with patch('backend.historical_measurements.MAX_HASH_BYTES',1):
            self.assertEqual(self.query()['excluded'][0]['reasonCode'],'archive_read_budget')
        effect=self.record['effects'][0];opdir=self.root/'data/runs/arclight_operations'/hashlib.sha256(effect['operation_id'].encode()).hexdigest()
        (opdir/'adapter_receipt.json').write_text('[]',encoding='utf8')
        self.assertEqual(self.query()['excluded'][0]['reasonCode'],'invalid_archive_document')

    def test_normal_get_is_read_only_and_revision_bound(self):
        controller=SimpleNamespace(root=self.root,store=SimpleNamespace(path=self.path))
        server=Server(('127.0.0.1',0),controller);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            before=self.path.read_bytes();url=f'http://127.0.0.1:{server.server_port}/api/workflows/current-run/nodes/{self.current["currentNodeId"]}/historical-evidence'
            with urlopen(url+'?revision=7',timeout=5) as response:out=json.load(response)
            self.assertEqual(len(out['cases']),1,out)
            with self.assertRaises(HTTPError) as error:urlopen(url+'?revision=6',timeout=5)
            self.assertEqual(error.exception.code,409);self.assertEqual(before,self.path.read_bytes())
        finally:server.shutdown();server.server_close();thread.join(2)


class ControllerHistoryContracts(unittest.TestCase):
    def test_actual_decide_capability_matches_persisted_decision_revision_and_never_runs_adapter(self):
        with tempfile.TemporaryDirectory(prefix='qcal-history-controller-') as folder:
            c,adapter=controller_fixture.make_fixture(Path(folder))
            try:
                wf=c.create({'name':'TEST_ONLY historical capability','resourceIds':['Q11'],'mode':'real','policy':'autopilot'})
                node_id=wf['nodes'][0]['id']
                with c.store.transaction() as rt:
                    current=c.get(wf['id'],rt);current.update(status='attention',engineState='waiting_model',currentNodeId=node_id)
                    current['nodes'][0].update(engineState='quality_attention',result={'artifacts':[],'quality':{'status':'failed'}})
                    c.store.save(rt,current,'TEST_ONLY_decision')
                calls=[]
                def brain(route,payload):
                    bound=payload['historicalEvidenceSearch'];fresh=c.get(wf['id']);calls.append(bound)
                    self.assertEqual(bound,capability(fresh,fresh['nodes'][0]));self.assertEqual(fresh['engineState'],'deciding')
                    # Fake adapter is intentionally not an audited receipt identity.
                    self.assertEqual(search(c.root,c.store.path,wf['id'],node_id,bound['revision'])['status'],'unavailable')
                    return {'status':'ok','decision':{'action':'wait','reason':'TEST ONLY','evidenceIds':[]}}
                c._brain=brain;c._decide(wf['id'],node_id)
                self.assertEqual(len(calls),1);self.assertEqual(adapter.calls,[])
            finally:c.close()
