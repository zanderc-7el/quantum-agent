"""Run the real launcher in PS5/pwsh with all process/network effects replaced."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENGINES = [path for name in ('powershell.exe', 'pwsh.exe') if (path := shutil.which(name))]


@unittest.skipUnless(ENGINES, 'PowerShell required')
class StartTimestampContracts(unittest.TestCase):
    def run_case(self, engine, kind, *, legacy_console=False):
        with tempfile.TemporaryDirectory(prefix='qcal-start-time-offline-') as directory:
            root = Path(directory)
            (root/'scripts').mkdir()
            (root/'logs').mkdir()
            shutil.copy2(ROOT/'scripts/start.ps1', root/'scripts/start.ps1')
            (root/'logs/owned-processes.json').write_text(json.dumps([{
                'pid': 424242, 'kind': 'api', 'port': 8765,
                'startedAt': 'invalid' if kind == 'invalid' else '2026-11-26T03:35:12.1234567+08:00'}]), encoding='utf-8')
            prefix = "$expectedRoot='" + str(root).replace("'", "''") + "';$timestampKind='" + kind + "'\n"
            if legacy_console:prefix += '[Console]::OutputEncoding=[Text.Encoding]::GetEncoding(936)\n'
            script = r'''
# Explicit pipe encoding is independent of the inherited Windows console CP.
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$OutputEncoding=[Console]::OutputEncoding
$ErrorActionPreference='Stop'
[Threading.Thread]::CurrentThread.CurrentCulture=[Globalization.CultureInfo]::GetCultureInfo('ar-SA')
$global:offlineProcessStarts=0
$global:offlineHealthCalls=0
$global:offlineParsedType=''
$global:offlineOwnedWritten=$false
function Start-Process { $global:offlineProcessStarts++; throw 'No processes may be started in this test' }
function Stop-Process { throw 'No process termination in this test' }
function Get-NetTCPConnection { throw 'No port queries in this test' }
function Get-Process {
    param($Id,$ErrorAction)
    if($Id -ne 424242){throw 'Unexpected process identity'}
    return [pscustomobject]@{Id=424242;StartTime=[DateTimeOffset]::Parse('2026-11-25T19:35:12.1234567Z',[Globalization.CultureInfo]::InvariantCulture).UtcDateTime}
}
function ConvertFrom-Json {
    param([Parameter(ValueFromPipeline=$true)]$InputObject)
    process {
        $value=Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $InputObject
        foreach($record in @($value)) {
            if($timestampKind -eq 'datetime'){$record.startedAt=[DateTime]::Parse('2026-11-25T19:35:12.1234567Z',[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind)}
            if($timestampKind -eq 'offset'){$record.startedAt=[DateTimeOffset]::Parse('2026-11-26T03:35:12.1234567+08:00',[Globalization.CultureInfo]::InvariantCulture)}
        }
        $global:offlineParsedType=$value.startedAt.GetType().FullName
        return $value
    }
}
function Set-Content {
    param($LiteralPath,$Encoding,[Parameter(ValueFromPipeline=$true)]$Value)
    process {$global:offlineOwnedWritten=$true;$Value | Microsoft.PowerShell.Management\Set-Content -LiteralPath $LiteralPath -Encoding $Encoding}
}
function Invoke-RestMethod {
    param($Uri,$TimeoutSec)
    $global:offlineHealthCalls++
    if($Uri -like '*8766/health'){return [pscustomobject]@{serviceId='qcal-brain';projectRoot=$expectedRoot}}
    if($Uri -like '*8765/api/health'){return [pscustomobject]@{projectId='qcal-local-f8-platform';projectRoot=$expectedRoot;agentPort=8766}}
    throw 'No other network request is allowed'
}
$failed=$false;$failure=''
try { & (Join-Path $expectedRoot 'scripts/start.ps1') | Out-Null } catch {$failed=$true;$failure=$_.Exception.Message}
$saved=Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject (Get-Content -LiteralPath (Join-Path $expectedRoot 'logs/owned-processes.json') -Raw)
@{failed=$failed;failure=$failure;starts=$global:offlineProcessStarts;healthCalls=$global:offlineHealthCalls;parsedType=$global:offlineParsedType;pid=$saved.pid;ownedWritten=$global:offlineOwnedWritten;version=$PSVersionTable.PSVersion.ToString();encodingProbe='时间戳／العربية'}|ConvertTo-Json -Compress
'''
            result = subprocess.run([engine, '-NoProfile', '-NonInteractive', '-Command', prefix+script],
                                    capture_output=True, timeout=20,
                                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            # Decode in the test thread, so an unexpected encoding failure is
            # reported here instead of silently losing a pipe reader's value.
            stdout=result.stdout.decode('utf-8');stderr=result.stderr.decode('utf-8')
            self.assertEqual(result.returncode, 0, stderr)
            value = json.loads(stdout)
            self.assertEqual(value['starts'], 0)
            return value

    def test_localized_diagnostics_survive_legacy_console_encoding(self):
        for engine in ENGINES:
            with self.subTest(engine=engine):
                result=self.run_case(engine,'invalid',legacy_console=True)
                self.assertTrue(result['failed'])
                self.assertEqual(result['encodingProbe'],'时间戳／العربية')
                self.assertIsInstance(result['failure'],str)
                self.assertTrue(result['failure'])
                self.assertEqual(result['healthCalls'],0)

    def test_iso_json_and_typed_timestamps_keep_exact_owned_process_under_non_gregorian_culture(self):
        for engine in ENGINES:
            for kind in ('json', 'datetime', 'offset'):
                with self.subTest(engine=engine, kind=kind):
                    result = self.run_case(engine, kind)
                    self.assertFalse(result['failed'], result)
                    self.assertEqual(result['pid'], 424242)
                    self.assertEqual(result['healthCalls'], 3)
                    self.assertTrue(result['ownedWritten'], result)

    def test_invalid_timestamp_fails_before_any_service_health_or_start(self):
        for engine in ENGINES:
            with self.subTest(engine=engine):
                result = self.run_case(engine, 'invalid')
                self.assertTrue(result['failed'])
                self.assertEqual(result['healthCalls'], 0)
