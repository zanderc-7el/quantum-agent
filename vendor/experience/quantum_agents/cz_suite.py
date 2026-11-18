"""Guarded bridge for the existing CZ suite, not an atomic per-agent backend."""
from pathlib import Path
import json,math
from .twin import ROOT,load_physics,_save

QUTRIT_LABELS={a+b for a in '012' for b in '012'}
BASIS_LABELS={'00','01','10','11'}
TOMO_BASES={a+b for a in 'XYZ' for b in 'XYZ'}
CHECK_NAMES={'coupling_hz','q1_anharmonicity_hz','residual_zz_hz','dressed_zz_hz',
             'q0_conditional_phase_error_rad','q1_conditional_phase_error_rad',
             'cz_average_fidelity','graphstate_fidelity','gate_average_comp_leakage'}|{
             'basis_'+b+'_'+suffix for b in BASIS_LABELS for suffix in ('leakage','correct_population')}

def validate_counts(counts,shots):
    return (isinstance(counts,dict) and set(counts)==QUTRIT_LABELS and type(shots) is int and shots>0
            and all(type(v) is int and v>=0 for v in counts.values()) and sum(counts.values())==shots)

def check_complete(calibration,validation,tomography):
    errors=[];cal=calibration.get('calibration',{});phases=cal.get('conditional_phases_rad')
    if not isinstance(phases,list) or len(phases)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in phases):errors.append('必须恰有两方向有限条件相位')
    table=calibration.get('fits',{}).get('truth_table',{})
    if set(table)!=BASIS_LABELS:errors.append('必须恰有00/01/10/11四输入')
    for b,m in table.items():
        if not validate_counts(m.get('counts'),m.get('shots')):errors.append('无效真值表计数 '+b)
    if set(tomography)!=TOMO_BASES:errors.append('必须恰有九个Pauli测量基')
    for basis,counts in tomography.items():
        if not isinstance(counts,dict) or not validate_counts(counts,sum(counts.values()) if all(type(v) is int for v in counts.values()) else 0):errors.append('无效层析计数 '+basis)
    checks=validation.get('checks',[])
    if len(checks)!=17 or {c.get('parameter') for c in checks}!=CHECK_NAMES:errors.append('17项验收不完整或重复')
    for c in checks:
        if c.get('passed') is not True or type(c.get('estimated')) not in (int,float) or not math.isfinite(c['estimated']):errors.append('验收项失败 '+str(c.get('parameter')))
    if validation.get('passed') is not True:errors.append('原套件报告未通过')
    return {'passed':not errors,'errors':errors,'required_checks':17,'data_kind':'simulated',
            'scope':'原CZ套件完整性补充门禁；并非各agent已能独立恢复，也不是实机RB/XEB验收'}

def run_cz(directory,seed=None):
    directory=Path(directory)
    if directory.exists() and any(directory.iterdir()):raise ValueError('CZ输出目录必须为空')
    directory.mkdir(parents=True,exist_ok=True)
    load_physics()
    from quantum_twin.two_qubit_cli import run
    run(ROOT/'resources/twin_source/configs/two_qubit.json',directory/'source_run',seed=seed,plots=False,quiet=False)
    source=directory/'source_run'
    read=lambda name:json.loads((source/name).read_text(encoding='utf-8'))
    result=check_complete(read('calibration.json'),read('validation.json'),read('tomography_counts.json'))
    jobs=set();shots=0;records=0
    with (source/'measurements.jsonl').open(encoding='utf-8') as handle:
        for line in handle:
            record=json.loads(line);m=record['measurement'];job=m['job_id']
            if job in jobs or not validate_counts(m['counts'],m['shots']) or m['shots']!=record['request']['shots']:
                result['errors'].append('原始采集计数/唯一性/请求shots不一致 '+job)
            jobs.add(job);shots+=m['shots'];records+=1
    if records!=read('manifest.json')['acquisitions']:result['errors'].append('采集数量与运行manifest不同')
    result.update(passed=not result['errors'],acquisitions=records,total_shots=shots)
    _save(directory/'guarded_validation.json',result)
    return result
