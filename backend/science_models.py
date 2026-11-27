"""Bounded scientific previews. No device, file, database or model-service I/O.

Conventions: z=+1 is |0>, phases in degrees, time in ns, frequencies in Hz.
DRAG Q=-alpha_ns*dI/dt_ns is an explicit preview convention, not a device calibration.
References: https://arxiv.org/abs/0901.0534 ;
https://qiskit-community.github.io/qiskit-experiments/stable/0.9/manuals/characterization/t2hahn.html
"""
from __future__ import annotations

import cmath
import math
from copy import deepcopy

from .common import APIError, number

MODEL_VERSION = 'qcal-science/3'
MAX_POINTS = 2049
MAX_REPLAY_WORK = 200000


def fail(message, code='SCIENCE_INPUT_INVALID'):
    raise APIError(code, message)


def scalar(data, key, default=None, low=None, high=None, integer=False):
    value = data.get(key, default)
    if not number(value) or (integer and int(value) != value) or (low is not None and value < low) or (high is not None and value > high):
        label = {'regularization': '正则项', 'sampleIntervalNs': '采样间隔', 'maxGain': '最大补偿增益',
                 'latencySamples': '补偿延迟', 'phaseDeg': 'XY 初相', 'readoutPhaseDeg': '读出初相',
                 'durationNs': '演化时间', 'rabiHz': 'Rabi 频率', 'detuningHz': '失谐',
                 't1Ns': 'T₁', 't2Ns': 'T₂', 'piPulses': 'π 脉冲数', 'points': '点数'}.get(key, key)
        limits = f'；允许范围 {low:g}～{high:g}' if low is not None and high is not None else ''
        fail(f'{label}数值无效{limits}')
    return int(value) if integer else float(value)


def vector(value, label, *, minimum=1, maximum=MAX_POINTS, missing=False, increasing=False):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        fail(f'{label} 点数须在 {minimum}～{maximum} 之间')
    if any(not (missing and v is None) and not number(v) for v in value):
        fail(f'{label} 包含无效数值')
    if increasing and any(a >= b for a, b in zip(value, value[1:])):
        fail(f'{label} 必须严格递增')
    return deepcopy(value)


def series(ident, label, values, quantity, unit):
    return dict(id=ident, label=label, values=values, quantity=quantity, unit=unit)


def result(kind, x, traces, *, xname='Time', xunit='ns', **extra):
    return dict(kind=kind, modelVersion=MODEL_VERSION, dataKind='simulation', source='model',
                dispatched=False, axes={'x': {'name': xname, 'unit': xunit, 'values': x}},
                series=traces, metrics={}, **extra)


def pulse(data, drag=False):
    amp = scalar(data, 'amplitude', .5, 0, 1)
    duration = scalar(data, 'durationNs', 20, .01, 1e8)
    phase_unknown = 'phaseDeg' in data and data['phaseDeg'] is None
    phase = 0 if phase_unknown else scalar(data, 'phaseDeg', 0, -360000, 360000)
    freq = scalar(data, 'frequencyHz', 5e9, 0, 1e12)
    points = scalar(data, 'points', 201, 3, MAX_POINTS, True)
    delay = scalar(data, 'delayNs', 0, 0, 1e9)
    gap = scalar(data, 'xyGapNs', 2, 0, 1e9)
    count = scalar(data, 'xyCount', 2, 1, 32, True)
    read_width = scalar(data, 'readoutDurationNs', 2000, .01, 1e9)
    adc_delay = scalar(data, 'acquisitionDelayNs', 50, 0, 1e9)
    adc_width = scalar(data, 'acquisitionDurationNs', 400, .01, 1e9)
    read_freq = scalar(data, 'readoutFrequencyHz', 7e9, 0, 1e12)
    read_phase_unknown = 'readoutPhaseDeg' in data and data['readoutPhaseDeg'] is None
    read_phase = None if read_phase_unknown else scalar(data, 'readoutPhaseDeg', 0, -360000, 360000)
    z_amp = scalar(data, 'zAmplitude', 0, -1, 1)
    clock = scalar(data, 'clockHz', 1e8, 1, 1e12)
    shape = data.get('shape', 'gaussian')
    if shape not in ('gaussian', 'cosine', 'square') or (drag and shape == 'square'):
        fail('DRAG 需平滑包络；支持 Gaussian 或 cosine' if drag else '未知脉冲形状')
    sigma = scalar(data, 'sigmaNs', duration/6, duration/1000, duration*10)
    alpha = scalar(data, 'alphaNs', .5, -1e6, 1e6) if drag else 0
    ts = [i*duration/(points-1) for i in range(points)]
    env, deriv = [], []
    floor = math.exp(-.5*(duration/2/sigma)**2)
    for t in ts:
        if shape == 'gaussian':
            g = math.exp(-.5*((t-duration/2)/sigma)**2)
            e = amp*(g-floor)/(1-floor)
            d = -amp*(t-duration/2)*g/(sigma*sigma*(1-floor))
        elif shape == 'cosine':
            e = amp*(1-math.cos(2*math.pi*t/duration))/2
            d = amp*math.pi/duration*math.sin(2*math.pi*t/duration)
        else:
            e, d = amp, 0
        env.append(e); deriv.append(d)
    angle = math.radians(phase)
    quadrature = [-alpha*d for d in deriv]
    i_values = [e*math.cos(angle)-q*math.sin(angle) for e,q in zip(env,quadrature)]
    q_values = [e*math.sin(angle)+q*math.cos(angle) for e,q in zip(env,quadrature)]
    segments = [dict(id=f'xy-{i+1}', label=f'XY {i+1}', channel='XY',
                     startNs=delay+i*(duration+gap), endNs=delay+i*(duration+gap)+duration,
                     amplitude=amp, frequencyHz=freq, phaseDeg=None if phase_unknown else phase, durationNs=duration) for i in range(count)]
    read_start = segments[-1]['endNs']
    end = max(read_start+read_width, read_start+adc_delay+adc_width)
    segments.extend([
        dict(id='z',label='Z',channel='Z',startNs=0,endNs=end,amplitude=z_amp,frequencyHz=0,phaseDeg=0,durationNs=end),
        dict(id='read-in',label='Read in',channel='Read in',startNs=read_start,endNs=read_start+read_width,
             amplitude=scalar(data,'readoutAmplitude',.5,0,1),frequencyHz=read_freq,phaseDeg=read_phase,durationNs=read_width),
        dict(id='read-out',label='Read out',channel='Read out',startNs=read_start+adc_delay,
             endNs=read_start+adc_delay+adc_width,amplitude=None,frequencyHz=read_freq,phaseDeg=read_phase,durationNs=adc_width)])
    output = result('drag' if drag else 'pulse', ts,
                    [series('i','I · 零参考相位示意（实际初相未提供）' if phase_unknown else 'I',i_values,'iq_envelope','normalized'),
                     series('q','Q · 零参考相位示意（实际初相未提供）' if phase_unknown else 'Q',q_values,'iq_envelope','normalized')],
                    segments=segments, clockHz=clock, convention='complex envelope in rotating frame; carrier frequency is metadata',
                    carrierFrequencyHz=freq, gateFidelityStatus='not_measured',
                    phaseReference={'xy':'unknown_zero_reference_model' if phase_unknown else 'configured',
                                    'readout':'unknown' if read_phase_unknown else 'configured'})
    output['metrics'] = dict(peakEnvelope=max(env), peakQuadrature=max(map(abs,q_values)),
                             peakIQ=max(math.hypot(i,q) for i,q in zip(i_values,q_values)),
                             alphaNs=alpha, gateFidelity=None, requiresAmplitudeReview=max(math.hypot(i,q) for i,q in zip(i_values,q_values))>1)
    return output


def _dft(values, inverse=False):
    n=len(values); sign=1 if inverse else -1
    return [sum(v*cmath.exp(sign*2j*math.pi*k*j/n) for j,v in enumerate(values))/(n if inverse else 1) for k in range(n)]


def predistortion(data):
    target=vector(data.get('target'), '目标波形', minimum=2, maximum=256)
    impulse=vector(data.get('impulseResponse'), '链路冲激响应', maximum=64)
    if any(abs(v)>1e6 for v in target+impulse):fail('预失真样点幅度超出计算范围')
    dt=scalar(data,'sampleIntervalNs',1,.001,1e9)
    reg=scalar(data,'regularization',.01,1e-12,1e6)
    gain=scalar(data,'maxGain',5,1,100)
    latency=scalar(data,'latencySamples',0,0,256,True)
    delayed=[0.]*latency+target
    n=1
    while n<len(delayed)+len(impulse)-1:n*=2
    padded=delayed+[0.]*(n-len(delayed)); h=_dft(impulse+[0.]*(n-len(impulse)))
    spectrum=_dft(padded); inv=[]
    for v in h:
        correction=v.conjugate()/(abs(v)**2+reg)
        if abs(correction)>gain: correction*=gain/abs(correction)
        inv.append(correction)
    sent=[s*c for s,c in zip(spectrum,inv)]
    pre=[v.real for v in _dft(sent,True)]
    # The finite waveform is emitted once. Validate its causal, linear response,
    # including the tail; circular FFT convolution would wrap late energy to t=0.
    recon=[0.]*(n+len(impulse)-1)
    for i,value in enumerate(pre):
        for j,response in enumerate(impulse):recon[i+j]+=value*response
    padded.extend([0.]*(len(impulse)-1))
    pre.extend([0.]*(len(impulse)-1))
    out=result('predistortion',[i*dt for i in range(len(recon))],
               [series('target','延迟后的目标' if latency else '目标',padded,'waveform','normalized'),series('predistorted','预补偿',pre,'waveform','normalized'),
                series('reconstructed','链路模型响应',recon,'waveform','normalized')],
               transferSource='user_supplied_impulse_response', boundary='finite causal waveform; full linear convolution with tail; explicit target latency',
               transfer={'frequencyHz':[i/(n*dt*1e-9) for i in range(n)],'real':[v.real for v in h],'imag':[v.imag for v in h]})
    out['metrics']={'maxAppliedGain':max(map(abs,inv)), 'rmse':math.sqrt(sum((a-b)**2 for a,b in zip(padded,recon))/len(recon)),
                    'peakPredistorted':max(map(abs,pre)), 'regularization':reg,'latencySamples':latency,'latencyNs':latency*dt}
    return out


def _rotate(v, axis, angle):
    c,s=math.cos(angle),math.sin(angle); dot=sum(a*b for a,b in zip(v,axis))
    cross=(axis[1]*v[2]-axis[2]*v[1],axis[2]*v[0]-axis[0]*v[2],axis[0]*v[1]-axis[1]*v[0])
    return tuple(a*c+b*s+k*dot*(1-c) for a,b,k in zip(v,cross,axis))


def bloch(data):
    sequence=data.get('sequence','x')
    if sequence not in ('x','t1','ramsey','echo','cpmg'):fail('未知布洛赫序列')
    duration=scalar(data,'durationNs',1000,.001,1e10)
    points=scalar(data,'points',201,2,MAX_POINTS,True)
    rabi=scalar(data,'rabiHz',1e7,0,1e10)
    detuning=scalar(data,'detuningHz',0,-1e10,1e10)
    t1=scalar(data,'t1Ns',50000,.001,1e16); t2=scalar(data,'t2Ns',30000,.001,1e16)
    if t2>2*t1*(1+1e-12):fail('T₂ 不得超过 2T₁')
    initial=data.get('initialState', '0' if sequence=='x' else '1' if sequence=='t1' else '+x')
    vectors={'0':(0.,0.,1.),'1':(0.,0.,-1.),'+x':(1.,0.,0.),'+y':(0.,1.,0.)}
    if initial not in vectors:fail('初态须为 0、1、+x 或 +y')
    phase=math.radians(scalar(data,'phaseDeg',0,-360000,360000))
    analysis_hz=scalar(data,'analysisPhaseHz',0,-1e10,1e10)
    analysis_phase=math.radians(scalar(data,'analysisPhaseDeg',0,-360000,360000))
    readout_sign=scalar(data,'readoutSign',1,-1,1,True)
    if readout_sign not in (-1,1):fail('分析脉冲投影符号须为 ±1')
    refocus_axis=data.get('refocusAxis','x')
    if refocus_axis not in ('x','y'):fail('重聚焦旋转轴须为 x 或 y')
    times=vector(data['timesNs'],'时间轴',minimum=1,increasing=True) if 'timesNs' in data else [i*duration/(points-1) for i in range(points)]
    if times[0]<0 or times[-1]>1e10:fail('时间轴超出模型范围')
    duration=times[-1]
    count=1 if sequence=='echo' else scalar(data,'piPulses',4,1,64,True) if sequence=='cpmg' else 0
    pulse_times=[duration*(i+.5)/count for i in range(count)] if count else []
    segments=[dict(id=f'pi-{i+1}',kind='refocus',label='π',channel='XY',startNs=t,endNs=t) for i,t in enumerate(pulse_times)]
    state=vectors[initial]; prior=0.; pulse_index=0; trajectory=[]; steps=0
    def evolve(v, dt):
        nonlocal steps
        if dt<=0:return v
        if sequence!='x':
            rotated=_rotate(v,(0,0,1),2*math.pi*detuning*dt*1e-9)
            return rotated[0]*math.exp(-dt/t2),rotated[1]*math.exp(-dt/t2),1+(rotated[2]-1)*math.exp(-dt/t1)
        omega=(2*math.pi*rabi*1e-9*math.cos(phase),2*math.pi*rabi*1e-9*math.sin(phase),2*math.pi*detuning*1e-9)
        norm=math.sqrt(sum(x*x for x in omega)); n=max(1,math.ceil(max(norm*dt,dt/t1,dt/t2)/.08));steps+=n
        if steps>200000:fail('演化采样过密，请缩短时间或降低频率')
        h=dt/n
        for _ in range(n):
            v=(v[0]*math.exp(-h/(2*t2)),v[1]*math.exp(-h/(2*t2)),1+(v[2]-1)*math.exp(-h/(2*t1)))
            if norm:v=_rotate(v,tuple(x/norm for x in omega),norm*h)
            v=(v[0]*math.exp(-h/(2*t2)),v[1]*math.exp(-h/(2*t2)),1+(v[2]-1)*math.exp(-h/(2*t1)))
        return v
    for t in times:
        while pulse_index<len(pulse_times) and pulse_times[pulse_index]<=t:
            pt=pulse_times[pulse_index];state=evolve(state,pt-prior);prior=pt
            state=_rotate(state,(1,0,0) if refocus_axis=='x' else (0,1,0),math.pi);pulse_index+=1
        state=evolve(state,t-prior);prior=t
        x,y,z=state
        angle=analysis_phase+2*math.pi*analysis_hz*t*1e-9
        projected=readout_sign*(x*math.cos(angle)-y*math.sin(angle))
        trajectory.append(dict(t=t,x=x,y=y,z=z,p1=max(0.,min(1.,(1-z)/2)),
                               readoutP1=max(0.,min(1.,(1-projected)/2)) if sequence in ('ramsey','echo','cpmg') else max(0.,min(1.,(1-z)/2))))
    out=result('bloch',times,[series('p1','读出概率',[p['readoutP1'] for p in trajectory],'excited_probability','probability')],
               trajectory=trajectory,segments=segments,sequence=sequence,initialState=initial,
               projection='analysis phase rotation followed by signed transverse projection' if sequence in ('ramsey','echo','cpmg') else 'z',
               analysisPulse={'phaseRampHz':analysis_hz,'phaseOffsetDeg':math.degrees(analysis_phase),'projectionSign':readout_sign},refocusAxis=refocus_axis,
               approximation='Markovian T1/T2, static detuning, ideal instantaneous refocusing pulses; no inferred decoupling gain')
    out['metrics']={'t1Ns':t1,'t2Ns':t2,'piPulses':count,'finalP1':trajectory[-1]['readoutP1']}
    return out


def bloch_scan_endpoints(parameters, times):
    """Complete a separate sequence at every delay, as in acquisition scans.

    Kept separate from bloch(): its trajectory is one fixed-duration experiment.
    The comparison service and explicit scan preview share these endpoint rules.
    """
    return [bloch({**parameters, 'timesNs': [0] if t == 0 else [0, t],
                   'durationNs': max(t, .001), 'points': 2})['trajectory'][-1]['readoutP1']
            for t in times]


def _replay_work(data, times, selected, trajectory_points):
    """Bound the whole preview before multiplying per-experiment solver limits."""
    sequence = data.get('sequence', 'x')
    if sequence not in ('x', 't1', 'ramsey', 'echo', 'cpmg'):
        fail('未知布洛赫序列')
    count = (1 if sequence == 'echo' else
             scalar(data, 'piPulses', 4, 1, 64, True) if sequence == 'cpmg' else 0)
    if sequence != 'x':
        # Each ideal pulse requires an evolution and a rotation; include samples.
        work = len(times) * (2 * count + 3) + trajectory_points + 2 * count
    else:
        rabi = scalar(data, 'rabiHz', 1e7, 0, 1e10)
        detuning = scalar(data, 'detuningHz', 0, -1e10, 1e10)
        t1 = scalar(data, 't1Ns', 50000, .001, 1e16)
        t2 = scalar(data, 't2Ns', 30000, .001, 1e16)
        rate = max(2 * math.pi * math.hypot(rabi, detuning) * 1e-9, 1/t1, 1/t2)
        steps = lambda dt: 0 if dt <= 0 else max(1, math.ceil(rate * dt / .08))
        work = sum(steps(t) for t in times)
        # The selected trajectory is computed on its own equally spaced grid.
        # An extra step per interval bounds floating point spacing roundoff.
        work += (trajectory_points-1) * (steps(selected/(trajectory_points-1)) + 1)
    if work > MAX_REPLAY_WORK:
        fail('扫描模型总计算量过大，请减少扫描点、π脉冲数或演化时间', 'SCIENCE_MODEL_BUDGET')
    return work


def bloch_scan_replay(data):
    """Scan endpoints plus an internal trajectory for one explicitly chosen delay."""
    times = vector(data.get('timesNs'), '扫描时间轴', minimum=1, increasing=True)
    if times[0] < 0 or times[-1] > 1e10:
        fail('时间轴超出模型范围')
    selected = data.get('selectedDelayNs', times[-1])
    if not number(selected) or selected not in times:
        fail('所选等待时间须精确对应原扫描点')
    points = scalar(data, 'trajectoryPoints', 201, 2, MAX_POINTS, True)
    work = _replay_work(data, times, selected, points)
    inner_times = [0] if selected == 0 else [selected*i/(points-1) for i in range(points)]
    out = bloch({**data, 'timesNs': inner_times, 'durationNs': max(selected, .001), 'points': points})
    endpoints = bloch_scan_endpoints(data, times)
    out['axes']['x']['values'] = times
    out['series'][0]['values'] = endpoints
    out['metrics'].update(finalP1=endpoints[-1], selectedReadoutP1=endpoints[times.index(selected)])
    out.update(modelVersion='qcal-bloch-scan-replay/1', replayScan=True,
               selectedScanIndex=times.index(selected), selectedDelayNs=selected,
               trajectoryMeaning='one complete experiment at selectedDelayNs; internal time is not the scan axis',
               scanInterpretation='independent_scan_endpoints',
               computationBudget={'estimatedWork': work, 'maximumWork': MAX_REPLAY_WORK})
    return out


def _counts(values):
    if not isinstance(values,list) or len(values)!=2 or any(not isinstance(row,list) or len(row)!=2 for row in values):fail('计数表须为 2×2，行是制备逻辑态、列是判别逻辑态')
    if any(type(v) is not int or not 0<=v<=10**9 for row in values for v in row) or any(sum(row)==0 for row in values):fail('计数须为非负整数，每个制备态至少一个样本')
    probs=[[v/sum(row) for v in row] for row in values]
    e0,e1=probs[0][1],probs[1][0]
    err=.5*math.sqrt(e0*(1-e0)/sum(values[0])+e1*(1-e1)/sum(values[1]))
    def wilson(success,total):
        z=1.959963984540054;p=success/total;d=1+z*z/total
        mid=(p+z*z/(2*total))/d;half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/d
        return [max(0,mid-half),min(1,mid+half)]
    intervals=[wilson(values[i][i],sum(values[i])) for i in (0,1)]
    return dict(counts=values,confusion=probs,error0=e0,error1=e1,fidelity=1-(e0+e1)/2,
                standardError=err,assignmentIntervals95=intervals,samples=[sum(row) for row in values])


def shelve(data):
    f01=scalar(data,'f01Hz',5e9,1,1e12)
    if 'twoPhotonFrequencyHz' in data:
        f12=2*scalar(data,'twoPhotonFrequencyHz',None,1,1e12)-f01
        formula='f12 = 2*f02_two_photon - f01'
    else:
        f12=f01+scalar(data,'anharmonicityHz',-250e6,-1e11,1e11);formula='f12 = f01 + anharmonicity'
    if f12<=0:fail('估计 f₁₂ 必须为正')
    off=_counts(data.get('counts'));on=_counts(data['shelvedCounts']) if 'shelvedCounts' in data else None
    out=result('shelve',[0,1],[series('without','关闭SHELVE',[off['confusion'][0][0],off['confusion'][1][1]],'assignment_probability','probability')],
               xname='Prepared logical state',xunit='state',requiresIndependentCalibration=True,calibrationStatus='not_verified',
               logicalMapping={'0':'original |0>','1':'original |1>, optionally transferred to |2> before readout'},formula=formula)
    out['source']='provided_counts';out['dataKind']='provided_data'
    out['metrics']={'f12EstimateHz':f12,'withoutShelve':off,'withShelve':on,'fidelityGain':on['fidelity']-off['fidelity'] if on else None}
    if on:out['series'].append(series('with','启用SHELVE',[on['confusion'][0][0],on['confusion'][1][1]],'assignment_probability','probability'))
    return out


def flux(data):
    bias=vector(data.get('biasValues'),'偏置轴',minimum=1,maximum=512,increasing=True)
    freq=vector(data.get('frequencyHz'),'频率轴',minimum=2,maximum=2049,increasing=True)
    if freq[0]<=0 or len(bias)*len(freq)>100000:fail('频率或二维点数超出范围')
    matrix=data.get('amplitude')
    if not isinstance(matrix,list) or len(matrix)!=len(bias):fail('幅度矩阵行数须与偏置点数一致')
    matrix=[vector(row,'谱线',minimum=len(freq),maximum=len(freq),missing=True) for row in matrix]
    selected=scalar(data,'selectedBiasIndex',0,0,len(bias)-1,True)
    unit=data.get('signalUnit','a.u.');biasunit=data.get('biasUnit','V')
    if unit not in ('a.u.','V','dB') or biasunit not in ('V','A','Phi0'):fail('未知单位')
    values=matrix[selected];valid=[(v,i) for i,v in enumerate(values) if v is not None]
    out=result('flux',freq,[series('slice','原始截线',values,'s21_magnitude',unit)],xname='Frequency',xunit='Hz',
               grid={'x':freq,'y':bias,'values':matrix,'xUnit':'Hz','yUnit':biasunit,'signalUnit':unit},
               selectedBias=bias[selected],purpose=data.get('purpose','work'))
    out['source']='provided_grid';out['dataKind']='provided_data'
    out['metrics']={'selectedMinimumHz':freq[min(valid)[1]] if valid else None,'selectedBiasIndex':selected,
                    'missingPoints':sum(v is None for row in matrix for v in row)}
    return out


def preview(kind, data):
    if not isinstance(data,dict):fail('输入应为参数对象')
    if kind=='bloch' and 'replayScan' in data:
        if type(data['replayScan']) is not bool:fail('扫描回放标记须为布尔值')
        if data['replayScan']:return bloch_scan_replay(data)
    handlers={'pulse':pulse,'drag':lambda d:pulse(d,True),'predistortion':predistortion,'bloch':bloch,'shelve':shelve,'flux':flux}
    if kind not in handlers:fail('该预览类型尚未接入','SCIENCE_KIND_UNSUPPORTED')
    return handlers[kind](data)
