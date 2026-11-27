"""Real half-plan lifecycle, native synthetic numeric data, injected I/O only.

Temporary database, no native worker, no instrument. Source authentication is
explicitly injected and these records cannot serve as physical acceptance.
"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.common import APIError,digest,now
from tests.test_controller import make_fixture
from tests.test_science_empirical_irb import half_fixture


class EmpiricalHalfLifecycle(unittest.TestCase):
    def test_frozen_signed_seconds_two_effects_and_explicit_acceptance(self):
        from adapters.science_v3_irb.adapter import ScienceIRBAdapter,build_irb_profile
        from adapters.science_v3_irb.half_scan import target_identity
        root=Path(__file__).resolve().parents[1]
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        c,_=make_fixture(Path(temp.name));self.addCleanup(c.close)
        first=half_fixture(self,'selection');second=half_fixture(self,'verification')
        base=json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf8'))
        base['resources']['Q11'].update(first['parameters'])
        profile=build_irb_profile(root,base);profile['connectionConfirmedAt']=now()
        manifest=c.root/profile['sourceManifest'];manifest.parent.mkdir(parents=True,exist_ok=True);manifest.write_text('[]',encoding='utf8')
        c.adapter=ScienceIRBAdapter(c.root,profile);c.profile=c.adapter.profile;c._physical_adapter=True
        c.adapter_id=profile['id'];c.adapter_profile_hash=c._adapter_fingerprint()
        observed=[];lineage=first['provenance']['scienceHalfDrag']['rabiLineage']
        def resolve(rt,**kwargs):
            proof=deepcopy(first['provenance']['scienceDependencies']);request=kwargs['consumer']
            proof['scope']=kwargs['scope']
            roles={'pi01':lineage['pi01RecordId'],'half01':lineage['half01RecordId'],
                'rabi_amplitude':lineage['amplitudeRecordId'],'rabi_time':lineage['time']['sourceRecordId'],'readout':'readout'}
            if not any(n['recordId']=='readout' for n in proof['nodes']):proof['nodes'].append({'recordId':'readout'})
            proof['rootReferences']=[{'role':role,'recordId':record} for role,record in roles.items()]
            proof['edges']=[{'toRecordId':record,'role':role} for role,record in roles.items()]
            keys=('schema','policyVersion','kind','scope','inputs','rootReferences','nodes','edges')
            proof['closureSha256']=digest({key:proof[key] for key in keys})
            proof['assessment']={'executionEligible':True,'consumerSha256':digest({k:v for k,v in request.items() if k!='scienceDependencies'})}
            return proof
        def execute(adapter,request,emit,cancel):
            plan=adapter.build_plan(request)
            with c.store.connect() as rt:leases=rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0]
            observed.append((deepcopy(request),plan,leases))
            out=deepcopy(second if 'verify_drag_beta_s' in plan['parameters'] else first)
            out.update(parameters=plan['parameters'],acquiredAt=now(),sourceRunId=request['operationId'])
            out['targetPulseIdentity']=target_identity(plan['parameters'],out['candidateParameters']['drag_beta_s'])
            out['provenance'].update(scienceDependencies=deepcopy(plan['validationEvidence']),operationId=request['operationId'])
            path=c.root/'data'/('synthetic-'+str(len(observed))+'.raw');path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('TEST_ONLY '+request['operationId'],encoding='utf8')
            out['artifacts']=[{'id':digest(str(path)),'name':'raw_scan.npz','kind':'raw','localPath':str(path),
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]
            return out
        with ExitStack() as stack:
            stack.enter_context(patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=resolve))
            stack.enter_context(patch('adapters.science_v3_irb.adapter.verify_sources',return_value={}))
            stack.enter_context(patch('adapters.science_v3_irb.adapter.rabi_lineage',return_value=lineage))
            stack.enter_context(patch('adapters.science_v3_irb.adapter.check_pulse_dependencies'))
            stack.enter_context(patch.object(ScienceIRBAdapter,'authenticate_dependency',return_value={'fixtureOnly':True}))
            stack.enter_context(patch.object(ScienceIRBAdapter,'preflight',return_value={'ready':True,'staticReady':True,'hardwareConnected':False}))
            stack.enter_context(patch.object(ScienceIRBAdapter,'execute',execute))
            stack.enter_context(patch.object(c,'hardware_ownership',return_value={'verified':True,'owners':[]}))
            wf=c.create({'name':'TEST_ONLY beta half lifecycle','mode':'real','policy':'strict','resourceIds':['Q11'],
                'executionProfile':'science-v3-irb','manualProcedureId':'sq_drag_half_scan',
                'manualParameters':{'drag_mode':'empirical_beta','beta_start_s':-2e-9,'beta_stop_s':2e-9,'points':5}})
            node=wf['nodes'][0]['id']
            def action(name,op):
                nonlocal wf
                wf=c.action(wf['id'],{'action':name,'nodeId':node,'operationId':op,'expectedRevision':wf['revision']})
            action('run_step','first-request');c.run_workflow(wf['id']);wf=c.get(wf['id'])
            self.assertEqual(wf['engineState'],'waiting_hardware')
            action('confirm_hardware','fixture-handover');action('run_step','single-permit')
            c.run_workflow(wf['id']);wf=c.get(wf['id'])
            self.assertEqual(wf['engineState'],'awaiting_acceptance',wf.get('events'))
            with patch('backend.science_dependencies.ScienceDependencyResolver.resolve',side_effect=APIError('STALE','changed source')):
                with self.assertRaises(APIError):action('approve','stale-accept')
            action('approve','fresh-accept')
        self.assertEqual(wf['status'],'completed');self.assertEqual(len(observed),2)
        self.assertTrue(all(lease==1 for _,_,lease in observed))
        selected=first['candidateParameters']['drag_beta_s']
        self.assertEqual(observed[1][0]['parameters']['verify_drag_beta_s'],selected)
        self.assertEqual(wf['nodes'][0]['result']['candidateParameters'],{'drag_beta_s':selected})
        self.assertNotIn('gate_fidelity',wf['nodes'][0]['result']['metrics'])
        self.assertEqual(wf['reservedShots'],sum(plan['estimated_total_shots'] for _,plan,_ in observed))
        with c.store.connect() as rt:
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_effects').fetchone()[0],2)
            self.assertEqual(rt.db.execute('SELECT count(*) FROM platform_leases').fetchone()[0],0)
            values=dict(rt.db.execute('SELECT name,value FROM parameters'))
            self.assertEqual(json.loads(values[wf['id']+':Q11:drag_beta_s']),selected)
            self.assertNotIn(wf['id']+':Q11:drag_lambda1',values)
