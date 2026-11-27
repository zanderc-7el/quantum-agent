"""Isolated version routing and worker admission. No instrument calls."""
from copy import deepcopy, copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
from adapters.f8 import _digest
from adapters.f8 import AdapterError
from backend.common import APIError, digest
from tests.test_controller import make_fixture

ROOT = Path(__file__).resolve().parents[1]


class VersionContracts(unittest.TestCase):
    def test_versions_keep_old_fingerprints_and_select_exact_identity(self):
        from backend.arclight_versions import profile_for_workflow, fingerprint
        for version in (1, 2, 3):
            name = 'arclight_profile.json' if version == 1 else f'arclight_profile_v{version}.json'
            p = json.loads((ROOT/'adapters'/name).read_text(encoding='utf8'))
            wf = {'mode':'real', 'adapterId':p['id'], 'adapterProfileHash':fingerprint(ROOT,p)}
            self.assertEqual(profile_for_workflow(ROOT,wf),p)
            with self.assertRaises(APIError):
                profile_for_workflow(ROOT,{**wf,'adapterProfileHash':'0'*64})
        self.assertEqual(fingerprint(ROOT,json.loads((ROOT/'adapters/arclight_profile_v2.json').read_text(encoding='utf8'))),
                         'dbe2590c7cb7dc401b0d12df7d70d75620a3b133ebfd21b7aa0182c56f950600')

    def test_new_create_and_old_manual_step_route_without_rebinding_or_dispatch(self):
        from backend.arclight_versions import fingerprint
        with tempfile.TemporaryDirectory() as tmp:
            c,_ = make_fixture(Path(tmp))
            try:
                def install(version):
                    c.adapter=ArcLightAdapter(ROOT,ROOT/f'adapters/arclight_profile_v{version}.json')
                    c.profile=c.adapter.profile;c.adapter_id=c.profile['id'];c._physical_adapter=True
                    c.adapter_profile_hash=fingerprint(ROOT,c.profile)
                install(2)
                old=c.create({'name':'Old source TEST ONLY','mode':'real','policy':'strict','coverage':'full','resourceIds':['Q11']})
                old_hash=old['adapterProfileHash']
                install(3)
                _,sources=c._supplemental_sources()
                self.assertEqual(sources['nativeAnalysisSha256'],hashlib.sha256((ROOT/'adapters/arclight_analysis_v3/platform_analysis.py').read_bytes()).hexdigest())
                new=c.create({'name':'Fresh version TEST ONLY','mode':'real','policy':'strict','coverage':'full',
                              'resourceIds':['Q11'],'analysisParentWorkflowId':old['id'],'operationId':'new-version-create'})
                self.assertEqual(c.for_workflow(old['id']).adapter.profile['id'],'arclight-private-f8-q11-v2')
                self.assertEqual(c.for_workflow(new['id']).adapter.profile['id'],'arclight-private-f8-q11-v3')
                self.assertEqual(c.get(old['id'])['adapterProfileHash'],old_hash)
                self.assertFalse(new['analysisLineage']['acceptedCalibrationInherited'])
                self.assertFalse(any(n.get('result') or n.get('latestAttemptId') for n in new['nodes']))
                # A scientific consumer must authenticate the source under the
                # source's actual normalization rules, not its own shape policy.
                from adapters.science_v3_adapter import ScienceV3Adapter, build_science_profile
                science=copy(c);science.adapter=ScienceV3Adapter(ROOT,build_science_profile(ROOT,c.profile))
                science.profile=science.adapter.profile;science._bound_workflow_id='consumer-fixture'
                source=science.for_workflow(new['id'])
                self.assertIs(type(source.adapter),ArcLightAdapter)
                self.assertEqual(source.adapter_profile_hash,new['adapterProfileHash'])
                self.assertEqual(source.adapter.normalize_candidate('sq_rabi_time',{'drive_length_s':330.37e-9}),{'drive_length_s':330.5e-9})
                self.assertEqual(c.create({'name':'Fresh version TEST ONLY','mode':'real','policy':'strict','coverage':'full',
                              'resourceIds':['Q11'],'analysisParentWorkflowId':old['id'],'operationId':'new-version-create'})['id'],new['id'])
                with patch.object(ArcLightAdapter,'hardware_ownership',return_value={'owners':[]}),patch.object(ArcLightAdapter,'execute',side_effect=AssertionError('NO DEVICE')):
                    for wf in (old,new):
                        queued=c.action(wf['id'],{'action':'run_step','nodeId':wf['nodes'][0]['id'],
                            'operationId':'queue-'+wf['id'],'expectedRevision':wf['revision']})
                        self.assertEqual(queued['adapterProfileHash'],wf['adapterProfileHash'])
                with c.store.connect() as rt:
                    self.assertEqual(rt.db.execute('select count(*) from platform_effects').fetchone()[0],0)
                    self.assertEqual(rt.db.execute('select count(*) from parameters').fetchone()[0],0)
                with self.assertRaises(APIError):
                    c.create({'name':'Invalid remeasurement TEST ONLY','mode':'real','policy':'strict','coverage':'main',
                              'resourceIds':['Q11'],'analysisParentWorkflowId':old['id']})
            finally:c.close()

    def test_worker_admission_binds_actual_wrapper_intent_plan_and_sources(self):
        from adapters.arclight_analysis_v3.execute_worker import validate_admission, verify_sources
        from backend.arclight_versions import fingerprint
        sources=verify_sources(ROOT)
        self.assertIn('adapters/arclight_runtime/source_manifest.json',sources)
        a=ArcLightAdapter(ROOT,ROOT/'adapters/arclight_profile_v3.json')
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            request={'operationId':'test/measure','runId':'test','nodeId':'Q11:s01_readout_frequency',
                     'attemptId':'test','resourceId':'Q11','procedureId':'ro_power_shift','parameters':{'points':31},
                     'adapterId':a.profile['id'],'adapterProfileHash':fingerprint(ROOT,a.profile)}
            plan=a.build_plan(request)
            intent={'operationId':request['operationId'],'request':request,'requestHash':_digest(request),
                    'profileHash':_digest({**a.profile,'enabled':True})}
            (folder/'adapter_intent.json').write_text(json.dumps(intent),encoding='utf8')
            self.assertEqual(validate_admission(ROOT,folder,plan)['operationId'],request['operationId'])
            for changed in ({**plan,'operationId':'wrong'}, {**plan,'hardwareLocks':[]}):
                with self.assertRaises(ValueError):validate_admission(ROOT,folder,changed)
            intent['requestHash']=digest(request)
            (folder/'adapter_intent.json').write_text(json.dumps(intent),encoding='utf8')
            with self.assertRaises(ValueError):validate_admission(ROOT,folder,plan)

    def test_actual_wrapper_writes_compatible_intent_before_subprocess_boundary(self):
        from adapters.arclight_analysis_v3.execute_worker import validate_admission
        from backend.arclight_versions import fingerprint
        with tempfile.TemporaryDirectory() as tmp:
            a=ArcLightAdapter(ROOT,ROOT/'adapters/arclight_profile_v3.json').with_execution_enabled()
            a.operations=Path(tmp)
            request={'operationId':'actual-wrapper/measure','runId':'test','nodeId':'Q11:s01_readout_frequency',
                     'attemptId':'test','resourceId':'Q11','procedureId':'ro_power_shift','parameters':{'points':31},
                     'adapterId':a.profile['id'],'adapterProfileHash':fingerprint(ROOT,a.profile)}
            checked=[]
            def never_start(*args,**kwargs):
                folder=a._operation_dir(request['operationId'])
                plan=json.loads((folder/'plan.json').read_text(encoding='utf8'))
                checked.append(validate_admission(ROOT,folder,plan))
                raise OSError('TEST ONLY: native process prohibited')
            with patch('adapters.arclight.subprocess.Popen',side_effect=never_start),self.assertRaises(AdapterError) as error:
                a.execute(request,lambda event:None,lambda:False)
            self.assertEqual(error.exception.code,'FAILED_BEFORE_START')
            self.assertEqual(checked,[request])

    def test_pinned_child_selects_new_analysis_and_unchanged_worker_without_device_import(self):
        python=json.loads((ROOT/'adapters/arclight_profile_v3.json').read_text(encoding='utf8'))['python']
        script=r'''
import importlib,json,pathlib,shutil,sys,tempfile,os,hashlib
from unittest.mock import patch
from adapters.arclight_analysis_v3 import execute_worker as version
from adapters.arclight import ArcLightAdapter
from adapters.f8 import _digest
from backend.arclight_versions import fingerprint
source=pathlib.Path.cwd()
def guard(event,args):
    if event in ('socket.connect','subprocess.Popen'):raise AssertionError('NO DEVICE/CHILD')
    if event=='import' and (args[0]=='run_single_qubit' or args[0]=='core' or args[0].startswith('core.')):raise AssertionError('NO HARDWARE IMPORT')
sys.addaudithook(guard)
with tempfile.TemporaryDirectory() as tmp:
    root=pathlib.Path(tmp)
    rows=json.loads((source/version.MANIFEST).read_text(encoding='utf8'))
    for name in [x['path'] for x in rows]+[version.MANIFEST]:
        dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,dst)
    a=ArcLightAdapter(root,root/version.PROFILE)
    req={'operationId':'fixture/measure','runId':'test','nodeId':'Q11:s01_readout_frequency','attemptId':'test',
         'resourceId':'Q11','procedureId':'ro_power_shift','parameters':{'points':31},'adapterId':a.profile['id'],
         'adapterProfileHash':fingerprint(root,a.profile)}
    folder=root/'data/runs/arclight_operations'/hashlib.sha256(req['operationId'].encode()).hexdigest();folder.mkdir(parents=True)
    plan=folder/'plan.json';plan.write_text(json.dumps(a.build_plan(req)),encoding='utf8')
    (folder/'adapter_intent.json').write_text(json.dumps({'operationId':req['operationId'],'request':req,
        'requestHash':_digest(req),'profileHash':_digest({**a.profile,'enabled':True})}),encoding='utf8')
    original=version._module;entered=[]
    def load(name,path):
        module=original(name,path)
        if name=='_qcal_versioned_native_worker':
            assert module.SOURCE==root/'adapters/arclight_runtime'
            def safe_boundary():
                analysis=importlib.import_module('platform_analysis')
                assert pathlib.Path(analysis.__file__)==root/'adapters/arclight_analysis_v3/platform_analysis.py'
                assert analysis.HERE==root/'adapters/arclight_runtime'
                entered.append(True)
            module.main=safe_boundary
        return module
    with patch.object(version,'__file__',str(root/'adapters/arclight_analysis_v3/execute_worker.py')),patch.object(version,'_module',side_effect=load),patch.object(sys,'argv',['version',str(plan)]),patch.dict(os.environ,{'QCAL_ARCLIGHT_RUN_DIR':str(folder)}):
        version.main()
    assert entered==[True]
print('versioned-main-safe-native-boundary: PASS')
'''
        done=subprocess.run([python,'-X','utf8','-B','-c',script],cwd=ROOT,capture_output=True,text=True,encoding='utf8',timeout=30)
        self.assertEqual(done.returncode,0,done.stdout+done.stderr)
        self.assertIn('versioned-main-safe-native-boundary: PASS',done.stdout)


if __name__=='__main__':unittest.main()
