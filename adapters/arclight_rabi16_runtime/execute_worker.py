"""Authenticate a Rabi-only extension, then run unchanged bounded native acquisition."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

BASE_MANIFEST = 'adapters/arclight_analysis_v3/source_manifest.json'
BASE_SHA = '96b3cf7cb560120f7a5f5babe01a637a99c680ac8b528a2ce26c43f3d2d83cfc'
MANIFEST = 'adapters/arclight_rabi16_runtime/source_manifest.json'
EXTRA = {BASE_MANIFEST, 'adapters/arclight_profile_rabi16_v1.json',
         'adapters/arclight_rabi16_adapter.py', 'adapters/arclight_rabi16_runtime/__init__.py',
         'adapters/arclight_rabi16_runtime/policy.py', 'adapters/arclight_rabi16_runtime/execute_worker.py'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_sources(root):
    root = Path(root).resolve()
    if sha(root / BASE_MANIFEST) != BASE_SHA:
        raise ValueError('Frozen v3 manifest changed')
    base = read(root / BASE_MANIFEST)
    rows = read(root / MANIFEST)
    expected = {r['path'] for r in base} | EXTRA
    if not isinstance(rows, list) or len(rows) != len(expected) or {r.get('path') for r in rows} != expected:
        raise ValueError('Rabi sampling source closure differs')
    original = {r['path']: r['sha256'] for r in base}
    checked = {}
    for row in rows:
        path = (root / row['path']).resolve()
        if not path.is_relative_to(root) or sha(path) != row['sha256']:
            raise ValueError('Rabi sampling source changed: ' + row['path'])
        if row['path'] in original and row['sha256'] != original[row['path']]:
            raise ValueError('Inherited native source identity changed')
        checked[row['path']] = row['sha256']
    return checked


def validate_admission(root, folder, plan):
    from adapters.arclight_rabi16_runtime import policy
    from adapters.arclight_rabi16_adapter import ArcLightRabi16Adapter
    root, folder = Path(root).resolve(), Path(folder).resolve()
    profile = policy.load_profile(root)
    intent = read(folder / 'adapter_intent.json')
    request = intent.get('request', {})
    policy.validate_binding(root, profile, request, executable=True)
    if (intent.get('operationId') != request.get('operationId')
            or intent.get('requestHash') != policy.intent_digest(request)
            or intent.get('profileHash') != policy.intent_digest({**profile, 'enabled': True})):
        raise ValueError('Rabi extension intent/profile identity differs')
    if not folder.is_relative_to(root / 'data/runs/arclight_operations') or folder.name != hashlib.sha256(request['operationId'].encode()).hexdigest():
        raise ValueError('Operation directory differs from the authorized operation')
    rebuilt = ArcLightRabi16Adapter(root, profile).build_plan(request)
    if policy.digest(plan) != policy.digest(rebuilt):
        raise ValueError('Rabi extension plan differs from the complete frozen request')
    return request


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def save(path, value):
    temporary = path.with_name(path.name + '.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def _sampling_evidence(folder, plan, completion):
    """Read actual per-block files; shared by construction and read-only authentication."""
    from adapters.arclight_rabi16_runtime import policy
    if completion.get('state') != 'completed' or completion.get('shutdown_ok') is not True:
        raise ValueError('Native acquisition/cleanup did not complete')
    if len(completion.get('folders', [])) != 1 or len(plan.get('jobs', [])) != 1:
        raise ValueError('Rabi sampling requires one native job')
    native = Path(completion['folders'][0]).resolve()
    folder = Path(folder).resolve()
    if not native.is_relative_to(folder / 'results'):
        raise ValueError('Native acquisition folder is outside this operation')
    job = read(native / 'job.json')
    if policy.digest(job) != policy.digest(plan['jobs'][0]):
        raise ValueError('Executed native job differs from the authorized plan')
    count, shots = job['acquisitions_per_point'], job['spec']['meta']['shots']
    expected_rows = plan['totalRows']
    status = read(native / 'status.json')
    if status.get('points_completed') != expected_rows or status.get('shutdown_ok') is not True:
        raise ValueError('Native completed rows/cleanup differs')
    records, files = [], []
    primary = {}
    for name in ('raw_scan.npz', 'axes.json', 'job.json', 'status.json'):
        path = native / name
        primary[name] = {'path': str(path.relative_to(folder)), 'sha256': sha(path)}
        files.append(str(path))
    if policy.digest(read(folder / 'plan.json')) != policy.digest(plan):
        raise ValueError('Archived operation plan differs')
    files.append(str(folder / 'plan.json'))
    for row in range(expected_rows):
        path = native / f'point_{row:04d}_acquisition_blocks.json'
        record = read(path)
        if (record.get('schema') != 'arclight_actual_repeated_iq_v1'
                or record.get('state') != 'complete' or record.get('point_index') != row
                or record.get('acquisitions_per_point') != count or record.get('shots_per_acquisition') != shots
                or record.get('blocks_completed') != count or record.get('effective_shots_completed') != count * shots
                or len(record.get('blocks', [])) != count or record.get('exact_duplicate_blocks_detected') is not False):
            raise ValueError('Actual native block count/identity differs at row ' + str(row))
        blocks = []
        for index, block in enumerate(record['blocks']):
            name = f'point_{row:04d}_block_{index:03d}.npz'
            raw = native / name
            if (block.get('point_index') != row or block.get('block_index') != index
                    or block.get('block_file') != name or block.get('shots_in_block') != shots
                    or block.get('shape') != [1, shots] or block.get('exact_duplicate_of_blocks') != []
                    or block.get('sha256') != sha(raw)):
                raise ValueError('Actual native block artifact differs')
            blocks.append({'index': index, 'name': name, 'sha256': block['sha256'],
                           'rawIqSha256': block['raw_iq_sha256'], 'shots': shots})
            files.append(str(raw))
        records.append({'row': row, 'manifest': path.name, 'manifestSha256': sha(path), 'blocks': blocks})
        files.append(str(path))
    total = expected_rows * count * shots
    if total != plan['estimated_total_shots']:
        raise ValueError('Actual sampling total differs from reserved plan')
    receipt = {'schema': 'qcal-native-rabi-blocks/1', 'policy': policy.POLICY,
               'operationId': plan['operationId'], 'executionBinding': plan['executionBinding'],
               'totalRows': expected_rows, 'captures': job['captures'], 'blocksPerPoint': count,
               'shotsPerBlock': shots, 'totalShots': total, 'rows': records, 'primaryArtifacts': primary,
               'meaning': 'Independent finite native ADC acquisitions; no software sample replication.'}
    return receipt, files


def sampling_receipt(folder, plan, completion):
    """Register counted native blocks only after acquisition and confirmed cleanup."""
    folder = Path(folder).resolve()
    receipt, files = _sampling_evidence(folder, plan, completion)
    path = folder / 'sampling_receipt.json'
    save(path, receipt)
    completion['files'] = list(dict.fromkeys(completion['files'] + files + [str(path), str(folder / 'completion.json')]))
    completion['samplingReceipt'] = {'schema': receipt['schema'], 'sha256': sha(path),
                                    **{k: receipt[k] for k in ('totalRows', 'blocksPerPoint', 'shotsPerBlock', 'totalShots')}}
    return receipt


def validate_sampling_receipt(folder, plan, completion, *, registered_artifacts=None):
    """Pure read-only evidence authentication; never creates or repairs a receipt.

    Backend callers pass their DB-frozen registered artifact metadata. Omitting
    it is useful only inside the native wrapper/adapter before DB registration.
    """
    from adapters.arclight_rabi16_runtime import policy
    folder = Path(folder).resolve()
    expected, files = _sampling_evidence(folder, plan, completion)
    path = folder / 'sampling_receipt.json'
    if policy.digest(read(path)) != policy.digest(expected):
        raise ValueError('Sampling receipt does not match actual native block evidence')
    summary = {'schema': expected['schema'], 'sha256': sha(path),
               **{k: expected[k] for k in ('totalRows', 'blocksPerPoint', 'shotsPerBlock', 'totalShots')}}
    if completion.get('samplingReceipt') != summary:
        raise ValueError('Completion sampling summary differs')
    files += [str(path), str(folder / 'completion.json')]
    frozen_files = completion.get('files')
    if not isinstance(frozen_files, list) or len(frozen_files) > policy.MAX_REGISTERED_ARTIFACTS:
        raise ValueError('Registered sampling artifact budget exceeded')
    declared = {str(Path(p).resolve()) for p in frozen_files}
    if any(str(Path(p).resolve()) not in declared for p in files):
        raise ValueError('Required sampling evidence is absent from completion files')
    if policy.digest(read(folder / 'completion.json')) != policy.digest(completion):
        raise ValueError('Archived completion differs from the source result')
    checked = {str(Path(p).resolve()): sha(p) for p in files}
    if registered_artifacts is not None:
        if not isinstance(registered_artifacts, list):
            raise ValueError('DB-frozen artifact metadata is required')
        by_name = {}
        for artifact in registered_artifacts:
            key = artifact.get('name')
            if key in by_name and by_name[key] != artifact.get('sha256'):
                raise ValueError('Ambiguous registered sampling artifact')
            by_name[key] = artifact.get('sha256')
        for name, expected_hash in checked.items():
            if by_name.get(Path(name).name) != expected_hash:
                raise ValueError('Sampling evidence is absent or differs from registered artifact: ' + Path(name).name)
    return {'status': 'valid', 'policy': policy.POLICY, 'receiptSha256': summary['sha256'],
            'totalShots': expected['totalShots'], 'checkedFiles': checked,
            'evidenceSha256': policy.digest(checked)}


def main():
    root = Path(__file__).resolve().parents[2]
    if len(sys.argv) != 2:
        raise ValueError('Only an explicit controller plan is supported')
    plan_path = Path(sys.argv[1]).resolve()
    folder = plan_path.parent
    if Path(os.environ.get('QCAL_ARCLIGHT_RUN_DIR', '')).resolve() != folder:
        raise ValueError('Worker operation directory differs')
    verify_sources(root)
    sys.path.insert(0, str(root))
    plan = read(plan_path)
    validate_admission(root, folder, plan)
    # No native or driver imports occur before complete source and admission checks.
    legacy = root / 'adapters/arclight_runtime'
    sys.path.insert(0, str(legacy))
    analysis = _module('platform_analysis', root / 'adapters/arclight_analysis_v3/platform_analysis.py')
    worker = _module('_qcal_rabi16_native_worker', legacy / 'platform_worker.py')
    if sys.modules.get('platform_analysis') is not analysis:
        raise ValueError('Frozen v3 analysis module binding changed')
    worker.main()
    completion = read(folder / 'completion.json')
    try:
        sampling_receipt(folder, plan, completion)
    except BaseException as exc:
        completion.update(state='failed_after_shutdown', error='Sampling receipt validation failed: ' + str(exc))
        save(folder / 'completion.json', completion)
        raise
    save(folder / 'completion.json', completion)


if __name__ == '__main__':
    main()
