"""Actual native single-carrier compilation for bounded long IRB circuits.

The native Sequence sums native envelopes before its one absolute-time carrier
mix. This is byte-identical to V3 per-segment mixing for disjoint same-carrier
pulses, without allocating a full 83 us carrier array for every Clifford gate.
"""
from copy import deepcopy
import types
import hashlib
import numpy as np
from core.utils import SettingConfig
from core.experiment.sequence import Sequence
from core.experiment.pulse import PulseType,DrivePulse
from adapters.science_runtime.extension import ExtensionCompiler,check_peak
from adapters.science_v3.compiler import wave_identity
from . import beta


class EmpiricalCompiler(ExtensionCompiler):
    def get_xy_seq(self,q,theta=np.pi,phase=0,pulse=None):
        p=pulse or {}
        if p.get('dragMode')!=beta.MODE:return super().get_xy_seq(q,theta,phase,pulse)
        if p.get('lambda1') is not None or p.get('anharmonicityHz') is not None:raise ValueError('Empirical pulse may not substitute alpha')
        coefficient=beta.finite_beta(p['betaSeconds']);cfg=self.get_q_cfg(q)
        if cfg.xy_srate(q)!=4e9:raise ValueError('Empirical pulse requires the calibrated 4 GHz native grid')
        value=DrivePulse(duration=p['durationNs']*1e-9+2e-9,buff=2e-9,A=p['amplitude']*theta/np.pi,
            srate=cfg.xy_srate(q),mrate=p['frequencyHz'],shape=p['shape'],
            shape_params={'sigma':p['sigmaNs']*1e-9} if p['shape']=='Gaussian' else {},
            phase=phase+p['phaseRad'],drag=coefficient!=0,beta=coefficient,
            pulse_type=PulseType.XY,label=p['label']).on(q)
        value.empirical_beta=True
        return value

class SingleCarrierSequence(Sequence):
    def perform_sequence_awg(self):
        if not self._preserve_existing_dc():raise ValueError('IRB requires preserved existing DC')
        carriers={pulse.mrate for values in self.pulse_list_per_qubit.values() for _,pulse in values if pulse.pulse_type==PulseType.XY}
        if len(carriers)>1:raise ValueError('IRB permits exactly one frozen f01 carrier')
        for values in self.pulse_list_per_qubit.values():
            intervals=sorted(interval for interval,pulse in values if pulse.pulse_type==PulseType.XY)
            if any(a[1]>b[0]+1e-15 for a,b in zip(intervals,intervals[1:])):raise ValueError('IRB native XY segments overlap')
        super().perform_sequence_awg()
        # Per-segment V3 aggregation adds each RF segment into +0. Native one-
        # carrier mixing retains IEEE -0 outside pulses. Canonicalize only exact
        # zeros, preserving every nonzero native sample bit and the upload hash.
        for name,wave in self.waves._waves.items():
            if name.startswith('xy_'):wave.data[wave.data==0]=0.
        self.segments=[];self.baseband_arrays={};index=0;roindex=0
        for q,values in self.pulse_list_per_qubit.items():
            for (start,end),pulse in values:
                if pulse.pulse_type==PulseType.XY:
                    wave=self.get_xy_wave(q);start+=self.gate_start;end+=self.gate_start;key='xy'+str(index);index+=1;channel='xy'
                elif pulse.pulse_type==PulseType.RO:
                    wave=self.get_ro_wave(q);key='ro'+str(roindex);roindex+=1;channel='readout'
                else:raise ValueError('IRB permits XY/readout native samples only')
                samples=pulse.sample_generator(self.param_ctx)();i0=round(start*wave.srate);i1=round(end*wave.srate)
                if i0<0 or i1>wave.n or i1-i0!=len(samples):raise ValueError('Native IRB segment clipped or padded')
                check_peak(samples,key+' native baseband')
                self.segments.append({'id':key,'label':pulse.label,'channel':channel,'startSample':i0,'endSample':i1,
                    'sampleCount':len(samples),'sampleRateHz':wave.srate,'envelopeDurationNs':pulse.wave_width*1e9,
                    'bufferNs':pulse.buff*1e9,'shape':pulse.shape,'shapeParameters':pulse.shape_params,
                    'phaseRad':getattr(pulse,'phase',0),'frequencyHz':pulse.mrate,'betaSeconds':pulse.beta if pulse.drag else 0,
                    'source':'native_DrivePulse_and_Sequence_single_carrier'})
                if pulse.pulse_type==PulseType.XY:
                    sample_i=np.ascontiguousarray(samples.real);sample_q=np.ascontiguousarray(samples.imag)
                    self.baseband_arrays[key+'_i']=sample_i;self.baseband_arrays[key+'_q']=sample_q
                    self.segments[-1].update(coefficientMode=beta.MODE if getattr(pulse,'empirical_beta',False) else 'normalized_alpha',
                        coefficientUnit='s' if getattr(pulse,'empirical_beta',False) else 'dimensionless',derivativeMultiplierUnit='s',
                        basebandI={'array':key+'_i','sha256':hashlib.sha256(sample_i.tobytes()).hexdigest(),'dtype':sample_i.dtype.str,'samples':len(sample_i)},
                        basebandQ={'array':key+'_q','sha256':hashlib.sha256(sample_q.tobytes()).hexdigest(),'dtype':sample_q.dtype.str,'samples':len(sample_q)},
                        peakNormalized=float(np.max(np.abs(samples))),
                        maximumSampleSlopePerSecond=float(np.max(np.abs(np.diff(samples)),initial=0)*pulse.srate),
                        slopeScope='Native discrete baseband slope; not a certified hardware slew limit')

def compile_row(row,base,parameters):
    cfg=deepcopy(base);p=parameters
    cfg['ADC_Meas']['global'].update(preserve_existing_dc=True,readout_only=False)
    cfg['ADC_Meas']['global']['timeSeq'].update(probT0=80e-6,gapTime=300e-9,wdLen=80e-6+p['readout_width_s']+1e-6,fill=True)
    cfg['ADC_Meas']['B1']['para'].update(width=p['readout_width_s'],shots=p['shots'],triggerDelay=80e-6)
    cfg['Q1']['para'].update(probFreq=p['readout_hz'],probCarryFreq=p['readout_hz'],probCarryScale=p['readout_amp'])
    cfg['Q1']['gate']['U_gates']['z_amp']=0
    setting=SettingConfig(cfg);compiler=EmpiricalCompiler(setting,opt=False)
    gates=[('r'+g['axis'],1,[g['theta']],{'id':'g'+str(n),'pulse':g['pulse']}) for n,g in enumerate(row['gates'])]
    gates.append(('measure',1,[]));qubits,pulses,_=compiler.compile(gates)
    seq=SingleCarrierSequence(qubits,setting,group='agent_f8');seq.add_pulses(pulses);seq.perform_sequence_awg()
    expected={w.name:wave_identity(w) for w in seq.waves._waves.values()};frozen=deepcopy(expected)
    if set(name.split('_')[0] for name in expected)!={'xy','ro'}:raise ValueError('IRB only refreshes XY/RO')
    perform=seq.perform_sequence_awg
    def guarded(self):
        perform()
        if {w.name:wave_identity(w) for w in self.waves._waves.values()}!=frozen:raise ValueError('Native IRB regeneration changed compiled upload bytes')
    seq.perform_sequence_awg=types.MethodType(guarded,seq)
    return seq,expected


def archive_row(path,seq,expected):
    from adapters.science_v3.compiler import archive_row as original
    if not getattr(seq,'baseband_arrays',None):return original(path,seq,expected)
    np.savez_compressed(path,**{w.name:np.ascontiguousarray(w.get_data()) for w in seq.waves._waves.values()},**seq.baseband_arrays)
    return {'file':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'waves':expected,'segments':seq.segments}
