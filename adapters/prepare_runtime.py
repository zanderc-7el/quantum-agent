"""Copy an inspected isolated F8 runner; never import or start it."""
import ast
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path('D:/总体-8号机测试/20261117_Agent_F8_Q11_Q18复现')
DEST = ROOT / 'adapters/f8_runtime'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_progress(text):
    changes = {
        'from datetime import datetime,timezone': 'from datetime import datetime,timezone\nfrom progress import SweepProgress',
        "for job in plan['jobs']:": "for job_index,job in enumerate(plan['jobs'],start=1):",
        "async def before_set(**kw):": "progress_tracker=SweepProgress(expr['sweep_setting'],native_repeats=int(job.get('native_repeats',1)),job_index=job_index,job_count=len(plan['jobs']))\n            async def before_set(**kw):",
        "async def after_get(**kw):emit('row_acquired',**current)": "async def after_get(**kw):emit('row_acquired',**current,**progress_tracker.acquired())",
        "emit('job_start',job=label,kind=kind,settings=job)": "emit('job_start',job=label,kind=kind,settings=job,**progress_tracker.state())",
        "rec=emit('job_complete',job=label,files=[str(x) for x in rd.id_list],elapsed_s=time.monotonic()-started)": "rec=emit('job_complete',job=label,files=[str(x) for x in rd.id_list],elapsed_s=time.monotonic()-started,**progress_tracker.complete())",
    }
    for old,new in changes.items():
        if text.count(old)!=1:
            raise ValueError('Progress patch target changed: '+old)
        text=text.replace(old,new)
    ast.parse(text)
    return text


def prepare():
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / 'F8_copy').mkdir(exist_ok=True)
    manifest = []
    (DEST / 'native_math').mkdir(exist_ok=True)
    for name in ['fitclass.py', 'function.py']:
        src = Path('D:/coding_tgqos_test/MeasModule/QOS/Tools/Fit') / name
        dst = DEST / 'native_math' / name
        shutil.copyfile(src, dst)
        manifest.append({'source': str(src), 'path': str(dst.relative_to(ROOT)), 'sha256': digest(dst)})
    for name in ['config_TGQ-TEST-F8.json', 'filedict_TGQ-TEST-F8_Agent.json', 'workspace_adc.py']:
        src, dst = SOURCE / 'F8_copy' / name, DEST / 'F8_copy' / name
        shutil.copyfile(src, dst)
        manifest.append({'source': str(src), 'path': str(dst.relative_to(ROOT)), 'sha256': digest(dst)})
    shutil.copyfile(SOURCE / 'native_rb_pairing.py', DEST / 'native_rb_pairing.py')
    manifest.append({'source': str(SOURCE / 'native_rb_pairing.py'), 'path': str((DEST / 'native_rb_pairing.py').relative_to(ROOT)), 'sha256': digest(DEST / 'native_rb_pairing.py')})
    original = SOURCE / 'run_f8_native_v5.py'
    text = original.read_text(encoding='utf-8')
    replacements = {
        "ROOT=Path(__file__).resolve().parent": "ROOT=Path(os.environ['QCAL_F8_RUN_DIR']).resolve()",
        "BASE=Path('D:/coding_tgqos_test')": "BASE=Path(os.environ['QCAL_F8_BASE']).resolve()",
        "LOCK=ROOT.parent/'ADC_Agent.lock'": "LOCK=Path(os.environ['QCAL_F8_HARDWARE_LOCK'])",
        "await start(workspace);um=QOS_WORKSPACE['um'];station=um.ins_pool['CY_Station1'];dev=station.handle": "await start(workspace);um=QOS_WORKSPACE['um'];station=um.ins_pool['CY_Station1'];dev=station.handle\n        dev.timeout=10000",
        "'t1','ramsey','echo','readout_cal','rb_iq','allxy']": "'t1','ramsey','echo','cpmg','readout_cal','rb_iq','allxy']",
        "elif kind=='readout_cal':": "elif kind=='cpmg':\n                expr=adc.Ramsey.CPMG(q,np.linspace(job['start_s'],job['stop_s'],job['points']),job.get('rotation_hz',0),N=job['pi_pulses'],tags=tags)\n            elif kind=='readout_cal':",
        "if kind in ['t1','ramsey','echo','readout_cal','rb_iq']:": "if kind in ['t1','ramsey','echo','cpmg','readout_cal','rb_iq']:",
        "async def before_set(**kw):current['row']=int(kw['idx'])": "async def before_set(**kw):\n                current['row']=int(kw['idx'])\n                if (ROOT/'STOP_ADC_AFTER_ROW').exists() or time.monotonic()-started>plan.get('max_seconds',3600):raise TimeoutError('Controlled row stop')",
    }
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise ValueError('Runner source changed; reinspect replacement: ' + old)
        text = text.replace(old, new)
    text = add_progress(text)
    ast.parse(text)
    runner = DEST / 'run_f8_worker.py'
    runner.write_text(text, encoding='utf-8')
    manifest.append({'source': str(original), 'sourceSha256': digest(original), 'path': str(runner.relative_to(ROOT)), 'sha256': digest(runner), 'changes': ['runtime output/base/lock via explicit environment', 'VISA 10 second request timeout', 'native CPMG mapping', 'cooperative cancellation before row set', 'actual sweep Cartesian-product row progress and explicitly equal-weight job progress']})
    manifest.append({'source': 'project-authored exact sweep progress', 'path': str((DEST/'progress.py').relative_to(ROOT)), 'sha256': digest(DEST/'progress.py')})
    (DEST / 'source_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    profile = {
        'version': 1, 'id': 'f8-native-cy8', 'enabled': False,
        'python': 'D:/Python312/python.exe', 'basePath': 'D:/coding_tgqos_test',
        'runner': 'adapters/f8_runtime/run_f8_worker.py', 'runnerSha256': digest(runner),
        'configurationDir': 'adapters/f8_runtime/F8_copy', 'sourceManifest': 'adapters/f8_runtime/source_manifest.json',
        'hardwareLock': 'D:/总体-8号机测试/ADC_Agent.lock', 'visaLibrary': 'C:/Windows/System32/visa64.dll',
        'instrument': {'id': 'CY_Station1', 'address': 'TCPIP::10.100.16.105::7110::SOCKET', 'xy': [0, 0], 'readout': [15, 0], 'adc': [14, 0], 'resourceGroup': 'CY8_SHARED_BUS1'},
        'shutdownReadbackVerified': False, 'cancelGraceSeconds': 20,
        'maximumRunSeconds': 3600, 'maximumTotalShots': 8000000,
        'resources': {
            'Q11': {'q': 'Q11', 'readout_hz': 7009160000.0, 'readout_amp': .75, 'drive_hz': 4862690000.0, 'drive_amp': .98, 'drive_length_s': 320e-9, 'half_pi_amp': .49, 'xy_delay_s': 100e-9, 'calibrationStatus': 'historical_seed_requires_current_verification'},
            'Q18': {'q': 'Q18', 'readout_hz': 7358766666.666667, 'readout_amp': .8, 'drive_hz': 4678120000.0, 'drive_amp': .98, 'drive_length_s': 488e-9, 'half_pi_amp': .49, 'xy_delay_s': 300e-9, 'calibrationStatus': 'historical_trial_requires_current_verification'}
        },
        'parameterBounds': {'readout_hz': [6.9e9, 7.5e9], 'readout_amp': [0, .8], 'drive_hz': [4e9, 5.5e9], 'drive_amp': [0, .98], 'drive_length_s': [4e-9, 5e-6], 'half_pi_amp': [0, .98], 'xy_delay_s': [0, 3e-6], 'shots': [1, 2000], 'capture_repeats': [1, 16], 'points': [2, 2001], 'start_s': [0, 100e-6], 'stop_s': [4e-9, 100e-6], 'span_hz': [1e3, 100e6], 'rotation_hz': [-2e6, 2e6], 'pi_pulses': [1, 32]},
        'capabilityNotes': ['Profile enabled is set by the single authorized hardware controller after ownership/connection handoff, not by a model.', 'No import or connection occurs during preflight.', 'Shutdown commands/zero waveforms do not prove unvalidated firmware state-query semantics.', 'Native same-duration half-amplitude XY/2; no historical short-half monkeypatch.', 'Q11/Q18 are ascending readout-frequency labels, not verified physical-layout identities.']
    }
    (ROOT / 'adapters/f8_profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
    return profile


if __name__ == '__main__':
    p = prepare()
    print(json.dumps({'runner': p['runner'], 'enabled': p['enabled'], 'hardwareConnected': False}))
