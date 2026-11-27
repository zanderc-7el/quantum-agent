"""Imported only by isolated worker; actual frozen native numeric compiler."""
from copy import deepcopy
import hashlib
import types
import numpy as np
from core.utils import SettingConfig
from adapters.science_runtime.extension import ExtensionCompiler,SegmentedCarrierSequence

def wave_identity(wave):
    samples=np.ascontiguousarray(wave.get_data())
    if samples.dtype.kind not in 'fc' or not len(samples) or not np.isfinite(samples).all() or np.max(np.abs(samples))>.990001:
        raise ValueError('Invalid native DAC samples')
    return {'name':wave.name,'dtype':samples.dtype.str,'shape':list(samples.shape),'sampleRateHz':wave.srate,
            'startAt':wave.start_at,'fill':wave.fill,'sha256':hashlib.sha256(samples.tobytes()).hexdigest()}

def compile_row(row,base,parameters):
    cfg=deepcopy(base);p=parameters
    cfg['ADC_Meas']['global'].update(preserve_existing_dc=True,readout_only=False)
    cfg['ADC_Meas']['global']['timeSeq'].update(probT0=80e-6,gapTime=300e-9,wdLen=80e-6+p['readout_width_s']+1e-6,fill=True)
    cfg['ADC_Meas']['B1']['para'].update(width=p['readout_width_s'],shots=p['shots'],triggerDelay=80e-6)
    cfg['Q1']['para'].update(probFreq=p['readout_hz'],probCarryFreq=p['readout_hz'],probCarryScale=p['readout_amp'])
    cfg['Q1']['gate']['U_gates']['z_amp']=0
    setting=SettingConfig(cfg);compiler=ExtensionCompiler(setting,opt=False)
    gates=[('r'+g['axis'],1,[g['theta']],{'id':'g'+str(index),'pulse':g['pulse']}) for index,g in enumerate(row['gates'])]
    gates.append(('measure',1,[]));qubits,pulses,_=compiler.compile(gates)
    seq=SegmentedCarrierSequence(qubits,setting,group='agent_f8');seq.add_pulses(pulses);seq.perform_sequence_awg()
    expected={w.name:wave_identity(w) for w in seq.waves._waves.values()}
    expected_frozen=deepcopy(expected)
    if set(name.split('_')[0] for name in expected)!={'xy','ro'}:raise ValueError('Only XY/RO refresh is allowed')
    actual_perform=seq.perform_sequence_awg
    def guarded(self):
        actual_perform()
        if {w.name:wave_identity(w) for w in self.waves._waves.values()}!=expected_frozen:
            raise ValueError('Regenerated waveform differs from precompiled upload identity')
    seq.perform_sequence_awg=types.MethodType(guarded,seq)
    return seq,expected

def archive_row(path,seq,expected):
    np.savez_compressed(path,**{w.name:np.ascontiguousarray(w.get_data()) for w in seq.waves._waves.values()})
    return {'file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'waves':expected,
            'segments':seq.segments}
