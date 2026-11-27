"""API-owned text protocols; isolated subprocesses, never a service or device."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class ApiEncodingContracts(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows launcher contract')
    def test_real_powershell_launcher_passes_mode_without_starting_services(self):
        for engine in filter(None,(shutil.which('powershell.exe'),shutil.which('pwsh.exe'))):
            with self.subTest(engine=engine), tempfile.TemporaryDirectory(prefix='TEST_ONLY_encoding_') as directory:
                root=Path(directory);(root/'scripts').mkdir();(root/'frontend/dist').mkdir(parents=True)
                shutil.copy2(ROOT/'scripts/start.ps1',root/'scripts/start.ps1')
                (root/'frontend/dist/index.html').write_text('TEST_ONLY',encoding='utf-8')
                script=r'''[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$OutputEncoding=[Console]::OutputEncoding
$ErrorActionPreference='Stop'
$testRoot=$env:QCAL_ENCODING_FIXTURE
$global:apiCalls=0;$global:apiArgs=@()
function Get-NetTCPConnection {return $null}
function Start-Process {
 param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
 if($FilePath -notmatch 'python.exe$'){throw 'Unexpected process; no child permitted'}
 $global:apiArgs=$ArgumentList
 return [pscustomobject]@{Id=424242;StartTime=[DateTime]::UtcNow}
}
function Invoke-RestMethod {
 param($Uri,$TimeoutSec)
 if($Uri -match '8766/health$'){return [pscustomobject]@{serviceId='qcal-brain';projectRoot=$testRoot}}
 if($Uri -match '8765/api/health$'){
   $global:apiCalls++
   if($global:apiCalls -eq 1){return $null}
   return [pscustomobject]@{projectId='qcal-local-f8-platform';projectRoot=$testRoot;agentPort=8766}
 }
 throw 'No real network permitted'
}
& (Join-Path $testRoot 'scripts/start.ps1') | Out-Null
@{args=$global:apiArgs;calls=$global:apiCalls}|ConvertTo-Json -Compress
'''
                result=subprocess.run([engine,'-NoProfile','-NonInteractive','-Command',script],capture_output=True,
                    timeout=15,env={**os.environ,'QCAL_ENCODING_FIXTURE':str(root)},
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
                value=json.loads(result.stdout.decode('utf-8'))
                self.assertEqual(value['args'][:5],['-B','-X','utf8=0','-m','backend.server'])
                self.assertEqual(value['calls'],2)

    def test_both_launcher_commands_select_native_pipe_decoding(self):
        source=(ROOT/'scripts/start.ps1').read_text('utf-8')
        self.assertIn("@('-B','-X','utf8=0','-m','backend.server'",source)
        node=shutil.which('node.exe') or shutil.which('node')
        self.assertIsNotNone(node)
        code="const s=require(process.argv[1]);console.log(JSON.stringify(s.serviceLaunchConfiguration({kind:'api'},{projectRoot:'TEST_ONLY',agentPort:8767},'TEST_ONLY')))"
        result=subprocess.run([node,'-e',code,str(ROOT/'desktop/src/services.cjs')],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8'))
        actual=json.loads(result.stdout)
        self.assertEqual(actual['args'][:5],['-B','-X','utf8=0','-m','backend.server'])
        self.assertEqual(actual['args'][-2:],['--agent-port','8767'])

    def test_api_streams_are_utf8_without_mutating_environment(self):
        code="""import os,sys,json
from backend.process_text import configure_api_streams
before=dict(os.environ);configure_api_streams()
print(json.dumps({'text':'中文诊断／العربية','stdout':sys.stdout.encoding,'stderr':sys.stderr.encoding,'environmentChanged':before!=dict(os.environ)},ensure_ascii=False))
sys.stderr.write('中文错误／العربية')
"""
        result=subprocess.run([sys.executable,'-X','utf8=0','-B','-c',code],cwd=ROOT,
                              capture_output=True,timeout=15,env={**os.environ,'PYTHONUTF8':'1'})
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
        value=json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(value['text'],'中文诊断／العربية')
        self.assertEqual(value['stdout'],'utf-8');self.assertEqual(value['stderr'],'utf-8')
        self.assertFalse(value['environmentChanged'])
        self.assertEqual(result.stderr.decode('utf-8'),'中文错误／العربية')

    def test_known_planner_rejection_has_readable_message_and_original_diagnostic(self):
        from backend.process_text import api_error_payload
        from backend.common import APIError
        raw='Native IRB planning failed: Traceback (most recent call last):\n  File "D:/中文/plan_worker.py"\nValueError: Native IRB sequence exceeds fixed control window\n'
        body=api_error_payload(APIError('SCIENCE_IRB_PLAN_REJECTED',raw))
        self.assertIn('79.7',body['message']);self.assertIn('控制时窗',body['message'])
        self.assertEqual(body['technicalMessage'],raw)
        unknown='Native IRB planning failed: 未知中文原因'
        self.assertEqual(api_error_payload(APIError('SCIENCE_IRB_PLAN_REJECTED',unknown)),
                         {'code':'SCIENCE_IRB_PLAN_REJECTED','message':unknown})
        self.assertEqual(api_error_payload(APIError('OTHER',raw)),{'code':'OTHER','message':raw})

    def test_http_preserves_chinese_diagnostic_and_rejected_command_has_no_effect(self):
        import threading
        import urllib.request
        import urllib.error
        from unittest.mock import patch
        from backend.common import APIError
        from backend.server import Server
        from tests.test_controller import make_fixture
        with tempfile.TemporaryDirectory(prefix='TEST_ONLY_encoding_http_') as directory:
            controller,adapter=make_fixture(Path(directory))
            server=Server(('127.0.0.1',0),controller)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                wf=controller.create({'name':'中文隔离草稿','mode':'real','policy':'strict','resourceIds':['Q11']})
                before=controller.get(wf['id'])
                raw='Native IRB planning failed: Traceback (most recent call last):\n  File "D:/中文/plan_worker.py"\nValueError: Native IRB sequence exceeds fixed control window\n'
                base='http://127.0.0.1:'+str(server.server_address[1])
                request=urllib.request.Request(base+'/api/workflows/'+wf['id']+'/nodes/'+wf['nodes'][0]['id']+'/parameters',
                    method='PATCH',data=json.dumps({'operationId':'TEST_ONLY_REJECT','expectedRevision':wf['revision'],'parameters':{'readout_hz':7.01e9}}).encode(),
                    headers={'Origin':base,'Content-Type':'application/json','X-Qcal-Token':server.session_token})
                with patch.object(controller,'_validate_plan',side_effect=APIError('SCIENCE_IRB_PLAN_REJECTED',raw)):
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request,timeout=5)
                self.assertEqual(error.exception.code,400)
                body=json.loads(error.exception.read().decode('utf-8'))['error']
                self.assertIn('控制时窗',body['message']);self.assertEqual(body['technicalMessage'],raw)
                self.assertEqual(controller.get(wf['id']),before);self.assertEqual(adapter.calls,[])
                with controller.store.connect() as rt:
                    for table in ('platform_attempts','platform_effects','platform_leases','platform_commands'):
                        self.assertEqual(rt.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
            finally:server.shutdown();server.server_close();thread.join(2);controller.close()

    @unittest.skipUnless(os.name=='nt','Windows native pipe encoding contract')
    def test_frozen_native_planner_success_and_failure_under_launcher_mode(self):
        mode=re.search(r"@\('-B','-X','([^']+)','-m','backend.server'",(ROOT/'scripts/start.ps1').read_text('utf-8')).group(1)
        code=r'''import hashlib,json,sys,threading
from pathlib import Path
from backend.process_text import configure_api_streams
configure_api_streams()
from adapters.science_v3_irb.client import plan_native
from tests.test_science_v3_irb import CampaignContracts
root=Path.cwd();profile=json.loads((root/'adapters/arclight_profile_v3.json').read_text('utf-8'))
p={**CampaignContracts().parameters(),'pulse_length_s':342.5e-9,'pulse_sigma_s':171.25e-9,
   'drive_length_s':342.5e-9,'shots':2000,'acquisitions_per_point':4,'rb_master_seed':20261126}
errors=[];threading.excepthook=lambda args:errors.append(str(args.exc_value))
try:plan_native(root,profile['python'],p)
except Exception as exc:failure={'type':type(exc).__name__,'message':str(exc)}
else:raise AssertionError('Actual over-window campaign must reject')
plan=plan_native(root,profile['python'],{**p,'rb_lengths':[1,4,16,32,64]})
print(json.dumps({'failure':failure,'threadErrors':errors,'rows':len(plan['rows']),'campaignSha256':plan['sha256'],'utf8Mode':sys.flags.utf8_mode},ensure_ascii=False))
'''
        result=subprocess.run([sys.executable,'-X',mode,'-B','-c',code],cwd=ROOT,
                              capture_output=True,timeout=30,env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'})
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
        value=json.loads(result.stdout.decode('utf-8'))
        self.assertEqual(value['threadErrors'],[])
        self.assertEqual(value['failure']['type'],'ValueError')
        self.assertIn('Native IRB sequence exceeds fixed control window',value['failure']['message'])
        self.assertNotIn('NoneType',value['failure']['message'])
        self.assertEqual(value['rows'],416);self.assertEqual(value['utf8Mode'],0)
        # Valid plans have identical content under the old API mode. Changing
        # the parent decoder must not rewrite pulse parameters or receipts.
        baseline=subprocess.run([sys.executable,'-X','utf8','-B','-c',code],cwd=ROOT,
                                capture_output=True,timeout=30,env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'})
        self.assertEqual(baseline.returncode,0,baseline.stderr.decode('utf-8',errors='replace'))
        old=json.loads(baseline.stdout.decode('utf-8'))
        self.assertEqual(old['campaignSha256'],value['campaignSha256'])
        self.assertEqual(old['rows'],value['rows'])


if __name__=='__main__':unittest.main()
