"""Native Arclight single-qubit acquisitions in the isolated workspace."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).absolute().parent))
from bootstrap import initialize
ROOT = initialize()
SOURCE_ROOT = Path(__file__).absolute().parent
from platform_hooks import check_cancel, progress_event, DelegatedLock
import argparse
from copy import deepcopy
import datetime as dt
import hashlib
import json
import os
import traceback
import numpy as np
from cavity_runtime import InstrumentLock, close_resources, save_json
from core.experiment.experiment import Experiment
from core.experiment.runner import SequenceRunner
from core.inst import create_hardware
from core.inst.driver_old import Ceyear_OSC as OSC
from core.inst.driver_old import Ceyear_AWG5000 as RF
from paired_rb_support import install_paired_rb, sequence_manifest
from repeated_iq_acquisition import get_repeated_raw_data
from validated_short_half_compat import install_validated_short_half


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('job', type=Path)
    parser.add_argument('--measure', action='store_true')
    args = parser.parse_args()
    if args.measure and (ROOT/'measurement_queue_hold.json').exists():
        parser.error('Queued acquisition held before instrument access; inspect measurement_queue_hold.json.')
    job_path = Path(os.path.abspath(args.job))
    if not job_path.is_relative_to(ROOT / 'plans'):
        parser.error('Job must be in this task plans directory.')
    job = json.loads(job_path.read_text(encoding='utf-8-sig'))
    if args.measure and job.get('fresh_reference_required_before_measurement'):
        reference = job.get('paired_rb_projection') or {}
        if reference.get('independent_reference') is not True or not reference.get('reference_contrast_repeated'):
            parser.error('This RB job requires its independently acquired, held-out-validated fresh reference first.')
        reference_raw = Path(reference.get('reference_raw', '')).absolute()
        if not reference_raw.is_relative_to(ROOT/'results') or not reference_raw.is_file():
            parser.error('Fresh RB reference raw file is missing or outside the isolated task results.')
        if hashlib.sha256(reference_raw.read_bytes()).hexdigest() != reference.get('reference_raw_sha256'):
            parser.error('Fresh RB reference raw hash mismatch.')
        reference_job = json.loads((reference_raw.parent/'job.json').read_text(encoding='utf-8-sig'))
        for field in ('target','config','xy_physical_channel','xy_attenuation_db','xy_delay_s','xy_filter_mhz'):
            if reference_job.get(field) != job.get(field):
                parser.error('Fresh RB reference conditions differ: '+field)
        if reference_job['spec']['meta'].get('pulse_compatibility') != job['spec']['meta'].get('pulse_compatibility'):
            parser.error('Fresh RB reference pulse policy differs.')
    cfg, spec = deepcopy(job['config']), deepcopy(job['spec'])
    name = job['name']
    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in name):
        parser.error('Invalid job name')
    physical, channel = job['xy_physical_channel']
    if physical not in range(8) or channel not in (0, 1):
        parser.error('XY must be a configured RF0..7 channel, separate from RF8 readout.')
    shots = int(spec['meta']['shots'])
    acquisitions_per_point = job.get('acquisitions_per_point', 1)
    if isinstance(acquisitions_per_point, bool) or not isinstance(acquisitions_per_point, int) or not 1 <= acquisitions_per_point <= 16:
        parser.error('acquisitions_per_point must be an integer in 1..16.')
    effective_shots = shots * acquisitions_per_point
    acquisition_block_index = np.repeat(np.arange(acquisitions_per_point, dtype=np.int16), shots)
    if not 2 <= shots <= 2047 or not 1 <= job.get('captures', 1) <= 12:
        parser.error('Require 2..2047 shots and 1..12 captures.')
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder = ROOT / 'results' / (stamp + ('_live_' if args.measure else '_offline_') + name)
    folder.mkdir(parents=True)
    for fname, obj in [('job.json', job), ('config.json', cfg), ('spec.json', spec)]:
        save_json(folder / fname, obj)
    hashes = {}
    for filename in ('run_single_qubit.py', 'bootstrap.py', 'cavity_runtime.py', 'native_single_qubit_specs.py', 'native_allxy_spec.py', 'paired_rb_support.py', 'repeated_iq_acquisition.py', 'validated_short_half_compat.py'):
        content = (SOURCE_ROOT / filename).read_bytes()
        (folder / ('executed_' + filename)).write_bytes(content)
        hashes[filename] = hashlib.sha256(content).hexdigest()
    save_json(folder / 'entry_source_hashes.json', hashes)
    save_json(folder / 'source_hashes.json', {str(p.relative_to(SOURCE_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (SOURCE_ROOT/'work'/'server'/'core').rglob('*.py')})
    hardware = create_hardware('CeyearStationF8', 'CeyearStationF8',
        'TCPIP::10.100.16.105::7110::SOCKET', group='agent_f8', debug=False,
        visa_backend='C:/Windows/System32/visa64.dll', acquisition_timeout=20, timeout=10)
    hardware.set_channel('RO', (14, 0))
    hardware.set_channel('AD', (15, 0))
    hardware.set_channel('XY', (physical + 6, channel))
    lock = DelegatedLock()
    exp, primary_error = None, None
    rows, metadata = [], []
    status = {'mode': 'real_hardware' if args.measure else 'offline_compile_only',
              'state': 'preflight', 'folder': str(folder), 'job': name,
              'target': job['target'], 'started': dt.datetime.now().isoformat(), 'points_completed': 0,
              'shots_per_acquisition': shots, 'acquisitions_per_point': acquisitions_per_point,
              'effective_shots_per_point': effective_shots}
    def update(**kw):
        status.update(kw)
        progress_event(status)
        save_json(folder / 'status.json', status)
        if lock.owned:
            save_json(ROOT/'latest_status.json', status)
        elif not args.measure:
            save_json(ROOT/'latest_preflight_status.json', status)
    try:
        exp = Experiment.load(cfg, spec, exp_id=stamp)
        if str(spec['meta']['type']) == '3':
            install_paired_rb(exp, mode='irb' if job.get('interleaved_gate') else 'rb',
                              interleaved=job.get('interleaved_gate', 'X/2'))
        if spec['meta'].get('pulse_compatibility'):
            save_json(folder/'pulse_compatibility.json', install_validated_short_half(exp))
        base_axes = exp._get_scan_points()
        if str(spec['meta']['type']) == '3':
            save_json(folder / 'sequence_manifest.json', sequence_manifest(exp.rb, base_axes, exp.run_seed))
        rng = np.random.default_rng(job.get('order_seed', 20261117))
        axes = []
        for capture in range(job.get('captures', 1)):
            indices = np.arange(len(base_axes))
            if job.get('randomize', False):
                rng.shuffle(indices)
            for index in indices:
                axes.append(deepcopy(base_axes[index]))
                metadata.append({'capture': capture, 'base_index': int(index), 'axes': base_axes[index]})
        if not 1 <= len(axes) <= 15000:
            raise ValueError('Point count outside 1..15000')
        exp._get_scan_points = lambda: axes
        save_json(folder / 'axes.json', metadata)
        update(points_expected=len(axes))
        compiled = []
        # Every variant is validated before touching the instrument, including RB sequences.
        for index, ax in enumerate(base_axes):
            check_cancel()
            seq = exp.get_sequence(ax)
            seq.perform_sequence_awg()
            required = {d.name for d in seq.required_devices()}
            if required != {'CeyearStationF8'}:
                raise ValueError(f'Unexpected hardware: {required}')
            record = {'base_index': index, 'axes': ax, 'waves': []}
            for w in seq.waves._waves.values():
                if not w.name.startswith(('xy_', 'ro_')):
                    raise ValueError('This stage preserves existing DC and forbids Z output.')
                y = w.get_data()
                peak = float(np.max(np.abs(y))) if len(y) else 0
                if not len(y) or not np.isfinite(y).all() or peak > 0.990001:
                    raise ValueError(f'Invalid waveform {w.name}: size={len(y)}, peak={peak}')
                record['waves'].append({'name': w.name, 'samples': len(y), 'srate': w.srate,
                    'peak': peak, 'is_complex': bool(np.iscomplexobj(y)),
                    'sha256': hashlib.sha256(y.tobytes()).hexdigest()})
                if index in (0, len(base_axes)//2, len(base_axes)-1):
                    np.savez_compressed(folder / f'preflight_{index}_{w.name}.npz', waveform=y)
            if {w.name.split('_')[0] for w in seq.waves._waves.values()} != {'xy', 'ro'}:
                raise ValueError('Each point must explicitly refresh both XY and readout.')
            record['pulse_timing'] = [{'channel': item[0], 'start_s': float(item[1]),
                'duration_s': float(item[2]), 'pulse': repr(item[3])}
                for item in seq.expand_pulse_sequence()]
            compiled.append(record)
        save_json(folder / 'compiled_plan.json', compiled)
        if not args.measure:
            update(state='offline_preflight_passed', hardware_connected=False)
            return folder
        check_cancel()
        lock.acquire({'pid': os.getpid(), 'folder': str(folder)})
        update(state='connecting')
        hardware.open()
        handle = hardware.driver.handle
        identity = handle.query('*IDN?').strip()
        OSC.oscboardSelect(handle, 0)
        adc_rate = float(OSC.oscSamRate(handle))*1e9
        if not np.isclose(adc_rate, 2.8e9):
            raise ValueError(f'Unexpected ADC clock {adc_rate}')
        hardware.write('sampleRate', 6e9, ch='RO')
        hardware.write('Amplitude', 0, ch='RO')
        hardware.write('triggerDelay', 0, ch='RO')
        hardware.write('shots', shots, ch='AD')
        hardware.write('triggerDelay', cfg['ADC_Meas']['B1']['para']['triggerDelay'], ch='AD')
        filter_report = hardware.driver.select_rf_filter((physical+6, channel), job['xy_filter_mhz'])
        filter_readback = handle.query('AWG5000:SELFILTERFREQ?').strip()
        if int(float(filter_readback)) != job['xy_filter_mhz']:
            raise ValueError(f'XY filter mismatch: {filter_readback}')
        hardware.write('Amplitude', job.get('xy_attenuation_db', 0), ch='XY')
        hardware.write('triggerDelay', job.get('xy_delay_s', 0), ch='XY')
        save_json(folder / 'instrument.json', {'idn': identity, 'adc_sample_rate_hz': adc_rate,
            'xy_logical': [physical+6, channel], 'xy_physical': [physical, channel],
            'RO': [14,0], 'AD': [15,0], 'xy_filter': filter_report,
            'xy_filter_readback_mhz': float(filter_readback), 'dc_policy': 'preserve_existing_dc'})
        update(state='measuring', hardware_connected=True)
        class Recorder(SequenceRunner):
            def get_raw_data(self, seq, shots, meta):
                check_cancel()
                index = len(rows)
                def block_record(record):
                    check_cancel()
                    if record['exact_duplicate_of_blocks']:
                        raise ValueError('Repeated acquisition returned exactly identical IQ; inspect saved block data.')
                    update(current_axes=metadata[index], current_point_blocks_completed=record['blocks_completed'],
                           current_point_effective_shots=record['effective_shots_completed'])
                raw, block_index = get_repeated_raw_data(self, seq, shots, meta,
                    acquisitions_per_point=acquisitions_per_point, output_folder=folder,
                    point_index=index, on_block=block_record, return_block_index=True)
                i, q, measured = raw
                i, q = np.asarray(i), np.asarray(q)
                if i.shape != (1, effective_shots) or q.shape != i.shape or not np.isfinite(i+1j*q).all():
                    raise ValueError('Invalid real IQ dimensions or values')
                if not np.array_equal(block_index, acquisition_block_index):
                    raise ValueError('Acquisition block index mismatch')
                np.savez_compressed(folder / f'point_{index:04d}.npz', i=i, q=q, measured=measured,
                                    acquisition_block_index=block_index)
                rows.append((i.copy(), q.copy()))
                mean = np.mean(i+1j*q)
                update(points_completed=len(rows), current_axes=metadata[index],
                       mean_i=float(mean.real), mean_q=float(mean.imag))
                print(f'{name} {len(rows)}/{len(axes)} meanIQ={mean.real:.7g},{mean.imag:.7g}', flush=True)
                return raw
        exp.runner = Recorder()
        exp.execute_scan()
        np.savez_compressed(folder / 'raw_scan.npz', i=np.stack([r[0] for r in rows]),
                            q=np.stack([r[1] for r in rows]), acquisition_block_index=acquisition_block_index)
        update(state='acquired')
    except BaseException as exc:
        primary_error = exc
        status.update(state='failed', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
        if rows:
            np.savez_compressed(folder / 'partial_raw_scan.npz', i=np.stack([r[0] for r in rows]),
                                q=np.stack([r[1] for r in rows]), acquisition_block_index=acquisition_block_index)
            save_json(folder / 'partial_axes.json', metadata[:len(rows)])
            status['partial_points_saved']=len(rows)
        raise
    finally:
        completion = close_resources(hardware, exp)
        status.update(completion, ended=dt.datetime.now().isoformat())
        if not completion['shutdown_ok'] and primary_error is None:
            status['state'] = 'shutdown_failed'
        try:
            update()
        finally:
            lock.release()
            print('RESULT_DIR=' + str(folder), flush=True)
        if not completion['shutdown_ok'] and primary_error is None:
            raise RuntimeError('Instrument cleanup incomplete; inspect this run status.')
    return folder


if __name__ == '__main__':
    main()
