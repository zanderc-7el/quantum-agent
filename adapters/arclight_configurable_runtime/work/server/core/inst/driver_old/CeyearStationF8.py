"""F8 station layout and bounded acquisition for the isolated cavity experiment.

Logical RF 14/channel 0 -> AWG5000 module 8/channel 0; logical ADC 15/channel
0 -> OSC module 0/channel 0. Sample clocks and RF carrier synthesis remain
the caller's responsibility. Constructing this driver does not open a device.
"""
import itertools
import math
import time

import numpy as np
import pyvisa as visa

from .CeyearStation import CeyearStation
from . import AWG1652AM as AWG_LF
from . import Ceyear_AWG5000 as AWG_RF
from . import Ceyear_OSC as OSC


class CeyearStationF8(CeyearStation):
    LF_boards = list(range(6))
    RF_boards = list(range(6, 15))
    LO_boards = []
    OSC_boards = [15]
    CHs = (list(itertools.product(LF_boards, range(4)))
           + list(itertools.product(RF_boards, range(2)))
           + list(itertools.product(OSC_boards, range(2))))
    READOUT_CH = (14, 0)
    ACQUIRE_CH = (15, 0)
    MAX_SHOTS = 2047  # Ceyear_OSC.oscSeqNum documents this hardware-demod limit.
    RESET_OFFSET_LF_BOARDS = ([], [])
    RESET_OFFSET_RF_BOARDS = ([], [])

    def __init__(self, addr=None, *, visa_backend=None, timeout=10,
                 acquisition_timeout=20, poll_interval=0.005, **kwargs):
        super().__init__(addr=addr, **kwargs)
        self._resource_manager = None
        self._touched = False
        self.last_cleanup = {}
        self.cleanup_history = []
        self.last_close_result = {}
        self.rf_filter_history = []
        self._rf_needs_cleanup = set()
        self._adc_needs_cleanup = set()
        self._trigger_needs_cleanup = False
        for name, value in (("timeout", timeout),
                            ("acquisition_timeout", acquisition_timeout),
                            ("poll_interval", poll_interval)):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        self.timeout = timeout
        self.visa_backend = visa_backend  # e.g. '@ni'; never silently retry a backend.
        self.acquisition_timeout = acquisition_timeout
        self.poll_interval = poll_interval
        self.freq_num_by_channel = {}

    def open(self, **kwargs):
        return self.performOpen(**kwargs)

    def performOpen(self, **kwargs):
        if self.handle is not None:
            return
        backend = kwargs.pop("visa_backend", self.visa_backend)
        if kwargs:
            raise TypeError(f"Unexpected open arguments: {sorted(kwargs)}")
        rm = visa.ResourceManager(backend) if backend else visa.ResourceManager()
        try:
            handle = rm.open_resource(self.addr)
            self.handle = handle
            handle.timeout = max(1, int(self.timeout * 1000))
            handle.write_termination = '\n'
            handle.read_termination = '\n'
            identity = handle.query('*IDN?').strip()
            fields = identity.split(',')
            self.model = fields[1].strip() if len(fields) > 1 else identity
            self._resource_manager = rm
            return identity
        except BaseException:
            if self.handle is not None:
                try:
                    self.handle.close()
                except BaseException:
                    self.log.exception('Handle close failed while preserving the open error')
            self.handle = None
            try:
                rm.close()
            except BaseException:
                self.log.exception('Resource manager close failed while preserving the open error')
            raise

    @staticmethod
    def _integer(value, name, low, high=None):
        if isinstance(value, bool) or not np.isfinite(value) or int(value) != value:
            raise ValueError(f"{name} must be an integer")
        value = int(value)
        if value < low or (high is not None and value > high):
            raise ValueError(f"{name} out of range: {value}")
        return value

    def _adc(self, ch):
        if not isinstance(ch, tuple) or ch not in self.CHs or ch[0] not in self.OSC_boards:
            raise ValueError(f"Not an ADC channel: {ch!r}")
        return ch[0] - self.OSC_boards[0], ch[1]

    def select_rf_filter(self, ch, center_mhz):
        """Select a documented RF bandpass; this does not set a clock or NCO.

        Readback is intentionally explicit at the caller: only the selection
        command's reply is recorded here, with no unverified SCPI query.
        """
        if not isinstance(ch, tuple) or ch not in self.CHs or ch[0] not in self.RF_boards:
            raise ValueError(f'Not an RF filter channel: {ch!r}')
        center = self._integer(center_mhz, 'RF filter center (MHz)', 0)
        if center not in (3700, 4700, 5700, 6900):
            raise ValueError('RF filter center must be one of3700/4700/5700/6900 MHz')
        record = {'logical_channel': list(ch), 'physical_rf_module': ch[0] - self.RF_boards[0],
                  'center_mhz': center, 'readback_verified': False}
        self.rf_filter_history.append(record)
        try:
            record['module_reply'] = str(AWG_RF.boardSelect(self.handle, ch[0] - self.RF_boards[0]))
            record['channel_reply'] = str(AWG_RF.channSelect(self.handle, ch[1]))
            reply = self.handle.query(f'AWG5000:SELFILTERFREQ {center}')
            record['reply'] = str(reply)
            record['command_returned'] = True
            return record.copy()
        except BaseException as exc:
            record['command_returned'] = False
            record['error'] = f'{type(exc).__name__}: {exc}'
            raise

    def performSetValue(self, quant, value, **kwargs):
        ch = kwargs.get('ch', (0, 0))
        if ch not in self.CHs:
            raise ValueError(f"Unknown logical channel: {ch!r}")
        board, channel = ch
        if quant.name == 'Waveform':
            return self.writeWaveform(value, ch=ch)
        if board in self.RF_boards and quant.name == 'Amplitude':
            if not np.isfinite(value) or not 0 <= value <= 31.75:
                raise ValueError('RF Amplitude is attenuation in dB, range 0..31.75')
            AWG_RF.boardSelect(self.handle, board - self.RF_boards[0])
            AWG_RF.channSelect(self.handle, channel)
            return AWG_RF.setAmplitude(self.handle, value, 'dBm')
        if board in self.RF_boards and quant.name == 'sampleRate':
            if value not in (4e9, 4.8e9, 6e9):
                raise ValueError('RF sampleRate selects a documented filter only: 4/4.8/6 GHz')
        if board in self.OSC_boards:
            physical, channel = self._adc(ch)
            if quant.name == 'sampleRate':
                OSC.oscboardSelect(self.handle, physical)
                actual = float(OSC.oscSamRate(self.handle)) * 1e9
                if not np.isfinite(value) or not np.isclose(actual, value, rtol=1e-9, atol=1):
                    raise ValueError(f'ADC clock is {actual} Hz, requested {value}; this driver does not change it')
                return actual
            if quant.name == 'shots':
                value = self._integer(value, 'shots', 1, self.MAX_SHOTS)
            elif quant.name == 'pointNum':
                value = self._integer(value, 'pointNum', 20)
                if value % 20:
                    raise ValueError('pointNum must be a multiple of 20')
            elif quant.name == 'coefficient':
                if len(value) != 6:
                    raise ValueError('Coefficient requires six fields')
                freq, phase, start, length, _, _ = value
                count = len(freq)
                if not 1 <= count <= 16 or any(len(v) != count for v in (phase, start, length)):
                    raise ValueError('Coefficient lists must have equal lengths in 1..16')
                if not np.isfinite(freq).all() or not np.isfinite(phase).all():
                    raise ValueError('Coefficient frequencies/phases must be finite')
                point_num = self.config['pointNum'][ch]['value']
                for first, size in zip(start, length):
                    first = self._integer(first, 'window start (samples)', 0)
                    size = self._integer(size, 'window length (samples)', 1)
                    if first + size > point_num:
                        raise ValueError('Demodulation window exceeds pointNum')
                # A partially failed update must not leave an old coefficient
                # count looking like a successfully programmed acquisition.
                self.freq_num_by_channel.pop(ch, None)
                result = super().performSetValue(quant, value, **kwargs)
                self.freq_num_by_channel[ch] = count
                return result
        return super().performSetValue(quant, value, **kwargs)

    def writeWaveform(self, data, ch=(14, 0), count=1, preDistort='ALL'):
        wave = np.asarray(data)
        if wave.ndim != 1 or wave.size == 0 or not np.isfinite(wave).all():
            raise ValueError('Waveform must be a nonempty finite one-dimensional array')
        if np.any(np.abs(wave.real) > 1) or np.any(np.abs(wave.imag) > 1):
            raise ValueError('Waveform components must lie in [-1, 1]')
        count = self._integer(count, 'waveform repetitions', 1, 65530)
        if ch not in self.CHs or ch[0] not in self.RF_boards:
            raise ValueError('This cavity driver only downloads RF waveforms')
        self._rf_needs_cleanup.add(ch)
        self._touched = True
        AWG_RF.boardSelect(self.handle, ch[0] - self.RF_boards[0])
        AWG_RF.channSelect(self.handle, ch[1])
        AWG_RF.waveSelect(self.handle, 'ARB')
        AWG_RF.globalTrigArb(self.handle, 'OFF')
        AWG_RF.ArbDownStart(self.handle)
        # Respect the low-level function's documented 2,000-sample transfer limit.
        payload, size = AWG_RF.float2bytes(wave)
        for start in range(0, size, 2000):
            stop = min(start + 2000, size)
            AWG_RF.ArbDownSeq(self.handle, payload[start * 2:stop * 2], stop - start)
        AWG_RF.ArbDownSeqEnd(self.handle, count)
        AWG_RF.ArbDownAllEnd(self.handle)
        AWG_RF.ArbRunEn(self.handle, 'ON')
        AWG_RF.ChannelOutput(self.handle, 'ON')
        AWG_RF.globalTrigArb(self.handle, 'ON')

    def start_capture(self, ch):
        self._adc(ch)
        self._integer(self.config['shots'][ch]['value'], 'shots', 1, self.MAX_SHOTS)
        self._adc_needs_cleanup.add(ch[0])
        self._touched = True
        return super().start_capture(ch)

    def channelOpen(self, ch):
        if ch not in self.CHs or ch[0] not in self.RF_boards:
            raise ValueError('This cavity driver only enables RF outputs')
        self._rf_needs_cleanup.add(ch)
        self._touched = True
        return super().channelOpen(ch)

    def writeFuncWave(self, ch=(0, 1), wavefrq=100, wavephase=0):
        if ch not in self.CHs or ch[0] not in self.RF_boards:
            raise ValueError('This cavity driver only enables RF outputs')
        self._rf_needs_cleanup.add(ch)
        self._touched = True
        return super().writeFuncWave(ch, wavefrq, wavephase)

    def set_trigger(self, shots, period):
        shots = self._integer(shots, 'trigger shots', 1, self.MAX_SHOTS)
        if not np.isfinite(period) or period < 100e-9:
            raise ValueError('Trigger period must be finite and at least 100 ns')
        self._trigger_needs_cleanup = True
        self._touched = True
        AWG_LF.setTrigType(self.handle, 'CLOSE')
        AWG_LF.trigCycleInterv(self.handle, period, 's')
        AWG_LF.trigCycleTimes(self.handle, shots)
        AWG_LF.setTrigType(self.handle, 'CYCLE')
        # The station starts/rearms its finite train on LOOPTIMES while in CYCLE.
        return AWG_LF.trigCycleTimes(self.handle, shots)

    def _wait_capture(self, ch, timeout=None):
        physical, _ = self._adc(ch)
        timeout = self.acquisition_timeout if timeout is None else timeout
        if not np.isfinite(timeout) or timeout <= 0:
            raise ValueError('Acquisition timeout must be finite and positive')
        OSC.oscboardSelect(self.handle, physical)
        deadline = time.monotonic() + timeout
        previous_timeout = self.handle.timeout
        primary_error = None
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'OSC {physical} capture did not complete within {timeout}s')
                # A pending VISA poll must not add a full original 10 s timeout.
                self.handle.timeout = max(1, min(1000, int(remaining * 1000)))
                state = int(OSC.oscStoreEnd(self.handle))
                if state == 1:
                    break
                if state != 0:
                    raise RuntimeError(f'Unexpected OSC store state: {state}')
                time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            self.handle.timeout = previous_timeout
            try:
                OSC.oscEndWriteDDR(self.handle)
            except Exception:
                if primary_error is None:
                    raise
                self.log.exception('DDR stop failed after acquisition error')
            finally:
                self.handle.timeout = previous_timeout

    def readHardFFTData(self, ch, timeout=None):
        _, channel = self._adc(ch)
        if ch not in self.freq_num_by_channel:
            raise RuntimeError(f'No successful coefficient configuration for {ch}')
        shots = self._integer(self.config['shots'][ch]['value'], 'shots', 1, self.MAX_SHOTS)
        frequencies = self.freq_num_by_channel[ch]
        self._wait_capture(ch, timeout)
        data = np.asarray(OSC.hardDemodu_Out(self.handle, channel, frequencies, shots))
        if data.shape != (shots, frequencies) or not np.isfinite(data).all():
            raise ValueError(f'Invalid IQ data shape/values: {data.shape}, expected {(shots, frequencies)}')
        return data.real.copy(), data.imag.copy()

    def readFFTData(self, ch, timeout=None):
        return self.readHardFFTData(ch, timeout=timeout)

    def readData(self, ch, timeout=None):
        self._adc(ch)
        shots = self._integer(self.config['shots'][ch]['value'], 'shots', 1, self.MAX_SHOTS)
        points = self._integer(self.config['pointNum'][ch]['value'], 'pointNum', 20)
        self._wait_capture(ch, timeout)
        data = OSC.readOut_real(self.handle, points, shots)
        if any(np.asarray(v).shape != (shots, points) or not np.isfinite(v).all() for v in data):
            raise ValueError('Invalid raw ADC data shape/values')
        return data

    def getData(self, ch, fft=False, avg=False, timeout=None):
        return (self.readHardFFTData(ch, timeout) if fft else self.readData(ch, timeout))

    def query(self, command, **kwargs):
        return self.handle.query(command, **kwargs)

    def reset_offset(self):
        raise RuntimeError('F8 cavity task does not reset LF/XY; use cleanup_readout()')

    def cleanup_readout(self, rf_ch=(14, 0), adc_ch=(15, 0)):
        """Stop global triggers, then only the specified RF channel/OSC module.

        Global trigger and OSC storage are chassis/module-wide resources, so this
        task must own them exclusively. Returned entries record command success,
        not a physical-output measurement. All independent cleanup is attempted.
        """
        if rf_ch not in self.CHs or rf_ch[0] not in self.RF_boards:
            raise ValueError(f'Invalid RF cleanup channel: {rf_ch}')
        self._adc(adc_ch)
        return self._cleanup_channels([rf_ch], [adc_ch[0]], legacy_labels=True)

    def cleanup_used_channels(self):
        """Clean only RF channels/ADC modules this instance has used or attempted."""
        return self._cleanup_channels(sorted(self._rf_needs_cleanup),
                                      sorted(self._adc_needs_cleanup))

    def _cleanup_channels(self, rf_channels, adc_boards, legacy_labels=False):
        result = {}
        if self.handle is None:
            return result
        self._rf_needs_cleanup.update(rf_channels)
        self._adc_needs_cleanup.update(adc_boards)
        deferred_interrupt = None

        def attempt(label, function, *args):
            nonlocal deferred_interrupt
            try:
                reply = function(self.handle, *args)
                result[label] = {'ok': True, 'reply': str(reply)}
                return True
            except BaseException as exc:
                result[label] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
                self.log.error('Cleanup %s failed: %s', label, exc)
                if not isinstance(exc, Exception) and deferred_interrupt is None:
                    deferred_interrupt = (exc, exc.__traceback__)
                return False

        self._trigger_needs_cleanup = not attempt('trigger_close', AWG_LF.setTrigType, 'CLOSE')
        for rf_ch in rf_channels:
            prefix = 'rf' if legacy_labels else f'rf_{rf_ch[0]}_{rf_ch[1]}'
            rf_selected = attempt(prefix + '_select', AWG_RF.boardSelect,
                                  rf_ch[0] - self.RF_boards[0])
            if rf_selected and attempt(prefix + '_channel', AWG_RF.channSelect, rf_ch[1]):
                # Do not short-circuit later output-off actions on an earlier failure.
                stopped = [attempt(prefix + '_trigger_off', AWG_RF.globalTrigArb, 'OFF'),
                           attempt(prefix + '_run_off', AWG_RF.ArbRunEn, 'OFF'),
                           attempt(prefix + '_output_off', AWG_RF.ChannelOutput, 'OFF')]
                if all(stopped):
                    self._rf_needs_cleanup.discard(rf_ch)
        for board in adc_boards:
            prefix = 'osc' if legacy_labels else f'osc_{board}'
            if attempt(prefix + '_select', OSC.oscboardSelect, board - self.OSC_boards[0]):
                stopped = [attempt(prefix + '_store_off', OSC.oscTrigStore, 'OFF'),
                           attempt(prefix + '_end_ddr', OSC.oscEndWriteDDR)]
                if all(stopped):
                    self._adc_needs_cleanup.discard(board)
        self.last_cleanup = result
        self.cleanup_history.append(result)
        self._touched = bool(self._rf_needs_cleanup or self._adc_needs_cleanup
                             or self._trigger_needs_cleanup)
        if deferred_interrupt is not None:
            raise deferred_interrupt[0].with_traceback(deferred_interrupt[1])
        return result

    def close(self, **kwargs):
        handle = getattr(self, 'handle', None)
        manager = getattr(self, '_resource_manager', None)
        if handle is None and manager is None:
            return getattr(self, 'last_close_result', {})
        result = {'cleanup': {}, 'handle_close': {'ok': handle is None},
                  'manager_close': {'ok': manager is None}}
        primary_error = None

        def retain_error(exc):
            nonlocal primary_error
            if primary_error is None:
                primary_error = (exc, exc.__traceback__)

        if handle is not None:
            try:
                if getattr(self, '_touched', False):
                    result['cleanup'] = self.cleanup_used_channels()
            except BaseException as exc:
                result['cleanup'] = self.last_cleanup
                retain_error(exc)
            finally:
                try:
                    handle.close()
                    result['handle_close'] = {'ok': True}
                except BaseException as exc:
                    result['handle_close'] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
                    retain_error(exc)
                finally:
                    self.handle = None
        if manager is not None:
            try:
                manager.close()
                result['manager_close'] = {'ok': True}
            except BaseException as exc:
                result['manager_close'] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
                retain_error(exc)
            finally:
                self._resource_manager = None
        result['pending_rf_channels'] = [list(ch) for ch in sorted(self._rf_needs_cleanup)]
        result['pending_adc_boards'] = sorted(self._adc_needs_cleanup)
        result['trigger_shutdown_pending'] = self._trigger_needs_cleanup
        result['cleanup_attempts'] = len(self.cleanup_history)
        result['ok'] = (not self._touched and result['handle_close']['ok']
                        and result['manager_close']['ok'])
        self.last_close_result = result
        if primary_error is not None:
            raise primary_error[0].with_traceback(primary_error[1])
        return result

    performClose = close

    def __exit__(self, exception_type, exception_value, traceback):
        if exception_type is None:
            self.close()
        else:
            try:
                self.close()
            except Exception:
                self.log.exception('Close failed while preserving the measurement exception')
        return False


Driver = CeyearStationF8
