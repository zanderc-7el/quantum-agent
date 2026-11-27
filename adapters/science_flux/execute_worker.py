"""One leased, finite measured-bias session; restore before closing station."""
from pathlib import Path
import datetime as dt
import json
import os
import sys
import time
import traceback
PROJECT=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
if str(PROJECT) not in sys.path:sys.path.insert(0,str(PROJECT))

def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))

def run_acquisition(plan,folder,*,transport_factory=None,provider=None,lock_acquire=None,
                    bias_factory=None,analyze_function=None,emit=None):
    """Injection hooks are Python test APIs; no plan field can select one."""
    import numpy as np
    from adapters.science_runtime.policy import digest,sha
    from adapters.science_flux.contracts import provider_for,policy_for,is_commanded
    from adapters.science_flux.bias_session import BiasSession,durable_save
    from adapters.science_flux.compiler import compile_row,archive_row
    from adapters.science_flux.transport import FluxTransport
    from adapters.science_flux.analysis import analyze_cut
    from platform_worker import acquire_locks
    from platform_hooks import check_cancel,emit as native_emit
    fixture=any(x is not None for x in (transport_factory,provider,lock_acquire,bias_factory,analyze_function))
    emit=emit or native_emit;transport_factory=transport_factory or FluxTransport
    command_mode=is_commanded(plan['biasRoute'])
    if command_mode:
        from adapters.science_flux.command_session import CommandedBiasSession
        bias_factory=bias_factory or CommandedBiasSession
    else:bias_factory=bias_factory or BiasSession
    analyze_function=analyze_function or analyze_cut
    folder=Path(folder);results=folder/'results';results.mkdir(exist_ok=True)
    if any(results.iterdir()):raise ValueError('Nonempty operation cannot replay bias writes')
    base=read(PROJECT/'adapters/arclight_configurable_runtime/base_config.json')
    base['ADC_Meas']['B1']['para']['ADC_sRate']=plan['routing']['adcSampleRateHz']
    deadline=time.monotonic()+plan['max_seconds']-plan['cleanupReserveSeconds']
    locks=[];token=None;transport=None;session=None;captured=[];captured_rows=[];compiled=[];points=[];local_cavity=None
    completion={'state':'preflight','operationId':plan['operationId'],'resourceId':plan['resourceId'],
        'routing':plan['routing'],'shutdown_ok':True,'pointsCompleted':0,'testOnly':fixture,'points':points,'policy':policy_for(plan['biasRoute'])}
    def check():
        check_cancel()
        if time.monotonic()>=deadline:raise InterruptedError('Acquisition deadline reached; cleanup reserve protected')
    try:
        check()
        entry=None if provider is not None else provider_for(plan['biasRoute'])
        durable_save(results/'planned_axes.json',plan['rows']);durable_save(results/'parameters.json',plan['parameters'])
        # Compile all static rows before opening. Adaptive f01 readout is only
        # compiled after the actual local cavity passes its independent captures.
        for row in plan['rows']:
            if row['phase']=='f01':continue
            check();seq,expected=compile_row(row,base,plan['parameters'])
            item=archive_row(results/f'preflight_{row["id"]:04d}.npz',seq,expected);item['rowId']=row['id'];compiled.append(item)
        durable_save(results/'compiled_plan.json',compiled)
        check()
        for lock in plan['hardwareLocks']:Path(lock).parent.mkdir(parents=True,exist_ok=True)
        if command_mode and not fixture:
            from adapters.science_flux.command_lease import acquire_command_locks
            locks,token=acquire_command_locks(plan,folder,folder.parent)
        else:
            locks,token=(lock_acquire or acquire_locks)(plan,folder)
            if command_mode and plan.get('commandLease'):
                from adapters.science_flux.command_lease import bind_locks
                bind_locks(plan,folder,locks,token)
        transport=transport_factory(plan,results,check);completion['shutdown_ok']=False
        durable_save(results/'instrument.json',transport.open())
        if provider is None and not command_mode:provider=entry.open(route=plan['biasRoute'],station_handle=transport.hardware.driver.handle)
        session=bias_factory(plan['biasRoute'],provider,transport.set_offset,results/'bias_journal.json',
            identity={'operationId':plan['operationId'],'resourceId':plan['resourceId']},check_cancel=check,
            **({'authorization':plan['biasCommandAuthorization']} if command_mode else {}))
        completion['initial']=session.open_and_read_initial()
        for cut in plan['cuts']:
            if cut['phase']=='f01' and local_cavity is None:
                completion['f01SkippedReason']='Fresh local cavity was unresolved; no f01 waveform uploaded';break
            check();target=completion['initial']['setting']['valueV'] if cut['requestedSettingV'] is None else cut['requestedSettingV']
            before=session.ramp_to(target,cut['pointId']);row_ids=cut['rows'];cut_i=[];cut_q=[];blocks=[];actual_rows=[]
            for row_id in row_ids:
                row=plan['rows'][row_id];check();before=session.verify_held(cut['pointId'])
                seq,expected=compile_row(row,base,plan['parameters'],readout_hz=local_cavity)
                previous=next((r for r in compiled if r['rowId']==row_id),None)
                if previous is None:
                    item=archive_row(results/f'preflight_{row_id:04d}.npz',seq,expected)
                    item.update(rowId=row_id,adaptiveReadoutHz=local_cavity,sourceLocalCutSha256=points[-1]['cutSha256']);compiled.append(item)
                    durable_save(results/'compiled_plan.json',compiled)
                elif previous['waves']!=expected:raise ValueError('Native preflight/execution waveform identity differs')
                i,q=transport.acquire(row,seq,expected)
                # Retain every completed ADC block even if its after-read fails.
                captured.append((i,q));captured_rows.append(row);cut_i.append(i);cut_q.append(q);actual_rows.append(row)
                completion['pointsCompleted']=len(captured)
                with np.load(results/f'point_{row_id:04d}.npz',allow_pickle=False) as raw:
                    if not np.array_equal(raw['i'],i) or not np.array_equal(raw['q'],q):raise ValueError('ADC return differs from immutable raw point')
                    blocks.append(raw['acquisition_block_index'].copy())
                after=session.verify_held(cut['pointId'])
                durable_save(results/f'bias_row_{row_id:04d}.json',{'rowId':row_id,'pointId':cut['pointId'],'before':before,'after':after})
                before=after;completion['pointsCompleted']=len(captured)
                emit({'event':'row_acquired','completedRows':len(captured),'totalRows':plan['totalRows'],
                    'progress':100*len(captured)/plan['totalRows'],'operationId':plan['operationId'],'resourceId':plan['resourceId'],
                    'currentAxes':{'requestedOffsetV':target,'measuredOutputV':None if command_mode else after['measurement']['valueV'],
                        'phase':row['phase'],'frequencyHz':row['frequencyHz'],'capture':row['capture']},
                    'progressBasis':'actual-native-ADC-row-plus-commanded-bias' if command_mode else 'actual-native-ADC-row-plus-fresh-measured-bias'})
            cut_path=results/f'cut_{cut["cutId"]:03d}.npz';np.savez_compressed(cut_path,i=np.stack(cut_i),q=np.stack(cut_q),acquisition_block_index=np.stack(blocks))
            analysis=analyze_function(cut,actual_rows,np.stack(cut_i),np.stack(cut_q),plan['parameters'],np.stack(blocks))
            point={'cutId':cut['cutId'],'pointId':cut['pointId'],'phase':cut['phase'],'requestedSettingV':target,
                'biasReceipt':after,'analysis':analysis,'frequenciesHz':cut['frequenciesHz'],
                'responseMagnitude':analysis['responseMagnitude'],'readoutFrequencyHz':analysis['candidateHz'] if cut['phase']!='f01' else local_cavity,
                'f01Hz':analysis['candidateHz'] if cut['phase']=='f01' else None,'rawSha256':sha(cut_path),
                'rawPath':str(cut_path),'rowIds':row_ids}
            point['cutSha256']=digest(point);points.append(point);durable_save(results/'points.json',points)
            if cut['phase']=='local_cavity' and analysis['accepted']:local_cavity=analysis['candidateHz']
        np.savez_compressed(results/'raw_scan.npz',i=np.stack([v[0] for v in captured]),q=np.stack([v[1] for v in captured]))
        durable_save(results/'axes.json',captured_rows)
        completion.update(state='acquired',acquiredAt=dt.datetime.now(dt.timezone.utc).isoformat())
    except BaseException as exc:
        completion.update(state='cancelled' if isinstance(exc,InterruptedError) else 'failed_after_shutdown',error=str(exc),traceback=traceback.format_exc())
        if captured:np.savez_compressed(results/'partial_raw_scan.npz',i=np.stack([v[0] for v in captured]),q=np.stack([v[1] for v in captured]))
    finally:
        rf={'verified':transport is None};restore={'verified':session is None,'noSessionOpened':session is None};closed={'shutdown_ok':transport is None};provider_closed=True
        if transport:
            try:rf=transport.stop_rf()
            except BaseException as exc:rf={'verified':False,'error':str(exc)}
        if session:
            try:restore=session.restore_initial(completion['state'])
            except BaseException as exc:restore={'verified':False,'error':str(exc)}
        if provider:
            try:
                before_close=time.monotonic();provider.close(timeout_seconds=plan['biasRoute']['readTimeoutSeconds'])
                if time.monotonic()-before_close>plan['biasRoute']['readTimeoutSeconds']:raise TimeoutError('Provider close exceeded timeout')
            except BaseException as exc:provider_closed=False;completion['providerCloseError']=str(exc)
        if transport:
            try:closed=transport.close()
            except BaseException as exc:closed={'shutdown_ok':False,'error':str(exc)}
        end_ok=restore.get('commandEndVerified') is True if command_mode and session is not None else restore.get('verified') is True
        completion.update(rfShutdown=rf,restoration=restore,transportClose=closed,providerClosed=provider_closed,
            shutdown_ok=rf.get('verified') is True and end_ok and closed.get('shutdown_ok') is True and provider_closed)
        if completion['shutdown_ok']:
            try:
                if command_mode and plan.get('commandLease') and locks:
                    from adapters.science_flux.command_lease import retain_locks,release_command_locks
                    if completion['state']=='acquired' and plan['biasCommandAuthorization']['stage']=='selection':
                        completion['commandContinuation']=retain_locks(plan,folder,locks,token,restore)
                    else:release_command_locks(plan,folder,locks,token)
                else:
                    for lock in reversed(locks):
                        if read(lock).get('token')==token:Path(lock).unlink()
            except BaseException as exc:completion.update(shutdown_ok=False,state='needs_reconciliation',lockFinalizationError=str(exc))
        else:completion['state']='needs_reconciliation'
        completion['files']=[str(p) for p in results.rglob('*') if p.is_file()]+[str(folder/name) for name in ('plan.json','adapter_intent.json','completion.json')]
        durable_save(folder/'completion.json',completion)
    if completion['state']=='acquired':
        relevant=[p for p in points if p['phase']!='baseline'];valid=[p for p in relevant if p['analysis']['accepted']]
        accepted=bool(valid) if plan['procedureId']=='ro_flux_scan' else bool(relevant) and all(p['analysis']['accepted'] for p in relevant) and not completion.get('f01SkippedReason')
        candidate={}
        if accepted and plan['procedureId']=='sq_flux_point_recheck':
            candidate['readout_hz']=local_cavity
            f01=next((p for p in points if p['phase']=='f01'),None)
            if f01:candidate['drive_hz']=f01['f01Hz']
        metrics={'resolved_bias_points':len(valid),'acquired_bias_points':len(relevant),'restoration_verified':1,
            'scan_coverage':len(captured)/plan['totalRows'],'bias_readback_coverage':len(list(results.glob('bias_row_*.json')))/max(1,len(captured))}
        if command_mode:
            metrics.pop('restoration_verified');metrics.pop('bias_readback_coverage')
            metrics.update(declared_end_completed=1,bias_command_coverage=len(list(results.glob('bias_row_*.json')))/max(1,len(captured)))
        local=next((p for p in points if p['phase']=='local_cavity'),None)
        f01=next((p for p in points if p['phase']=='f01'),None)
        if local:metrics['local_cavity_contrast_snr']=local['analysis']['contrast_snr']
        if f01:metrics['peak_snr']=f01['analysis']['contrast_snr']
        completion['analysis']={'metrics':metrics,
            'candidateParameters':{},'scopedCandidateParameters':candidate,'series':[s for p in points for s in p['analysis'].get('series',[])],
            'analysis':{'accepted':accepted,'reasons':[] if accepted else ['No qualified same-bias feature or required local cavity/f01 phase failed'],
                'scope':('Commanded voltage / raw readout-IQ cuts; voltage output not independently measured; no cavity-to-f01 inference' if command_mode else
                    'Measured bias / raw readout-IQ cuts; each point qualified independently; no cavity-to-f01 inference')}}
        completion['state']='completed';durable_save(results/'analysis.json',completion['analysis']);completion['files'].append(str(results/'analysis.json'))
        durable_save(folder/'completion.json',completion)
    return completion

def main():
    folder=Path(sys.argv[1]).resolve().parent
    if Path(os.environ.get('QCAL_ARCLIGHT_RUN_DIR','')).resolve()!=folder:raise ValueError('Worker root identity mismatch')
    from adapters.science_flux_adapter import validate_admission
    plan=read(folder/'plan.json');validate_admission(PROJECT,plan,folder)
    native=PROJECT/'adapters/arclight_configurable_runtime';sys.path.insert(0,str(native))
    from bootstrap import initialize
    initialize()
    if run_acquisition(plan,folder)['state']!='completed':raise SystemExit(1)

if __name__=='__main__':main()
