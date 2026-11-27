"""Worker request/profile binding and exact new/old lineage admission."""
from pathlib import Path
from copy import deepcopy
import json,tempfile,unittest
from unittest.mock import patch
from adapters.science_runtime.policy import digest
from adapters.f8 import _digest as intent_digest
from adapters.f8 import AdapterError
from tests.test_science_repeat5 import parameters

ROOT=Path(__file__).resolve().parents[1]

class Admission(unittest.TestCase):
    def test_same_half_irb_native_plan_inherits_repeat5_lineage_and_rejects_different_gate(self):
        """Real native campaign planner, fixture-only source receipts/authentication.

        No waves upload and no hardware calls; the full repeat5 operation source
        authenticator is exercised separately by the native transport test.
        """
        import hashlib,math
        from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter,build_repeat5_profile
        from adapters.science_shape_repeat5.timing import rabi_lineage,inherited_rabi_lineage
        from adapters.science_v3_irb.plans import identity
        fixture=json.loads((ROOT/'tests/fixtures/r23_repeat5_native_results.json').read_text('utf8'))
        pi=fixture['results'][0]['result'];p={**parameters(),'shape_pi_amp':pi['candidateParameters']['shape_pi_amp'],
            'drag_mode':'empirical_beta','drag_beta_s':-.3e-9,'rb_lengths':[1,2,4,8,16],'rb_master_seed':23}
        for key in ('amp_start','amp_stop','points'):p.pop(key)
        base=json.loads((ROOT/'adapters/arclight_profile.json').read_text('utf8'))
        adapter=ScienceRepeat5Adapter(ROOT,build_repeat5_profile(ROOT,base))
        half=deepcopy(pi['targetPulseIdentity']);half['identity'].update(amplitude=p['shape_pi_amp']/2,rotationRad=math.pi/2)
        half['sha256']=digest(half['identity'])
        nodes=[{'recordId':'pi','procedureId':'sq_shape_rabi_amplitude_repeat5','parameters':pi['parameters'],
                'targetPulseIdentity':pi['targetPulseIdentity']},
            {'recordId':'half','procedureId':'sq_shape_half_verify','parameters':p,'targetPulseIdentity':half},
            {'recordId':'time','procedureId':'sq_rabi_time','parameters':p},
            {'recordId':'readout','procedureId':'ro_iq01','parameters':p},
            {'recordId':'drag','procedureId':'sq_drag_half_scan','parameters':p,'targetPulseIdentity':identity(p),
                'operations':[{'operationId':'fixture-only-half-'+s,'stage':s} for s in ('selection','verification')]}]
        ancestor=[{'role':r,'recordId':v} for r,v in [('pi01','pi'),('rabi_amplitude','pi'),('half01','half'),('rabi_time','time')]]
        timing={'sourceRecordId':'time','targetDurationS':p['pulse_length_s']};timing['sha256']=digest(timing)
        attestation={'recordId':'time','timingMigration':timing}
        lineage=rabi_lineage({'nodes':nodes,'rootReferences':ancestor},[attestation],p)
        request={'runId':'fixture-only','nodeId':'node','attemptId':'attempt','operationId':'irb','resourceId':'Q11',
            'procedureId':'sq_drag_irb','parameters':p,'parameterVersions':{}}
        def bind(request):
            proof={'schema':'qcal-science-dependencies/1','policyVersion':'science-dependency-policy/1','kind':'measurement',
                'scope':{'workflowId':'fixture-only','resourceId':'Q11'},'inputs':{},'nodes':nodes,
                'rootReferences':[{'role':r,'recordId':v} for r,v in [('pi01','pi'),('half01','half'),('drag','drag'),('readout','readout')]],
                'edges':[{'fromRecordId':'drag','role':r['role'],'toRecordId':r['recordId']} for r in ancestor]}
            proof['closureSha256']=digest(proof);proof['assessment']={'executionEligible':True,'consumerSha256':digest(request)}
            return {**request,'scienceDependencies':proof}
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory)
            for op in nodes[-1]['operations']:
                folder=temp/'data/runs/arclight_operations'/hashlib.sha256(op['operationId'].encode()).hexdigest();folder.mkdir(parents=True)
                (folder/'adapter_receipt.json').write_text(json.dumps({'result':{'provenance':{'scienceHalfDrag':{'rabiLineage':lineage}}}}))
            with patch.object(adapter,'authenticate_dependency',side_effect=lambda req,dep:attestation if dep['recordId']=='time' else {'fixtureOnly':True}),\
                    patch('adapters.science_shape_repeat5.adapter.inherited_rabi_lineage',side_effect=lambda root,*args:inherited_rabi_lineage(temp,*args)):
                first=adapter.build_plan(bind(request))
                self.assertEqual(first['rabiLineage']['pi01RecordId'],'pi');self.assertEqual(first['totalRows'],416)
                verify=adapter.build_verification_parameters('sq_drag_irb',first['parameters'],{})
                second=adapter.build_plan(bind({**request,'operationId':'irb-verify','parameters':verify}))
                self.assertEqual(first['campaign']['targetPulseIdentity'],second['campaign']['targetPulseIdentity'])
                self.assertNotEqual(first['parameters']['rb_master_seed'],second['parameters']['rb_master_seed'])
                with self.assertRaises(AdapterError):adapter.build_plan(bind({**request,'parameters':{**p,'drag_beta_s':.3e-9}}))
                with self.assertRaises(AdapterError):adapter.build_plan(bind({**request,'parameters':{**p,'pulse_length_s':320e-9}}))

    def test_real_planner_wrapper_canonical_request_and_output_directory_projection(self):
        from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter,build_repeat5_profile
        from adapters.science_shape_repeat5.execute_worker import validate_admission
        base=json.loads((ROOT/'adapters/arclight_profile.json').read_text('utf8'));profile=build_repeat5_profile(ROOT,base);profile['enabled']=True
        adapter=ScienceRepeat5Adapter(ROOT,profile)
        request={'runId':'test-run','nodeId':'node','attemptId':'attempt','operationId':'test-operation','resourceId':'Q11',
            'procedureId':'sq_shape_rabi_amplitude_repeat5','parameters':parameters(),'parameterVersions':{},'leaseToken':'fixture-lease',
            'outputDir':'display-only-controller-directory'}
        proof={'schema':'qcal-science-dependencies/1','policyVersion':'science-dependency-policy/1','kind':'measurement',
            'scope':{'workflowId':'test-run','resourceId':'Q11'},'inputs':{},'rootReferences':[{'role':r,'recordId':r} for r in ('f01','readout')],
            'nodes':[{'recordId':r,'parameters':parameters(),'resolvedOutputs':{'candidateParameters':{'drive_hz':parameters()['drive_hz']}}} for r in ('f01','readout')],
            'edges':[{'role':r,'toRecordId':r} for r in ('f01','readout')]}
        proof['closureSha256']=digest(proof);proof['assessment']={'executionEligible':True,'consumerSha256':digest(request)}
        request['scienceDependencies']=proof
        with tempfile.TemporaryDirectory() as directory,patch.object(ScienceRepeat5Adapter,'authenticate_dependency',return_value={'fixtureOnly':True}):
            folder=Path(directory);plan=adapter.build_plan(request);canonical={k:v for k,v in request.items() if k!='outputDir'}
            intent={'request':canonical,'requestHash':intent_digest(canonical),'profileHash':intent_digest(profile)}
            (folder/'adapter_intent.json').write_text(json.dumps(intent))
            self.assertEqual(validate_admission(plan,folder),canonical)
            for mutate in ('repeat','lease','output','profile'):
                bad=deepcopy(plan)
                if mutate=='repeat':bad['rows'][0]['gates'].pop()
                elif mutate=='output':bad['consumerOutputDir']='changed'
                elif mutate=='profile':bad['profileSnapshot']['enabled']=False
                else:bad['requestIdentity']['leaseToken']='different'
                with self.subTest(mutate=mutate),self.assertRaises((ValueError,AdapterError)):validate_admission(bad,folder)

    def test_old_and_five_gate_lineage_use_same_exact_pi_record(self):
        from adapters.science_shape_repeat5.timing import rabi_lineage
        timing={'targetDurationS':660e-9};timing['sha256']=digest(timing)
        refs=[{'role':k,'recordId':v} for k,v in [('pi01','pi'),('rabi_amplitude','pi'),('half01','half'),('rabi_time','time')]]
        for method in ('sq_shape_rabi_amplitude','sq_shape_rabi_amplitude_repeat5'):
            proof={'rootReferences':refs,'nodes':[{'recordId':'pi','procedureId':method}]}
            lineage=rabi_lineage(proof,[{'recordId':'time','timingMigration':timing}],{'pulse_length_s':660e-9})
            self.assertEqual(lineage['pi01RecordId'],lineage['amplitudeRecordId'])
        with self.assertRaises(ValueError):rabi_lineage({'rootReferences':refs,'nodes':[{'recordId':'pi','procedureId':'sq_rabi_amplitude'}]},[{'recordId':'time','timingMigration':timing}],{'pulse_length_s':660e-9})

    def test_actual_repeat5_identity_is_usable_by_new_half_plan_and_wrong_width_is_rejected(self):
        from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter,build_repeat5_profile
        fixture=json.loads((ROOT/'tests/fixtures/r23_repeat5_native_results.json').read_text('utf8'))
        pi=fixture['results'][0]['result'];p={**parameters(),'shape_pi_amp':pi['candidateParameters']['shape_pi_amp']}
        base=json.loads((ROOT/'adapters/arclight_profile.json').read_text('utf8'))
        adapter=ScienceRepeat5Adapter(ROOT,build_repeat5_profile(ROOT,base))
        request={'runId':'wf','nodeId':'node','attemptId':'attempt','operationId':'half','resourceId':'Q11',
            'procedureId':'sq_shape_half_verify','parameters':p,'parameterVersions':{}}
        def bind(request):
            proof={'schema':'qcal-science-dependencies/1','policyVersion':'science-dependency-policy/1','kind':'measurement',
                'scope':{'workflowId':'wf','resourceId':'Q11'},'inputs':{},
                'rootReferences':[{'role':r,'recordId':r} for r in ('pi01','readout')],
                'nodes':[{'recordId':'pi01','procedureId':'sq_shape_rabi_amplitude_repeat5','parameters':pi['parameters'],
                    'resolvedOutputs':{'candidateParameters':pi['candidateParameters']},'targetPulseIdentity':pi['targetPulseIdentity']},
                    {'recordId':'readout','parameters':parameters()}],
                'edges':[{'role':r,'toRecordId':r} for r in ('pi01','readout')]}
            proof['closureSha256']=digest(proof);proof['assessment']={'executionEligible':True,'consumerSha256':digest(request)}
            return {**request,'scienceDependencies':proof}
        with patch.object(adapter,'authenticate_dependency',return_value={'fixtureOnly':True}):
            plan=adapter.build_plan(bind(request));self.assertEqual(len(plan['rows']),10)
            self.assertEqual(plan['parameters']['shape_pi_amp'],pi['candidateParameters']['shape_pi_amp'])
            with self.assertRaises(AdapterError):adapter.build_plan(bind({**request,'parameters':{**p,'pulse_length_s':320e-9}}))
