"""Import archived Q11/Q18 F8 evidence without importing instrument libraries."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

SOURCE = Path('D:/总体-8号机测试/20261117_Agent_F8_Q11_Q18复现')
METHODS = {
    's21': ('s01_readout_frequency', 'ro_resonator_survey', 'F8 S21_dds'),
    's21_power': ('s01_readout_frequency', 'ro_power_shift', 'F8 S21_dds_Power'),
    's21_ge': ('s05_readout_optimization', 'ro_s21_ge', 'F8 S21_dds_ge'),
    'spectrum': ('s02_qubit_frequency', 'sq_spectroscopy', 'F8 Spectrum_dds'),
    'rabi': ('s03_rabi_amplitude', 'sq_rabi_time', 'F8 TimeRabi（时长辅助方法）'),
    'power_rabi': ('s03_rabi_amplitude', 'sq_rabi_amplitude', 'F8 PowerRabi'),
    'allxy_diagnostic': ('s03_rabi_amplitude', 'sq_allxy', 'F8 AllXY 诊断'),
    'readout': ('s04_readout_fidelity', 'ro_iq01', 'F8 get_ReadoutM（样本内制备标签判别）'),
    'fidelity_scale': ('s05_readout_optimization', 'ro_optimize', 'F8 读出幅度扫描标签判别'),
    'snr_scale': ('s05_readout_optimization', 'ro_optimize', 'F8 SNR_probCarryScale'),
    'snr_freq': ('s05_readout_optimization', 'ro_optimize', 'F8 SNR_Freq'),
    't1': ('s06_t1', 'sq_t1', 'F8 T1'),
    'ramsey': ('s07_t2', 'sq_ramsey', 'F8 Ramsey（CPMG 的独立辅助方法）'),
    'echo': ('s07_t2', 'sq_echo', 'F8 Echo（不是默认 CPMG）'),
    'rb': ('s08_single_qubit_rb', 'sq_rb', 'F8 RB'),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _metrics(detail):
    metrics, units = {}, {}
    for key, value in detail.get('params', {}).items():
        if type(value) in (int, float) and math.isfinite(value):
            metrics[key] = value
            units[key] = 'us' if key in ('Tr', 'T1', 'T2', 'Tphi', 'pplen') else 'MHz' if key in ('Omega', 'Delta') else 'native'
    for key in ('r2', 'threshold', 'phi', 'rms', 'mean_snr'):
        value = detail.get(key)
        if type(value) in (int, float) and math.isfinite(value):
            metrics[key] = value
    matrix = detail.get('readout_matrix')
    if isinstance(matrix, list) and len(matrix) == 2 and all(len(row) == 2 for row in matrix):
        metrics.update(preparation_label_accuracy_0=matrix[0][0], preparation_label_accuracy_1=matrix[1][1], preparation_label_accuracy_mean=(matrix[0][0]+matrix[1][1])/2)
    for key in ('snrmean', 'fidelity_mean'):
        values = detail.get(key)
        if isinstance(values, list) and values and all(type(x) in (int, float) and math.isfinite(x) for x in values):
            metrics[key + '_max'] = max(values)
    return metrics, units


def import_history(project_root: Path, source: Path = SOURCE) -> dict:
    """Copy small evidence artifacts; register raw archives by path/hash only.

    Input files are historical artifacts even when their original producer called
    them ``new_measurement``. This importer never grants current calibration.
    """
    project_root, source = Path(project_root).resolve(), Path(source).resolve()
    destination = project_root / 'data' / 'imports'
    artifact_dir = destination / 'artifacts'
    artifact_dir.mkdir(parents=True, exist_ok=True)
    index_path = source / 'results/native_plot_index.json'
    index = read_json(index_path)
    artifacts, checks, records = {}, {}, []

    def register(entry, kind=None):
        path = Path(entry['path']).resolve()
        # Do not follow source-code references to original F8 or external roots.
        if not path.is_relative_to(source):
            raise ValueError('Historical artifact lies outside selected experiment archive')
        if not path.is_file():
            checks[str(path)] = {'path': str(path), 'status': 'missing', 'expectedSha256': entry.get('sha256')}
            return None
        digest = sha256(path)
        expected = entry.get('sha256')
        checks[str(path)] = {'path': str(path), 'sha256': digest, 'expectedSha256': expected, 'status': 'matched' if not expected or digest == expected else 'mismatch', 'bytes': path.stat().st_size}
        identifier = 'history-' + digest[:24]
        suffix = path.suffix.lower()
        kind = kind or ('image' if suffix in ('.png', '.jpg', '.jpeg') else 'json' if suffix == '.json' else 'raw')
        if kind in ('image', 'json', 'text'):
            copied = artifact_dir / (digest + suffix)
            if not copied.exists():
                shutil.copyfile(path, copied)
            local_path = copied
        else:
            local_path = path
        item = {'id': identifier, 'name': path.name, 'kind': kind, 'url': '/api/artifacts/' + identifier, 'sha256': digest, 'sourcePath': str(path), 'localPath': str(local_path)}
        artifacts[identifier] = item
        return item

    index_artifact = register({'path': str(index_path)}, 'json')
    for item in index['items']:
        if item['qubit'] not in ('Q11', 'Q18'):
            continue
        if item['kind'] not in METHODS:
            raise ValueError('Missing method mapping: ' + item['kind'])
        owner, procedure, label = METHODS[item['kind']]
        report_entry = item['analysis_report']
        report = read_json(Path(report_entry['path']))
        detail = next((x for x in report.get('items', []) if x.get('id') == item['id']), {})
        evidence = [item['figure'], report_entry, *item.get('qdat_sources', []), *item.get('completion_receipts', [])]
        registered = [x for x in (register(entry) for entry in evidence) if x]
        acquired_at = None
        for entry in item.get('completion_receipts', []):
            receipt = read_json(Path(entry['path']))
            acquired_at = receipt.get('utc', acquired_at)
        metrics, units = _metrics(detail)
        parameters = item.get('acquisition_configuration', {})
        mismatch = [checks[str(Path(e['path']).resolve())] for e in evidence if checks[str(Path(e['path']).resolve())]['status'] != 'matched']
        reasons = ['历史实测导入；不是本平台本次采集，也不自动成为当前有效校准。']
        if item.get('fit_status'):
            reasons.append('归档拟合状态：' + item['fit_status'] + '；尚未执行当前工作点独立复测。')
        if item.get('scope'):
            reasons.append(item['scope'])
        if detail.get('statistical_scope'):
            reasons.append(detail['statistical_scope'])
        if mismatch:
            reasons.append('部分来源缺失或哈希不符；保留档案，禁止升级为已验证。')
        result = {'dataKind': 'historical', 'sourceRunId': item['id'], 'acquiredAt': acquired_at, 'metrics': metrics, 'units': units,
                  'quality': {'status': 'unverified', 'reasons': reasons}, 'artifacts': registered, 'parameters': parameters,
                  'methodLabel': label, 'provenance': {'archive': str(source), 'indexArtifactId': index_artifact['id'], 'sourceFitStatus': item.get('fit_status'), 'hashVerification': mismatch or 'matched', 'freshAcquisition': False, 'currentCalibrationVerified': False}}
        records.append({'id': item['id'], 'resourceId': item['qubit'], 'ownerId': owner, 'procedureId': procedure,
                        'parameters': parameters, 'result': result, 'sourceFiles': [checks[str(Path(e['path']).resolve())] for e in evidence]})
    resources = [{'id': q, 'label': '八号机 ' + q, 'engineKey': q, 'kind': 'qubit', 'identityBasis': 'readout-frequency-order', 'physicalLayoutIdentityVerified': False, 'sharedResources': ['CY_Station1', 'B1', 'XY0']} for q in ('Q11', 'Q18')]
    value = {'version': 1, 'resources': resources, 'records': records, 'artifacts': list(artifacts.values()),
             'sourceManifest': list(checks.values()), 'mode': 'replay', 'methodMapping': METHODS}
    (destination / 'history.json').write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return value


if __name__ == '__main__':
    result = import_history(Path(__file__).resolve().parents[1])
    print(json.dumps({'records': len(result['records']), 'artifacts': len(result['artifacts']), 'mode': result['mode']}))
