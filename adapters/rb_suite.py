"""Controller-validated native RB suite planning and result registration.

This module is standard-library-only. Mathematical schedule generation and
analysis run in separate offline subprocesses; hardware uses the native worker.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

CAL_KEYS = ('readout_hz','readout_amp','drive_hz','drive_amp','drive_length_s','half_pi_amp','xy_delay_s')
RB_OPTIONS = {'rb_lengths','rb_sequences','rb_shots','rb_capture_repeats','rb_bootstrap_replicates','rb_master_seed'}
DEFAULTS = {'rb_lengths':[1,2,3,4,6,8,12,16], 'rb_sequences':30, 'rb_shots':1000,
            'rb_capture_repeats':2, 'rb_bootstrap_replicates':300}


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _same(a,b):
    return type(a) in (float,int) and type(b) in (float,int) and math.isfinite(a) and math.isfinite(b) and math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12)


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_snapshot(adapter,request,job):
    from .f8 import AdapterError
    snapshot=request.get('validationSnapshot')
    if not isinstance(snapshot,dict) or snapshot.get('version')!=1:
        raise AdapterError('RB_VALIDATION_REQUIRED','RB 需要控制器本轮独立验证参数快照；旧 TRIAL/历史文件不可替代')
    for key in ('runId','resourceId'):
        if snapshot.get(key)!=request.get(key):
            raise AdapterError('RB_VALIDATION_IDENTITY','验证快照身份/版本不匹配: '+key)
    frozen_versions=snapshot.get('parameterVersions',{});current_versions=request.get('parameterVersions',{})
    for key in CAL_KEYS:
        if type(current_versions.get(key)) is not int or current_versions[key]<0 or frozen_versions.get(key)!=current_versions[key]:
            raise AdapterError('RB_VALIDATION_IDENTITY','当前校准参数版本不匹配: '+key)
    parameters=snapshot.get('parameters',{})
    for key in CAL_KEYS:
        if not _same(parameters.get(key),job[key]):
            raise AdapterError('RB_VALIDATION_PARAMETERS','当前请求不等于验证参数: '+key)
    accepted=[];kinds=set();raw_ids=set()
    for entry in snapshot.get('acceptedEvidence',[]):
        procedure=entry.get('procedureId')
        category='rabi' if procedure in ('sq_rabi_amplitude','sq_rabi_time') else 'readout' if procedure in ('ro_iq01','ro_optimize','ro_optimize_frequency') else None
        if category is None:continue
        if entry.get('status')!='passed' or not entry.get('ruleVersion'):
            raise AdapterError('RB_EVIDENCE_NOT_ACCEPTED','本轮校准证据尚未通过冻结规则')
        selection=entry.get('selectionOperationId');verification=entry.get('verificationOperationId')
        if not selection or not verification or selection==verification:
            raise AdapterError('RB_INDEPENDENT_EVIDENCE_REQUIRED','RB 前置校准必须保留不同的选择和验证操作')
        results=[];receipts=[]
        for operation in (selection,verification):
            directory=adapter._operation_dir(operation)
            receipt_path=directory/'adapter_receipt.json';intent_path=directory/'adapter_intent.json'
            if not receipt_path.is_file() or not intent_path.is_file():
                raise AdapterError('RB_MISSING_RECEIPT','当前平台找不到前置真实采集回执')
            receipt,intent=_json(receipt_path),_json(intent_path);original=intent['request']
            if receipt.get('status')!='completed' or original.get('runId')!=request['runId'] or original.get('resourceId')!=request['resourceId'] or original.get('procedureId')!=procedure:
                raise AdapterError('RB_RECEIPT_IDENTITY','前置回执不属于本轮同对象同方法')
            result=receipt['result']
            if result.get('dataKind')!='real' or result.get('provenance',{}).get('freshAcquisition') is not True:
                raise AdapterError('RB_FRESH_DATA_REQUIRED','前置证据不是本平台新采集')
            ids={a['sha256'] for a in result.get('artifacts',[]) if a['kind']=='raw'}
            if not ids:raise AdapterError('RB_RAW_DATA_REQUIRED','前置回执缺少原始数据')
            for artifact in result['artifacts']:
                if artifact['kind']!='raw':continue
                from .history import sha256
                source=Path(artifact.get('localPath',artifact['sourcePath'])).resolve()
                if not source.is_relative_to(directory) or not source.is_file() or sha256(source)!=artifact['sha256']:
                    raise AdapterError('RB_EVIDENCE_HASH','前置原始数据身份发生变化')
            results.append((result,ids));receipts.append({'operationId':operation,'receiptPath':str(receipt_path),'receiptSha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest(),'rawSha256':sorted(ids)})
        if results[0][1]&results[1][1]:
            raise AdapterError('RB_REUSED_VALIDATION','选择和验证重复使用原始采集')
        verification_parameters=results[1][0].get('parameters',{})
        for key in CAL_KEYS:
            if not _same(verification_parameters.get(key),job[key]):
                raise AdapterError('RB_STALE_CALIBRATION','已验证校准参数与本轮 RB 不一致: '+key)
        if raw_ids & results[1][1]:raise AdapterError('RB_EVIDENCE_REUSE','不同前置校准重复使用同一原始采集')
        raw_ids.update(results[1][1]);kinds.add(category)
        accepted.append({'procedureId':procedure,'category':category,'ruleVersion':entry['ruleVersion'],'receipts':receipts})
    if kinds!={'rabi','readout'}:
        raise AdapterError('RB_VALIDATION_INCOMPLETE','RB 需同时具备本轮当前参数的 Rabi 与读出独立验证')
    return {'snapshotHash':_digest(snapshot),'acceptedEvidence':accepted,'parameterVersions':request['parameterVersions']}


def build_suite_plan(adapter,request,output_dir=None):
    from .f8 import AdapterError
    parameters=dict(request.get('parameters',{}));options={k:parameters.pop(k) for k in list(parameters) if k in RB_OPTIONS}
    forbidden={'calibration_file','frozen_plan_file','rb_variant','sequence_index','sequence_repeats','gate_lengths','random_seed'} & set(parameters)
    if forbidden:raise AdapterError('RB_LEGACY_FREEZE_REJECTED','完整 RB suite 不接受旧冻结校准或单分支字段')
    basic={**request,'procedureId':'ro_iq01','parameters':parameters}
    base=adapter.build_plan(basic);job=base['jobs'][0]
    options={**DEFAULTS,**options}
    options.setdefault('rb_master_seed',int(hashlib.sha256(request['operationId'].encode()).hexdigest()[:8],16))
    if options['rb_sequences']!=30:raise AdapterError('RB_SEQUENCE_BUDGET','原生配对套件固定 30 个随机序列簇')
    lengths=options['rb_lengths']
    if not isinstance(lengths,list) or not 6<=len(lengths)<=10 or lengths!=sorted(set(lengths)) or any(type(x)is not int or not 1<=x<=32 for x in lengths):
        raise AdapterError('RB_LENGTHS_INVALID','RB 长度为 6–10 个递增不重复整数，范围 1–32')
    for name,limits in [('rb_shots',[100,2000]),('rb_capture_repeats',[2,4]),('rb_bootstrap_replicates',[200,2000]),('rb_master_seed',[0,2**32-1])]:
        adapter._validate_number(name,options[name],limits,integer=True)
    validated=validate_snapshot(adapter,request,job)
    total=30*len(lengths)*2*options['rb_capture_repeats']*options['rb_shots']+2*2*4*options['rb_shots']
    if total>adapter.profile.get('maximumTotalShots',8000000):raise AdapterError('BUDGET_EXCEEDED','完整 RB suite 超过 shots 预算')
    plan={**base,'jobs':[],'estimated_total_shots':total,'suite':'native_paired_rb','suiteOptions':options,
          'validatedCalibration':validated,'requestIdentity':{k:request.get(k) for k in ('runId','nodeId','attemptId','operationId','resourceId','procedureId','parameterVersions')}}
    if output_dir is None:return plan
    output=Path(output_dir);calibration={
        'label':job['q'],'status':'FROZEN_FOR_NATIVE_RB','source':'current_controller_independent_validation',
        'runId':request['runId'],'resourceId':request['resourceId'],'parameterVersions':request['parameterVersions'],
        'pi_len_s':job['drive_length_s'],'pi_amp':job['drive_amp'],'half_pi_amp':job['half_pi_amp'],
        **{key:job[key] for key in ('readout_hz','readout_amp','drive_hz','xy_delay_s')},
        'pulse_policy':'unmodified native same-duration half-amplitude XY/2',
        'validatedEvidence':validated,'frozenBeforeAcquisition':True}
    calibration['calibrationVersion']=_digest(calibration)
    calibration_path=output/'calibration.json';calibration_path.write_text(json.dumps(calibration,ensure_ascii=False,indent=2),encoding='utf-8')
    spec={'q':job['q'],'lengths':lengths,'repeats':30,'masterSeed':options['rb_master_seed'],
          'nativeSource':str(Path(adapter.profile['basePath'])/'MeasModule/QOS/Gate/utils/clifford_RB.py'),
          'piLength':job['drive_length_s'],'xyDelay':job['xy_delay_s'],'probT0':job['prob_t0_s'],
          'calibrationVersion':calibration['calibrationVersion'],'output':str(output/'frozen_rb.json')}
    spec_path=output/'schedule_spec.json';spec_path.write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    with (output/'schedule_generation.log').open('w',encoding='utf-8') as log:
        result=subprocess.run([adapter.profile['python'],'-I','-B',str(adapter.root/'adapters/rb_analysis.py'),'prepare',str(spec_path)],
                              cwd=output,stdout=log,stderr=subprocess.STDOUT,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode!=0:raise AdapterError('RB_SCHEDULE_INVALID','原生配对序列或时序验证失败，未启动硬件')
    frozen=_json(output/'frozen_rb.json')
    prefix=job['id'];common={**job,'shots':options['rb_shots'],'capture_repeats':options['rb_capture_repeats'],'calibration_file':str(calibration_path),'save_backend':'dat'}
    pre={**common,'id':prefix+'_readout_pre','kind':'readout_cal','capture_repeats':4}
    post={**pre,'id':prefix+'_readout_post'}
    # A deterministic shuffled seed/branch order avoids a fixed reference-first trend.
    pairs=sorted(range(30),key=lambda i:hashlib.sha256(f"{options['rb_master_seed']}:order:{i}".encode()).digest())
    jobs=[pre]
    for index in pairs:
        variants=['reference','interleaved']
        if hashlib.sha256(f"{options['rb_master_seed']}:branch:{index}".encode()).digest()[0]%2:variants.reverse()
        for variant in variants:
            jobs.append({**common,'id':prefix+'_'+variant+'_'+str(index).zfill(2),'kind':'rb_iq','rb_variant':variant,
                         'random_seed':options['rb_master_seed'],'gate_lengths':lengths,'sequence_repeats':30,'sequence_index':index,
                         'frozen_plan_file':str(output/'frozen_rb.json')})
    jobs.append(post);plan['jobs']=jobs;plan['calibrationVersion']=calibration['calibrationVersion'];plan['frozenPlanHash']=_digest(frozen)
    plan['readoutBefore']=pre['id'];plan['readoutAfter']=post['id']
    return plan


def suite_result(adapter,directory,plan,request,cleanup):
    from .f8 import AdapterError
    from .history import sha256
    output=directory/'rb_analysis';output.mkdir(exist_ok=True)
    with (output/'analysis.log').open('w',encoding='utf-8') as log:
        process=subprocess.run([adapter.profile['python'],'-I','-B',str(adapter.root/'adapters/rb_analysis.py'),'analyze',str(directory/'plan.json')],
                               cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=240,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    report_path=output/'rb_suite_analysis.json';report=_json(report_path) if report_path.is_file() else None
    if process.returncode!=0 or not report:
        raise AdapterError('RB_ANALYSIS_INCOMPLETE','完整 RB 采集已留档，离线配对汇总未完成',{'analysisLog':str(output/'analysis.log')})
    paths=[report_path,output/'rb_suite.png',directory/'plan.json',directory/'calibration.json',directory/'frozen_rb.json',directory/'frozen_rb_validation.json',directory/(plan['phase']+'_hardware_after.json')]
    last_time=None
    for job in plan['jobs']:
        receipt_path=directory/(job['id']+'_complete.json');receipt=_json(receipt_path);last_time=receipt.get('utc',last_time)
        paths.append(receipt_path);paths.extend(Path(p) for p in receipt['files'])
    artifacts=[]
    for path in dict.fromkeys(paths):
        path=path.resolve()
        if not path.is_relative_to(directory) or not path.is_file():raise AdapterError('RB_PROVENANCE_INVALID','RB 原始文件或回执缺失')
        digest=sha256(path);kind='image' if path.suffix.lower() in ('.png','.jpg') else 'json' if path.suffix=='.json' else 'raw'
        identifier='real-'+digest[:24]
        artifacts.append({'id':identifier,'name':path.name,'kind':kind,'url':'/api/artifacts/'+identifier,'sha256':digest,'sourcePath':str(path),'localPath':str(path)})
    return {'dataKind':'real','sourceRunId':plan['phase'],'acquiredAt':last_time,'metrics':report['metrics'],
            'quality':{'status':'unverified','reasons':['完整原生配对 RB 模型条件估计；由控制器按冻结阈值和独立复测验收。']},
            'candidateParameters':{},'series':report['series'],'artifacts':artifacts,'parameters':{k:plan['jobs'][0][k] for k in CAL_KEYS},
            'methodLabel':'F8 native paired reference/X/2 interleaved RB · 30 seed clusters',
            'provenance':{'freshAcquisition':True,'operationId':request['operationId'],'parameterVersions':request['parameterVersions'],
                          'calibrationVersion':plan['calibrationVersion'],'frozenPlanHash':plan['frozenPlanHash'],
                          'runnerSha256':adapter.profile['runnerSha256'],'nativeHalfPolicy':'same-duration-half-amplitude',
                          'shutdownCommandsCompleted':not cleanup.get('shutdown_failures'),'shutdownReadbackVerified':False,
                          'analysis':report['scope'],'publicationChecks':report['publicationChecks']}}


def publish_validated_session(adapter,snapshot):
    """Publish only after the controller accepted a fresh second complete suite.

    The controller is the authority for rule evaluation; this function verifies
    its claim against immutable adapter receipts and current parameter identity.
    It never changes hardware or the controller's parameter registry.
    """
    from .f8 import AdapterError
    from .history import sha256
    if snapshot.get('version')!=1:
        raise AdapterError('CALIBRATION_PUBLICATION_SCHEMA','发布快照版本不正确')
    request={'runId':snapshot.get('runId'),'resourceId':snapshot.get('resourceId'),
             'parameterVersions':snapshot.get('parameterVersions'),'validationSnapshot':snapshot}
    job={**snapshot.get('parameters',{}),'q':snapshot.get('resourceId')}
    validated=validate_snapshot(adapter,request,job)
    rb_entries=[e for e in snapshot.get('acceptedEvidence',[]) if e.get('procedureId')=='sq_rb']
    if len(rb_entries)!=1:raise AdapterError('RB_PUBLICATION_REQUIRED','发布会话必须有唯一的完整 RB 独立复测通过记录')
    entry=rb_entries[0]
    if entry.get('status')!='passed' or not entry.get('ruleVersion'):raise AdapterError('RB_PUBLICATION_NOT_PASSED','RB 尚未由冻结规则验收')
    select,verify=entry.get('selectionOperationId'),entry.get('verificationOperationId')
    if not select or not verify or select==verify:raise AdapterError('RB_PUBLICATION_NOT_INDEPENDENT','发布需要两个独立完整 RB suite')
    observations=[];raw_sets=[];sources=[]
    for operation in (select,verify):
        directory=adapter._operation_dir(operation);receipt_path=directory/'adapter_receipt.json';intent_path=directory/'adapter_intent.json'
        if not receipt_path.is_file() or not intent_path.is_file():raise AdapterError('RB_PUBLICATION_MISSING','完整 RB 回执缺失')
        receipt,intent=_json(receipt_path),_json(intent_path);actual=intent['request']
        if receipt.get('status')!='completed' or actual.get('runId')!=snapshot['runId'] or actual.get('resourceId')!=snapshot['resourceId'] or actual.get('procedureId')!='sq_rb':
            raise AdapterError('RB_PUBLICATION_IDENTITY','RB 回执与当前会话不匹配')
        result=receipt['result'];provenance=result.get('provenance',{})
        if result.get('dataKind')!='real' or provenance.get('freshAcquisition') is not True:raise AdapterError('RB_PUBLICATION_NOT_REAL','发布拒绝历史或模拟 RB')
        for key in CAL_KEYS:
            if not _same(result.get('parameters',{}).get(key),job.get(key)):raise AdapterError('RB_PUBLICATION_STALE','RB 实际参数不等于发布参数: '+key)
        metrics=result.get('metrics',{})
        fidelity=metrics.get('gate_fidelity')
        if type(fidelity) not in (int,float) or not math.isfinite(fidelity) or not 0<=fidelity<=1:raise AdapterError('RB_PUBLICATION_UNRESOLVED','RB 模型条件保真度未辨识')
        checks=provenance.get('publicationChecks',{})
        if not checks or not all(checks.values()):raise AdapterError('RB_PUBLICATION_MODEL_CHECK','RB 配对统计发布检查未通过')
        raw_ids=set()
        for artifact in result.get('artifacts',[]):
            if artifact['kind']!='raw':continue
            path=Path(artifact.get('localPath',artifact['sourcePath'])).resolve()
            if not path.is_relative_to(directory) or not path.is_file() or sha256(path)!=artifact['sha256']:raise AdapterError('RB_PUBLICATION_HASH','RB 原始数据完整性检查失败')
            raw_ids.add(artifact['sha256'])
        if len(raw_ids)!=62:raise AdapterError('RB_PUBLICATION_INCOMPLETE','完整套件应有 60 分支及前后读出，共 62 条原始记录')
        raw_sets.append(raw_ids);observations.append(result)
        sources.append({'operationId':operation,'receiptPath':str(receipt_path),'receiptSha256':sha256(receipt_path),'rawSha256':sorted(raw_ids)})
    if raw_sets[0]&raw_sets[1]:raise AdapterError('RB_PUBLICATION_REUSED_RAW','两轮完整 suite 重用了原始数据')
    if observations[0]['provenance']['frozenPlanHash']==observations[1]['provenance']['frozenPlanHash']:raise AdapterError('RB_PUBLICATION_REUSED_PLAN','独立验证需新的冻结随机序列计划')
    publication={'version':1,'status':'VALIDATED_SESSION','dataKind':'real','runId':snapshot['runId'],'resourceId':snapshot['resourceId'],
                 'parameters':{k:job[k] for k in CAL_KEYS},'parameterVersions':snapshot['parameterVersions'],
                 'upstreamValidation':validated,'rbValidation':{'ruleVersion':entry['ruleVersion'],'sources':sources,
                    'selectionMetrics':observations[0]['metrics'],'verificationMetrics':observations[1]['metrics']},
                 'scope':'Current native F8 waveform policy, recorded routing and parameter versions only. Conditional IRB estimate, not a future validity guarantee.',
                 'instrument':adapter.profile['instrument'],'nativeHalfPolicy':'same-duration-half-amplitude'}
    publication['calibrationVersion']=_digest(publication)
    destination=adapter.root/'data/calibrations';destination.mkdir(parents=True,exist_ok=True)
    path=destination/(snapshot['resourceId']+'_'+publication['calibrationVersion']+'.json')
    text=json.dumps(publication,ensure_ascii=False,indent=2,allow_nan=False)
    if path.exists() and path.read_text(encoding='utf-8')!=text:raise AdapterError('CALIBRATION_VERSION_CONFLICT','已发布版本内容不一致')
    if not path.exists():path.write_text(text,encoding='utf-8')
    digest=sha256(path);identifier='calibration-'+digest[:24]
    return {'id':identifier,'name':path.name,'kind':'json','url':'/api/artifacts/'+identifier,'sha256':digest,'localPath':str(path),'sourcePath':str(path),
            'calibrationVersion':publication['calibrationVersion'],'status':'VALIDATED_SESSION'}
