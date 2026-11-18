"""Isolated original F8 ADC acquisitions, explicit plans and native records."""
import argparse,asyncio,hashlib,json,os,sys,time,traceback,types,subprocess
from pathlib import Path
from datetime import datetime,timezone
from progress import SweepProgress
ROOT=Path(os.environ['QCAL_F8_RUN_DIR']).resolve()
BASE=Path(os.environ['QCAL_F8_BASE']).resolve()
F8=ROOT/'F8_copy'
parser=argparse.ArgumentParser();parser.add_argument('plan',type=Path);args=parser.parse_args()
plan=json.loads(args.plan.read_text(encoding='utf-8'));phase=plan['phase']
assert plan.get('ready_for_acquisition',True) and 'DRAFT' not in plan.get('status',''), 'Draft plan cannot acquire'
RUNNER_SOURCE=Path(__file__).read_bytes()
assert phase.replace('_','').isalnum()
assert not (ROOT/(phase+'_complete.json')).exists()
LOCK=Path(os.environ['QCAL_F8_HARDWARE_LOCK'])
fd=os.open(LOCK,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,json.dumps({'pid':os.getpid(),'phase':phase}).encode());os.close(fd)
def encode(v):
    if isinstance(v,complex):return {'__complex__':[v.real,v.imag]}
    if hasattr(v,'tolist'):return v.tolist()
    raise TypeError(type(v).__name__)
def save(name,value):
    (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=encode),encoding='utf-8')
def emit(event,**kw):
    row=dict(utc=datetime.now(timezone.utc).isoformat(),phase=phase,event=event,**kw)
    with (ROOT/(phase+'_events.jsonl')).open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False,default=encode)+'\n')
    if event not in ('row_set','row_acquired','waveform') or int(kw.get('row',0))%25==0:print(json.dumps(row,ensure_ascii=False,default=encode),flush=True)
    return row
async def main():
    um=station=None;complete=False;records=[];started=time.monotonic();current={'job':None,'row':-1}
    try:
        os.chdir(F8)
        for p in reversed([BASE/'MeasModule',BASE/'meas',F8]):sys.path.insert(0,str(p))
        import numpy as np
        import matplotlib;matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plt.rcParams['font.sans-serif']=['Microsoft YaHei'];plt.rcParams['axes.unicode_minus']=False
        import pyvisa,pyvisa.highlevel
        rm_original=pyvisa.highlevel.ResourceManager
        pyvisa.ResourceManager=lambda backend=None:rm_original(backend or 'C:/Windows/System32/visa64.dll')
        from QOS._config import config as qos_config
        qos_config['storage']['data_path']=str(ROOT/'raw');qos_config['drivers']=[]
        Path(qos_config['storage']['data_path']).mkdir(exist_ok=True)
        from QOS.WorkSpace import start,QOS_WORKSPACE
        from QOS.Chip.utils import cfgContext
        from meas.ini import set_ini,ini_dict
        from meas.executor.cy import ADC_Executor,Repeat_Executor
        import meas.experiment as adc
        from QOS.Gate.GateEditor_cache import GateEditor
        from QOS.Gate.utils.compiler_utils import aliasMap_decompos
        GateEditor.use_compiler=True;GateEditor.aliasMap=aliasMap_decompos
        pulse_policy_record=None
        for name in ['QOS','Meas','meas']:
            assert all(Path(p).resolve().is_relative_to(BASE) for p in __import__(name).__path__)
        set_ini(cfg_file=str(F8/'config_TGQ-TEST-F8'),filedict_file=str(F8/'filedict_TGQ-TEST-F8_Agent'))
        from workspace_adc import workspace
        await start(workspace);um=QOS_WORKSPACE['um'];station=um.ins_pool['CY_Station1'];dev=station.handle
        dev.timeout=10000
        import AWG1652AM as LF,Ceyear_AWG5000 as RF,Ceyear_OSC as OSC
        LF.setTrigType(dev,'CLOSE')
        prior={}
        for slot,module in [(0,0),(15,8)]:
            RF.boardSelect(dev,module);RF.channSelect(dev,0)
            prior[str(slot)]={k:dev.query('AWG5000:'+k+'?').strip() for k in ['SELFILTERFREQ','AMPL','WAVESEL','CHLOUTPUT','ARB:RUNCE','GLOBALTRIGARB']}
            RF.ChannelOutput(dev,'OFF');RF.globalTrigArb(dev,'OFF');RF.ArbRunEn(dev,'OFF')
        OSC.oscboardSelect(dev,0);OSC.oscGain1(dev,16);OSC.oscGain2(dev,16)
        emit('connected',idn=dev.query('*IDN?').strip(),prior=prior)
        original_write=station.writeWaveform
        def checked_write(data,ch=(0,0),**kwargs):
            assert ch in [(0,0),(15,0)],ch
            a=np.asarray(data); peak=float(np.abs(a).max(initial=0))
            assert np.isfinite(a).all() and peak<=1.00000001,('invalid waveform',ch,peak)
            emit('waveform',**current,ch=ch,points=a.size,peak=peak,sha256=hashlib.sha256(a.tobytes()).hexdigest())
            return original_write(data,ch=ch,**kwargs)
        station.writeWaveform=checked_write
        original_hard_read=station.readHardFFTData
        snapshot_dir=ROOT/'runner_snapshots';snapshot_dir.mkdir(exist_ok=True)
        script_hash=hashlib.sha256(RUNNER_SOURCE).hexdigest()
        source_snapshot=snapshot_dir/f'{phase}_{script_hash[:12]}.py';source_snapshot.write_bytes(RUNNER_SOURCE)
        save(phase+'_plan_snapshot.json',{'plan':plan,'script_sha256':script_hash,'source_snapshot':str(source_snapshot)})
        for job_index,job in enumerate(plan['jobs'],start=1):
            q=job['q'];label=job['id'];kind=job['kind'];current.update(job=label,row=-1)
            assert label!=phase, 'Use distinct job and phase names to preserve separate receipts'
            assert job.get('shots',500)<=2000 or job.get('allow_segment_limit_diagnostic',False), 'Hardware IQ limit verified: use <=2000 shots per capture; aggregate separate captures for more averaging'
            station.readHardFFTData=original_hard_read
            if job.get('demod_compare'):
                assert len(job.get('readout_targets',[q]))==1
                def comparison_read(ch):
                    hardware,unused=original_hard_read(ch)
                    shots=int(station.config['shots'][ch]['value'])
                    points=int(station.config['pointNum'][ch]['value'])
                    raw1,raw2=OSC.readOut_real(dev,points,shots)
                    raw1=np.asarray(raw1);raw2=np.asarray(raw2)
                    assert raw1.shape==(shots,points) and np.isfinite(raw1).all()
                    head=int(job.get('software_demod_head',1000));length=int(job.get('software_demod_length',3000))
                    cycles=float(job['software_demod_cycles'])
                    assert 0<cycles<.5 and head+length<=points
                    coefficient=np.exp(-2j*np.pi*cycles*np.arange(head,head+length))/length
                    software=(raw1[:,head:head+length]@coefficient)[:,None]
                    folder=ROOT/'raw_adc_demod_comparison'/label;folder.mkdir(parents=True,exist_ok=True)
                    rawfile=folder/f'row_{current["row"]:04d}.npz'
                    np.savez_compressed(rawfile,physical_ch1=raw1,physical_ch2=raw2,hardware_iq=hardware,software_iq=software,cycles_per_sample=cycles,head=head,length=length)
                    emit('demod_comparison',**current,path=str(rawfile),sha256=hashlib.sha256(rawfile.read_bytes()).hexdigest(),hardware_mean=np.asarray(hardware).mean(axis=0),hardware_std=np.asarray(hardware).std(axis=0),software_mean=software.mean(axis=0),software_std=software.std(axis=0),returned='software' if job.get('use_software_demod') else 'hardware')
                    return (software,np.zeros_like(software)) if job.get('use_software_demod') else (hardware,unused)
                station.readHardFFTData=comparison_read
            GateEditor.use_compiler=job.get('use_compiler',True)
            sr=float(job.get('drive_srate',4e9));assert sr in [4e9,4.8e9]
            RF.boardSelect(dev,0);RF.channSelect(dev,0);RF.SelFilter(dev,int(sr/1e6))
            band_reply=dev.query('AWG5000:SELFILTERFREQ?').strip()
            assert int(float(band_reply))==(4700 if sr==4e9 else 3700),band_reply
            emit('drive_band',job=label,srate=sr,filter_reply=band_reply,use_compiler=GateEditor.use_compiler)
            assert q in ['Q11','Q18','Q19'] and kind in ['s21','s21_drive','s21_power','s21_ge','snr_amp','snr_freq','power_rabi','spectrum','spectrum_delay','rabi','rabi_freq','trace','t1','ramsey','echo','cpmg','readout_cal','rb_iq','allxy']
            assert not (ROOT/(label+'_complete.json')).exists()
            with cfgContext(str(F8/'config_TGQ-TEST-F8')) as cfg:
                cfg['Chip'].update(All_Qubits=[q],current_Qubit=q,crossTalk_Qubits=[q])
                readouts=job.get('readout_targets',[{'q':q,'hz':job['readout_hz'],'amp':job.get('readout_amp',.2)}])
                assert q in [r['q'] for r in readouts]
                cfg['ADC_Meas']['B1']['Multi_Readout']['Qubits']=[r['q'] for r in readouts]
                for r in readouts:
                    cfg[r['q']]['para'].update(probFreq=r['hz'],probCarryFreq=r['hz'],probCarryScale=r['amp'])
                cfg[q]['para']['probFreq']=job['readout_hz'];cfg[q]['para']['probCarryFreq']=job['readout_hz']
                cfg[q]['para']['probCarryScale']=job.get('readout_amp',.2)
                cfg[q]['para']['drivFreq']=job.get('drive_hz',cfg[q]['para']['drivFreq']);cfg[q]['para']['drivCarryFreq']=cfg[q]['para']['drivFreq']
                cfg['ADC_Meas']['B1']['para']['shots']=job.get('shots',500)
                if 'prob_t0_s' in job:
                    cfg['ADC_Meas']['global']['timeSeq']['probT0']=job['prob_t0_s']
                    cfg['ADC_Meas']['global']['timeSeq']['wdLen']=job['prob_t0_s']+6e-6
                    cfg['ADC_Meas']['B1']['para']['triggerDelay']=job['prob_t0_s']
                cfg[q]['gate']['U_gates'].update(pi_amp=job.get('drive_amp',.4),pi_len=job.get('drive_length_s',2e-6),shape=job.get('drive_shape','CosPulse'),use_drag=False,lambda1=0)
                cfg[q]['gate']['sRate']['XY']=sr
                cfg[q]['gate']['U_gates']['sRate']=[sr,1.2e9]
                cfg[q]['cali']['Delay']['XY']=job.get('xy_delay_s',0)
            setting={'readout_mode':'IQ','preprocess_mode':'original','shots_avg':True,'singleShot_mode':None,'populationConvert_mode':None}
            tags=[f'*F8NativeReplication20261117@{label}',f'*AscendingFrequencyLabel@{q}', '*ReadoutOrder@'+','.join(r['q'] for r in readouts)]
            if kind=='s21_power':
                expr=adc.S21.S21_dds_Power(q,job['span_hz'],job['points'],np.asarray(job['powers']),tags=tags)
            elif kind=='s21_ge':
                expr=adc.S21.S21_dds_ge(q,job['span_hz'],job['points'],tags=tags)
            elif kind=='power_rabi':
                expr=adc.Rabi.PowerRabi(q,np.asarray(job['amplitudes']),tags=tags)
            elif kind=='snr_amp':
                expr=adc.S21.SNR_probCarryScale(q,job['amp_start'],job['amp_stop'],job['points'],tags=tags)
            elif kind=='snr_freq':
                expr=adc.S21.SNR_Freq(q,job['span_hz'],job['points'],tags=tags)
            elif kind in ['s21','s21_drive','trace']:
                if kind=='trace':setting.update(readout_mode='TraceIQ',preprocess_mode=None,shots_avg=False)
                expr=adc.S21.S21_dds(q,job.get('span_hz',1),job.get('points',2),tags=tags,setting=setting)
                if kind=='s21_drive':
                    expr['setting']['gateMap']={q:['X'] if job.get('drive_on',False) else ['Blank_2e-6']}
                    if job.get('interleave_drive_state'):
                        expr['setting']['agent_drive_state']=0
                        expr['sweep_config']['DriveState']='st.agent_drive_state'
                        expr['sweep_config']['unit']['DriveState']='OFF0_ON1'
                        expr['sweep_setting']['DriveState']=np.array([0,1])
                        def drive_gate_map(state, target=q, width=job['drive_length_s']):
                            return {target:['X'] if int(state)==1 else [f'Blank_{width}']}
                        expr['constrains'].append((('st.agent_drive_state',),drive_gate_map,'st.gateMap'))
            elif kind in ['spectrum','spectrum_delay']:
                axis=np.asarray(job['frequencies_hz']) if kind=='spectrum_delay' else np.linspace(job['start_hz'],job['stop_hz'],job['points'])
                if job.get('reverse'):axis=axis[::-1]
                expr=adc.Spectrum.Spectrum_dds(q,axis,pi_amp=job['drive_amp'],pi_len=job['drive_length_s'],tags=tags,setting=setting)
                expr['setting']['gateMap']={q:['X'] if job.get('drive_on',True) else ['Blank_2e-6']}
                if kind=='spectrum_delay':
                    expr['name']='Spectrum_XYDelay'
                    expr['sweep_config']['name']=f'Spectrum_XYDelay{q}'
                    expr['sweep_config']['XYDelay']=f'cfg.{q}.cali.Delay.XY'
                    expr['sweep_config']['unit']['XYDelay']='s'
                    expr['sweep_setting']['XYDelay']=np.linspace(job['delay_start_s'],job['delay_stop_s'],job['delay_points'])
            elif kind=='rabi':
                time_axis=np.linspace(job['start_s'],job['stop_s'],job['points'])
                if job.get('reverse_time'):time_axis=time_axis[::-1]
                if 'shuffle_time_seed' in job:
                    assert not job.get('reverse_time')
                    time_axis=np.random.default_rng(job['shuffle_time_seed']).permutation(time_axis)
                expr=adc.Rabi.TimeRabi(q,time_axis,tags=tags,setting=setting)
                if 'drive_amplitudes' in job:
                    amps=np.asarray(job['drive_amplitudes']);assert np.all((amps>=0)&(amps<=.98))
                    expr['sweep_config']['DriveAmplitude']=f'cfg.{q}.gate.U_gates.pi_amp'
                    expr['sweep_config']['unit']['DriveAmplitude']='normalized'
                    expr['sweep_setting']['DriveAmplitude']=amps
            elif kind=='rabi_freq':
                expr=adc.Rabi.TimeRabi_Freq(q,np.asarray(job['frequencies_hz']),np.linspace(job['start_s'],job['stop_s'],job['points']),tags=tags,setting=setting)
            elif kind=='allxy':
                expr=adc.AllXY.AllXY(q,repeat=1,tags=tags,setting=setting)
                cal_path=Path(job['calibration_file']).resolve();assert cal_path.is_relative_to(ROOT)
                cal=json.loads(cal_path.read_text(encoding='utf-8'))
                assert abs(cal['pi_len_s']-job['drive_length_s'])<1e-12 and abs(cal['pi_amp']-job['drive_amp'])<1e-12
                expr['config'][q]['gate']['XY_gates'].update(Y_shift=0,half_pi_amp=cal['half_pi_amp'])
                expr['config'][q]['gate']['U_gates'].update(z_amp=0,detuning=0,use_decompos=False)
                if 'shuffle_index_seed' in job:
                    expr['sweep_setting']['AllXY_index']=np.random.default_rng(job['shuffle_index_seed']).permutation(21)
                expr['setting']['agent_calibration_file']={'path':str(cal_path),'sha256':hashlib.sha256(cal_path.read_bytes()).hexdigest()}
            elif kind=='t1':
                expr=adc.T1.T1(q,np.linspace(job['start_s'],job['stop_s'],job['points']),tags=tags)
            elif kind=='ramsey':
                expr=adc.Ramsey.Ramsey(q,np.linspace(job['start_s'],job['stop_s'],job['points']),job['rotation_hz'],tags=tags)
            elif kind=='echo':
                expr=adc.Ramsey.Echo(q,np.linspace(job['start_s'],job['stop_s'],job['points']),job['rotation_hz'],tags=tags)
            elif kind=='cpmg':
                expr=adc.Ramsey.CPMG(q,np.linspace(job['start_s'],job['stop_s'],job['points']),job.get('rotation_hz',0),N=job['pi_pulses'],tags=tags)
            elif kind=='readout_cal':
                expr=adc.readout.get_ReadoutM(q,shots=job['shots'],tags=tags)
            elif kind=='rb_iq':
                # Native factories and native Clifford generation; no waveform patch.
                np.random.seed(job['random_seed'])
                if job['rb_variant']=='reference':
                    expr=adc.RB1Q.RB(q,job['gate_lengths'],repeats=job['sequence_repeats'],tags=tags)
                else:
                    expr=adc.RB1Q.RB_interleaved(q,[4],job['gate_lengths'],repeats=job['sequence_repeats'],tags=tags)
                import native_rb_pairing
                frozen_path=Path(job['frozen_plan_file']).resolve()
                assert frozen_path.is_relative_to(ROOT)
                frozen=json.loads(frozen_path.read_text(encoding='utf-8'))
                assert frozen['status']=='FROZEN_FOR_NATIVE_ACQUISITION'
                native_cal_path=Path(job['calibration_file']).resolve()
                assert native_cal_path.is_relative_to(ROOT)
                native_cal=json.loads(native_cal_path.read_text(encoding='utf-8'))
                assert native_cal['status']=='FROZEN_FOR_NATIVE_RB'
                for ck,jk in [('pi_len_s','drive_length_s'),('pi_amp','drive_amp'),('half_pi_amp','half_pi_amp'),('drive_hz','drive_hz'),('readout_hz','readout_hz'),('readout_amp','readout_amp'),('xy_delay_s','xy_delay_s')]:
                    assert abs(native_cal[ck]-job[jk])<=max(1e-12,abs(native_cal[ck])*1e-12),(ck,native_cal[ck],job[jk])
                ts=expr['config']['ADC_Meas']['global']['timeSeq']
                timing_audit=native_rb_pairing.validate_plan(frozen,job['drive_length_s'],
                    buff_s=float(expr['config'][q]['gate']['U_gates']['buff']),xy_delay_s=job['xy_delay_s'],
                    prob_t0_s=float(ts['probT0']),gap_time_s=float(ts['gapTime']),waveform_length_s=float(ts['wdLen']))
                expr['setting']['shots_avg']=True
                expr['setting']['native_rb_readout_basis']='per-capture mean complex IQ; original F8 magnitude analysis, independent frozen projection crosscheck'
                expr['setting']['native_calibration_file']={'path':str(native_cal_path),'sha256':hashlib.sha256(native_cal_path.read_bytes()).hexdigest()}

                assert timing_audit['decision']=='PASS',timing_audit
                expr=native_rb_pairing.bind_frozen_gate_map(expr,frozen,job['rb_variant'],job['sequence_index'],ROOT/'rb_dispatch'/f'{label}.jsonl')
                expr['setting']['frozen_plan_file']=str(frozen_path)
                expr['setting']['frozen_plan_file_sha256']=hashlib.sha256(frozen_path.read_bytes()).hexdigest()

            if kind in ['t1','ramsey','echo','cpmg','readout_cal','rb_iq']:
                assert abs(expr['config'][q]['gate']['U_gates']['pi_amp']-job['drive_amp'])<1e-12
            else:
                expr['config'][q]['gate']['U_gates']['pi_amp']=job.get('drive_amp',.4)
            expr['config'][q]['gate']['U_gates'].update(use_drag=False,lambda1=0,z_amp=0,detuning=0,use_decompos=False)
            expr['config'][q]['gate']['XY_gates'].update(half_pi_amp=job.get('half_pi_amp',job.get('drive_amp',.98)/2),Y_shift=0)
            expr['setting']['F8_native_replication']={'waveform_policy':'unmodified native XY_gates; same-duration half-amplitude XY/2','original_notebook':'D:/coding_tgqos/F8/8号机测试.ipynb','native_constructor_kind':kind}

            if pulse_policy_record:
                expr['setting']['agent_pulse_policy']=pulse_policy_record
                expr['tags'].append('*PulsePolicy@half_duration_full_amplitude;XY_half_duration=pi_len/2;XY_half_amplitude=pi_amp')
            repeats=int(job.get('capture_repeats',1));assert 1<=repeats<=16
            if repeats>1 and 'CaptureRepeat' not in expr['sweep_setting']:
                assert kind!='trace' and not job.get('demod_compare')
                expr['setting']['agent_capture_repeat']=0
                expr['sweep_config']['CaptureRepeat']='st.agent_capture_repeat'
                expr['sweep_config']['unit']['CaptureRepeat']='index'
                expr['sweep_setting']['CaptureRepeat']=np.arange(repeats)
                expr['tags'].append(f'*CaptureRepeat@{repeats}x{job.get("shots",500)}shots_each;independent_captures')
            if repeats>1:assert len(expr['sweep_setting']['CaptureRepeat'])==repeats
            save(label+'_request.json',{'job':job,'config':expr['config'],'setting':expr['setting'],'sweep_setting':expr['sweep_setting']})
            progress_tracker=SweepProgress(expr['sweep_setting'],native_repeats=int(job.get('native_repeats',1)),job_index=job_index,job_count=len(plan['jobs']))
            async def before_set(**kw):
                current['row']=int(kw['idx'])
                if (ROOT/'STOP_ADC_AFTER_ROW').exists() or time.monotonic()-started>plan.get('max_seconds',3600):raise TimeoutError('Controlled row stop')
            async def after_set(**kw):
                current['row']=int(kw['idx'])
                if (ROOT/'STOP_ADC_AFTER_ROW').exists() or time.monotonic()-started>plan.get('max_seconds',3600):raise TimeoutError('Controlled row stop')
                if kind=='rb_iq':
                    cfg_actual=kw['cfg'][q]
                    assert abs(cfg_actual['gate']['XY_gates']['half_pi_amp']-native_cal['half_pi_amp'])<1e-12
                    assert abs(cfg_actual['gate']['U_gates']['pi_len']-native_cal['pi_len_s'])<1e-12
                    assert abs(cfg_actual['gate']['U_gates']['pi_amp']-native_cal['pi_amp'])<1e-12
                    verified=native_rb_pairing.verify_dispatched_gate_map(kw['st'],frozen,job['rb_variant'],q,job['sequence_index'])
                    emit('native_rb_dispatch_verified',**current,**verified)
                c=kw['cfg'];p=c[q]['para'];g=c[q]['gate']['U_gates']
                assert 0<=p['probCarryScale']<=.8 and 0<=g['pi_amp']<=.98
                emit('row_set',**current,prob_hz=p['probFreq'],prob_carry_hz=p['probCarryFreq'],drive_hz=p['drivFreq'],drive_carry_hz=p['drivCarryFreq'],pi_len=g['pi_len'],pi_amp=g['pi_amp'],xy_delay_s=c[q]['cali']['Delay']['XY'],shots=c['ADC_Meas']['B1']['para']['shots'])
            async def after_get(**kw):emit('row_acquired',**current,**progress_tracker.acquired())
            emit('job_start',job=label,kind=kind,settings=job,**progress_tracker.state())
            if job.get('native_repeats',1)>1:
                assert kind in ['snr_amp','snr_freq']
                assert job.get('capture_repeats',1)==1
                expr.update(before_set=before_set,after_set=after_set,after_get=after_get)
                rd=await Repeat_Executor(expr,repeats=job['native_repeats'],delta_time=1,wait=job.get('wait_s',.02),bar_mode='NoneBar')
            else:
                rd=await ADC_Executor(expr,wait=job.get('wait_s',.02),before_set=before_set,after_set=after_set,after_get=after_get,bar_mode='NoneBar',save_backend=job.get('save_backend','dat' if kind in ['trace','rb_iq','readout_cal'] else 'dat.fig'))
            rec=emit('job_complete',job=label,files=[str(x) for x in rd.id_list],elapsed_s=time.monotonic()-started,**progress_tracker.complete())
            save(label+'_complete.json',rec);records.append(rec);plt.close('all')
        complete=True;save(phase+'_complete.json',{'completed':True,'records':records})
    except BaseException as e:
        emit('failed',type=type(e).__name__,detail=str(e),traceback=traceback.format_exc());raise
    finally:
        failures=[];states={}
        if station:
            for action in [lambda:LF.setTrigType(station.handle,'CLOSE'),lambda:OSC.oscboardSelect(station.handle,0),lambda:OSC.oscTrigStore(station.handle,'OFF'),lambda:OSC.oscEndWriteDDR(station.handle)]:
                try:action()
                except Exception as e:failures.append(str(e))
            for slot,module in [(0,0),(15,8)]:
                try:
                    # Replace the last finite pulse with zero before shutdown.
                    # Firmware state-query replies are retained without claiming OFF.
                    station.writeWaveform(np.zeros(4096,dtype=float),ch=(slot,0))
                    RF.boardSelect(station.handle,module);RF.channSelect(station.handle,0);RF.globalTrigArb(station.handle,'OFF');RF.ArbRunEn(station.handle,'OFF');RF.ChannelOutput(station.handle,'OFF')
                    states[str(slot)]={k:station.handle.query('AWG5000:'+k+'?').strip() for k in ['CHLOUTPUT','ARB:RUNCE','GLOBALTRIGARB']}
                except Exception as e:failures.append(str(e))
        save(phase+'_hardware_after.json',emit('finally',completed=complete,shutdown_failures=failures,states=states,shutdown_method='CLOSE trigger; replace both last waveforms with zeros; request RF OFF; preserve unvalidated firmware state-query replies'))
        if um:await um.close()
try:asyncio.run(main())
finally:
    if LOCK.exists():LOCK.unlink()
