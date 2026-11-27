"""Independent interpretation of frozen five-X calibration receipts."""
import math
from .common import digest,number
from adapters.science_shape_repeat5.plans import POLICY,N

def check(result,*,independent=False):
    try:
        def require(ok,text):
            if not ok:raise ValueError(text)
        p=result['parameters'];axis=p['scan_axis_values'];provenance=result['provenance']
        value=provenance['shapeRabiCalibration'];fit=value['fit'];frequency=fit['value']
        require(value['policy']==POLICY and provenance['policy']==POLICY and value['gateRepeats']==N,
                '缺少固定五门幅度标定策略')
        require(number(frequency) and frequency>0,'五门振荡频率无效')
        candidate=N/(2*frequency)
        require(axis[0]<candidate<axis[-1] and 0<candidate<=.99,'单门 π 幅度未落在真实扫描范围内')
        require(math.isclose(candidate,value['singleGatePiAmplitude'],rel_tol=1e-12), '单门幅度未按五门振荡频率换算')
        require(value['piAmplitudeFormula']=='5 / (2 * fittedFrequency)' and value['singleGateRotation']=='X(pi)',
                '五门与单门标定语义不一致')
        expected=p['verify_shape_pi_amp'] if independent else candidate
        require(result['candidateParameters']=={'shape_pi_amp':expected},'冻结单门候选与首测或复测回执不一致')
        identity=result['targetPulseIdentity'];native=identity['identity']
        require(digest(native)==identity['sha256'] and native['amplitude']==expected and native['rotationRad']==math.pi,
                '单门 π 物理身份不一致')
        evidence=provenance['compiledSequenceEvidence'];rows=evidence['rows']
        require(evidence['gateRepeats']==N and len(rows)==p['capture_repeats']*(len(axis)+(1 if independent else 0)),
                '五门真实编译行数或独立候选行数不一致')
        require(len({r['rowId'] for r in rows})==len(rows),'五门编译行编号重复')
        for row in rows:
            raw={k:v for k,v in row.items() if k not in ('sha256','rowId')}
            single=row['singlePulse'];duration=N*(p['pulse_length_s']+2e-9)
            require(row['gateRepeats']==N and len(set(row['segmentIds']))==N and digest(raw)==row['sha256']
                and digest(single)==row['singlePulseSha256'] and row['sampleRateHz']==4e9
                and math.isclose(row['totalControlDurationS'],duration,rel_tol=1e-12,abs_tol=1e-22)
                and row['endSample']-row['startSample']==N*single['sampleCount'],
                '五个同形门的数量、面积摘要或时窗不一致')
        if independent:require(value['independentFixedCandidate']==expected,'缺少独立固定候选采集身份')
        return []
    except (KeyError,ValueError,TypeError,ZeroDivisionError,OverflowError) as exc:
        return ['五门幅度标定证据不完整：'+str(exc)]
