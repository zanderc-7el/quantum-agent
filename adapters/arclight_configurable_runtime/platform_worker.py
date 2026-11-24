"""Bounded isolated ArcLight worker. Hardware execution only after -- plan contract."""
from pathlib import Path
import sys
SOURCE=Path(__file__).absolute().parent
sys.path.insert(0,str(SOURCE))
import os,json,hashlib,datetime,traceback,uuid,subprocess
from copy import deepcopy
from platform_hooks import emit,check_cancel
from routing import receiver_route

def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def acquire_locks(plan,root):
    owned=[];token=str(uuid.uuid4())
    try:
        for item in plan['hardwareLocks']:
            path=Path(item)
            with path.open('x',encoding='utf8') as f:json.dump({'pid':os.getpid(),'token':token,'operationId':plan['operationId'],'root':str(root)},f)
            owned.append(path)
        output=subprocess.run(['netstat.exe','-ano','-p','tcp'],capture_output=True,text=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if output.returncode!=0:raise RuntimeError('Cannot verify local chassis connection ownership')
        target=plan['instrument']['host']+':'+str(plan['instrument']['port'])
        if any(target in line and 'ESTABLISHED' in line for line in output.stdout.splitlines()):raise RuntimeError('Another established chassis connection exists; no hardware open attempted')
        os.environ['QCAL_ARCLIGHT_LOCK_TOKEN']=token;os.environ['QCAL_ARCLIGHT_LOCKS']=json.dumps([str(p) for p in owned])
        return owned,token
    except BaseException:
        for path in reversed(owned):
            if read(path).get('token')==token:path.unlink()
        raise

def freeze_reference(folder):
    import freeze_historical_point_reference as freezer
    from export_paired_lda_reference import build_reference
    frozen=freezer.freeze(folder)
    if not frozen['projection']['reference_contrast_repeated']:
        raise ValueError('Fresh held-out reference contrast failed; no downstream pulse sequence executed')
    exported=build_reference(folder);save(folder/'paired_rb_projection_export.json',exported)
    return exported['paired_rb_projection']

def analyze_rb(folders,plan,projection):
    import numpy as np
    from paired_rb_support import analyze_paired_rb
    from analyze_rb_pair import plot_diagnostic
    from analyze_rb_reference_drift import capture_diagnostics,summarize_rows,compare_mean
    loaded=[]
    for folder in folders:
        with np.load(folder/'raw_scan.npz',allow_pickle=False) as data:
            loaded.append((read(folder/'axes.json'),data['i']+1j*data['q'],data['acquisition_block_index']))
    pre,ref,irb,post=loaded
    result=analyze_paired_rb(ref[0],ref[1],irb[0],irb[1],projection,
        ref_manifest=read(folders[1]/'sequence_manifest.json'),irb_manifest=read(folders[2]/'sequence_manifest.json'),
        bootstrap_samples=plan['parameters']['rb_bootstrap_replicates'],bootstrap_seed=plan['parameters']['rb_master_seed'],min_seeds=20)
    result['target']=plan['resourceId']
    pre_rows,_=capture_diagnostics(*pre,projection);post_rows,_=capture_diagnostics(*post,projection)
    before=summarize_rows([x for x in pre_rows if x['capture'] in projection['validation_captures']]);after=summarize_rows(post_rows)
    comparison={k:compare_mean(before[k],after[k],relative=k=='contrast') for k in before}
    # Include both prepared-state offsets and contrast changes, on the fixed PRE scale.
    drift=max(abs(comparison['ground']['post_minus_pre']),abs(comparison['excited']['post_minus_pre']),abs(comparison['contrast']['relative_change_fraction']))
    metrics={'reference_drift_fraction':float(drift)}
    if result.get('reportable'):
        metrics.update(gate_fidelity=result['F_X_half_conditional'],clifford_fidelity=result['F_clifford'])
    r2=[]
    for key,label in [('reference_fit','reference'),('interleaved_fit','interleaved')]:
        fit=result.get(key,{})
        observed=result.get('curves',{}).get(label+'_mean')
        if observed is not None and fit.get('fitted_values'):
            y=np.asarray(observed);den=float(np.sum((y-y.mean())**2))
            if den>0:r2.append(1-float(np.sum((y-np.asarray(fit['fitted_values']))**2))/den)
    if len(r2)==2:metrics['r_squared']=min(r2)
    result['pre_post_comparison']=comparison;result['reference_drift_fraction']=drift
    result['acquisition_order']='Sequential reference and interleaved branches with matching randomized point order; not alternated in time.'
    result['currentValidationEvidence']=plan['validationEvidence'];result['fixedCalibrationUsedForBothModes']=True
    folder=folders[1];save(folder/'platform_paired_rb.json',result);plot_diagnostic(result,folder/'platform_paired_rb.png')
    reasons=list(result.get('reasons',[]))
    if drift>.1:reasons.append('Fresh prepared reference changed by more than 10% of PRE scale')
    if len(r2)!=2:reasons.append('Both branch R squared diagnostics unavailable')
    return {'metrics':metrics,'candidateParameters':{},'series':[],
        'analysis':{'accepted':bool(result.get('reportable')) and drift<=.1 and len(r2)==2,'reasons':reasons,
            'scope':'Conditional native paired RB estimate; fixed PRE calibration; systematics excluded'},
        'files':[str(folder/'platform_paired_rb.json'),str(folder/'platform_paired_rb.png')]}

def main():
    plan_path=Path(sys.argv[1]).absolute();plan=read(plan_path);root=plan_path.parent
    if Path(os.environ.get('QCAL_ARCLIGHT_RUN_DIR','')).absolute()!=root:raise ValueError('Worker root identity mismatch')
    for job in plan['jobs']:
        route, instrument = receiver_route(job)
        if job.get('operationId') != plan['operationId'] or job['target'] != plan['resourceId'] or route != plan['routing'] or instrument != plan['instrument']:
            raise ValueError('Worker job differs from frozen resource route')
    compile_only='--compile-only' in sys.argv
    if plan.get('compileOnlyEvidence') and not compile_only:raise ValueError('Offline fixture cannot be executed on hardware')
    for name in ('plans','results','logs','runtime_state'):(root/name).mkdir(exist_ok=True)
    os.environ['MPLCONFIGDIR']=str(root/'runtime_state/matplotlib');os.environ['MPLBACKEND']='Agg'
    folders=[];locks=[];token=None;completion={'state':'preflight','shutdown_ok':True,'files':[]};projection=None
    try:
        # Native source import is confined to this isolated process.
        import run_single_qubit as native
        if not compile_only:locks,token=acquire_locks(plan,root)
        completed_rows=0
        for index,job_source in enumerate(plan['jobs']):
            check_cancel();job=deepcopy(job_source)
            if projection is not None and job.get('rb_role') not in ('pre','independent_reference'):
                job.setdefault('calibration',{})['projection']=projection
                if job['spec']['meta']['type']=='3':
                    job['paired_rb_projection']=projection;job['fresh_reference_required_before_measurement']=True
            path=root/'plans'/(job['name']+'.json');save(path,job)
            os.environ.update(QCAL_ARCLIGHT_JOB_INDEX=str(index+1),QCAL_ARCLIGHT_JOB_COUNT=str(len(plan['jobs'])),
                QCAL_ARCLIGHT_COMPLETED_ROWS=str(completed_rows),QCAL_ARCLIGHT_TOTAL_ROWS=str(plan['totalRows']))
            sys.argv=[str(SOURCE/'run_single_qubit.py'),str(path)]+([] if compile_only else ['--measure'])
            folder=native.main();folders.append(folder)
            state=read(folder/'status.json')
            if state.get('shutdown_ok') is not True:raise RuntimeError('Native shutdown receipt failed')
            if compile_only:continue
            if state['points_completed']!=plan['expectedJobRows'][index]:raise ValueError('Native actual scan length differs from bounded plan')
            completed_rows+=state['points_completed']
            completion['files'].extend(str(folder/n) for n in ['raw_scan.npz','axes.json','job.json','status.json','compiled_plan.json','source_hashes.json','entry_source_hashes.json','instrument.json'])
            if job.get('rb_role') in ('pre','independent_reference'):
                projection=freeze_reference(folder);completion['files'] += [str(folder/'independent_projection.json'),str(folder/'paired_rb_projection_export.json')]
        completion.update(state='compiled' if compile_only else 'acquired',acquiredAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),folders=[str(p) for p in folders])
    except BaseException as exc:
        completion.update(state='failed',error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
        statuses=[read(p/'status.json') for p in (root/'results').iterdir() if (p/'status.json').exists()]
        completion['shutdown_ok']=all(s.get('shutdown_ok') is True for s in statuses)
        if completion['shutdown_ok']:
            completion['state']='cancelled' if isinstance(exc,InterruptedError) else 'failed_after_shutdown'
        completion['pointsCompleted']=sum(s.get('points_completed',0) for s in statuses)
        completion['files']=sorted({str(p) for p in (root/'results').rglob('*') if p.is_file() and
            (p.suffix=='.json' or (p.suffix=='.npz' and not p.name.startswith('preflight_')))})
        raise
    finally:
        # Keep lock files if native shutdown is not confirmed, preventing retries.
        if completion['shutdown_ok']:
            for lock in reversed(locks):
                if read(lock).get('token')==token:lock.unlink()
        save(root/'completion.json',completion)
    if compile_only:return
    try:
        if plan['procedureId']=='sq_rb':analysis=analyze_rb(folders,plan,projection)
        else:
            from platform_analysis import analyze_job
            analysis=analyze_job(folders[-1],read(folders[-1]/'job.json'),plan['procedureId'])
    except BaseException as exc:
        # Preserve successful real acquisition even if offline analysis rejects it.
        analysis={'metrics':{},'candidateParameters':{},'series':[],'analysis':{'accepted':False,'reasons':[f'Analysis failed: {type(exc).__name__}: {exc}']},'files':[]}
        save(root/'analysis_error.json',{'error':str(exc),'traceback':traceback.format_exc()});analysis['files'].append(str(root/'analysis_error.json'))
    completion.update(state='completed',analysis=analysis)
    completion['files']+=analysis.get('files',[]);completion['files']+=[str(root/'plan.json')]
    save(root/'completion.json',completion);emit({'event':'acquisition_complete','progress':100,'operationId':plan['operationId'],'resourceId':plan['resourceId'],'routing':plan['routing'],'analysisAccepted':analysis.get('analysis',{}).get('accepted',False)})

if __name__=='__main__':main()
