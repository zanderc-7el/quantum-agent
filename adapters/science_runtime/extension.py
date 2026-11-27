"""Native compiler extension. Imported only inside the guarded compile worker."""
import math
import numpy as np
from core.compiler.compile import Compiler
from core.experiment.pulse import DrivePulse, PulseType
from core.experiment.sequence import Sequence
from core.experiment.wave import Wave, WaveStore


class ExtensionCompiler(Compiler):
    """Fixed envelope duration; half rotations scale amplitude, not duration.

    Native gate decomposition owns negative rotations, Y and virtual-Z phase.
    All pulse dictionaries below are produced by our fixed template builder.
    """
    def get_xy_seq(self,q,theta=np.pi,phase=0,pulse=None):
        p=pulse or {}
        cfg=self.get_q_cfg(q,{'lambda1':p['lambda1'],'Delta2':p['anharmonicityHz'] or -1e6})
        duration=p['durationNs']*1e-9
        return DrivePulse(duration=duration+2e-9,buff=2e-9,A=p['amplitude']*theta/np.pi,
            srate=cfg.xy_srate(q),mrate=p['frequencyHz'],shape=p['shape'],
            shape_params={'sigma':p['sigmaNs']*1e-9} if p['shape']=='Gaussian' else {},
            phase=phase+p['phaseRad'],drag=p['lambda1']!=0,beta=cfg.xy_beta(q),
            pulse_type=PulseType.XY,label=p['label']).on(q)


def check_peak(data,label):
    if not np.isfinite(data).all() or np.max(np.abs(data),initial=0)>1+1e-12:
        raise ValueError(label+' exceeds normalized DAC peak or contains non-finite samples')


class SegmentedCarrierSequence(Sequence):
    """Native placement and native Wave.mix, independently for each segment.

    Phase origin is absolute sequence t=0; no phase reset at carrier changes.
    Pre-carrier envelopes and real carrier samples are archived separately.
    """
    def perform_sequence_awg(self):
        if not self._preserve_existing_dc():raise ValueError('Compile policy must preserve existing DC')
        self.waves=WaveStore(length_s=self.config.global_wd_len())
        self.segments=[];self.arrays={};index=0;roindex=0
        for q,pulses in self.pulse_list_per_qubit.items():
            for (start,end),pulse in pulses:
                if pulse.pulse_type==PulseType.XY:
                    start+=self.gate_start;end+=self.gate_start
                    wave=self.get_xy_wave(q);key=f'xy{index}';index+=1;channel='xy'
                elif pulse.pulse_type==PulseType.RO:
                    wave=self.get_ro_wave(q);key=f'ro{roindex}';roindex+=1;channel='readout'
                else:raise ValueError('Only XY and readout native samples are permitted')
                i0=round(start*wave.srate);i1=round(end*wave.srate)
                samples=pulse.sample_generator(self.param_ctx)()
                if i0<0 or i1>wave.n or i1-i0!=len(samples):raise ValueError('Native segment would be clipped or padded inconsistently')
                check_peak(samples,key+' baseband')
                # Native Wave owns both the absolute time carrier and addition.
                segment=Wave(name=key,srate=wave.srate,length_s=wave.length_s)
                segment.add_segment(start,end,samples)
                segment.mix(pulse.mrate,iq=channel=='readout' and wave.srate==2e9)
                check_peak(segment.data,key+' carrier')
                wave.add_segment(0,wave.length_s,segment.data)
                check_peak(wave.data,channel+' aggregate')
                self.arrays[key+'_i']=samples.real
                self.arrays[key+'_q']=samples.imag
                self.arrays[key+'_rf']=segment.data[i0:i1].real
                self.segments.append({'id':key,'label':pulse.label or 'native readout','channel':channel,
                    'startNs':i0/wave.srate*1e9,'endNs':i1/wave.srate*1e9,
                    'startSample':i0,'endSample':i1,'sampleCount':i1-i0,'sampleRateHz':wave.srate,
                    'envelopeDurationNs':pulse.wave_width*1e9,'bufferNs':pulse.buff*1e9,
                    'shape':pulse.shape,'shapeParameters':pulse.shape_params,'phaseRad':getattr(pulse,'phase',0),
                    'frequencyHz':pulse.mrate,'digitalAliasHz':(pulse.mrate+wave.srate/2)%wave.srate-wave.srate/2,
                    'betaSeconds':pulse.beta if pulse.drag else 0,'peak':float(np.max(np.abs(samples),initial=0)),
                    'arrayPrefix':key,'source':'native_DrivePulse_and_Wave'})
        xy=self.get_xy_wave(1).data;ro=self.get_ro_wave(1).data
        self.arrays.update(xy_rf=xy.real,readout_i=ro.real,readout_q=ro.imag)


def compile_request(request,base):
    from copy import deepcopy
    from core.utils import SettingConfig
    p=request['input'];cfg=deepcopy(base)
    global_=cfg['ADC_Meas']['global']
    global_.update(preserve_existing_dc=True,readout_only=False)
    ts=global_['timeSeq']
    ts.update(probT0=(80000+p['readoutDelayNs'])*1e-9,
              gapTime=(p['gapNs']+p['readoutDelayNs'])*1e-9,
              wdLen=(80000+p['readoutDelayNs']+p['readoutWidthNs']+1000)*1e-9,fill=True)
    ro=cfg['ADC_Meas']['B1']['para']
    ro.update(width=p['readoutWidthNs']*1e-9,triggerDelay=ts['probT0']+p['adcDelayNs']*1e-9)
    cfg['Q1']['para'].update(probFreq=p['readoutFrequencyHz'],probCarryScale=p['readoutAmplitude'])
    cfg['Q1']['gate']['U_gates']['z_amp']=0
    setting=SettingConfig(cfg)
    compiler=ExtensionCompiler(setting,opt=False)
    # Numeric fixed IR; compile_by_auto never receives strings to evaluate.
    prep={**p,'label':'prep01' if request['kind']=='shelve' else 'drive01'}
    circuit=[('rz',1,[p['virtualZRad']]),('rx',1,[p['rotationRad']],{'id':'prep01','pulse':prep})]
    if request['kind']=='shelve':
        probe={**p,'label':'probe12','frequencyHz':p['f12Hz'],'durationNs':p['probeDurationNs'],'amplitude':p['probeAmplitude']}
        circuit.append(('rx',1,[math.pi],{'id':'probe12','pulse':probe}))
    circuit.append(('measure',1,[]))
    qubits,pulses,measured=compiler.compile(circuit)
    sequence=SegmentedCarrierSequence(qubits,setting,group='compile_only_no_hardware')
    sequence.add_pulses(pulses)
    sequence.perform_sequence_awg()
    adc={'startNs':ro['triggerDelay']*1e9,'sampleRateHz':ro['ADC_sRate'],'sampleCount':ro['pointNum'],
         'durationNs':ro['pointNum']/ro['ADC_sRate']*1e9,'acquired':False,'source':'configured_timing_only'}
    return sequence,adc,cfg
