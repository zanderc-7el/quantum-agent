"""Finite timing view of a built native plan. No compiler, device or file I/O.

Only the inspected ArcLight type-1 circuits are covered. The native dictionaries
choose gates, sweep values, static overrides and timing; this module interprets
their elementary X/Y/RX/RY/RZ/BLANK/MEASURE timing, not an illustrative model.
"""
from copy import deepcopy
import math

from .common import APIError

POLICY='f8_validated_short_half_dc_buff2ns_v1'
METHODS=frozenset(('ro_resonator_survey','ro_power_shift','sq_spectroscopy','sq_rabi_time',
    'sq_rabi_frequency','sq_rabi_amplitude','ro_iq01','ro_optimize','ro_optimize_frequency',
    'ro_s21_ge','sq_t1','sq_ramsey','sq_echo','sq_cpmg'))


def unavailable(reason):
    return {'status':'unavailable','reason':reason,'segments':[],'axes':[],
            'referenceClockHz':None,'zSetting':None}


def number(value,*,minimum=None):
    if type(value) not in (int,float) or not math.isfinite(value) or (minimum is not None and value<minimum):
        raise ValueError('执行计划缺少有限时序参数')
    return value


def _set(target,path,value):
    for part in path[:-1]:
        if not isinstance(target,dict) or part not in target:raise ValueError('未覆盖的原生参数绑定')
        target=target[part]
    if not isinstance(target,dict):raise ValueError('未覆盖的原生参数绑定')
    target[path[-1]]=value


def summarize(plan,scan_indices=None):
    if plan.get('adapter')!='arclight' or plan.get('procedureId') not in METHODS:
        return unavailable('此方法的完整原生时序尚未覆盖；参数仍由控制器校验，未绘制替代模型。')
    try:return _summarize(plan,scan_indices)
    except APIError:raise
    except (KeyError,TypeError,ValueError,IndexError,OverflowError,StopIteration) as exc:
        return unavailable('执行计划时序未覆盖或缺少来源：'+str(exc))


def _summarize(plan,scan_indices):
    jobs=plan['jobs']
    if len(jobs)!=1:return unavailable('多任务门表的完整时序尚未覆盖，未绘制替代模型。')
    job=jobs[0];spec=deepcopy(job['spec']);cfg=deepcopy(job['config'])
    if str(spec['meta']['type'])!='1':raise ValueError('当前仅覆盖固定电路与其扫描点')
    if spec['meta'].get('pulse_compatibility') not in (None,POLICY):raise ValueError('脉冲策略未覆盖')
    policy=spec['meta'].get('pulse_compatibility')
    for key,value in spec.get('static_config',{}).items():
        if not key.startswith('cfg.'):raise ValueError('静态绑定未覆盖')
        _set(cfg,key.split('.')[1:],deepcopy(value))
    sweeps=spec['sweeps'];mode=spec['meta']['expand_mode']
    if mode not in ('zip','cartesian') or len(sweeps)>70 or sum(len(a['values']) for a in sweeps)>12000:
        raise ValueError('扫描规模超出预览范围')
    indices=scan_indices or [0]*len(sweeps)
    if not isinstance(indices,list) or len(indices)!=len(sweeps) or any(type(i) is not int or i<0 for i in indices):
        raise APIError('PULSE_PREVIEW_POINT','请选择当前计划中的有效扫描点')
    if mode=='zip' and len(set(indices))>1:raise APIError('PULSE_PREVIEW_POINT','联动扫描须选择同一索引')
    circuits=spec['circuit_variants']['circuits'];variant=spec['circuit_variants']['default'];axes=[]
    for sweep,index in zip(sweeps,indices):
        values=sweep['values'];binding=sweep['bindings']
        if not values or len(values)>161 or index>=len(values):raise APIError('PULSE_PREVIEW_POINT','扫描点已改变，请重新选择')
        if any(not ((type(v) in (int,float) and math.isfinite(v)) or (isinstance(v,str) and len(v)<=64)) for v in values):
            raise ValueError('扫描轴值未覆盖')
        value=values[index];axes.append({'name':sweep['name'],'binding':binding,'values':values,'index':index})
        if binding.startswith('cfg.'):_set(cfg,binding.split('.')[1:],value)
        elif binding=='exp.cir.default':variant=value
        elif binding.startswith('exp.cir.') and binding.endswith('.params'):
            parts=binding.split('.');circuit=circuits[parts[2]]
            gate=next(g for g in circuit['sequences'] if g['id']==parts[3]);gate['params']=[value]
        else:raise ValueError('扫描绑定未覆盖')
    q=spec['meta']['qubit'];qc=cfg[f'Q{q}'];u=qc['gate']['U_gates'];adc=cfg['ADC_Meas'][qc['inst']['ADC_PROB']]['para']
    timing=cfg['ADC_Meas']['global']['timeSeq']
    read_start=number(timing['probT0'],minimum=0);gap=number(timing['gapTime'],minimum=0)
    width=number(u['pi_len'],minimum=0);buffer=number(u['buff'],minimum=0);amplitude=number(u['pi_amp'],minimum=0)
    if u.get('shape')!='DC' or u.get('use_drag') is not False:raise ValueError('该包络不属于已覆盖的实际 DC 策略')
    if u.get('use_decompos',False) or any(number(u.get(k,0))!=0 for k in ('z_amp','detuning','lambda1')) or number(qc['gate'].get('XY_gates',{}).get('Y_shift',0))!=0:
        raise ValueError('额外分解、Z 耦合或频率 / 相位修正未覆盖')
    if policy and not math.isclose(buffer,2e-9,rel_tol=0,abs_tol=1e-18):raise ValueError('原生缓冲策略不一致')
    gates=circuits[variant]['sequences']
    if len(gates)>160:raise ValueError('门数超出预览范围')
    segments=[];cursor=0.;phase=0.;measures=0
    for gate in gates:
        name=gate['gate'].upper();args=gate.get('params',[])
        if gate.get('pulse') or gate.get('ctrls') or gate.get('targets')!=[0]:raise ValueError('门覆盖或多比特时序未覆盖')
        if name=='MEASURE':measures+=1;continue
        if measures:raise ValueError('测量后的门序列未覆盖')
        if name=='RZ':phase+=number(args[0]);continue
        if name=='BLANK':
            duration=number(args[0],minimum=0)
            if duration:segments.append({'id':gate['id'],'channel':'Wait','label':'等待',
                'startS':cursor,'durationS':duration,'amplitude':None,'frequencyHz':None,'phaseRad':None})
        elif name in ('X','Y','RX','RY'):
            theta=math.pi if name in ('X','Y') else number(args[0]);theta%=2*math.pi;rotation_phase=-phase
            if theta>math.pi:theta=2*math.pi-theta;rotation_phase+=math.pi
            if name in ('Y','RY'):rotation_phase+=math.pi/2
            half=bool(policy and math.isclose(theta,math.pi/2,rel_tol=0,abs_tol=1e-12))
            envelope=width/2 if half else width;duration=envelope+buffer
            amp=amplitude if half else amplitude*theta/math.pi
            segments.append({'id':gate['id'],'channel':'XY','label':name,'startS':cursor,'durationS':duration,
                'envelopeStartS':cursor+buffer/2,'envelopeDurationS':envelope,'bufferS':buffer,
                'amplitude':amp,'frequencyHz':number(qc['para']['drivFreq'],minimum=0),
                'phaseRad':rotation_phase,'phaseReference':'原生旋转轴 / 基带相位','shape':'DC'})
        else:raise ValueError('门 '+name+' 的时序未覆盖')
        cursor+=duration
    if measures!=1:raise ValueError('读出门数量未覆盖')
    offset=read_start-gap-cursor
    if offset<0:raise ValueError('控制序列超过实际读出起点')
    for segment in segments:
        segment['startS']+=offset
        if 'envelopeStartS' in segment:segment['envelopeStartS']+=offset
    read_width=number(adc['width'],minimum=0);pre_width=number(adc['shape_kw']['pre_width'],minimum=0)
    factor=number(adc['shape_kw']['factor']);read_amp=number(qc['para']['probCarryScale'])
    read_frequency=number(qc['para']['probFreq'],minimum=0)
    if pre_width>read_width:raise ValueError('读出前沿超过读出时长')
    for suffix,start,duration,amp in (('lead',read_start,pre_width,read_amp),
            ('hold',read_start+pre_width,read_width-pre_width,read_amp*factor)):
        if duration:segments.append({'id':'readout-'+suffix,'channel':'Read in','label':'读出前沿' if suffix=='lead' else '读出保持',
            'startS':start,'durationS':duration,'envelopeDurationS':duration,'amplitude':amp,
            'frequencyHz':read_frequency,'phaseRad':0.,'phaseReference':'编译基带相位；绝对本振相位未提供','shape':'DC'})
    rate=number(adc['ADC_sRate'],minimum=1);points=number(adc['pointNum'],minimum=1)
    segments.append({'id':'adc-window','channel':'Read out','label':'ADC 采集窗口','startS':number(adc['triggerDelay'],minimum=0),
        'durationS':points/rate,'amplitude':None,'frequencyHz':None,'phaseRad':None})
    return {'status':'ready','segments':segments,'variant':variant,'sweepMode':mode,'axes':axes,
        'readoutStartS':read_start,'readoutDurationS':read_width,'readoutAmplitude':read_amp,
        'controlReadoutGapS':gap,'xyTriggerDelayS':job.get('xy_delay_s'),
        'referenceClockHz':None,'zSetting':None,'pulsePolicy':policy,'source':'adapter.build_plan jobs/spec/config',
        'limitations':['图为计划逻辑边沿，不是实测反馈或实际下发样点。',
            'XY 触发延时单列，未据此推定通道间物理到达时间。',
            '此计划不提供参考 CLK 或站端现有 Z 偏置读数。']}
