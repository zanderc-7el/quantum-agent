"""Actual fixed-profile -> observed -> four science drafts, no executor.

All acquisition rows here are explicitly synthetic fixture metadata. Source
authentication is deliberately NOT bypassed when attempting run_step.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
# Load their frozen planner imports before the explicit test-only client stub;
# otherwise Python's first-import cache would retain a mock in another module.
from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter
from adapters.science_v3_irb.adapter import ScienceIRBAdapter
from backend.common import APIError, ROOT, digest, now
from backend.controller import Controller
from backend import smart_science as s


class SmartScienceCreation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='smart-science-offline-create-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        shutil.copytree(ROOT/'adapters',self.root/'adapters',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        imports=self.root/'data/imports';imports.mkdir(parents=True)
        (imports/'history.json').write_text(json.dumps({'version':1,'resources':[{'id':'Q11','label':'Q11'}],'records':[]}),'utf8')
        self.c=Controller(self.root,adapter=ArcLightAdapter(self.root,self.root/'adapters/arclight_profile_v3.json'),start_worker=False)
        self.addCleanup(self.c.close)
        self.parent=self.c.create({'name':'TEST_ONLY synthetic main receipts','mode':'real','policy':'autopilot',
            'coverage':'full','resourceIds':['Q11'],'budget':{'mode':'accounting_only'}})
        with self.c.store.transaction() as rt:
            p=self.c.get(self.parent['id'],rt)
            for n in p['nodes']:
                path=self.root/'data'/(n['procedureId']+'.fixture');path.write_text('TEST_ONLY synthetic '+n['procedureId'],'utf8')
                artifact={'id':'fixture-'+n['id'],'name':'raw_scan.npz','kind':'raw','localPath':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
                artifact=self.c._artifact(rt,artifact)
                result={'dataKind':'real','acquiredAt':now(),'parameters':deepcopy(n['parameters']),
                    'quality':{'status':'passed'},'artifacts':[artifact],'candidateParameters':{},'fixtureOnly':True,
                    'selectionResult':{'candidateParameters':{'drive_length_s':342.37e-9}}}
                result['parameters'].update(readout_hz=7e9,readout_amp=.76)
                attempt={'id':'fixture-'+n['id'],'nodeId':n['id'],'number':1,'state':'verified',
                    'operationId':'fixture-'+n['id']+'/measure','verificationOperationId':'fixture-'+n['id']+'/verify',
                    'parameters':result['parameters'],'result':result,'mode':'real','procedureId':n['procedureId']}
                self.c.store.attempt(rt,p['id'],attempt)
                n.update(engineState='verified',latestAttemptId=attempt['id'],attempt=1,result=result)
            p.update(status='running',engineState='science_running',hardwareConfirmed=True,
                smartRun={'schema':s.PARENT_SCHEMA,'adapterProfileHash':p['adapterProfileHash'],'main':'verified','gate':'pending'})
            p['smartRun']['mainContextSha256']=s.main_context(p)
            self.c.store.save(rt,p,'test_only_main_fixture');self.parent=p

    def test_all_four_normal_drafts_use_versioned_schema_and_same_parent_budget(self):
        records={};ids=[]
        def source_archive(controller,wf,node):return {'id':'fixture-'+node['procedureId']}
        def source_load(service,rt,record_id,**kwargs):return deepcopy(records[record_id])
        def inert_planner(root,python,p):return {'sha256':digest(p),'budget':{'rows':496},'rows':[]}
        with (patch('backend.smart_science._archive',side_effect=source_archive),
              patch('backend.science_service.ScienceService._load',source_load),
              patch('backend.smart_science.native_initial_planner',side_effect=inert_planner),
              patch('subprocess.Popen',side_effect=AssertionError('No subprocess or instrument'))):
            for index,method in enumerate(s.METHODS):
                p=self.c.get(self.parent['id']);wid=s._create_child(self.c,p,index);ids.append(wid)
                self.assertEqual(s._create_child(self.c,self.c.get(p['id']),index),wid)
                child=self.c.get(wid);node=child['nodes'][0]
                self.assertEqual((child['executionProfile'],child['policy'],child['status']),(s.PROFILE,'strict','draft'))
                self.assertEqual(child['budget']['parent']['parentWorkflowId'],p['id'])
                self.assertEqual(child['observedResourceBinding']['sourceWorkflowId'],p['id'])
                self.assertEqual(node['parameterOverrides']['pulse_length_s'],342.25e-9)
                s.assert_authorized(self.c,child)
                with self.c.store.connect() as rt:
                    current_params,_=self.c.for_workflow(wid,rt)._parameters(rt,child,node)
                    if index in (0,2):
                        candidate_key=s.SELECTED_OUTPUT[method]
                        current_params[candidate_key]=.7 if index==0 else -.7e-9
                        current_params['verify_'+candidate_key]=current_params[candidate_key]
                    s.validate_target(self.c,rt,child,current_params)
                    with self.assertRaises(APIError):s.validate_target(self.c,rt,child,{**current_params,'pulse_length_s':40e-9})
                if index>=2:
                    self.assertEqual(node['parameterOverrides']['drag_mode'],'empirical_beta')
                    self.assertNotIn('anharmonicity',str(node['scienceReferences']))
                    self.assertNotIn('drive_amp',node['parameterOverrides'])
                record_id='fixture-science-'+str(index)
                native_parameters=deepcopy(node['parameters'])
                # The new beta native contract removes these legacy metadata.
                if index==2:
                    native_parameters.pop('drive_amp',None);native_parameters.pop('drive_length_s',None)
                records[record_id]={'id':record_id,'result':{'parameters':native_parameters,
                    'targetPulseIdentity':{'identity':{'dragMode':'empirical_beta','betaSeconds':-.7e-9}}}}
                with self.c.store.transaction() as rt:
                    c=self.c.get(wid,rt);c['nodes'][0]['result']={'candidateParameters':{'shape_pi_amp':.7}}
                    self.c.store.save(rt,c,'test_only_prior_output')
                    p=self.c.get(p['id'],rt);p['smartRun'].setdefault('scienceRecords',{})[method]=record_id
                    self.c.store.save(rt,p,'test_only_record_reference')
        self.assertEqual(set(self.c.get(self.parent['id'])['smartRun']['children']),set(s.METHODS))
        self.assertEqual(self.c.get(self.parent['id'])['smartRun']['childrenOrder'],list(s.METHODS))
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],0)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)
        # Synthetic draft refs must never authorize a real attempt or effect.
        child=self.c.get(ids[0])
        with self.assertRaises(APIError):
            self.c.action(child['id'],{'action':'run_step','nodeId':child['currentNodeId'],
                'operationId':'fixture-must-not-dispatch','expectedRevision':child['revision']})
        with self.c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],0)


if __name__=='__main__':unittest.main()
