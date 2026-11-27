"""One controller lease, one bounded PRE/paired RB/IRB/POST operation."""
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
from adapters.science_v3.execute_worker import save,read

def validate_admission(plan,folder):
    from adapters.science_runtime.policy import digest
    from adapters.f8 import _digest as intent_digest
    from adapters.science_v3_irb.policy import verify_sources
    from adapters.science_v3_irb.plans import POLICY,PROCEDURE,normalize_parameters
    from adapters.science_v3_irb.half_scan import POLICY as HALF_POLICY,PROCEDURE as HALF_PROCEDURE,SCAN_FIELDS,build_half_scan
    from adapters.science_v3_irb.timing import authenticate_dependency,rabi_lineage,inherited_rabi_lineage
    from adapters.science_v3_irb import beta
    policies={PROCEDURE:POLICY,HALF_PROCEDURE:HALF_POLICY}
    if plan.get('adapter')!='science-v3-irb' or plan.get('procedureId') not in policies or plan.get('policy')!=beta.policy_for(plan['procedureId'],plan.get('parameters',{})) or plan.get('version')!=3:raise ValueError('Only frozen third-batch execution is accepted')
    if any(plan.get(k) for k in ('compileOnly','compileOnlyEvidence','testOnly')):raise ValueError('Preview/mock evidence cannot execute')
    intent=read(folder/'adapter_intent.json');request=intent.get('request',{})
    # Wrapper and plan hashes deliberately retain their different JSON formats.
    if not request.get('leaseToken') or digest(request)!=plan['requestHash'] or intent.get('requestHash')!=intent_digest(request):raise ValueError('IRB intent or controller lease differs')
    if any(request.get(k)!=v for k,v in plan['requestIdentity'].items()):raise ValueError('IRB request identity differs')
    if digest(verify_sources(PROJECT))!=plan['sourceHash']:raise ValueError('IRB source closure changed')
    if plan['procedureId']==HALF_PROCEDURE:
        p,campaign=build_half_scan(plan['parameters']);budget=campaign['budget']
        requested,_=build_half_scan({k:v for k,v in request.get('parameters',{}).items() if k in SCAN_FIELDS})
    else:
        p,budget=normalize_parameters(plan['parameters']);requested,_=normalize_parameters(request.get('parameters',{}))
    if requested!=p:raise ValueError('IRB plan parameters differ from controller intent')
    if p!=plan['parameters'] or plan['totalRows']!=budget['rows'] or len(plan['rows'])!=budget['rows'] or plan['estimated_total_shots']!=budget['shots'] or plan['sampleBudget']!=budget['samples']:raise ValueError('IRB frozen budget differs')
    proof=plan['validationEvidence']
    if proof!=request.get('scienceDependencies') or proof.get('assessment',{}).get('executionEligible') is not True:raise ValueError('IRB dependency proof changed')
    if beta.empirical(p) and not set(beta.required_roles(plan['procedureId'],p)) <= {r['role'] for r in proof.get('rootReferences',[])}:raise ValueError('IRB required source role missing')
    if beta.empirical(p):
        if plan.get('pulsePolicy')!=beta.PULSE_POLICY:raise ValueError('Empirical pulse policy changed')
        if plan['procedureId']==HALF_PROCEDURE and (plan.get('campaign')!=campaign or plan['rows']!=campaign['rows']):raise ValueError('Empirical half pulse rows changed')
    attestations=[]
    for dep in proof['nodes']:
        roles=sorted({e['role'] for e in proof['edges'] if e.get('toRecordId')==dep['recordId']})
        attestations.append(authenticate_dependency(PROJECT,request,{**dep,'sourceRoles':roles},expected_route=plan['routing'],
            expected_binding=plan.get('configurationBinding',{}),expected_locks=plan['hardwareLocks'],expected_instrument=plan['instrument'],
            expected_observed=plan.get('observedResourceBinding')))
    lineage=rabi_lineage(proof,attestations,p) if plan['procedureId']==HALF_PROCEDURE else inherited_rabi_lineage(PROJECT,proof,attestations,p)
    if lineage!=plan.get('rabiLineage'):raise ValueError('Rabi amplitude/time source lineage changed before execution')
    from adapters.science_v3_irb.plans import identity
    if plan['procedureId']==PROCEDURE:
        nodes={d['recordId']:d for d in proof['nodes']};drag=next(nodes[r['recordId']] for r in proof['rootReferences'] if r['role']=='drag')
        if drag.get('procedureId')!=HALF_PROCEDURE or drag.get('targetPulseIdentity')!=identity(p):raise ValueError('IRB plan half pulse differs from accepted half-scan source')
    if type(plan.get('max_seconds')) not in (int,float) or not 0<plan['max_seconds']<=3600:raise ValueError('IRB deadline outside bounded policy')
    return request

def run_acquisition(plan,folder,*,transport_factory=None,compile_function=None,lock_acquire=None):
    import numpy as np
    from adapters.science_v3_irb.compiler import archive_row,compile_row
    from adapters.science_v3.transport import NativeTransport
    from adapters.science_v3_irb.analysis import freeze_pre,analyze
    from adapters.science_v3_irb.half_scan import PROCEDURE as HALF_PROCEDURE,analyze as analyze_half
    from adapters.science_v3_irb.runtime_contract import check_runtime
    from platform_worker import acquire_locks
    from platform_hooks import check_cancel,emit
    runtime=check_runtime()
    folder=Path(folder);results=folder/'results';results.mkdir(exist_ok=True)
    if any(results.iterdir()):raise ValueError('IRB operation results directory is not empty')
    compile_function=compile_function or compile_row;transport_factory=transport_factory or NativeTransport
    base=read(PROJECT/'adapters/arclight_configurable_runtime/base_config.json')
    base['ADC_Meas']['B1']['para']['ADC_sRate']=plan['routing']['adcSampleRateHz']
    deadline=time.monotonic()+plan['max_seconds'];locks=[];token=None;transport=None;captured=[];manifest=[];frozen=None
    completion={'state':'preflight','operationId':plan['operationId'],'resourceId':plan['resourceId'],'routing':plan['routing'],
                'shutdown_ok':True,'pointsCompleted':0,'files':[],'runtime':runtime}
    def check():
        check_cancel()
        if time.monotonic()>deadline:raise InterruptedError('IRB operation deadline exceeded')
    try:
        save(results/'native_runtime.json',runtime)
        save(results/'axes.json',plan['rows']);save(results/'parameters.json',plan['parameters']);save(results/'sequence_manifest.json',plan['campaign'])
        for row in plan['rows']:
            check();seq,expected=compile_function(row,base,plan['parameters'])
            item=archive_row(results/f'preflight_{row["id"]:04d}.npz',seq,expected);item['rowId']=row['id'];manifest.append(item);del seq
        save(results/'compiled_plan.json',manifest);check()
        locks,token=(lock_acquire or acquire_locks)(plan,folder)
        transport=transport_factory(plan,results,check);completion['shutdown_ok']=False
        save(results/'instrument.json',transport.open())
        for row,item in zip(plan['rows'],manifest):
            check()
            if plan['procedureId']!=HALF_PROCEDURE and row['stage']!='pre' and frozen is None:
                pre_rows=plan['rows'][:len(captured)]
                pre_i=np.stack([v[0] for v in captured]);pre_q=np.stack([v[1] for v in captured])
                np.savez_compressed(results/'raw_pre.npz',i=pre_i,q=pre_q)
                frozen=freeze_pre(pre_rows,pre_i+1j*pre_q,shots=plan['parameters']['shots'],source=str(results/'frozen_pre.json'))
                save(results/'frozen_pre.json',frozen)
                emit({'event':'irb_pre_reference_frozen','operationId':plan['operationId'],'resourceId':plan['resourceId'],'sha256':frozen['sha256']})
            seq,expected=compile_function(row,base,plan['parameters'])
            if expected!=item['waves']:raise ValueError('IRB execution waveform differs from preflight bytes')
            i,q=transport.acquire(row,seq,expected);captured.append((i,q));del seq
            completion['pointsCompleted']=len(captured)
            emit({'event':'row_acquired','row':len(captured),'completedRows':len(captured),'totalRows':len(plan['rows']),
                  'progress':100*len(captured)/len(plan['rows']),'operationId':plan['operationId'],'resourceId':plan['resourceId'],
                  'currentAxes':{k:row[k] for k in ('stage','capture','condition','axisValue','logicalState')},'progressBasis':'actual-completed-native-ADC-rows'})
        i=np.stack([v[0] for v in captured]);q=np.stack([v[1] for v in captured])
        np.savez_compressed(results/'raw_scan.npz',i=i,q=q)
        for stage in (() if plan['procedureId']==HALF_PROCEDURE else ('rb','irb','post')):
            ids=[j for j,row in enumerate(plan['rows']) if row['stage']==stage]
            np.savez_compressed(results/('raw_'+stage+'.npz'),i=i[ids],q=q[ids])
        completion.update(state='acquired',acquiredAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
    except BaseException as exc:
        completion.update(state='cancelled' if isinstance(exc,InterruptedError) else 'failed_after_shutdown',error=str(exc),traceback=traceback.format_exc())
        if captured:np.savez_compressed(results/'partial_raw_scan.npz',i=np.stack([v[0] for v in captured]),q=np.stack([v[1] for v in captured]))
    finally:
        if transport:
            try:completion.update(transport.close())
            except BaseException as exc:completion.update(shutdown_ok=False,cleanupError=str(exc))
        if completion['shutdown_ok']:
            for lock in reversed(locks):
                if read(lock).get('token')==token:Path(lock).unlink()
        else:completion['state']='needs_reconciliation'
        completion['files']=[str(p) for p in results.iterdir() if p.is_file()]+[str(folder/'plan.json')]
        save(results/'status.json',completion);completion['files'].append(str(results/'status.json'));save(folder/'completion.json',completion)
    if completion['state']!='acquired':return completion
    try:
        check()
        arrays=(np.stack([v[0] for v in captured]),np.stack([v[1] for v in captured]))
        analysis=analyze_half(plan,*arrays) if plan['procedureId']==HALF_PROCEDURE else analyze(plan,*arrays,frozen)
        json.dumps(analysis,allow_nan=False);check()
    except BaseException as exc:analysis={'metrics':{},'candidateParameters':{},'series':[],
        'analysis':{'accepted':False,'reasons':['IRB analysis failed: '+str(exc)]}}
    save(results/'analysis.json',analysis);completion.update(state='completed',analysis=analysis)
    completion['files'].append(str(results/'analysis.json'));save(folder/'completion.json',completion)
    emit({'event':'acquisition_complete','progress':100,'operationId':plan['operationId'],'resourceId':plan['resourceId'],'analysisAccepted':analysis['analysis']['accepted']})
    return completion

def main():
    folder=Path(sys.argv[1]).resolve().parent
    if Path(os.environ.get('QCAL_ARCLIGHT_RUN_DIR','')).resolve()!=folder:raise ValueError('Worker root identity mismatch')
    plan=read(folder/'plan.json');validate_admission(plan,folder)
    native=PROJECT/'adapters/arclight_configurable_runtime';sys.path.insert(0,str(native))
    from bootstrap import initialize
    initialize()
    from adapters.science_v3_irb.half_scan import PROCEDURE as HALF_PROCEDURE,build_half_scan
    if plan['procedureId']==HALF_PROCEDURE:
        if build_half_scan(plan['parameters'])[1]!=plan['campaign']:raise ValueError('Half-DRAG campaign changed')
    else:
        from adapters.science_v3_irb.sequences import verify_campaign
        verify_campaign(plan['campaign'],plan['parameters'])
    if plan['rows']!=plan['campaign']['rows']:raise ValueError('Executable rows differ from native manifest')
    completion=run_acquisition(plan,folder)
    if completion['state']!='completed':raise SystemExit(1)

if __name__=='__main__':main()
