"""Pure, independent consistency checks for authenticated native receipts.

This does not authenticate files or authorize hardware. The controller and
dependency resolver retain those responsibilities. No received accepted flag
can replace the numerical, pulse-identity and source-closure checks below.
"""
import math
import statistics
from .common import digest,number


def require(value,message):
    if not value:raise ValueError(message)


def close(a,b):
    return number(a) and number(b) and math.isclose(a,b,rel_tol=1e-8,abs_tol=1e-10)


def values(value,n):
    require(isinstance(value,list) and len(value)==n and all(number(v) for v in value),'数值数组缺失或非有限')
    return value


def _fit(fit,curves,name,lengths):
    n=len(lengths);y=values(curves[name+'_mean'],n);sem=values(curves[name+'_seed_sem'],n)
    pred=values(fit['fitted_values'],n);a,p,b=fit['A'],fit['p'],fit['B']
    require(all(number(v) for v in (a,p,b)) and 0<a<1-1e-5 and 1e-5<p<1-1e-5 and 0<=b<1-1e-5,'衰减拟合参数未在可识别范围内')
    require(all(close(v,a*p**m+b) for v,m in zip(pred,lengths)),'拟合曲线与指数参数不一致')
    errors=fit['parameter_standard_errors'];require(all(number(errors[k]) and errors[k]>=0 for k in ('A','p','B')),'拟合误差缺失')
    require(a>=3*errors['A'] and errors['p']<=.1 and number(fit['jacobian_condition']) and 0<=fit['jacobian_condition']<=1e7,'拟合参数不可识别')
    scale=[max(v,1e-4) for v in sem];require(min(sem)>=0,'种子标准误不能为负')
    require(pred[0]-pred[-1]>max(.015,3*math.hypot(scale[0],scale[-1])),'衰减对比度未解析')
    chi=sum(((v-w)/s)**2 for v,w,s in zip(y,pred,scale))/max(1,n-3)
    require(close(fit['reduced_chi_square'],chi) and chi<=9 and fit['identifiable'] is True and fit['reasons']==[],'拟合残差或可识别性声明不一致')
    total=sum((v-statistics.mean(y))**2 for v in y);require(total>0,'拟合数据无衰减信息')
    return p,1-sum((v-w)**2 for v,w in zip(y,pred))/total


def _references(reference,irb,metrics,parameters):
    pre,post=reference['pre'],reference['post'];projection=pre['projection']
    require(pre['schema']=='science-v3-irb-frozen-pre/1' and pre['accepted'] is True
            and digest({k:v for k,v in pre.items() if k!='sha256'})==pre['sha256'],'PRE 冻结摘要不一致')
    train,held=projection['training_captures'],projection['validation_captures'];count=parameters['reference_captures']
    require(train==list(range(count//2)) and held==list(range(count//2,count)) and len(held)>=2,'PRE 训练与留出采集未独立分离')
    require(projection['independent_reference'] is True and projection==irb['projection'] and projection['reference_raw_sha256']==pre['rawSha256'],'RB 投影不是本轮冻结的 PRE 投影')
    direction=values(projection['direction'],2);g=values(projection['ground_iq'],2);e=values(projection['excited_iq'],2)
    require(close(sum(v*v for v in direction),1) and sum((b-a)*d for a,b,d in zip(g,e,direction))>1e-15,'PRE 投影方向或对比度无效')
    captures=pre['heldOut']['captures'];require([v['capture'] for v in captures]==held,'PRE 留出采集不完整')
    for capture in captures:
        require(close(capture['contrast'],capture['excited']-capture['ground'])
            and close(capture['contrastSem'],math.hypot(capture['groundSem'],capture['excitedSem']))
            and close(capture['contrastSnr'],capture['contrast']/max(capture['contrastSem'],1e-15))
            and capture['contrastSnr']>=5 and max(abs(capture['ground'])+5*capture['groundSem'],abs(capture['excited']-1)+5*capture['excitedSem'])<=.1,'PRE 留出采集对比度或逐轮偏移未通过')
    require(pre['heldOut']['statistics']['contrast']['ci95Conditional'][0]>0 and post['accepted'] is True,'PRE 对比度区间或 POST 漂移未通过')
    changes=post['changes'];drift=[];upper=[]
    for key in ('ground','excited','contrast'):
        before=pre['heldOut']['statistics'][key];after=post['post']['statistics'][key];change=changes[key]
        require(before['captureCount']==len(held) and after['captureCount']==count,'参考采集轮数不一致')
        scale=abs(before['mean']) if key=='contrast' else 1
        delta=(after['mean']-before['mean'])/scale;sem=math.hypot(before['sem'],after['sem'])/scale
        require(close(change['postMinusPreFraction'],delta) and close(change['semFraction'],sem),'POST 改变量与参考统计不一致')
        bound=change['absolute95UpperDiagnostic'];require(number(bound) and bound>=abs(delta) and bound>=abs(delta)+1.959963984540054*sem,'POST 漂移上界不包含参考不确定性')
        drift.append(abs(delta));upper.append(bound)
    for key,actual in (('reference_drift_fraction',max(drift)),('reference_drift_upper_diagnostic',max(upper))):
        require(close(post[key],actual) and close(metrics[key],actual) and actual<=.1,'POST 漂移指标不一致或超过门槛')


def _lineage(lineage,proof,p):
    """DC timing supports the window; shaped pi/half sources prove rotation."""
    def near(a,b):return number(a) and number(b) and math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-22)
    require(lineage['schema']=='science-v3-rabi-lineage/1' and digest({k:v for k,v in lineage.items() if k!='sha256'})==lineage['sha256'],'幅度/时间 Rabi 来源摘要不一致')
    nodes={node['recordId']:node for node in proof['nodes']}
    require(lineage['amplitudeRecordId']==lineage['pi01RecordId'] and nodes[lineage['pi01RecordId']]['procedureId'] in ('sq_shape_rabi_amplitude','sq_shape_rabi_amplitude_repeat5')
        and nodes[lineage['half01RecordId']]['procedureId']=='sq_shape_half_verify','形状 π/半门与幅度来源不是同一标定链')
    time=lineage['time'];require(time['policy']=='dc_time_workpoint_to_shaped_window_nearest_250ps_v1' and time['sourceProcedureId']=='sq_rabi_time'
        and nodes[time['sourceRecordId']]['procedureId']=='sq_rabi_time' and time['calibratesShapedRotation'] is False
        and digest({k:v for k,v in time.items() if k!='sha256'})==time['sha256'],'时间 Rabi 来源角色或摘要不一致')
    continuous=time['sourceContinuousDurationS'];require(number(continuous) and 4e-9<=continuous<=2e-6,'连续时间窗来源无效')
    require(time['sampleQuantumS']==.25e-9 and time['sourceCommittedQuantumS']==.5e-9 and time['maximumQuantizationErrorS']==.125e-9
        and time['rounding']=='nearest sample, ties to even','时间窗量化策略不一致')
    require(near(time['sourceCommittedDurationS'],round(continuous*2e9)/2e9) and near(time['targetDurationS'],round(continuous*4e9)/4e9)
        and near(time['targetDurationS'],p['pulse_length_s']) and near(time['quantizationErrorS'],time['targetDurationS']-continuous)
        and abs(time['quantizationErrorS'])<=.125e-9+1e-22,'时间窗迁移值或量化偏差不一致')
    ops=time['fitOperations'];require(len(ops)==2 and {op['stage'] for op in ops}=={'selection','verification'} and len({op['operationId'] for op in ops})==2,'时间 Rabi 缺少分离首测与复测')
    for op in ops:
        fits=op['fitRepeats'];require(len(fits)>=2 and isinstance(op['nativeReceiptSha256'],str) and len(op['nativeReceiptSha256'])==64,'时间 Rabi 缺少实际重复拟合与回执摘要')
        for fit in fits:
            f,se=fit['frequencyHz'],fit['frequencyStandardErrorHz']
            require(number(f) and f>0 and number(se) and 0<=se/f<=.1 and near(fit['continuousDurationS'],1/(2*f))
                and near(fit['durationStandardErrorS'],se/(2*f*f)),'时间 Rabi 拟合误差或连续时长不一致')
        frequency=statistics.mean(f['frequencyHz'] for f in fits)
        require(near(op['frequencyHz'],frequency) and near(op['continuousDurationS'],1/(2*frequency)),'时间 Rabi 汇总频率/时间不一致')
    first=next(op for op in ops if op['stage']=='selection')
    require(near(first['continuousDurationS'],continuous),'时间窗不是首测冻结的连续拟合值')


def check(result,procedure,*,independent):
    """Return reasons, never import NumPy or open devices/files in the API."""
    from adapters.science_v3_irb.plans import identity
    from adapters.science_v3_irb.half_scan import target_identity
    from adapters.science_v3_irb.beta import policy_for, candidate_key, empirical
    try:
        p=result['parameters'];metrics=result['metrics'];provenance=result['provenance'];candidate=result['candidateParameters']
        require(provenance['freshAcquisition'] is True and provenance['campaignPolicy']==policy_for(procedure,p),'本轮采集或半门实验策略不一致')
        key=candidate_key(p)
        expected=target_identity(p,candidate[key]) if procedure=='sq_drag_half_scan' else identity(p)
        require(result['targetPulseIdentity']==expected,'目标脉冲不是实际参数对应的同一 X/2 门')
        proof=provenance['scienceDependencies'];keys=('schema','policyVersion','kind','scope','inputs','rootReferences','nodes','edges')
        require(digest({k:proof[k] for k in keys})==proof['closureSha256'],'前置来源闭包摘要不一致')
        _lineage(provenance['scienceHalfDrag' if procedure=='sq_drag_half_scan' else 'scienceIRB']['rabiLineage'],proof,p)
        if procedure=='sq_drag_half_scan':
            half=provenance['scienceHalfDrag']
            require(set(candidate)=={key} and half['targetRotation']=='X/2' and half['zeroBaselineAcquired'] is True and 0. in p['scan_axis_values'],'半门扫描缺少实际零系数基线')
            if independent:
                comparison=half['candidateComparison'];data=values(comparison['pairedCaptureDifferences'],p['capture_repeats'])
                mean=statistics.mean(data);sem=statistics.stdev(data)/math.sqrt(len(data))
                if empirical(p):
                    same=lambda a,b:number(a) and number(b) and math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-22)
                    require(comparison['coefficientMode']=='empirical_beta' and comparison['coefficientUnit']=='s'
                        and comparison['baselineBetaSeconds']==0 and same(comparison['candidateBetaSeconds'],candidate[key])
                        and same(p['verify_drag_beta_s'],candidate[key]),'固定 β 候选的模式、秒单位或零基线不一致')
                else:
                    require(comparison['baselineLambda1']==0 and close(comparison['candidateLambda1'],candidate[key])
                        and close(p['verify_drag_lambda1'],candidate[key]),'固定候选与零基线的逐轮比较不一致')
                require(close(comparison['mean'],mean) and close(comparison['captureSem'],sem),'固定候选与零基线的逐轮比较不一致')
                require(close(metrics['candidate_improvement_vs_zero'],mean) and close(metrics['candidate_improvement_sem'],sem)
                    and close(metrics['candidate_improvement_upper_5sem'],mean+5*sem) and mean+5*sem>=0,'固定半门候选显著劣于实际零系数基线')
            return []
        require(candidate=={},'IRB 不得重新选择或发布标定参数')
        refs={ref['role']:ref['recordId'] for ref in proof['rootReferences']};nodes={node['recordId']:node for node in proof['nodes']}
        drag=nodes[refs['drag']];require(drag['procedureId']=='sq_drag_half_scan' and drag['targetPulseIdentity']==expected,'IRB 目标与已独立验收半门扫描不是同一脉冲')
        names={a['name'] for a in result['artifacts']};require({'raw_pre.npz','raw_rb.npz','raw_irb.npz','raw_post.npz','raw_scan.npz'}<=names,'PRE/RB/IRB/POST 原始采集产物不完整')
        irb=provenance['scienceIRB']['irb'];require(irb['schema']=='arclight_paired_rb_analysis_v1' and irb['target_gate']=='X/2' and irb['reportable'] is True and irb['reasons']==[],'同半门 IRB 分析不可报告')
        lengths=p['rb_lengths'];require(irb['lengths']==lengths and irb['captures_per_sequence']==p['rb_captures'] and irb['random_sequences_per_length']=={str(m):p['rb_sequences'] for m in lengths},'IRB 独立序列/轮次与执行计划不一致')
        require(metrics['rb_captures']==p['rb_captures'] and metrics['independent_sequences_per_length']==p['rb_sequences'],'IRB 采样指标与计划不一致')
        count=len(lengths)*p['rb_sequences']*p['rb_captures']
        values(irb['reference_projected_points'],count);values(irb['interleaved_projected_points'],count)
        pref,rref=_fit(irb['reference_fit'],irb['curves'],'reference',lengths)
        pint,rint=_fit(irb['interleaved_fit'],irb['curves'],'interleaved',lengths);ratio=pint/pref
        require(close(metrics['r_squared'],min(rref,rint)) and min(rref,rint)>=.95,'双曲线 R² 不一致或未通过')
        require(0<=ratio<=1 and close(irb['p_ratio_unclipped'],ratio) and close(irb['F_X_half_conditional'],(1+ratio)/2)
            and close(irb['F_clifford'],(1+pref)/2) and close(metrics['gate_fidelity'],irb['F_X_half_conditional']) and close(metrics['clifford_fidelity'],irb['F_clifford']),'保真度未由同一对参考/交错拟合导出')
        boot=irb['bootstrap'];require(boot['requested']==p['rb_bootstrap_replicates'] and type(boot['successful']) is int and boot['successful']>=.9*boot['requested']
            and boot['successful']+boot['failures']==boot['requested'] and boot['rng_seed']==p['rb_master_seed'] and boot['confidence']==.95
            and boot['systematic_model_error_included'] is False and boot['method'] and boot['conditional_on'],'配对 bootstrap 次数、种子或条件范围不完整')
        intervals=boot['intervals_unclipped']
        for key in ('p_ref','p_irb','p_ratio','F_clifford','F_X_half_conditional'):
            interval=values(intervals[key],2);require(0<=interval[0]<=interval[1]<=1,'未裁剪条件区间越过物理范围')
        for key,source in (('F_clifford','p_ref'),('F_X_half_conditional','p_ratio')):
            require(all(close(a,(1+b)/2) for a,b in zip(intervals[key],intervals[source])),'保真度区间未来自对应未裁剪参数区间')
        _references(provenance['scienceIRB']['reference'],irb,metrics,p)
    except (ValueError,KeyError,TypeError,IndexError,ZeroDivisionError,OverflowError,statistics.StatisticsError) as exc:
        return ['X/2 科学验收一致性检查失败：'+str(exc)]
    return []


def check_independent(selection,verification):
    """The second campaign characterizes the same pulse with fresh sequences/PRE."""
    try:
        a,b=selection['parameters'],verification['parameters']
        require(b['rb_master_seed']==(a['rb_master_seed']+0x9e3779b9)%(2**32)
            and {k:v for k,v in a.items() if k!='rb_master_seed'}=={k:v for k,v in b.items() if k!='rb_master_seed'},'独立 IRB 必须只切换随机种子，保持实际脉冲和采样轴')
        require(selection['targetPulseIdentity']==verification['targetPulseIdentity'],'独立 IRB 改变了待表征目标门')
        pa,pb=selection['provenance'],verification['provenance']
        for key in ('operationId','sequenceManifestSha256'):
            require(pa.get(key) and pb.get(key) and pa[key]!=pb[key],'独立 IRB 复用了操作或随机序列清单')
        require(pa['scienceIRB']['reference']['pre']['rawSha256']!=pb['scienceIRB']['reference']['pre']['rawSha256'],'独立 IRB 复用了 PRE 原始参考')
    except (ValueError,KeyError,TypeError) as exc:return ['X/2 独立复测检查失败：'+str(exc)]
    return []
