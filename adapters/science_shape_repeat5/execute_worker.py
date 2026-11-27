"""One controller-authorized V3 operation. No standalone preview admission."""
from pathlib import Path
import datetime
import json
import os
import sys
import time
import traceback

PROJECT=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
sys.path.insert(0,str(PROJECT))

def save(path,value):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.partial')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');os.replace(temporary,path)

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def validate_admission(plan,folder):
    from adapters.f8 import _digest as intent_digest
    from adapters.science_runtime.policy import digest
    from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter
    from adapters.science_shape_repeat5.policy import verify_sources
    intent=read(folder/'adapter_intent.json');request=intent.get('request',{})
    profile=plan.get('profileSnapshot',{})
    if (not request.get('leaseToken') or intent.get('requestHash')!=intent_digest(request)
            or plan.get('requestHash')!=digest(request) or profile.get('enabled') is not True
            or intent.get('profileHash')!=intent_digest(profile) or digest(profile)!=plan.get('profileHash')):
        raise ValueError('Repeat5 intent, lease or frozen profile differs')
    if any(plan.get(k) for k in ('compileOnly','compileOnlyEvidence','testOnly')):raise ValueError('Non-execution artifact rejected')
    verify_sources(PROJECT)
    consumer={**request}
    if 'consumerOutputDir' in plan:consumer['outputDir']=plan['consumerOutputDir']
    rebuilt=ScienceRepeat5Adapter(PROJECT,profile).build_plan(consumer)
    if rebuilt!=plan:raise ValueError('Frozen repeat5 chain plan differs from exact native reconstruction')
    return request


def run_acquisition(plan,folder,*,transport_factory=None,compile_function=None,lock_acquire=None):
    """Production path; injections exist only for in-process isolated tests."""
    import numpy as np
    from adapters.science_shape_repeat5.compiler import compile_row,archive_row
    from adapters.science_v3.transport import NativeTransport
    from adapters.science_shape_repeat5.analysis import analyze
    from platform_worker import acquire_locks
    from platform_hooks import check_cancel,emit
    folder=Path(folder);results=folder/'results'
    # Native bootstrap creates an empty results directory before entry here.
    # Existing acquisition contents still fail closed: never overwrite a run.
    results.mkdir(exist_ok=True)
    if any(results.iterdir()):raise ValueError('Operation results directory is not empty')
    compile_function=compile_function or compile_row;transport_factory=transport_factory or NativeTransport
    base=read(PROJECT/'adapters/arclight_configurable_runtime/base_config.json')
    base['ADC_Meas']['B1']['para']['ADC_sRate']=plan['routing']['adcSampleRateHz']
    start=time.monotonic();deadline=start+plan['max_seconds'];locks=[];token=None;transport=None;captured=[];manifest=[]
    completion={'state':'preflight','operationId':plan['operationId'],'resourceId':plan['resourceId'],
                'routing':plan['routing'],'shutdown_ok':True,'pointsCompleted':0,'files':[]}
    def check():
        check_cancel()
        if time.monotonic()>deadline:raise InterruptedError('V3 operation deadline exceeded')
    try:
        save(results/'axes.json',plan['rows']);save(results/'parameters.json',plan['parameters'])
        # Precompile the entire finite job before acquiring locks or opening hardware.
        for row in plan['rows']:
            check();seq,expected=compile_function(row,base,plan['parameters'])
            item=archive_row(results/f'preflight_{row["id"]:04d}.npz',seq,expected);item['rowId']=row['id'];manifest.append(item)
            del seq
        save(results/'compiled_plan.json',manifest)
        check();locks,token=(lock_acquire or acquire_locks)(plan,folder)
        transport=transport_factory(plan,results,check);completion['shutdown_ok']=False
        instrument=transport.open();save(results/'instrument.json',instrument)
        for row,item in zip(plan['rows'],manifest):
            check();seq,expected=compile_function(row,base,plan['parameters'])
            if expected!=item['waves']:raise ValueError('Preflight and execution waveforms differ')
            i,q=transport.acquire(row,seq,expected);captured.append((i,q));del seq
            completion['pointsCompleted']=len(captured)
            emit({'event':'row_acquired','row':len(captured),'completedRows':len(captured),'totalRows':len(plan['rows']),
                  'progress':100*len(captured)/len(plan['rows']),'operationId':plan['operationId'],
                  'resourceId':plan['resourceId'],'currentAxes':{k:row[k] for k in ('capture','condition','axisValue','logicalState')},
                  'progressBasis':'actual-completed-native-ADC-rows'})
        np.savez_compressed(results/'raw_scan.npz',i=np.stack([x[0] for x in captured]),q=np.stack([x[1] for x in captured]))
        completion.update(state='acquired',acquiredAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
    except BaseException as exc:
        completion.update(state='cancelled' if isinstance(exc,InterruptedError) else 'failed_after_shutdown',
                          error=str(exc),traceback=traceback.format_exc())
        if captured:np.savez_compressed(results/'partial_raw_scan.npz',i=np.stack([x[0] for x in captured]),q=np.stack([x[1] for x in captured]))
    finally:
        if transport:
            try:completion.update(transport.close())
            except BaseException as exc:completion.update(shutdown_ok=False,cleanupError=str(exc))
        if completion['shutdown_ok']:
            for lock in reversed(locks):
                if read(lock).get('token')==token:Path(lock).unlink()
        else:completion['state']='needs_reconciliation'
        completion['files']=[str(p) for p in results.iterdir() if p.is_file()]+[str(folder/'plan.json')]
        save(results/'status.json',completion);completion['files'].append(str(results/'status.json'))
        save(folder/'completion.json',completion)
    if completion['state']!='acquired':return completion
    try:
        analysis=analyze(plan,np.stack([x[0] for x in captured]),np.stack([x[1] for x in captured]))
        json.dumps(analysis,allow_nan=False)
    except BaseException as exc:analysis={'metrics':{},'candidateParameters':{},'series':[],
            'analysis':{'accepted':False,'reasons':['Analysis failed: '+str(exc)]}}
    save(results/'analysis.json',analysis)
    completion.update(state='completed',analysis=analysis,repeat5Evidence={'gateRepeats':5,'rows':[{'rowId':r['rowId'],**r['repeat5']} for r in manifest]})
    completion['files'].append(str(results/'analysis.json'));save(folder/'completion.json',completion)
    emit({'event':'acquisition_complete','progress':100,'operationId':plan['operationId'],'resourceId':plan['resourceId'],
          'analysisAccepted':analysis['analysis']['accepted']})
    return completion

def main():
    folder=Path(sys.argv[1]).resolve().parent
    if Path(os.environ.get('QCAL_ARCLIGHT_RUN_DIR','')).resolve()!=folder:raise ValueError('Worker root identity mismatch')
    plan=read(folder/'plan.json');validate_admission(plan,folder)
    # Fixed verified native package, never a caller-selected Python path.
    native=PROJECT/'adapters/arclight_configurable_runtime';sys.path.insert(0,str(native))
    from bootstrap import initialize
    initialize()
    if plan['procedureId'] in ('sq_drag_half_scan','sq_drag_irb'):
        from adapters.science_v3_irb.execute_worker import run_acquisition as acquire
    elif plan['procedureId']!='sq_shape_rabi_amplitude_repeat5':
        from adapters.science_v3.execute_worker import run_acquisition as acquire
    else:acquire=run_acquisition
    completion=acquire(plan,folder)
    if completion['state']!='completed':raise SystemExit(1)

if __name__=='__main__':
    try:main()
    except BaseException:
        traceback.print_exc();raise
