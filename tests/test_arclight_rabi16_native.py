"""Real frozen block-acquisition code with an explicit injected ADC, never hardware."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get('QCAL_RABI16_BASE_ROOT', ROOT))


class PinnedNativeBlocks(unittest.TestCase):
    def test_frozen_native_82_rows_of_16_independent_2000_shot_blocks(self):
        profile = json.loads((BASE / 'adapters/arclight_profile_v3.json').read_text(encoding='utf-8'))
        result = subprocess.run([profile['python'], '-I', '-B', str(Path(__file__).resolve()),
                                 '--native', str(BASE), str(ROOT)], capture_output=True, text=True,
                                encoding='utf-8', timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"totalShots": 2624000', result.stdout)
        self.assertIn('"hardwareConnections": 0', result.stdout)


def native_cases(base, stage):
    # This subprocess does not import bootstrap, VISA, any instrument driver, or
    # Controller. Only the frozen repeated acquisition helper receives fake ADC IO.
    import importlib.util
    import types
    import tempfile
    import hashlib
    import numpy as np

    forbidden = []
    def audit(event, args):
        if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'):
            forbidden.append(event)
            raise AssertionError('Hardware/network/process launch forbidden in injected native test')
    sys.addaudithook(audit)
    os.environ['QCAL_RABI16_BASE_ROOT'] = str(base)
    sys.path.insert(0, str(stage / 'tests'))
    import test_arclight_rabi16 as contract
    contract.SamplingContracts.setUpClass()
    try:
        owner = contract.SamplingContracts()
        request = owner.request()
        plan = contract.ArcLightRabi16Adapter(owner.root, owner.profile).build_plan(request)
        folder = owner.root / 'data/runs/arclight_operations' / hashlib.sha256(request['operationId'].encode()).hexdigest()
        native_folder = folder / 'results' / 'TEST_ONLY_injected_adc_no_hardware'
        native_folder.mkdir(parents=True)
        os.environ['QCAL_ARCLIGHT_RUN_DIR'] = str(folder)
        calls = {'full': 0, 'arm': 0, 'trigger': 0, 'read': 0}
        current = {'row': 0, 'block': 0}

        def next_block(shots=2000):
            b = current['block']
            current['block'] += 1
            # Deterministic fixture values are intentionally distinct, not noise
            # claimed to have been acquired from an instrument.
            i = (current['row'] * 32 + b + np.arange(shots) / shots).reshape(1, shots)
            return [i, -i - .125, ['Q1']]

        class AD:
            _param_specs_inst = {'start_cap': types.SimpleNamespace(use_cache=False)}
            def set_start_cap(self, value, ch):
                assert value == 0 and ch == 0
                calls['arm'] += 1
            def read(self, mode, ch):
                assert mode == 'IQ' and ch == 0
                calls['read'] += 1
                return next_block()[:2]
        class SYNC:
            def set_trigger(self, shots, period):
                assert shots == 2000 and period == .0001
                calls['trigger'] += 1
        class Runner:
            @staticmethod
            def get_raw_data(runner, sequence, shots, meta):
                assert shots == 2000
                calls['full'] += 1
                return next_block(shots)

        fake = types.ModuleType('core.experiment.runner')
        fake.SequenceRunner = Runner
        fake.MetaKeySchema = types.SimpleNamespace(readout_mode='readout_mode', group='group',
                                                   sync_inst='sync_inst', time_to_read='time_to_read')
        fake.ChannelTypeSchema = types.SimpleNamespace(ad='AD', ro='ro')
        ad, sync = AD(), SYNC()
        fake.get_dev_and_ch = lambda *a, **k: (ad, 0)
        fake.get_hardware = lambda *a, **k: sync
        fake.get_IQ_data = lambda data, *a: data[0][0]
        sys.modules['core.experiment.runner'] = fake
        path = owner.root / 'adapters/arclight_runtime/repeated_iq_acquisition.py'
        spec = importlib.util.spec_from_file_location('frozen_native_repeated_iq', path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        seq = types.SimpleNamespace(measure_list=['Q1'],
            config=types.SimpleNamespace(global_period=lambda: .0001),
            waves=types.SimpleNamespace(_waves={'ro_1': types.SimpleNamespace(name='ro_1', use=True, qids=['Q1'])}))
        rows, axes = [], []
        for capture in range(2):
            for point in range(41):
                row = capture * 41 + point
                current.update(row=row, block=0)
                raw, index = helper.get_repeated_raw_data(Runner(), seq, 2000, {},
                    acquisitions_per_point=16, output_folder=native_folder, point_index=row,
                    return_block_index=True)
                assert raw[0].shape == raw[1].shape == (1, 32000)
                assert np.array_equal(index, np.repeat(np.arange(16, dtype=np.int16), 2000))
                rows.append((raw[0], raw[1]))
                axes.append({'capture': capture, 'point': point})
        assert calls == {'full': 82, 'arm': 1230, 'trigger': 1230, 'read': 1230}, calls
        np.savez_compressed(native_folder / 'raw_scan.npz', i=np.stack([r[0] for r in rows]),
                            q=np.stack([r[1] for r in rows]), acquisition_block_index=index)
        contract.put(native_folder / 'axes.json', axes)
        contract.put(folder / 'plan.json', plan)
        contract.put(native_folder / 'job.json', plan['jobs'][0])
        contract.put(native_folder / 'status.json', {'points_completed': 82, 'shutdown_ok': True})
        completion = {'state': 'completed', 'shutdown_ok': True, 'folders': [str(native_folder)],
                      'files': [str(native_folder / 'raw_scan.npz')]}
        from adapters.arclight_rabi16_runtime.execute_worker import sampling_receipt, validate_sampling_receipt
        receipt = sampling_receipt(folder, plan, completion)
        contract.put(folder / 'completion.json', completion)
        assert receipt['totalShots'] == 2624000 and receipt['captures'] == 2
        assert len(receipt['rows']) == 82 and len(completion['files']) == 1401
        assert len(list(native_folder.glob('point_*_block_*.npz'))) == 1312
        artifacts = [{'name': Path(p).name, 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()}
                     for p in completion['files']]
        before_files = {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                        for p in folder.rglob('*') if p.is_file()}
        evidence = validate_sampling_receipt(folder, plan, completion, registered_artifacts=artifacts)
        assert evidence['status'] == 'valid' and evidence['totalShots'] == 2624000
        after_files = {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                       for p in folder.rglob('*') if p.is_file()}
        assert before_files == after_files
        for missing in ('sampling_receipt.json', 'raw_scan.npz', 'point_0000_block_000.npz', 'completion.json'):
            try:
                validate_sampling_receipt(folder, plan, completion,
                    registered_artifacts=[a for a in artifacts if a['name'] != missing])
            except ValueError:
                pass
            else:
                raise AssertionError('Missing DB-frozen evidence accepted: ' + missing)
        # The new receipt must reject a changed actual block even if its outer
        # completion claims success. It does not promote synthetic data to real.
        block_path = native_folder / 'point_0000_block_000.npz'
        original = block_path.read_bytes()
        block_path.write_bytes(original + b'tampered')
        try:
            sampling_receipt(folder, plan, completion)
        except ValueError:
            pass
        else:
            raise AssertionError('Modified block was accepted')
        block_path.write_bytes(original)
        # Cancellation after block 3 persists its acquired bytes and stops before
        # a fourth rearm, just as the unchanged entry's on_block check_cancel does.
        stop_folder = folder / 'results' / 'TEST_ONLY_cancel'
        stop_folder.mkdir()
        current.update(row=0, block=0)
        before = dict(calls)
        def stop(record):
            if record['blocks_completed'] == 3:
                raise InterruptedError('TEST_ONLY explicit cancellation')
        try:
            helper.get_repeated_raw_data(Runner(), seq, 2000, {}, acquisitions_per_point=16,
                output_folder=stop_folder, point_index=0, on_block=stop)
        except InterruptedError:
            pass
        else:
            raise AssertionError('Expected cooperative cancellation')
        stopped = json.loads((stop_folder / 'point_0000_acquisition_blocks.json').read_text(encoding='utf-8'))
        assert stopped['state'] == 'failed' and stopped['blocks_completed'] == 3
        assert len(list(stop_folder.glob('*.npz'))) == 3
        assert calls['read'] - before['read'] == calls['arm'] - before['arm'] == 2
        for count, shots in [(17, 2000), (True, 2000), (1, 2048)]:
            try:
                helper.get_repeated_raw_data(Runner(), seq, shots, {}, acquisitions_per_point=count,
                    output_folder=stop_folder, point_index=999)
            except ValueError:
                pass
            else:
                raise AssertionError('Illegal block/count accepted')
        assert forbidden == []
        print(json.dumps({'fixture': 'TEST_ONLY injected ADC; not physical data', 'rows': 82,
            'captures': 2, 'blocksPerPoint': 16, 'shotsPerBlock': 2000, 'totalShots': 2624000,
            'nativeFullSetupCalls': 82, 'additionalArmTriggerReadTransactions': 1230,
            'blockArtifacts': 1312, 'cancellationPreservedBlocks': 3,
            'hardwareConnections': 0, 'subprocessesFromNativeHelper': 0}, ensure_ascii=False))
    finally:
        contract.SamplingContracts.tearDownClass()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--native':
        native_cases(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        unittest.main()
