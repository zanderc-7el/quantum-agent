"""Exercise CheckOnly with inert files. Never launch a desktop or local service."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE=Path(__file__).resolve().parents[1]/'scripts/start-desktop.ps1'
POWERSHELL=shutil.which('powershell.exe')


@unittest.skipUnless(os.name=='nt' and POWERSHELL,'Windows PowerShell required')
class DesktopReleaseLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'scripts').mkdir(); (self.root/'desktop').mkdir()
        shutil.copy2(SOURCE,self.root/'scripts/start-desktop.ps1')
        self.fake=self.root/'desktop/validated/QuantumControl.exe'
        self.fake.parent.mkdir();self.fake.write_bytes(b'inert check-only fixture, not executable')
        self.release={'schema':1,'version':'0.1.2','executable':'desktop/validated/QuantumControl.exe',
                      'sha256':hashlib.sha256(self.fake.read_bytes()).hexdigest()}

    def tearDown(self):
        self.temp.cleanup()

    def check(self,release=True,*,legacy_console=False):
        if release:(self.root/'desktop/release.json').write_text(json.dumps(self.release),encoding='utf-8')
        script=str(self.root/'scripts/start-desktop.ps1').replace("'","''")
        prefix='[Console]::OutputEncoding=[Text.Encoding]::GetEncoding(936);' if legacy_console else ''
        prefix+='[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$OutputEncoding=[Console]::OutputEncoding;'
        if legacy_console:prefix+='[Console]::Error.WriteLine("时间戳／العربية");'
        result=subprocess.run([POWERSHELL,'-NoProfile','-NonInteractive','-Command',
            prefix+"& '"+script+"' -CheckOnly | ConvertTo-Json -Compress"],capture_output=True,timeout=10,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        result.stdout=result.stdout.decode('utf-8');result.stderr=result.stderr.decode('utf-8')
        return result

    def test_valid_release_check_does_not_launch(self):
        result=self.check();self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout);self.assertFalse(data['Started'])
        self.assertTrue(data['ReleaseHashVerified']);self.assertEqual(data['Sha256'],self.release['sha256'])
        self.assertEqual(Path(data['Executable']),self.fake)

    def test_missing_release_keeps_legacy_default_without_starting(self):
        old=self.root/'desktop/dist/win-unpacked/QuantumControl.exe';old.parent.mkdir(parents=True);shutil.copy2(self.fake,old)
        result=self.check(False);self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout);self.assertEqual(data['Source'],'legacy-default');self.assertFalse(data['Started'])

    def test_invalid_hash_or_escape_throws_without_ui(self):
        for patch in [{'sha256':'0'*64},{'executable':'../outside.exe'},
                      {'executable':str(self.fake)},{'sha256':'bad'}]:
            with self.subTest(patch=patch):
                original=dict(self.release);self.release.update(patch)
                result=self.check();self.assertNotEqual(result.returncode,0)
                self.assertNotIn('Started',result.stdout);self.release=original

    def test_failure_diagnostics_are_not_lost_under_legacy_console_encoding(self):
        self.release['sha256']='bad'
        result=self.check(legacy_console=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIsInstance(result.stderr,str)
        self.assertIn('时间戳／العربية',result.stderr)


if __name__=='__main__':unittest.main()
