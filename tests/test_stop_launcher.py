"""Exercise the real PowerShell script with fake HTTP; never contact a service."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe')


@unittest.skipUnless(POWERSHELL, 'Windows PowerShell required')
class StopLauncher(unittest.TestCase):
    def run_script(self, scenario):
        # Literal paths are single quoted; no shell interpolation or live HTTP.
        script = str(ROOT / 'scripts' / 'stop.ps1').replace("'", "''")
        root = str(ROOT).replace("'", "''")
        program = r'''
$ErrorActionPreference='Stop'
$global:offlineStopCalls=[System.Collections.Generic.List[string]]::new()
$global:offlineProcessCalls=[System.Collections.Generic.List[string]]::new()
function Stop-Process { $global:offlineProcessCalls.Add('Stop-Process'); throw 'Process termination must never be requested' }
function Get-Process { $global:offlineProcessCalls.Add('Get-Process'); throw 'Process enumeration must never be requested' }
function Invoke-RestMethod {
    param($Uri,$Method,$Headers,$ContentType,$Body,$TimeoutSec)
    $path=([uri]$Uri).AbsolutePath
    $global:offlineStopCalls.Add($path)
    if($path -eq '/api/health') {
        return @{projectId='qcal-local-f8-platform';projectRoot=$(if($scenario -eq 'foreign'){'C:\Foreign'}else{$expectedRoot})}
    }
    if($path -eq '/api/session'){return @{token='FAKE_OFFLINE_TOKEN'}}
    if($path -eq '/api/system/shutdown') {
        if($Headers['X-Qcal-Token'] -ne 'FAKE_OFFLINE_TOKEN' -or $Method -ne 'Post' -or $Body -ne '{}'){throw 'Bad checkpoint request'}
        if($scenario -eq 'active'){throw 'SHUTDOWN_CHECKPOINT_REQUIRED'}
        if($scenario -eq 'unconfirmed'){return @{status='unknown'}}
        return @{status='stopping_at_checkpoint'}
    }
    throw 'Unexpected network request'
}
$failed=$false
$failure=''
try { & $scriptPath | Out-Null } catch { $failed=$true; $failure=$_.Exception.Message }
@{failed=$failed;failure=$failure;calls=@($global:offlineStopCalls);processCalls=@($global:offlineProcessCalls)}|ConvertTo-Json -Compress
'''
        prefix = f"$expectedRoot='{root}'; $scriptPath='{script}'; $scenario='{scenario}'\n"
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', prefix + program],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['processCalls'], [])
        return payload

    def test_active_and_unconfirmed_shutdown_fail_without_terminating_services(self):
        for scenario in ('active', 'unconfirmed'):
            with self.subTest(scenario=scenario):
                result = self.run_script(scenario)
                self.assertTrue(result['failed'])
                self.assertEqual(result['calls'], ['/api/health', '/api/session', '/api/system/shutdown'])

    def test_foreign_project_cannot_request_shutdown(self):
        result = self.run_script('foreign')
        self.assertTrue(result['failed'])
        self.assertEqual(result['calls'], ['/api/health'])

    def test_accepted_checkpoint_uses_only_graceful_api(self):
        result = self.run_script('accepted')
        self.assertFalse(result['failed'], result['failure'])
        self.assertEqual(result['calls'], ['/api/health', '/api/session', '/api/system/shutdown'])
