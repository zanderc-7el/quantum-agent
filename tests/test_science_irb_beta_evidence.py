"""TEST_ONLY receipt/intent fixtures; never a physical calibration source."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from adapters.science_runtime.policy import digest,sha
from adapters.f8 import _digest as intent_digest
from adapters.science_v3.evidence import result_content
from adapters.science_v3_irb import beta
from adapters.science_v3_irb.half_scan import build_half_scan,target_identity
from adapters.science_v3_irb.timing import authenticate_dependency
from tests import test_science_irb_beta as beta_tests


class EmpiricalEvidence(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory(prefix='beta-receipt-test-only-')))
        self.binding={'chipVersionId':'fixture-chip','deploymentVersionId':'fixture-deployment','chipHash':'c'*64,'deploymentHash':'d'*64}
        self.p,self.campaign=build_half_scan({**beta_tests.BetaContracts().parameters(),'capture_repeats':2,'scan_axis_values':[-1e-9,-.5e-9,0.,.5e-9,1e-9]})
        self.consumer={'runId':'fixture-run','resourceId':'Q11','parameters':self.p,'parameterVersions':{}}
        self.dep={'recordId':'fixture-drag','procedureId':'sq_drag_half_scan','parameters':self.p,'sourceRoles':['drag'],
                  'scope':{'workflowId':'fixture-run','resourceId':'Q11'},'operations':[]}
        self.folders=[]
        for stage in ('selection','verification'):
            op='fixture-'+stage;folder=self.root/'data/runs/arclight_operations'/hashlib.sha256(op.encode()).hexdigest();(folder/'results').mkdir(parents=True)
            self.folders.append(folder);p,campaign=build_half_scan({**self.p,**({'verify_drag_beta_s':.2e-9} if stage=='verification' else {})})
            request={'runId':'fixture-run','resourceId':'Q11','procedureId':'sq_drag_half_scan','operationId':op,'parameters':p,'leaseToken':'fixture'}
            plan={'parameters':p,'rows':campaign['rows'],'campaign':campaign,'procedureId':'sq_drag_half_scan','operationId':op,
                  'policy':beta.HALF_POLICY,'pulsePolicy':beta.PULSE_POLICY,'sources':{'testOnly':True},'sourceHash':digest({'testOnly':True}),
                  'requestHash':digest(request),'configurationBinding':self.binding}
            self.write(folder/'plan.json',plan);compiled=[];uploads=[]
            for row in campaign['rows']:
                path=folder/'results'/('compiled-'+str(row['id'])+'.npz');path.write_bytes(('TEST_ONLY ARRAY '+stage+str(row['id'])).encode())
                waves={name:{'name':name,'sha256':sha(path),'sampleRateHz':4e9} for name in ('xy_1','ro_B1')}
                compiled.append({'rowId':row['id'],'file':str(path),'sha256':sha(path),'waves':waves})
                uploads.extend({'rowId':row['id'],**wave,'uploadCommandsCompleted':True} for wave in waves.values())
            self.write(folder/'results/compiled_plan.json',compiled)
            (folder/'results/uploads.jsonl').write_text('\n'.join(json.dumps(x) for x in uploads),encoding='utf-8')
            (folder/'results/raw_scan.npz').write_bytes(('TEST_ONLY ADC '+stage).encode())
            self.write(folder/'results/analysis.json',{'testOnly':True});self.write(folder/'results/sequence_manifest.json',campaign)
            self.write(folder/'results/instrument.json',{'testOnly':True})
            artifacts=[]
            for path in [folder/'plan.json',*sorted((folder/'results').iterdir())]:
                artifacts.append({'id':stage+'-'+path.name,'name':path.name,'kind':'raw' if path.name=='raw_scan.npz' else 'manifest',
                                  'sha256':sha(path),'localPath':str(path)})
            result={'dataKind':'real','sourceRunId':op,'acquiredAt':'TEST_ONLY','parameters':p,'candidateParameters':{'drag_beta_s':.2e-9},
                    'targetPulseIdentity':target_identity(p,.2e-9),'artifacts':artifacts,'analysisQuality':{'accepted':True},
                    'provenance':{'freshAcquisition':True,'policy':beta.PULSE_POLICY,'sourceHash':plan['sourceHash']}}
            self.write(folder/'adapter_intent.json',{'request':request,'requestHash':intent_digest(request)})
            self.write(folder/'adapter_receipt.json',{'status':'completed','operationId':op,'requestHash':intent_digest(request),'result':result})
            self.write(folder/'completion.json',{'state':'completed','shutdown_ok':True,'operationId':op})
            self.dep['operations'].append({'stage':stage,'operationId':op,'adapterRequestSha256':digest(request),'resultContentSha256':digest(result_content(result)),
                'rawArtifacts':[{'id':a['id'],'sha256':a['sha256']} for a in artifacts if a['kind']=='raw'],
                'registeredArtifacts':[{k:a[k] for k in ('id','name','kind','sha256')} for a in artifacts]})
    def write(self,path,value):path.write_text(json.dumps(value),encoding='utf-8')
    def read(self,path):return json.loads(path.read_text(encoding='utf-8'))
    def auth(self):return authenticate_dependency(self.root,self.consumer,self.dep)
    def test_complete_beta_source_binds_distinct_raw_and_compiled_uploads(self):
        result=self.auth();self.assertEqual(result['status'],'valid');self.assertEqual(result['dependencySha256'],digest(self.dep))
        self.assertEqual(len(result['receipts']),2)
    def test_mode_identity_metadata_and_upload_mutations_rejected(self):
        original=deepcopy(self.dep)
        for name,change in (
            ('plan.json',lambda x:x.update(pulsePolicy='legacy')),
            ('plan.json',lambda x:x['rows'][0]['gates'][0]['pulse'].update(betaSeconds=.9e-9)),
            ('completion.json',lambda x:x.update(shutdown_ok=False)),
            ('adapter_intent.json',lambda x:x.update(requestHash='0'*64)),
            ('adapter_receipt.json',lambda x:x['result']['targetPulseIdentity']['identity'].update(betaSeconds=.3e-9))):
            with self.subTest(name=name):
                path=self.folders[0]/name;data=path.read_bytes();value=self.read(path);change(value);self.write(path,value)
                with self.assertRaises(ValueError):self.auth()
                path.write_bytes(data)
        self.dep['operations'][0]['registeredArtifacts']=[a for a in self.dep['operations'][0]['registeredArtifacts'] if a['name']!='plan.json']
        with self.assertRaises(ValueError):self.auth()
        self.dep=original;path=self.folders[0]/'results/uploads.jsonl';path.write_text('',encoding='utf-8')
        with self.assertRaises(ValueError):self.auth()


class EmpiricalAdmission(unittest.TestCase):
    def test_real_wrapper_request_and_empirical_worker_admission(self):
        from adapters.science_v3_irb.adapter import ScienceIRBAdapter,build_irb_profile
        from adapters.science_v3_irb.execute_worker import validate_admission
        root=Path(__file__).resolve().parents[1];profile=build_irb_profile(root,json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf-8')))
        profile['enabled']=True;adapter=ScienceIRBAdapter(root,profile)
        adapter.operations=Path(self.enterContext(tempfile.TemporaryDirectory(prefix='beta-wrapper-test-only-')))
        p={**beta_tests.BetaContracts().parameters(),'pulse_compatibility':profile['resources']['Q11']['pulse_compatibility']};request={'runId':'fixture','nodeId':'n','attemptId':'a','operationId':'a/measure','resourceId':'Q11',
            'procedureId':'sq_drag_half_scan','parameters':p,'leaseToken':'TEST_ONLY','explicitParameterKeys':[],'parameterVersions':{}}
        roles=beta.required_roles('sq_drag_half_scan',p)
        proof={'schema':'qcal-science-dependencies/1','policyVersion':'TEST_ONLY','kind':'measurement','scope':{'workflowId':'fixture','resourceId':'Q11'},'inputs':{},
               'nodes':[{'recordId':r} for r in roles],'rootReferences':[{'role':r,'recordId':r} for r in roles],
               'edges':[{'role':r,'toRecordId':r} for r in roles]}
        proof['closureSha256']=digest(proof);proof['assessment']={'executionEligible':True,'consumerSha256':digest(request)};request['scienceDependencies']=proof
        class HaltBeforeChild(BaseException):pass
        with patch.object(adapter,'authenticate_dependency',return_value={'testOnly':True}),patch('adapters.science_v3_irb.adapter.check_pulse_dependencies'),patch('adapters.science_v3_irb.adapter.rabi_lineage',return_value={'testOnly':True}),patch('adapters.arclight.subprocess.Popen',side_effect=HaltBeforeChild):
            with self.assertRaises(HaltBeforeChild):adapter.execute(request,lambda e:None,lambda:False)
        folder=adapter._operation_dir(request['operationId']);plan=json.loads((folder/'plan.json').read_text(encoding='utf-8'))
        with patch('adapters.science_v3_irb.timing.authenticate_dependency',return_value={'testOnly':True}),patch('adapters.science_v3_irb.timing.rabi_lineage',return_value={'testOnly':True}):
            self.assertEqual(validate_admission(plan,folder),request)
            changed=deepcopy(plan);changed['rows'][0]['gates'][0]['pulse']['betaSeconds']=10e-9
            with self.assertRaisesRegex(ValueError,'rows changed'):validate_admission(changed,folder)
            changed=deepcopy(plan);changed['parameters']['drag_mode']='normalized_alpha'
            with self.assertRaises(ValueError):validate_admission(changed,folder)
