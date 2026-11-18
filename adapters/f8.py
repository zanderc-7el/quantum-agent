"""F8 subprocess bridge. Import/preflight never import QOS or contact hardware.

Only the controller calls execute. Unknown process outcomes remain unknown;
reconcile never reruns a measurement and cancellation is not a hardware receipt.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime, timezone

from .history import sha256

PROCEDURES = {
    'ro_resonator_survey': ('s21', 's01_readout_frequency', 'meas.experiment.S21.S21_dds'),
    'ro_power_shift': ('s21_power', 's01_readout_frequency', 'meas.experiment.S21.S21_dds_Power'),
    'sq_spectroscopy': ('spectrum', 's02_qubit_frequency', 'meas.experiment.Spectrum.Spectrum_dds'),
    'sq_rabi_amplitude': ('power_rabi', 's03_rabi_amplitude', 'meas.experiment.Rabi.PowerRabi'),
    'sq_rabi_time': ('rabi', 's03_rabi_amplitude', 'meas.experiment.Rabi.TimeRabi'),
    'sq_rabi_frequency': ('rabi_freq', 's03_rabi_amplitude', 'meas.experiment.Rabi.TimeRabi_Freq'),
    'sq_allxy': ('allxy', 's03_rabi_amplitude', 'meas.experiment.AllXY.AllXY'),
    'ro_iq01': ('readout_cal', 's04_readout_fidelity', 'meas.experiment.readout.get_ReadoutM'),
    'ro_optimize': ('snr_amp', 's05_readout_optimization', 'meas.experiment.S21.SNR_probCarryScale'),
    'ro_optimize_frequency': ('snr_freq', 's05_readout_optimization', 'meas.experiment.S21.SNR_Freq'),
    'ro_s21_ge': ('s21_ge', 's05_readout_optimization', 'meas.experiment.S21.S21_dds_ge'),
    'sq_t1': ('t1', 's06_t1', 'meas.experiment.T1.T1'),
    'sq_ramsey': ('ramsey', 's07_t2', 'meas.experiment.Ramsey.Ramsey'),
    'sq_echo': ('echo', 's07_t2', 'meas.experiment.Ramsey.Echo'),
    'sq_cpmg': ('cpmg', 's07_t2', 'meas.experiment.Ramsey.CPMG'),
    'sq_rb': ('rb_suite', 's08_single_qubit_rb', 'meas.experiment.RB1Q.RB/RB_interleaved · full paired suite'),
    'sq_rb_branch': ('rb_iq', 's08_single_qubit_rb', 'meas.experiment.RB1Q.RB/RB_interleaved · internal branch'),
}
ALIASES = {'readout_frequency_hz': 'readout_hz', 'drive_frequency_hz': 'drive_hz', 'pi_duration_s': 'drive_length_s',
           'pi_amplitude': 'drive_amp', 'readout_amplitude': 'readout_amp', 'frequency_span_hz': 'span_hz', 'averages': 'shots',
           'pi_length_s': 'drive_length_s', 'readout.frequency_hz': 'readout_hz', 'qubit.f01_hz': 'drive_hz', 'qubit.pi_duration_s': 'drive_length_s'}
BOUNDED_FIELDS = {'readout_hz', 'readout_amp', 'drive_hz', 'drive_amp', 'drive_length_s', 'half_pi_amp', 'xy_delay_s', 'shots', 'capture_repeats', 'points', 'start_s', 'stop_s', 'span_hz', 'rotation_hz', 'pi_pulses'}
EXTRA_FIELDS = {'start_hz', 'stop_hz', 'amp_start', 'amp_stop', 'powers', 'amplitudes', 'frequencies_hz', 'shuffle_time_seed',
                'drive_amplitudes', 'calibration_file', 'frozen_plan_file', 'gate_lengths', 'random_seed', 'sequence_repeats',
                'sequence_index', 'rb_variant', 'native_repeats', 'scan_axis'}


class AdapterError(RuntimeError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details or {}


def _dump(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class F8Adapter:
    physical_hardware = True

    def __init__(self, project_root, profile):
        self.root = Path(project_root).resolve()
        self.profile = _load(profile) if isinstance(profile, (str, Path)) else json.loads(json.dumps(profile))
        self.operations = self.root / 'data/runs/f8_operations'

    def with_execution_enabled(self):
        """Controller-scoped authorization; never persist global enabled state."""
        return type(self)(self.root, {**self.profile, 'enabled': True})

    def hardware_ownership(self, refresh=False):
        from backend.hardware import connection_owners
        return connection_owners(self.profile)

    def validation_parameter_keys(self):
        from .rb_suite import CAL_KEYS
        return CAL_KEYS

    def normalize_candidate(self, procedure_id, candidate):
        result = dict(candidate)
        if procedure_id == 'sq_rabi_amplitude' and type(result.get('drive_amp')) in (int, float):
            result['half_pi_amp'] = result['drive_amp'] / 2
        return result

    def _local(self, value):
        path = Path(value)
        path = path.resolve() if path.is_absolute() else (self.root / path).resolve()
        if not path.is_relative_to(self.root):
            raise AdapterError('PATH_OUTSIDE_PROJECT', '可执行文件和配置必须位于新平台副本内')
        return path

    def capabilities(self):
        return {'id': self.profile.get('id'), 'dataKind': 'real', 'enabled': self.profile.get('enabled') is True,
                'isolatedProcess': True, 'directImportInApi': False, 'supportsReconcile': True,
                'supportsCancel': 'cooperative-row-boundary; deadline kill remains unknown', 'hardwareLease': 'controller-owned exclusive resource',
                'resources': list(self.profile.get('resources', {})), 'shutdownReadbackVerified': False,
                'procedures': {key: {'available': True, 'status': 'conditional' if key in ('sq_rb', 'sq_rb_branch', 'sq_allxy') else 'code_inspected',
                                    'kind': val[0], 'ownerId': val[1], 'entrypoint': val[2],
                                    'requires': ['current-run independently accepted readout and Rabi validationSnapshot; 30 paired seeds; new pre/post readout'] if key == 'sq_rb' else ['explicit frozen calibration and sequence plan'] if key == 'sq_rb_branch' else []}
                               for key, val in PROCEDURES.items()},
                'unavailable': {'double_qubit': 'No bound flux/coupler workflow', 'network_analyzer': 'This adapter is ADC/F8 pulse control, not the NA path'}}

    def preflight(self):
        checks = []
        def record(name, ok, detail):
            checks.append({'name': name, 'ok': bool(ok), 'detail': str(detail)})
        try:
            runner = self._local(self.profile['runner'])
            record('runner_hash', runner.is_file() and sha256(runner) == self.profile['runnerSha256'], runner)
            if runner.is_file():
                ast.parse(runner.read_text(encoding='utf-8'))
            record('runner_syntax', True, 'AST only; not executed')
            source_manifest = _load(self._local(self.profile['sourceManifest']))
            for item in source_manifest:
                path = self._local(item['path'])
                record('copy_hash:' + path.name, path.is_file() and sha256(path) == item['sha256'], path)
            interpreter = Path(self.profile['python']).resolve()
            record('python', interpreter.is_file(), interpreter)
            for package in ('numpy', 'pyvisa', 'matplotlib', 'scipy'):
                path = interpreter.parent / 'Lib/site-packages' / package
                record('python_package:' + package, path.exists(), path)
            base = Path(self.profile['basePath']).resolve()
            # Deliberately restricted to the pre-existing test copy.
            record('dependency_copy', str(base).replace('\\', '/').lower() == 'd:/coding_tgqos_test', base)
            for relative in ('MeasModule/QOS', 'MeasModule/Meas', 'meas/meas/experiment/Ramsey.py'):
                record('dependency:' + relative, (base / relative).exists(), base / relative)
            record('visa_library', Path(self.profile['visaLibrary']).is_file(), self.profile['visaLibrary'])
            config_dir = self._local(self.profile['configurationDir'])
            config = _load(config_dir / 'config_TGQ-TEST-F8.json')
            workspace_tree = ast.parse((config_dir / 'workspace_adc.py').read_text(encoding='utf-8'))
            workspace_assignment = next(n for n in workspace_tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'workspace' for t in n.targets))
            workspace = ast.literal_eval(workspace_assignment.value)
            tables = workspace['UManager']['um_table']
            instrument = self.profile['instrument']
            record('instrument_address', tables['addr_table'][instrument['id']][1] == instrument['address'], instrument['address'])
            for kind, group, key in [('xy','AWG','CY_XY1'),('readout','AWG','CY_R1'),('adc','ADC','CH1')]:
                channel = tables['ch_table'][group][key]
                record('routing:'+kind, channel[0] == instrument['id'] and list(channel[1]) == instrument[kind], channel)
            for key in self.profile['resources']:
                record('config_resource:' + key, key in config, 'Profile seeds are historical, require fresh measurement')
                record('profile_identity:' + key, self.profile['resources'][key]['q'] == key, 'Frequency-order labels must not be silently remapped')
            record('resources', set(self.profile['resources']) <= {'Q11', 'Q18'} and bool(self.profile['resources']), 'Only copied Q11/Q18 field mapping')
        except (OSError, ValueError, KeyError, AdapterError, StopIteration) as exc:
            record('preflight_error', False, exc)
        ok = all(c['ok'] for c in checks)
        return {'ready': ok, 'staticReady': ok, 'enabled': self.profile.get('enabled') is True,
                'hardwareConnected': False, 'ownershipVerified': False, 'checks': checks,
                'executionReady': ok and self.profile.get('enabled') is True,
                'notes': ['静态预检不读取设备；现场独占与接线由控制器的硬件交接状态记录。', '历史种子不是新的校准证据。']}

    def _validate_number(self, name, value, bounds=None, integer=False):
        if type(value) not in (float, int) or not math.isfinite(value) or (integer and type(value) is not int):
            raise AdapterError('INVALID_PARAMETER', name + ' 必须为有限数值' + ('整数' if integer else ''))
        bounds = bounds or self.profile['parameterBounds'].get(name)
        if bounds and not bounds[0] <= value <= bounds[1]:
            raise AdapterError('PARAMETER_OUT_OF_RANGE', name + ' 超出现场受限范围', {'bounds': bounds, 'value': value})

    def build_plan(self, request, output_dir=None):
        procedure = request.get('procedureId')
        if procedure not in PROCEDURES:
            raise AdapterError('UNSUPPORTED_PROCEDURE', 'F8 未绑定方法: ' + str(procedure))
        if procedure == 'sq_rb':
            from .rb_suite import build_suite_plan
            return build_suite_plan(self, request, output_dir)
        resource = request.get('resourceId')
        if resource not in self.profile['resources']:
            raise AdapterError('UNKNOWN_RESOURCE', '未确认的对象映射')
        kind = PROCEDURES[procedure][0]
        operation = str(request.get('operationId', ''))
        if not operation or len(operation) > 256:
            raise AdapterError('INVALID_OPERATION_ID', 'operationId 必须非空且不超过 256 字符')
        suffix = hashlib.sha256(operation.encode()).hexdigest()[:24]
        job = {k: v for k, v in self.profile['resources'][resource].items() if k != 'calibrationStatus'}
        job.update(id='job_' + suffix, kind=kind, shots=500, capture_repeats=1, drive_srate=4e9,
                   drive_shape='DC', use_compiler=False, prob_t0_s=80e-6, wait_s=.02)
        if kind in ('s21', 's21_power', 's21_ge', 'snr_freq'):
            job.update(span_hz=4e6, points=81)
        if kind == 'spectrum':
            job.update(start_hz=job['drive_hz']-10e6, stop_hz=job['drive_hz']+10e6, points=81)
        if kind in ('rabi', 'rabi_freq'):
            job.update(start_s=0, stop_s=2.4e-6, points=121)
        if kind in ('t1', 'ramsey', 'echo', 'cpmg'):
            job.update(start_s=0, stop_s=12e-6, points=81, rotation_hz=0 if kind in ('t1', 'cpmg') else 8e5)
        if kind == 'cpmg':
            job['pi_pulses'] = 4
        if kind == 'snr_amp':
            job.update(amp_start=.1, amp_stop=.8, points=15)
        if kind == 's21_power':
            job['powers'] = [.05, .1, .2, .3, .4, .5, .6, .7, .8]
        if kind == 'power_rabi':
            job['amplitudes'] = [round(i*.02, 10) for i in range(50)]
        if kind == 'rabi_freq':
            job['frequencies_hz'] = [job['drive_hz']-2e6, job['drive_hz'], job['drive_hz']+2e6]
        for key, value in request.get('parameters', {}).items():
            name = ALIASES.get(key, key)
            if name not in BOUNDED_FIELDS | EXTRA_FIELDS:
                raise AdapterError('UNKNOWN_PARAMETER', '不支持或不可由模型控制的参数: ' + name)
            if name in job and key != name and name in request.get('parameters', {}):
                raise AdapterError('DUPLICATE_PARAMETER', '同一参数重复使用别名')
            job[name] = value
        if job.pop('scan_axis', None) == 'frequency':
            if procedure != 'ro_optimize':
                raise AdapterError('INVALID_PARAMETER', 'scan_axis 只用于读出优化')
            kind = job['kind'] = 'snr_freq'
            job.setdefault('span_hz', 2e6)
            job.pop('amp_start', None); job.pop('amp_stop', None)
        for name in BOUNDED_FIELDS:
            if name in job:
                self._validate_number(name, job[name], integer=name in {'shots', 'capture_repeats', 'points', 'pi_pulses'})
        if 'start_s' in job and job['stop_s'] <= job['start_s']:
            raise AdapterError('INVALID_SCAN', '时间扫描上限必须大于下限')
        for field in ('start_hz', 'stop_hz'):
            if field in job:
                self._validate_number(field, job[field], self.profile['parameterBounds']['drive_hz'])
        if 'start_hz' in job and job['stop_hz'] <= job['start_hz']:
            raise AdapterError('INVALID_SCAN', '频率扫描上下限错误')
        if 'amp_start' in job:
            for field in ('amp_start', 'amp_stop'):
                self._validate_number(field, job[field], [0, .8])
            if job['amp_stop'] <= job['amp_start']:
                raise AdapterError('INVALID_SCAN', '读出幅度扫描上下限错误')
        for name, bounds in [('powers', [0, .8]), ('amplitudes', [0, .98]), ('drive_amplitudes', [0, .98]), ('frequencies_hz', self.profile['parameterBounds']['drive_hz'])]:
            if name in job:
                values = job[name]
                if not isinstance(values, list) or not 2 <= len(values) <= 201:
                    raise AdapterError('INVALID_SCAN', name + ' 必须为 2–201 点数组')
                for value in values:
                    self._validate_number(name, value, bounds)
        if 'native_repeats' in job:
            self._validate_number('native_repeats', job['native_repeats'], [1, 8], integer=True)
            if kind not in ('snr_amp', 'snr_freq') or job['capture_repeats'] != 1:
                raise AdapterError('INVALID_REPEAT', 'native_repeats 仅限 SNR 且 capture_repeats=1')
        if 'shuffle_time_seed' in job:
            self._validate_number('shuffle_time_seed', job['shuffle_time_seed'], [0, 2**32-1], integer=True)
        if kind == 'allxy':
            self._calibration(job, output_dir, rb=False)
        if kind == 'rb_iq':
            self._calibration(job, output_dir, rb=True)
        points = job.get('points', 21 if kind == 'allxy' else 2)
        if kind == 'power_rabi':
            points = len(job['amplitudes'])
        multiplier = len(job.get('powers', [0])) * len(job.get('frequencies_hz', [0])) * len(job.get('drive_amplitudes', [0]))
        if kind in ('snr_amp', 'snr_freq', 's21_ge', 'readout_cal'):
            multiplier *= 2
        if kind == 'rb_iq':
            points = len(job['gate_lengths'])
        total_shots = points * multiplier * job['shots'] * job['capture_repeats'] * job.get('native_repeats', 1)
        if total_shots > self.profile.get('maximumTotalShots', 8000000):
            raise AdapterError('BUDGET_EXCEEDED', '扫描总 shots 超出运行预算')
        deadline = request.get('deadline')
        if isinstance(deadline, str):
            deadline = datetime.fromisoformat(deadline.replace('Z', '+00:00')).timestamp()
        if deadline is None:
            deadline = time.time() + self.profile.get('maximumRunSeconds', 3600)
        self._validate_number('deadline', deadline)
        seconds = min(deadline-time.time(), self.profile.get('maximumRunSeconds', 3600))
        if seconds <= 0:
            raise AdapterError('DEADLINE_EXPIRED', '任务截止时间已到')
        return {'phase': 'phase_' + suffix, 'status': 'CONTROLLER_VALIDATED_REQUEST', 'ready_for_acquisition': True,
                'max_seconds': seconds, 'jobs': [job], 'estimated_total_shots': total_shots,
                'requestIdentity': {k: request.get(k) for k in ('runId', 'nodeId', 'attemptId', 'operationId', 'resourceId', 'procedureId', 'parameterVersions')}}

    def _calibration(self, job, output_dir, rb):
        path = job.get('calibration_file')
        if not path:
            raise AdapterError('CALIBRATION_REQUIRED', '该方法需要显式冻结的本平台校准文件')
        path = self._local(path)
        cal = _load(path)
        if cal.get('label', cal.get('q')) != job['q']:
            raise AdapterError('CALIBRATION_IDENTITY', '校准对象不匹配')
        pairs = [('pi_len_s', 'drive_length_s'), ('pi_amp', 'drive_amp'), ('half_pi_amp', 'half_pi_amp')]
        if rb:
            if cal.get('status') != 'FROZEN_FOR_NATIVE_RB':
                raise AdapterError('CALIBRATION_NOT_FROZEN', 'RB 校准状态必须为 FROZEN_FOR_NATIVE_RB')
            pairs += [(x, x) for x in ('drive_hz', 'readout_hz', 'readout_amp', 'xy_delay_s')]
        for ck, jk in pairs:
            if ck not in cal or not math.isclose(cal[ck], job[jk], rel_tol=1e-12, abs_tol=1e-12):
                raise AdapterError('CALIBRATION_MISMATCH', '校准与实际请求不符: ' + ck)
        if output_dir:
            target = Path(output_dir) / 'calibration.json'
            shutil.copyfile(path, target)
            job['calibration_file'] = str(target)
        if rb:
            if not job.get('frozen_plan_file'):
                raise AdapterError('RB_PLAN_REQUIRED', 'RB 需要显式冻结并配对的序列计划')
            frozen = self._local(job.get('frozen_plan_file', ''))
            plan = _load(frozen)
            if plan.get('status') != 'FROZEN_FOR_NATIVE_ACQUISITION' or plan.get('q') != job['q']:
                raise AdapterError('RB_PLAN_INVALID', '冻结 RB 计划身份/状态不符')
            if job.get('rb_variant') not in ('reference', 'interleaved'):
                raise AdapterError('RB_BRANCH_INVALID', '必须明确 reference/interleaved 分支')
            for key in ('sequence_index', 'random_seed'):
                self._validate_number(key, job.get(key), [0, 2**32-1], integer=True)
            job.setdefault('gate_lengths', plan['lengths'])
            job.setdefault('sequence_repeats', plan['repeats'])
            if job['gate_lengths'] != plan['lengths'] or job['sequence_repeats'] != plan['repeats'] or not 0 <= job['sequence_index'] < plan['repeats']:
                raise AdapterError('RB_PLAN_MISMATCH', 'RB 长度/seed 簇必须与冻结计划一致')
            native_source = Path(plan['native_source']).resolve()
            expected_source = Path(self.profile['basePath']).resolve() / 'MeasModule/QOS/Gate/utils/clifford_RB.py'
            if native_source != expected_source or sha256(native_source) != plan['native_source_sha256']:
                raise AdapterError('RB_SOURCE_CHANGED', '原生 Clifford 源码身份变化')
            if output_dir:
                target = Path(output_dir) / 'frozen_rb.json'
                shutil.copyfile(frozen, target)
                job['frozen_plan_file'] = str(target)

    def _operation_dir(self, operation_id):
        return self.operations / hashlib.sha256(str(operation_id).encode()).hexdigest()

    def publish_calibration(self, validation_snapshot):
        """Register a validated session after controller-accepted independent RB."""
        from .rb_suite import publish_validated_session
        return publish_validated_session(self, validation_snapshot)

    def reconcile(self, operation_id):
        directory = self._operation_dir(operation_id)
        receipt_path = directory / 'adapter_receipt.json'
        intent_path = directory / 'adapter_intent.json'
        if receipt_path.is_file():
            receipt = _load(receipt_path)
            if receipt.get('status') == 'completed':
                return {'status': 'completed', 'result': receipt['result'], 'receipt': str(receipt_path)}
            if receipt.get('status') == 'failed_before_start':
                return {'status': 'failed_before_start', 'reason': receipt.get('reason'), 'receipt': str(receipt_path)}
            return {'status': 'unknown', 'reason': receipt.get('reason', receipt.get('status')), 'receipt': str(receipt_path)}
        if intent_path.is_file():
            return {'status': 'unknown', 'reason': '持久化执行意图存在，但尚无可信完成回执；不得自动重采', 'intent': str(intent_path)}
        return {'status': 'not_started'}

    def execute(self, request, emit, cancel):
        if self.profile.get('enabled') is not True:
            raise AdapterError('HARDWARE_DISABLED', '现场 F8 profile 尚未由控制器启用')
        preflight = self.preflight()
        if not preflight['ready']:
            raise AdapterError('PREFLIGHT_FAILED', 'F8 静态预检未通过', preflight)
        operation = request.get('operationId')
        canonical = {k: v for k, v in request.items() if k != 'outputDir'}
        request_hash = _digest(canonical)
        directory = self._operation_dir(operation)
        if directory.exists():
            intent = _load(directory / 'adapter_intent.json') if (directory / 'adapter_intent.json').exists() else None
            if not intent or intent['requestHash'] != request_hash:
                raise AdapterError('IDEMPOTENCY_CONFLICT', '同一 operationId 的请求不一致或目录尚未完成登记')
            prior = self.reconcile(operation)
            if prior['status'] == 'completed':
                emit({'event': 'receipt_replayed', 'operationId': operation})
                return prior['result']
            raise AdapterError('NEEDS_RECONCILIATION', '该操作已提交，状态待核对，不得重发', prior)
        # Validate before recording an execution intent or creating hardware work.
        self.build_plan(request)
        if cancel():
            raise AdapterError('CANCELLED_BEFORE_START', '任务在启动子进程前取消')
        self.operations.mkdir(parents=True, exist_ok=True)
        directory.mkdir()
        _dump(directory / 'adapter_intent.json', {'operationId': operation, 'requestHash': request_hash, 'request': canonical, 'createdAt': _utc(), 'profileHash': _digest(self.profile)})
        try:
            plan = self.build_plan(request, directory)
            _dump(directory / 'plan.json', plan)
            shutil.copytree(self._local(self.profile['configurationDir']), directory / 'F8_copy')
        except Exception as exc:
            _dump(directory / 'adapter_receipt.json', {'status': 'failed_before_start', 'reason': str(exc), 'operationId': operation, 'recordedAt': _utc()})
            raise
        # File dictionary is private to this operation, never written in archive.
        runner = self._local(self.profile['runner'])
        environment = os.environ.copy()
        environment.update(QCAL_F8_RUN_DIR=str(directory), QCAL_F8_BASE=self.profile['basePath'],
                           QCAL_F8_HARDWARE_LOCK=self.profile['hardwareLock'], PYTHONIOENCODING='utf-8')
        environment.pop('PYTHONPATH', None)
        log_path = directory / 'worker.log'
        started = time.monotonic()
        emit({'event': 'worker_starting', 'operationId': operation, 'estimatedTotalShots': plan['estimated_total_shots']})
        position, stop_at, stop_reason = 0, None, None
        event_failure = None
        def dispatch(event):
            nonlocal event_failure
            if event_failure is not None:
                return
            try:
                emit(event)
            except Exception as exc:
                # Losing the controller/lease must not abandon a live worker.
                event_failure = f'{type(exc).__name__}: {exc}'
        with log_path.open('w', encoding='utf-8') as logfile:
            process = subprocess.Popen([self.profile['python'], '-B', '-u', str(runner), str(directory / 'plan.json')],
                                       cwd=str(directory), env=environment, stdout=logfile, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            _dump(directory / 'process.json', {'pid': process.pid, 'startedAt': _utc(), 'operationId': operation})
            dispatch({'event': 'worker_started', 'pid': process.pid, 'operationId': operation})
            event_path = directory / (plan['phase'] + '_events.jsonl')
            while process.poll() is None:
                if event_path.exists():
                    with event_path.open('rb') as events:
                        events.seek(position)
                        while True:
                            line = events.readline()
                            if not line or not line.endswith(b'\n'):
                                break
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                break
                            dispatch(event)
                            position = events.tell()
                elapsed = time.monotonic()-started
                try:
                    cancelled = cancel()
                except Exception as exc:
                    event_failure = f'cancel callback failed: {exc}'
                    cancelled = True
                if stop_at is None and (cancelled or event_failure is not None or elapsed >= plan['max_seconds']):
                    stop_reason = 'controller_event_failure' if event_failure is not None else 'cancel_requested' if elapsed < plan['max_seconds'] else 'deadline_exceeded'
                    (directory / 'STOP_ADC_AFTER_ROW').write_text(stop_reason, encoding='utf-8')
                    stop_at = time.monotonic()
                    dispatch({'event': 'cooperative_stop_requested', 'reason': stop_reason})
                if stop_at is not None and time.monotonic()-stop_at > self.profile.get('cancelGraceSeconds', 20):
                    process.kill()
                    process.wait(timeout=10)
                    _dump(directory / 'adapter_receipt.json', {'status': 'unknown', 'reason': 'forced_process_termination_hardware_state_unknown', 'operationId': operation, 'recordedAt': _utc()})
                    raise AdapterError('NEEDS_RECONCILIATION', '子进程超时终止，硬件状态未确认；保留资源隔离')
                time.sleep(.15)
        completion = directory / (plan['phase'] + '_complete.json')
        cleanup_path = directory / (plan['phase'] + '_hardware_after.json')
        cleanup = _load(cleanup_path) if cleanup_path.exists() else None
        if process.returncode != 0 or not completion.exists() or not cleanup or cleanup.get('shutdown_failures'):
            _dump(directory / 'adapter_receipt.json', {'status': 'unknown', 'reason': stop_reason or 'worker_failed_or_cleanup_unconfirmed', 'operationId': operation, 'returnCode': process.returncode, 'cleanup': cleanup, 'recordedAt': _utc()})
            raise AdapterError('NEEDS_RECONCILIATION', '未取得完整采集及收尾回执；保留失败日志和资源隔离', {'log': str(log_path), 'cleanup': cleanup})
        result = self._result(directory, plan, request, cleanup)
        _dump(directory / 'adapter_receipt.json', {'status': 'completed', 'operationId': operation, 'requestHash': request_hash, 'result': result, 'recordedAt': _utc()})
        dispatch({'event': 'acquisition_complete', 'operationId': operation, 'quality': result['quality']})
        return result

    def _result(self, directory, plan, request, cleanup):
        if plan.get('suite') == 'native_paired_rb':
            from .rb_suite import suite_result
            return suite_result(self, directory, plan, request, cleanup)
        job = plan['jobs'][0]
        completed = _load(directory / (job['id'] + '_complete.json'))
        artifacts = []
        paths = [Path(p) for p in completed.get('files', [])]
        analysis = None
        raw_files = [p for p in paths if p.suffix.lower() == '.qdat']
        if not raw_files:
            raise AdapterError('RAW_DATA_MISSING', '采集完成回执没有原始 qdat 数据')
        if len(raw_files) == 1:
            analysis_dir = directory / 'analysis'
            analysis_dir.mkdir(exist_ok=True)
            with (analysis_dir / 'analysis.log').open('w', encoding='utf-8') as logfile:
                try:
                    result = subprocess.run([self.profile['python'], '-I', '-B', str(self.root / 'adapters/analyze_capture.py'),
                                             '--raw', str(raw_files[0]), '--job', str(directory / 'plan.json'), '--output', str(analysis_dir)],
                                            cwd=str(directory), stdout=logfile, stderr=subprocess.STDOUT,
                                            timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    report = analysis_dir / 'capture_analysis.json'
                    if result.returncode == 0 and report.is_file():
                        analysis = _load(report)
                        paths.extend([report, analysis_dir / 'capture_analysis.png'])
                except subprocess.TimeoutExpired:
                    pass  # Acquisition receipt survives an offline-analysis timeout.
        paths += [directory / (job['id'] + '_request.json'), directory / (job['id'] + '_complete.json'), directory / (plan['phase'] + '_hardware_after.json')]
        for path in paths:
            path = path.resolve()
            if not path.is_relative_to(directory) or not path.is_file():
                raise AdapterError('RESULT_PROVENANCE_INVALID', '完成回执中的输出必须存在且位于本次任务目录内')
            digest = sha256(path)
            kind = 'image' if path.suffix.lower() in ('.png', '.jpg', '.jpeg') else 'json' if path.suffix == '.json' else 'raw'
            identifier = 'real-' + digest[:24]
            artifacts.append({'id': identifier, 'name': path.name, 'kind': kind, 'url': '/api/artifacts/' + identifier,
                              'sha256': digest, 'sourcePath': str(path), 'localPath': str(path)})
        metrics = {'elapsed_s': completed.get('elapsed_s'), 'shots_per_capture': job['shots'], 'capture_repeats': job['capture_repeats'], 'estimated_total_shots': plan['estimated_total_shots']}
        if analysis:
            metrics.update(analysis['metrics'])
        return {'dataKind': 'real', 'sourceRunId': plan['phase'], 'acquiredAt': completed.get('utc'),
                'metrics': metrics, 'candidateParameters': analysis.get('candidateParameters', {}) if analysis else {},
                'series': analysis.get('series', []) if analysis else [],
                'units': {'elapsed_s': 's'}, 'quality': {'status': 'unverified', 'reasons': ['原生 F8 新采集已完成；需要本方法拟合、独立复测与冻结规则后才能发布校准。']},
                'artifacts': artifacts, 'parameters': job, 'methodLabel': PROCEDURES[request['procedureId']][2],
                'provenance': {'freshAcquisition': True, 'operationId': request['operationId'], 'parameterVersions': request.get('parameterVersions', {}),
                               'runnerSha256': self.profile['runnerSha256'], 'profileHash': _digest(self.profile), 'nativeHalfPolicy': 'same-duration-half-amplitude',
                               'shutdownCommandsCompleted': not cleanup.get('shutdown_failures'), 'shutdownReadbackVerified': False,
                               'cleanupReceipt': str(directory / (plan['phase'] + '_hardware_after.json')), 'analysis': analysis.get('analysis', {}) if analysis else {'status': 'not_available'}}}
