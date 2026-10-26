#!/usr/bin/env python3
"""Independent cold-build acceptance. Run only after the owner freezes product code."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo('Asia/Shanghai')


def stamp():
    return datetime.now(ZONE).isoformat(timespec='seconds')


def emit(event, **data):
    print(json.dumps({'time': stamp(), 'event': event, **data}, ensure_ascii=False), flush=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files():
    files = []
    for name in ['package.json', 'package-lock.json', 'index.html', 'README.md', 'BACKEND_INTEGRATION.md']:
        files.append(ROOT / name)
    files.extend(ROOT.glob('tsconfig*.json'))
    files.extend(ROOT.glob('vite.config.*'))
    for folder in ['src', 'scripts']:
        files.extend(path for path in (ROOT / folder).rglob('*') if path.is_file() and path.suffix != '.tsbuildinfo')
    files.extend((ROOT / 'qa').glob('*.test.ts'))
    return sorted(set(files))


def manifest(paths, root):
    return {str(path.relative_to(root)): {'sha256': digest(path), 'bytes': path.stat().st_size} for path in paths}


def compare(left, right):
    return {
        'missing': sorted(set(left) - set(right)),
        'extra': sorted(set(right) - set(left)),
        'changed': sorted(name for name in set(left) & set(right) if left[name] != right[name]),
    }


def matches(diff):
    return not any(diff.values())


def main():
    started = time.monotonic()
    output = ROOT / 'qa' / 'clean-validation.json'
    temp_root = Path(tempfile.mkdtemp(prefix='qcal-clean-validation-'))
    logs = temp_root / 'logs'
    logs.mkdir()
    inputs = source_files()
    frozen = manifest(inputs, ROOT)
    result = {
        'schema_version': 1,
        'task': 'QCal frontend independent clean build and simulator acceptance',
        'started_at': stamp(), 'project_directory': str(ROOT), 'temporary_directory': str(temp_root),
        'orchestrator_pid': os.getpid(),
        'runtime': {'node': subprocess.check_output(['node', '--version'], text=True).strip(), 'npm': subprocess.check_output(['npm', '--version'], text=True).strip()},
        'source_file_count': len(frozen), 'source_manifest': frozen, 'runs': [],
        'decision': 'RUNNING',
    }

    def persist():
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')

    def execute(run, label, args):
        log = logs / f'{run["name"]}-{label}.log'
        began = time.monotonic()
        with log.open('w') as stream:
            process = subprocess.Popen(args, cwd=run['directory'], stdout=stream, stderr=subprocess.STDOUT)
            record = {'label': label, 'command': args, 'cwd': run['directory'], 'pid': process.pid, 'log': str(log), 'started_at': stamp()}
            run['commands'].append(record)
            persist()
            emit('command_started', run=run['name'], **record)
            record['exit_code'] = process.wait()
        record['elapsed_seconds'] = round(time.monotonic() - began, 3)
        record['finished_at'] = stamp()
        if label == 'test':
            text = log.read_text(errors='replace')
            record['test_counts'] = {key: int(match.group(1)) if (match := re.search(r'(?:#|ℹ)\s+' + key + r'\s+(\d+)', text)) else None for key in ['tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo']}
        persist()
        emit('command_finished', run=run['name'], **record)
        return record

    try:
        emit('validation_started', pid=os.getpid(), root=str(ROOT), source_files=len(frozen), temporary_directory=str(temp_root))
        persist()
        for name in ['cold-a', 'cold-b', 'workspace-repeat']:
            directory = ROOT if name == 'workspace-repeat' else temp_root / name
            if directory != ROOT:
                directory.mkdir()
                for path in inputs:
                    destination = directory / path.relative_to(ROOT)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
                copy_diff = compare(frozen, manifest([directory / path.relative_to(ROOT) for path in inputs], directory))
                if not matches(copy_diff):
                    raise RuntimeError(f'Source copy mismatch in {name}: {copy_diff}')
            run = {'name': name, 'directory': str(directory), 'started_at': stamp(), 'commands': []}
            result['runs'].append(run)
            run_started = time.monotonic()
            if name != 'workspace-repeat':
                install = execute(run, 'install-ignore-scripts', ['npm', 'ci', '--ignore-scripts'])
                if install['exit_code'] != 0:
                    install['attempt_class'] = 'infra_invalid_before_tests'
                    standard = execute(run, 'install-standard-fallback', ['npm', 'ci'])
                    if standard['exit_code'] != 0:
                        raise RuntimeError(f'Installation failed in {name}; see install logs.')
            build = execute(run, 'build', ['npm', 'run', 'build'])
            if build['exit_code'] != 0:
                build_text = Path(build['log']).read_text(errors='replace')
                missing_binary = re.search(r'Cannot find module[^\n]*@esbuild|The package[^\n]*@esbuild[^\n]*could not be found|esbuild for another platform|Could not find[^\n]*binary|Failed to validate[^\n]*binary', build_text, re.I)
                if name != 'workspace-repeat' and missing_binary:
                    build['attempt_class'] = 'infra_invalid_before_tests'
                    standard = execute(run, 'install-standard-after-build-failure', ['npm', 'ci'])
                    if standard['exit_code'] == 0:
                        build = execute(run, 'build-after-standard-install', ['npm', 'run', 'build'])
                if build['exit_code'] != 0:
                    raise RuntimeError(f'Build failed in {name}; see build logs.')
            test = execute(run, 'test', ['npm', 'test'])
            test['attempt_class'] = 'scoreable_test_result'
            counts = test.get('test_counts', {})
            if test['exit_code'] != 0 or not counts.get('tests') or counts.get('fail') != 0:
                raise RuntimeError(f'Tests failed or test counts were not independently observable in {name}.')
            built = sorted(path for path in (directory / 'dist').rglob('*') if path.is_file())
            run['dist_manifest'] = manifest(built, directory / 'dist')
            run['dist_file_count'] = len(built)
            run['dist_total_bytes'] = sum(item['bytes'] for item in run['dist_manifest'].values())
            run['elapsed_seconds'] = round(time.monotonic() - run_started, 3)
            run['finished_at'] = stamp()
            run['decision'] = 'PASS'
            persist()
            emit('run_finished', name=name, directory=str(directory), elapsed_seconds=run['elapsed_seconds'], test_counts=counts, dist_file_count=len(built))

        runs = {run['name']: run for run in result['runs']}
        result['hashdiff'] = {
            'cold-a_vs_cold-b': compare(runs['cold-a']['dist_manifest'], runs['cold-b']['dist_manifest']),
            'cold-a_vs_workspace-repeat': compare(runs['cold-a']['dist_manifest'], runs['workspace-repeat']['dist_manifest']),
        }
        result['source_hashdiff'] = compare(frozen, manifest(source_files(), ROOT))
        result['decision'] = 'PASS' if all(matches(diff) for diff in result['hashdiff'].values()) and matches(result['source_hashdiff']) else 'FAIL'
        if result['decision'] != 'PASS':
            result['failure'] = 'Frozen source changed or independently built artifacts differ.'
    except Exception as error:
        result['decision'] = 'FAIL'
        result['failure'] = str(error)
    finally:
        result['finished_at'] = stamp()
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        persist()
        emit('validation_finished', decision=result['decision'], elapsed_seconds=result['elapsed_seconds'], output=str(output), failure=result.get('failure'))
    return 0 if result['decision'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
