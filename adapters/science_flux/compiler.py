"""Native flat-top spectroscopy/zero-drive cavity compiler, DC untouched."""
from copy import deepcopy
import math
import types
from core.utils import SettingConfig
from core.compiler.compile import Compiler
from adapters.science_runtime.extension import SegmentedCarrierSequence
from adapters.science_v3.compiler import wave_identity,archive_row

def compile_row(row,base,parameters,*,readout_hz=None):
    p=parameters;cfg=deepcopy(base);f01=row['phase']=='f01'
    cavity=readout_hz if f01 else row['frequencyHz']
    if cavity is None or not 6.5e9<=cavity<=7.6e9:raise ValueError('Explicit valid readout frequency required')
    cfg['ADC_Meas']['global'].update(preserve_existing_dc=True,readout_only=False)
    cfg['ADC_Meas']['global']['timeSeq'].update(probT0=80e-6,gapTime=300e-9,wdLen=81e-6+p['readout_width_s'],fill=True)
    cfg['ADC_Meas']['B1']['para'].update(width=p['readout_width_s'],shots=p['shots'],triggerDelay=80e-6)
    q=cfg['Q1'];q['inst'].pop('fluxCH',None)
    q['para'].update(probFreq=cavity,probCarryFreq=cavity,probCarryScale=p['readout_amp'],
        drivFreq=row['frequencyHz'] if f01 else 1e6,drivCarryFreq=row['frequencyHz'] if f01 else 1e6)
    q['gate']['U_gates'].update(pi_len=p['spectroscopy_width_s'] if f01 else 4e-9,
        pi_amp=p['drive_amp'] if f01 else 0.,shape='DC',buff=0.,use_drag=False,lambda1=0.,z_amp=0.)
    setting=SettingConfig(cfg);compiler=Compiler(setting,opt=False)
    gates=[('rx',1,[math.pi if f01 and row['condition']=='on' else 0.]),('measure',1,[])]
    qubits,pulses,_=compiler.compile(gates)
    seq=SegmentedCarrierSequence(qubits,setting,group='agent_f8');seq.add_pulses(pulses);seq.perform_sequence_awg()
    expected={w.name:wave_identity(w) for w in seq.waves._waves.values()}
    if set(expected)!={'xy_1','ro_B1'}:raise ValueError('Only explicit zero/drive XY and RO refresh is permitted')
    perform=seq.perform_sequence_awg
    def guarded(self):
        perform()
        if {w.name:wave_identity(w) for w in self.waves._waves.values()}!=expected:raise ValueError('Regenerated upload bytes differ')
    seq.perform_sequence_awg=types.MethodType(guarded,seq)
    return seq,deepcopy(expected)
