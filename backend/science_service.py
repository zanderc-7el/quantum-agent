"""Scientific workbench in the controller's SQLite. No instrument operations.

All caller-supplied curves remain provided_data/model data. Real acquisition is
resolved by persisted workflow/attempt IDs and registered raw artifact hashes.
"""
from __future__ import annotations
from copy import deepcopy
import bisect
import json
import math
import re

from .common import APIError, digest, dumps, ident, now, file_hash, checked_path, number
from .science_models import preview, bloch, bloch_scan_endpoints, MODEL_VERSION, vector, series, scalar, fail
from .science_dependencies import ScienceDependencyResolver, LEGACY, MAX_ARTIFACTS, quality_policy_current
from .science_comparison import (READOUT_KEYS, calibration_template, import_calibration,
                                 forward_observation, projection_context, excluded_points)
from .science_shelve_display import measurement_fields as shelve_measurement_fields
from .science_channel_comparison import (KIND as CHANNEL_COMPARISON_KIND, template as channel_template,
                                         import_comparison as import_channel_comparison)

MAX_LIST_BYTES=8*1024*1024
MAX_DETAIL_BYTES=64*1024*1024

KINDS=('pulse','drag','predistortion','bloch','simulation','shelve','flux','working_point','measurement','readout_calibration',CHANNEL_COMPARISON_KIND)
LABELS={'pulse':'脉冲时序','drag':'DRAG','predistortion':'预失真','bloch':'布洛赫演化','simulation':'同点模拟',
        'shelve':'SHELVE','flux':'S21–Flux','working_point':'工作点','measurement':'采集记录','readout_calibration':'外部读出标定',
        CHANNEL_COMPARISON_KIND:'外部通道补偿比较'}


class ScienceService:
    def __init__(self, controller):
        self.c=controller;self.store=controller.store;self.dependencies=ScienceDependencyResolver(controller)
        with self.store.transaction() as rt:
            rt.db.execute('CREATE TABLE IF NOT EXISTS platform_science_records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, workflow_id TEXT, revision INTEGER NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL)')
            rt.db.execute('CREATE TABLE IF NOT EXISTS platform_science_operations(id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, response TEXT NOT NULL)')

    @staticmethod
    def _path(path):
        if not isinstance(path,str):
            raise APIError('NOT_FOUND','科学接口不存在',404)
        if path.startswith('/api/science/'):return path[len('/api/science/'):]
        if path.startswith('/') and not path.startswith('/api/'):return path[1:]
        raise APIError('NOT_FOUND','科学接口不存在',404)

    def _scope(self, rt, supplied, reference=None):
        if not isinstance(supplied,dict) or set(supplied)-{'workflowId','resourceId','chipVersionId','deploymentVersionId','observedResourceBindingSha256'}:
            fail('范围字段不正确')
        scope=deepcopy(supplied)
        if any(not isinstance(v,str) or not v.strip() or len(v)>256 for v in scope.values()):fail('范围标识无效')
        if reference:
            if not isinstance(reference,dict) or set(reference)!={'workflowId','attemptId'} or any(not isinstance(v,str) or not v for v in reference.values()):fail('真实来源须提供任务和采集编号')
            if scope.get('workflowId',reference['workflowId'])!=reference['workflowId']:fail('来源与任务范围不一致','SCIENCE_SCOPE_MISMATCH')
            scope['workflowId']=reference['workflowId']
        wf=None
        if scope.get('workflowId'):
            wf=self.store.get(rt,scope['workflowId'])
            if not wf:raise APIError('WORKFLOW_NOT_FOUND','任务不存在',404)
            resources=wf.get('resourceIds',[])
            if scope.get('resourceId') and scope['resourceId'] not in resources:fail('比特不属于该任务','SCIENCE_SCOPE_MISMATCH')
            if not scope.get('resourceId') and len(resources)==1:scope['resourceId']=resources[0]
            for key in ('chipVersionId','deploymentVersionId'):
                actual=wf.get(key)
                if key in scope and scope[key]!=actual:fail('配置版本与任务不一致','SCIENCE_SCOPE_MISMATCH')
                if actual:scope[key]=actual
        else:
            if scope.get('resourceId') and not scope.get('chipVersionId'):fail('未绑定任务的比特必须选择芯片版本','SCIENCE_SCOPE_MISMATCH')
            for key,kind in [('chipVersionId','chip'),('deploymentVersionId','deployment')]:
                if scope.get(key):
                    if not rt.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='config_versions'").fetchone():
                        fail('尚未发布芯片或接线配置','SCIENCE_SCOPE_MISMATCH')
                    row=rt.db.execute('SELECT payload FROM config_versions WHERE id=? AND kind=?',(scope[key],kind)).fetchone()
                    if not row:fail('请选择已发布配置版本','SCIENCE_SCOPE_MISMATCH')
                    config=json.loads(row[0])
                    if kind=='chip' and scope.get('resourceId') and scope['resourceId'] not in [q['id'] for q in config['qubits']]:fail('比特不属于芯片版本','SCIENCE_SCOPE_MISMATCH')
        return self.dependencies.scope(rt,scope),wf

    def _reference(self, rt, reference, scope,hash_cache=None):
        if not isinstance(reference,dict) or set(reference)!={'workflowId','attemptId'} or any(not isinstance(v,str) or not v for v in reference.values()):fail('真实来源须提供任务和采集编号')
        scope,wf=self._scope(rt,scope,reference)
        row=rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=?',(reference['attemptId'],reference['workflowId'])).fetchone()
        if not row:raise APIError('SCIENCE_REFERENCE_NOT_FOUND','未找到该任务的采集记录',404)
        attempt=json.loads(row[0]);raw=deepcopy(attempt.get('result') or {})
        node=next((n for n in wf['nodes'] if n['id']==attempt.get('nodeId')),None)
        if not node or not node.get('resourceId') or node['resourceId'] not in wf.get('resourceIds',[]):
            fail('采集节点缺少可核对的比特身份','SCIENCE_SCOPE_MISMATCH')
        if scope.get('resourceId',node['resourceId'])!=node['resourceId'] or attempt.get('procedureId')!=node.get('procedureId'):
            fail('所选比特或实验方法与采集节点不一致','SCIENCE_SCOPE_MISMATCH')
        scope['resourceId']=node['resourceId']
        if raw.get('dataKind')!='real' or not raw.get('acquiredAt') or wf.get('mode')!='real':fail('来源不是有采集时间的真实记录','SCIENCE_REFERENCE_NOT_REAL')
        evidence=[]
        # Selection and independently repeated files remain separate, all bound.
        sources=[raw]+([raw['selectionResult']] if isinstance(raw.get('selectionResult'),dict) else [])
        seen=set()
        for source in sources:
            for artifact in source.get('artifacts',[]):
                if artifact.get('kind')!='raw' or artifact.get('id') in seen:continue
                row=rt.db.execute('SELECT * FROM platform_artifacts WHERE id=?',(artifact.get('id'),)).fetchone()
                if not row or row['sha256']!=artifact.get('sha256'):fail('原始文件登记与采集记录不一致','SCIENCE_EVIDENCE_CHANGED')
                try:
                    path=checked_path(row['path'])
                    # Match Controller._artifact's existing archive allowlist;
                    # a DB row alone cannot authorize an arbitrary external path.
                    permitted=path.is_relative_to(self.c.root.resolve()) or str(path).replace('\\','/').lower().startswith('d:/总体-8号机测试/')
                    if not permitted:fail('原始文件不属于已登记实验目录','SCIENCE_EVIDENCE_CHANGED')
                    token=(str(path),row['sha256'])
                    actual=hash_cache[token] if hash_cache is not None and token in hash_cache else file_hash(path)
                except (OSError,APIError):fail('原始文件不可读取','SCIENCE_EVIDENCE_CHANGED')
                if actual!=row['sha256']:fail('原始文件摘要已改变','SCIENCE_EVIDENCE_CHANGED')
                if hash_cache is not None and len(hash_cache)<MAX_ARTIFACTS:hash_cache[token]=actual
                evidence.append({'id':artifact['id'],'sha256':actual,'kind':'raw'});seen.add(artifact['id'])
        if not evidence:fail('采集缺少已登记的原始文件','SCIENCE_EVIDENCE_MISSING')
        current=bool(node and node.get('latestAttemptId')==attempt['id'] and not raw.get('stale')
                     and not (node.get('result') or {}).get('stale') and node.get('engineState')!='stale'
                     and quality_policy_current(node['procedureId'],raw.get('quality',{}),wf.get('gates',{}),
                         wf.get('gatesVersion'),independent=bool(attempt.get('verificationOperationId'))))
        return dict(scope=scope,workflow=wf,attempt=attempt,raw=raw,evidence=evidence,current=current,
                    independentlyVerified=bool(attempt.get('state')=='verified' and raw.get('quality',{}).get('status')=='passed' and not raw.get('partial')))

    @staticmethod
    def _measurement(source):
        raw=source['raw'];traces=[];source_series=[]
        for index, original in enumerate(raw.get('series',[])):
            if not isinstance(original,dict) or (original.get('fit') and source['attempt'].get('procedureId')!='sq_drag_irb'):continue
            try:
                xs=vector(original.get('x'),'采集横轴',minimum=1,maximum=100000)
                ys=vector(original.get('y'),'采集纵轴',minimum=len(xs),maximum=len(xs),missing=True)
            except APIError:continue
            unit=original.get('yUnit','unknown')
            quantity=original.get('quantity') or ('excited_probability' if unit=='probability' else 'iq_amplitude' if 'IQ' in unit else 'measured_signal')
            trace=series(f'trace-{index}',original.get('name',f'曲线 {index+1}'),ys,quantity,unit)
            source_series.append({**trace,'x':xs,'xUnit':original.get('xUnit','unknown')})
            if not traces:traces=[trace];axis={'x':{'name':'Scan','unit':original.get('xUnit','unknown'),'values':xs}}
        out=dict(kind='measurement',modelVersion=None,dataKind='real',source='acquisition',dispatched=False,
                    axes=axis if traces else {'x':{'name':'Scan','unit':'unknown','values':[]}},series=traces,
                    sourceSeries=source_series,metrics=deepcopy(raw.get('metrics',{})),partial=bool(raw.get('partial')),
                    quality=deepcopy(raw.get('quality',{})),acquiredAt=raw['acquiredAt'],
                    parameters=deepcopy(raw.get('parameters',source['attempt'].get('parameters',{}))),
                    measurementContext={k:deepcopy(raw[k]) for k in ('channel','measurementPoint','acquisitionWindow','readoutCalibrationId') if k in raw},
                    procedureId=source['attempt'].get('procedureId'))
        if raw.get('targetPulseIdentity') is not None:out['targetPulseIdentity']=deepcopy(raw['targetPulseIdentity'])
        out.update(shelve_measurement_fields(raw,source['attempt']))
        provenance=raw.get('provenance',{})
        if source['attempt'].get('procedureId')=='sq_shape_rabi_amplitude_repeat5':
            out['shapeRabiCalibration']=deepcopy(provenance.get('shapeRabiCalibration'))
            out['compiledSequenceEvidence']=deepcopy(provenance.get('compiledSequenceEvidence'))
        if source['attempt'].get('procedureId') in ('ro_flux_scan','sq_flux_point_recheck'):
            flux=deepcopy(provenance.get('scienceFlux',{}));out['scienceFlux']=flux
            cuts=[point for point in flux.get('points',[]) if point.get('phase')=='cavity']
            if cuts and all(point.get('frequenciesHz')==cuts[0].get('frequenciesHz') for point in cuts):
                out['grid']={'x':deepcopy(cuts[0]['frequenciesHz']),'y':[p['requestedSettingV'] for p in cuts],
                    'values':[deepcopy(p['responseMagnitude']) for p in cuts],'xUnit':'Hz','yUnit':'V',
                    'unit':'ADC','quantity':'readout_iq_magnitude','biasMeaning':'commanded_setpoint' if flux.get('route',{}).get('schema')=='qcal-bias-command-route/1' else 'hardware_setting_register'}
        if source['attempt'].get('procedureId')=='sq_drag_half_scan':
            out['coefficientDiagnostic']=deepcopy(provenance.get('scienceHalfDrag',{}))
        if source['attempt'].get('procedureId')=='sq_drag_irb':
            analysis=deepcopy(provenance.get('scienceIRB',{}));proof=provenance.get('scienceDependencies',{})
            procedures={node.get('procedureId') for node in proof.get('nodes',[])}
            lineage=analysis.get('rabiLineage',{})
            out['gateCharacterization']={**analysis,'reportable':raw.get('quality',{}).get('status')=='passed' and analysis.get('irb',{}).get('reportable') is True,
                'sourceReferences':deepcopy(proof.get('rootReferences',[])),
                'dependencyClosureSha256':proof.get('closureSha256'),
                'rabiLineage':{'amplitude':bool(lineage.get('amplitudeRecordId')) and bool({'sq_shape_rabi_amplitude','sq_shape_rabi_amplitude_repeat5'} & procedures),
                    'time':bool(lineage.get('time')) and 'sq_rabi_time' in procedures,
                    'timeWindow':deepcopy(lineage.get('time')),'frozenLineage':deepcopy(lineage)},
                'scope':'同一 X/2 门的条件交错 RB；不包含系统模型误差或泄漏估计'}
        return out

    def _load(self,rt,record_id,*,check=True,hash_cache=None):
        if not isinstance(record_id,str):fail('记录编号无效')
        row=rt.db.execute('SELECT payload,payload_hash FROM platform_science_records WHERE id=?',(record_id,)).fetchone()
        if not row:raise APIError('SCIENCE_RECORD_NOT_FOUND','科学记录不存在',404)
        value=json.loads(row[0])
        if digest(value)!=row[1]:raise APIError('SCIENCE_RECORD_CHANGED','科学记录内容摘要不一致',409)
        if check and value.get('reference'):
            try:
                source=self._reference(rt,value['reference'],value['scope'],hash_cache)
                value['evidenceCurrent']=source['current'] and source['evidence']==value['evidence']
                value['evidenceIntact']=source['evidence']==value['evidence']
                value['validity']='current' if value['evidenceCurrent'] else 'historical'
            except APIError as exc:
                value.update(evidenceCurrent=False,evidenceIntact=False,validity='stale',validationError={'code':exc.code,'message':exc.message})
        if check and value.get('sourceRecordId'):
            source=self._load(rt,value['sourceRecordId'],hash_cache=hash_cache)
            if source.get('evidenceIntact') is False:value.update(evidenceCurrent=False,evidenceIntact=False,validity='stale')
            elif source.get('evidenceCurrent') is False:value.update(evidenceCurrent=False,evidenceIntact=source.get('evidenceIntact'),validity='historical')
        if check and value.get('kind')=='simulation' and value.get('input',{}).get('readoutCalibrationRecordId'):
            calibration=self._load(rt,value['input']['readoutCalibrationRecordId'],hash_cache=hash_cache)
            if calibration.get('evidenceIntact') is False:value.update(evidenceCurrent=False,evidenceIntact=False,validity='stale')
        if check and value.get('kind')==CHANNEL_COMPARISON_KIND:
            scope,_=self._scope(rt,value['scope'])
            if digest(import_channel_comparison(value['input'],scope))!=digest(value['result']):
                fail('外部通道比较结果与冻结原文件不一致','SCIENCE_RECORD_CHANGED')
        if check:
            # The frozen proof is part of the record receipt. Only the separate
            # live assessment is refreshed; reading never rewrites its digest.
            if value.get('dependencyProof'):
                checked=self.dependencies.validate(rt,value['dependencyProof'],hash_cache=hash_cache)
                self._dependency_assessment(value,checked['assessment'])
            else:
                annotated=any(key in value.get('input',{}) for key in LEGACY)
                value['dependencyAssessment']={'status':'legacy_unvalidated' if annotated else 'unbound',
                    'integrityIntact':None,'evidenceCurrent':None,'executionEligible':False,'missingRoles':[],
                    'issues':[{'code':'SCIENCE_LEGACY_UNVALIDATED','message':'历史来源编号未冻结核验；重新保存后才能作为依赖使用'}] if annotated else []}
            value['acceptedCalibration']=False;value['physicalAccepted']=False
        return value

    @staticmethod
    def _dependency_assessment(value,assessment):
        value['dependencyAssessment']=deepcopy(assessment)
        if assessment.get('evidenceCurrent') is not None:
            value['evidenceCurrent']=value.get('evidenceCurrent') is not False and assessment['evidenceCurrent']
            value['evidenceIntact']=value.get('evidenceIntact') is not False and assessment['integrityIntact']
            if not value['evidenceIntact']:value['validity']='stale'
            elif not value['evidenceCurrent']:value['validity']='historical'

    @staticmethod
    def _compatible_scope(a,b):
        # Same legacy workflow can be compared without pretending it had a chip version.
        for key in ('resourceId','chipVersionId','deploymentVersionId'):
            if a.get(key)!=b.get(key):fail('记录不属于同一比特及接线版本','SCIENCE_SCOPE_MISMATCH')
        if not a.get('chipVersionId') and a.get('workflowId')!=b.get('workflowId'):fail('未确认芯片身份的记录只可在同一任务内比较','SCIENCE_SCOPE_MISMATCH')

    @staticmethod
    def _trace(record,series_id=None):
        r=record['result'];candidates=r.get('sourceSeries') or r.get('series',[])
        s=next((s for s in candidates if s.get('id')==series_id),None) if series_id else next(iter(candidates),None)
        if not s:fail('记录没有选中的数值曲线','SCIENCE_SERIES_MISSING')
        axis=r['axes']['x']
        return s, s.get('x',axis['values']),s.get('xUnit',axis['unit'])

    def _readout_context(self,rt,record,trace):
        if record.get('dataKind')!='real' or not record.get('reference'):
            fail('读出标定须绑定真实采集记录','SCIENCE_CALIBRATION_CONTEXT_MISSING')
        source=self._reference(rt,record['reference'],record['scope'])
        raw=source['raw'];parameters=record['result'].get('parameters',{})
        if any(not number(parameters.get(k)) for k in READOUT_KEYS) or trace['unit'] not in ('IQ a.u.','V','mV','ADC'):
            fail('采集缺少读出条件或明确响应单位','SCIENCE_CALIBRATION_CONTEXT_MISSING')
        profile=raw.get('provenance',{}).get('profileHash') or source['workflow'].get('adapterProfileHash')
        if not isinstance(profile,str) or not re.fullmatch('[a-fA-F0-9]{64}',profile):
            fail('采集没有可核对的设备配置摘要','SCIENCE_CALIBRATION_CONTEXT_MISSING')
        # The historical F8 analysis names its actual projection and zero. Never
        # infer these from curve extrema or from a different readout acquisition.
        candidates=[a for a in raw.get('artifacts',[]) if a.get('name')=='platform_analysis.json']
        if len(candidates)!=1:fail('原始分析未记录唯一投影来源','SCIENCE_CALIBRATION_CONTEXT_MISSING')
        artifact=candidates[0]
        row=rt.db.execute('SELECT * FROM platform_artifacts WHERE id=?',(artifact.get('id'),)).fetchone()
        if not row or row['sha256']!=artifact.get('sha256'):fail('投影来源登记不一致','SCIENCE_EVIDENCE_CHANGED')
        path=checked_path(row['path'])
        permitted=path.is_relative_to(self.c.root.resolve()) or str(path).replace('\\','/').lower().startswith('d:/总体-8号机测试/')
        if not permitted or file_hash(path)!=row['sha256']:fail('投影来源文件已改变','SCIENCE_EVIDENCE_CHANGED')
        try:analysis=json.loads(path.read_text(encoding='utf-8-sig')).get('analysis',{})
        except (OSError,ValueError):fail('投影来源文件不可读取','SCIENCE_EVIDENCE_CHANGED')
        if analysis.get('procedureId')!=source['attempt']['procedureId'] or analysis.get('raw_sha256') not in {a.get('sha256') for a in raw.get('artifacts',[]) if a.get('kind')=='raw'}:
            fail('投影分析与采集方法不一致','SCIENCE_CALIBRATION_CONTEXT_MISSING')
        projection=projection_context(analysis.get('projection'))
        return {'sourceRecordId':record['id'],'sourceSeriesId':trace['id'],'scope':deepcopy(record['scope']),
                'adapterProfileHash':profile,'readoutParameters':{k:parameters[k] for k in READOUT_KEYS},
                'quantity':trace['quantity'],'unit':trace['unit'],'projection':projection,
                'projectionArtifact':{'id':artifact['id'],'sha256':artifact['sha256']},
                'acquiredAt':record['result']['acquiredAt']}

    def _simulation(self,rt,inputs,scope):
        source=self._load(rt,inputs.get('sourceRecordId'));self._compatible_scope(scope or source['scope'],source['scope'])
        if source.get('evidenceIntact') is False:fail('原记录证据已失效','SCIENCE_EVIDENCE_CHANGED')
        trace,xs,unit=self._trace(source,inputs.get('sourceSeriesId'))
        factors={'ns':1.,'us':1000.,'µs':1000.,'μs':1000.,'s':1e9}
        if unit not in factors:fail('该曲线不是时间轴，无法使用时域模型','SCIENCE_AXIS_INCOMPATIBLE')
        times=vector([v*factors[unit] for v in xs],'原始时间点',minimum=2,increasing=True)
        if times[0]<0:fail('时间点不可为负')
        model=inputs.get('modelParameters',{})
        if not isinstance(model,dict):fail('模型参数须为对象')
        parameters=deepcopy(model);parameters['timesNs']=times;parameters['durationNs']=max(times[-1],.001)
        binding=None
        pulse_convention_known=True
        if source['dataKind']=='real':
            procedure=source['result'].get('procedureId')
            sequence={'sq_rabi_time':'x','sq_t1':'t1','sq_ramsey':'ramsey','sq_echo':'echo','sq_cpmg':'cpmg'}.get(procedure)
            if not sequence:fail('该采集方法没有已绑定的时域模型；仅可独立查看','SCIENCE_CONDITIONS_INCOMPATIBLE')
            frozen=source['result'].get('parameters',{})
            locked={'sequence':sequence,'initialState':'0' if sequence=='x' else '1' if sequence=='t1' else '+x'}
            if sequence=='cpmg':
                if not number(frozen.get('pi_pulses')):fail('原 CPMG 未记录 π 脉冲数，不能声称同配置仿真','SCIENCE_CONDITIONS_INCOMPATIBLE')
                locked['piPulses']=frozen['pi_pulses']
            if sequence in ('ramsey','echo','cpmg'):
                pulse_convention_known=frozen.get('pulse_compatibility')=='f8_validated_short_half_dc_buff2ns_v1'
                if pulse_convention_known:
                    # platform_specs uses RX(pi/2) preparation and analysis.
                    # Rotate native -y preparation to model +x. Native X is
                    # then model Y, while native Y is model -X (same pi axis).
                    # The final RX(pi/2) projects model -x for every sequence.
                    # Virtual RZ is applied at analysis, never refocused.
                    locked.update(readoutSign=-1,analysisPhaseDeg=0,refocusAxis='y' if sequence=='echo' else 'x')
                    if sequence in ('ramsey','echo'):
                        if not number(frozen.get('rotation_hz')):fail('原实验缺少末端分析相位扫描参数','SCIENCE_CONDITIONS_INCOMPATIBLE')
                        locked['analysisPhaseHz']=-frozen['rotation_hz']
                    else:locked['analysisPhaseHz']=0
            for key,value in locked.items():
                if key in parameters and parameters[key]!=value:fail('模型实验、初态或脉冲数与原采集不一致','SCIENCE_CONDITIONS_INCOMPATIBLE')
                parameters[key]=value
            binding={'sourceRecordId':source['id'],'sourceSeriesId':trace['id'],'reference':deepcopy(source.get('reference')),
                     'scope':deepcopy(source['scope']),'procedureId':procedure,'parameters':deepcopy(frozen),
                     'parameterVersions':deepcopy(source.get('provenance',{}).get('parameterVersions',{}))}
        out=bloch(parameters)
        if parameters.get('sequence') in ('echo','cpmg'):
            out['series'][0]['values']=bloch_scan_endpoints(parameters,times)
        out.update(kind='simulation',sourceRecordId=source['id'],sourceSeriesId=trace['id'],
                   axes={'x':{'name':source['result']['axes']['x']['name'],'unit':unit,'values':xs}},
                   modelParameters=parameters,comparisonCompatible=trace['quantity']=='excited_probability' and trace['unit']=='probability' and pulse_convention_known)
        if not pulse_convention_known:out['comparisonUnavailableReason']='采集未记录已核对的制备与分析脉冲相位约定；模型仅供独立查看'
        if binding:out.update(experimentBinding=binding,experimentBindingSha256=digest(binding))
        if inputs.get('readoutCalibrationRecordId'):
            calibration=self._load(rt,inputs['readoutCalibrationRecordId'])
            self._compatible_scope(source['scope'],calibration['scope'])
            if calibration.get('kind')!='readout_calibration' or calibration.get('evidenceIntact') is False or not pulse_convention_known:
                fail('标定来源或脉冲约定不可用于比较','SCIENCE_CALIBRATION_INVALID')
            context=self._readout_context(rt,source,trace)
            values=forward_observation(out['series'][0]['values'],calibration,context,source['result']['acquiredAt'])
            out['series'].insert(0,series('observation','按外部标定预测的读出响应',values,trace['quantity'],trace['unit']))
            out.update(comparisonCompatible=True,observationCalibration={'recordId':calibration['id'],
                'documentSha256':calibration['result']['documentSha256'],'contextSha256':digest(context),
                'qualification':'external_claim_not_platform_verified',
                'meaning':'以用户提供的外部标定有效为条件；不代表平台验收或门保真度'})
        if parameters.get('sequence') in ('echo','cpmg'):
            out['trajectoryMeaning']='trajectory is the longest-delay experiment; each series sample is a separately timed endpoint acquisition'
        return out,source

    def _point(self,inputs,source,source_record=None,*,rt=None):
        if inputs.get('purpose','work') not in ('work','idle'):fail('点位类型须为工作点或闲置点')
        point={k:deepcopy(v) for k,v in inputs.items() if k in ('purpose','fluxChannel','bias','biasUnit','sourceRecordId')}
        if 'bias' in point and not number(point['bias']):fail('偏置须为有限数值')
        if 'biasUnit' in point and point['biasUnit'] not in ('V','A','Phi0'):fail('偏置单位无效')
        if source_record and source_record.get('result',{}).get('procedureId')=='ro_flux_scan':
            from .flux_points import source_point
            frozen=source_point(self.c,rt,source_record,inputs)
            line=source_record.get('result',{}).get('scienceFlux',{}).get('route',{}).get('lineId')
            if 'fluxChannel' in inputs and inputs['fluxChannel']!=line:fail('点位通道必须来自原始偏置线路','SCIENCE_POINT_SOURCE_MISMATCH')
            point.update(bias=frozen['requestedSettingV'],biasUnit='V',readoutFrequencyHz=frozen['originalCut']['readoutFrequencyHz'],
                measuredOutputV=frozen['measuredOutputV'],measurementUncertaintyV=frozen['measurementUncertaintyV'],
                pointId=frozen['pointId'],routeSha256=frozen['routeSha256'],sourceRecordId=source_record['id'],fluxChannel=line)
            if frozen['schema']=='qcal-flux-point-evidence/2':point.update(biasMeaning='commanded_setpoint',readbackStatus='not_measured')
            return {'kind':'working_point','status':'requires_verification','acceptedCalibration':False,
                'source':'commanded_point_derived' if frozen['schema']=='qcal-flux-point-evidence/2' else 'measured_point_derived','dataKind':'derived',
                'requiresIndependentVerification':True,'dispatched':False,'point':point,'fluxPointEvidence':frozen,
                'series':[],'axes':{'x':{'name':'Frequency','unit':'Hz','values':[]}},'metrics':point}
        if source:
            parameters=source['raw'].get('parameters',source['attempt'].get('parameters',{}))
            candidates=source['raw'].get('candidateParameters',{})
            for field,key in [('readoutFrequencyHz','readout_hz'),('driveFrequencyHz','drive_hz')]:
                actual=candidates.get(key,parameters.get(key))
                if field in inputs and (not number(inputs[field]) or not number(actual) or abs(inputs[field]-actual)>.001):fail('点位频率必须来自已记录采集参数','SCIENCE_POINT_SOURCE_MISMATCH')
                if number(actual):point[field]=actual
            if 'bias' in point:
                actual=parameters.get('flux_bias',parameters.get('bias'))
                if not number(actual) or abs(actual-point['bias'])>1e-12:fail('该采集没有相同偏置的记录','SCIENCE_POINT_SOURCE_MISMATCH')
        elif source_record and source_record['kind']=='flux':
            grid=source_record['result'];point.setdefault('bias',grid['selectedBias']);point.setdefault('biasUnit',grid['grid']['yUnit'])
            if point['bias'] not in grid['grid']['y'] or point['biasUnit']!=grid['grid']['yUnit']:fail('点位须来自所选原始偏置轴')
            i=grid['grid']['y'].index(point['bias']);row=grid['grid']['values'][i];valid=[(v,j) for j,v in enumerate(row) if v is not None]
            if not valid:fail('所选偏置没有有效原始谱线')
            actual=grid['grid']['x'][min(valid)[1]]
            if 'readoutFrequencyHz' in inputs:
                if inputs['readoutFrequencyHz'] not in grid['grid']['x']:fail('点位频率必须是原始截线的采样点')
                selected=grid['grid']['x'].index(inputs['readoutFrequencyHz'])
                if row[selected] is None:fail('所选频率缺少原始样点')
                actual=inputs['readoutFrequencyHz']
            point['readoutFrequencyHz']=actual
        else:fail('保存点位需要采集记录或二维谱记录','SCIENCE_POINT_SOURCE_REQUIRED')
        return {'kind':'working_point','status':'requires_verification','acceptedCalibration':False,
                'source':'measured_point_derived' if source else 'provided_grid_derived','dataKind':'derived' if source else 'provided_data',
                'requiresIndependentVerification':True,'dispatched':False,'point':point,'series':[],
                'axes':{'x':{'name':'Frequency','unit':'Hz','values':[]}},'metrics':point}

    @staticmethod
    def _summary(value):
        keys=('id','kind','label','revision','createdAt','updatedAt','scope','dataKind','source','reference',
              'sourceRecordId','validity','evidenceCurrent','evidenceIntact','validationError')
        result={key:deepcopy(value[key]) for key in keys if key in value}
        result.update(summary=True,input={},acceptedCalibration=False,physicalAccepted=False)
        result['result']={key:deepcopy(value.get('result',{})[key]) for key in
            ('kind','modelVersion','dataKind','source','procedureId','methodLabel','parameters','quality') if key in value.get('result',{})}
        result['provenance']={key:deepcopy(value.get('provenance',{})[key]) for key in
            ('procedureId','independentlyVerified','workflowId','attemptId') if key in value.get('provenance',{})}
        result['dependencyAssessment']={key:deepcopy(value.get('dependencyAssessment',{})[key]) for key in
            ('status','integrityIntact','evidenceCurrent','executionEligible','missingRoles','issues') if key in value.get('dependencyAssessment',{})}
        result['evidenceCount']=len(value.get('evidence',[]))
        result['dependencyNodeCount']=len(value.get('dependencyProof',{}).get('nodes',[]))
        if value.get('kind') in ('readout_calibration',CHANNEL_COMPARISON_KIND):
            result['input']={k:deepcopy(value.get('input',{}).get(k)) for k in ('sourceRecordId','sourceSeriesId')}
            result['result'].update({k:deepcopy(value.get('result',{}).get(k)) for k in ('fileName','documentSha256','qualification')})
            if value.get('kind')==CHANNEL_COMPARISON_KIND:
                result['result'].update({k:deepcopy(value['result'][k]) for k in ('dataOrigin','context','calibrationId','calibrationVersion')})
        return result

    @staticmethod
    def _response_size(value,budget):
        for _ in range(4):
            size=len(dumps(value).encode('utf8'))
            if value.get('responseBytes')==size:break
            value['responseBytes']=size
        if len(dumps(value).encode('utf8'))>budget:
            raise APIError('SCIENCE_RESPONSE_LIMIT','科学记录响应超过容量上限，请使用摘要列表或单条详情；未截断证据',413)
        return value

    def get(self,path,query):
        endpoint=self._path(path)
        with self.store.connect() as rt:
            if endpoint in ('attempt-signals','attempt-signal-pulse','attempt-signal-adc'):
                from .science_attempt_signal import read
                return self._response_size(read(self,rt,endpoint,query or {}),MAX_DETAIL_BYTES)
            pulse_match=re.fullmatch(r'records/([^/]+)/pulse-samples',endpoint)
            if pulse_match:
                from .science_pulse_samples import read
                return self._response_size(read(self,rt,self._load(rt,pulse_match[1],check=False),query),MAX_LIST_BYTES)
            if endpoint.startswith('records/'):
                return self._response_size(self._load(rt,endpoint[8:],hash_cache={}),MAX_DETAIL_BYTES)
            if endpoint not in ('records','overview'):raise APIError('NOT_FOUND','科学接口不存在',404)
            def q(key):
                value=(query or {}).get(key);return value[0] if isinstance(value,list) and value else value
            clauses=[];params=[]
            for key,column in [('kind','kind'),('workflowId','workflow_id')]:
                if q(key):clauses.append(column+'=?');params.append(q(key))
            cursor=q('cursor')
            if cursor is not None:
                if not isinstance(cursor,str) or not re.fullmatch(r'[1-9]\d{0,18}',cursor):fail('科学记录分页游标无效')
                clauses.append('rowid<?');params.append(int(cursor))
            summary=endpoint=='overview' or q('summary') in ('true','1',True)
            limit=20 if endpoint=='overview' else 100
            sql='SELECT id,rowid FROM platform_science_records'+(' WHERE '+' AND '.join(clauses) if clauses else '')+f' ORDER BY rowid DESC LIMIT {limit+1}'
            rows=rt.db.execute(sql,params).fetchall();records=[];used=0;last=None;has_more=False;hash_cache={}
            for row in rows:
                if len(records)>=limit:has_more=True;break
                record=self._load(rt,row[0],hash_cache=hash_cache)
                if summary:record=self._summary(record)
                size=len(dumps(record).encode('utf8'))
                if used+size>MAX_LIST_BYTES-(2048 if endpoint=='overview' else 512):
                    if not records:raise APIError('SCIENCE_RESPONSE_LIMIT','单条记录超过列表容量，请使用 summary=true 或单条详情；未截断证据',413)
                    has_more=True;break
                records.append(record);used+=size;last=str(row[1])
            page={'records':records,'limit':limit,'summary':summary,'hasMore':has_more,'nextCursor':last if has_more else None}
            if endpoint=='records':return self._response_size(page,MAX_LIST_BYTES)
            count=rt.db.execute('SELECT COUNT(*) FROM platform_science_records').fetchone()[0]
            points=rt.db.execute("SELECT COUNT(*) FROM platform_science_records WHERE kind='working_point'").fetchone()[0]
            return self._response_size({**page,'modelVersion':MODEL_VERSION,'capabilities':[{'kind':k,'label':LABELS[k],'preview':k not in ('measurement','working_point','readout_calibration',CHANNEL_COMPARISON_KIND),
                     'acquisition':'controller_only' if k=='measurement' else 'not_connected'} for k in KINDS],
                    'temperature':{'status':'not_connected','value':None,'unit':'K','source':None},
                    'counts':{'records':count,'workingPoints':points}},MAX_LIST_BYTES)

    def write(self,path,body,method):
        endpoint=self._path(path)
        if method!='POST' or not isinstance(body,dict):fail('该接口仅支持 POST 参数对象')
        if endpoint=='preview':
            if set(body)-{'kind','input','scope'}:fail('预览请求包含未知字段')
            if body.get('kind')=='simulation':
                with self.store.connect() as rt:
                    scope,_=self._scope(rt,body.get('scope',{}));return self._simulation(rt,body.get('input',{}),scope)[0]
            return preview(body.get('kind'),body.get('input',{}))
        if endpoint=='compare':return self._compare(body)
        if endpoint in ('channel-comparison-template','channel-comparison-preview'):
            expected={'scope'} if endpoint.endswith('template') else {'scope','input'}
            if set(body)-expected:fail('通道比较请求包含未知字段')
            with self.store.connect() as rt:
                scope,_=self._scope(rt,body.get('scope',{}))
                if endpoint.endswith('template'):return channel_template(scope)
                output,_,_=self._channel_import(rt,body.get('input',{}),scope)
                return output
        if endpoint=='readout-calibration-template':
            with self.store.connect() as rt:
                source=self._load(rt,body.get('sourceRecordId'))
                trace,_,_=self._trace(source,body.get('sourceSeriesId'))
                return calibration_template(self._readout_context(rt,source,trace))
        if endpoint!='records':raise APIError('NOT_FOUND','科学接口不存在',404)
        if set(body)-{'kind','input','scope','reference','operationId','label','expectedRevision','dependencies'}:fail('数据来源、证据与验收状态由服务端生成')
        operation=body.get('operationId')
        if not isinstance(operation,str) or not 1<=len(operation)<=256:fail('保存需要唯一操作编号','OPERATION_ID_REQUIRED')
        try:fingerprint=digest(body)
        except (TypeError,ValueError):fail('参数包含不可保存的数值')
        with self.store.transaction() as rt:
            previous=rt.db.execute('SELECT request_hash,response FROM platform_science_operations WHERE id=?',(operation,)).fetchone()
            if previous:
                if previous[0]!=fingerprint:raise APIError('OPERATION_CONFLICT','操作编号已用于其他请求',409)
                return json.loads(previous[1])
            kind=body.get('kind');inputs=body.get('input',{});ref=body.get('reference')
            if kind not in KINDS or not isinstance(inputs,dict):fail('记录类型或输入不正确')
            scope,wf=self._scope(rt,body.get('scope',{}),ref)
            if 'expectedRevision' in body and (not wf or body['expectedRevision']!=wf['revision']):raise APIError('REVISION_CONFLICT','任务已更新，请重新读取',409)
            source=self._reference(rt,ref,scope) if ref else None;parent=None
            if source:scope=source['scope']
            if source and kind not in ('measurement','working_point'):fail('分析输入不能借用采集来源冒充真实数据')
            if kind=='measurement':
                if not source:fail('采集记录必须引用控制器已保存的尝试')
                output=self._measurement(source)
            elif kind=='working_point':
                if inputs.get('sourceRecordId'):
                    parent=self._load(rt,inputs['sourceRecordId']);self._compatible_scope(scope or parent['scope'],parent['scope'])
                    scope=scope or parent['scope']
                output=self._point(inputs,source,parent,rt=rt)
            elif kind=='simulation':
                output,parent=self._simulation(rt,inputs,scope);scope=scope or parent['scope']
            elif kind=='readout_calibration':
                parent=self._load(rt,inputs.get('sourceRecordId'));self._compatible_scope(scope or parent['scope'],parent['scope'])
                scope=scope or parent['scope'];trace,_,_=self._trace(parent,inputs.get('sourceSeriesId'))
                context=self._readout_context(rt,parent,trace)
                output=import_calibration(inputs,context,parent['result']['acquiredAt'])
            elif kind==CHANNEL_COMPARISON_KIND:
                output,parent,scope=self._channel_import(rt,inputs,scope)
            else:output=preview(kind,inputs)
            native_proofs=[]
            if source:
                raw=source['raw']
                for original in [raw]+([raw['selectionResult']] if isinstance(raw.get('selectionResult'),dict) else []):
                    for proof in (original.get('scienceDependencies'),original.get('provenance',{}).get('scienceDependencies')):
                        if proof:native_proofs.append(proof)
            dependency_proof=self.dependencies.resolve(rt,kind=kind,scope=scope,refs=body.get('dependencies',[]),inputs=inputs,
                                                       frozen_proofs=native_proofs)
            scope=dependency_proof['scope']
            data_kind='real' if source else output.get('dataKind','provided_data')
            value={'id':ident('science'),'kind':kind,'label':str(body.get('label') or LABELS[kind])[:500],
                   'revision':1,'createdAt':now(),'scope':scope,'input':deepcopy(inputs),'modelVersion':MODEL_VERSION,
                   'inputSha256':digest(inputs),'dataKind':data_kind,'source':'acquisition' if source else output.get('source','provided_record'),
                   'result':output,'acceptedCalibration':False,'physicalAccepted':False,'reference':deepcopy(ref),
                   'dependencyProof':dependency_proof,
                   'evidence':source['evidence'] if source else [],'evidenceCurrent':source['current'] if source else None,
                   'evidenceIntact':True if source else None,'validity':'current' if source else 'preview',
                   'provenance':{'workflowRevision':source['workflow']['revision'],'procedureId':source['attempt'].get('procedureId'),
                                 'adapterId':source['workflow'].get('adapterId'),'adapterProfileHash':source['workflow'].get('adapterProfileHash'),
                                 'parameterVersions':source['attempt'].get('parameterVersions',{}),
                                 'independentlyVerified':source['independentlyVerified']} if source else {'modelVersion':MODEL_VERSION},
                   'sourceRecordId':parent['id'] if parent else None}
            if parent:
                value.update(evidenceCurrent=parent.get('evidenceCurrent'),evidenceIntact=parent.get('evidenceIntact'))
                if parent.get('evidenceCurrent') is False:value['validity']='historical' if parent.get('evidenceIntact') else 'stale'
            if source and not source['current']:value['validity']='historical'
            self._dependency_assessment(value,dependency_proof['assessment'])
            rt.db.execute('INSERT INTO platform_science_records VALUES(?,?,?,?,?,?)',
                          (value['id'],kind,scope.get('workflowId'),1,dumps(value),digest(value)))
            rt.db.execute('INSERT INTO platform_science_operations VALUES(?,?,?)',(operation,fingerprint,dumps(value)))
            return value

    def _channel_import(self,rt,inputs,scope):
        parent=None
        if isinstance(inputs,dict) and inputs.get('sourceRecordId'):
            parent=self._load(rt,inputs['sourceRecordId'])
            if parent.get('kind')!='measurement' or parent.get('dataKind')!='real' or not parent.get('reference'):
                fail('可选锚点须为平台已登记采集记录；模型不能冒充来源','SCIENCE_REFERENCE_NOT_REAL')
            if parent.get('evidenceIntact') is False:fail('锚点原始证据已改变','SCIENCE_EVIDENCE_CHANGED')
            self._compatible_scope(scope or parent['scope'],parent['scope']);scope=scope or parent['scope']
        return import_channel_comparison(inputs,scope),parent,scope

    def _compare(self,body):
        with self.store.connect() as rt:
            left=self._load(rt,body.get('leftRecordId'));right=self._load(rt,body.get('rightRecordId'))
            self._compatible_scope(left['scope'],right['scope'])
            if left.get('evidenceIntact') is False or right.get('evidenceIntact') is False:fail('原始证据已改变，不能继续比较','SCIENCE_EVIDENCE_CHANGED')
            a,ax,au=self._trace(left,body.get('leftSeriesId'));b,bx,bu=self._trace(right,body.get('rightSeriesId'))
            if any(not value or value=='unknown' for value in (a['quantity'],b['quantity'],a['unit'],b['unit'],au,bu)):
                fail('观测量或单位未记录，不能计算差值','SCIENCE_QUANTITY_INCOMPATIBLE')
            if a['quantity']!=b['quantity'] or a['unit']!=b['unit'] or au!=bu:fail('物理量或单位不同，仅可并排查看','SCIENCE_QUANTITY_INCOMPATIBLE')
            vector(ax,'左横轴',minimum=2,maximum=100000,increasing=True);vector(bx,'右横轴',minimum=2,maximum=100000,increasing=True)
            if left['dataKind']=='real' and right['dataKind']=='real':
                pa=left['result'].get('parameters',{});pb=right['result'].get('parameters',{})
                condition_keys=('readout_hz','readout_amp','readout_width_s','xy_delay_s')
                if any(k not in pa or k not in pb or pa[k]!=pb[k] for k in condition_keys):
                    fail('缺少相同读出频率、幅度、窗口或时序的记录，仅可并排查看','SCIENCE_CONDITIONS_INCOMPATIBLE')
                # Sampling density, repetitions and random order may differ;
                # all remaining pulse, bias, channel and demodulation settings
                # must match, including fields only one acquisition recorded.
                scan_keys={'points','start_s','stop_s','widths','amp_start','amp_stop','amplitudes',
                           'start_hz','stop_hz','frequencies_hz','span_hz','shots','capture_repeats',
                           'acquisitions_per_point','order_seed'}
                if ({k:v for k,v in pa.items() if k not in scan_keys}!={k:v for k,v in pb.items() if k not in scan_keys}
                        or left['result'].get('measurementContext',{})!=right['result'].get('measurementContext',{})):
                    fail('驱动、偏置、通道、测点或读出条件不同，仅可并排查看','SCIENCE_CONDITIONS_INCOMPATIBLE')
                if left.get('provenance',{}).get('procedureId')!=right.get('provenance',{}).get('procedureId'):
                    fail('测量方法不同，仅可并排查看','SCIENCE_CONDITIONS_INCOMPATIBLE')
                if a['unit']=='IQ a.u.' or a['quantity'] in ('iq_amplitude','iq_projection'):
                    ca=self._readout_context(rt,left,a);cb=self._readout_context(rt,right,b)
                    if any(ca[k]!=cb[k] for k in ('projection','adapterProfileHash','quantity','unit')):
                        fail('两次 I/Q 投影方向、偏移或设备配置不同，仅可并排查看','SCIENCE_CONDITIONS_INCOMPATIBLE')
            elif (left['dataKind']=='real') != (right['dataKind']=='real'):
                measured,model=(left,right) if left['dataKind']=='real' else (right,left)
                if model.get('sourceRecordId')!=measured['id']:fail('模型需冻结这条实测记录的配置和横轴','SCIENCE_CONDITIONS_INCOMPATIBLE')
                if model['result'].get('comparisonCompatible') is not True:
                    fail(model['result'].get('comparisonUnavailableReason','模型缺少可核对的同条件观测量'),'SCIENCE_CONDITIONS_INCOMPATIBLE')
                measured_trace=a if left['dataKind']=='real' else b
                expected={'sourceRecordId':measured['id'],'sourceSeriesId':measured_trace['id'],
                          'reference':measured.get('reference'),'scope':measured['scope'],
                          'procedureId':measured['result'].get('procedureId'),'parameters':measured['result'].get('parameters',{}),
                          'parameterVersions':measured.get('provenance',{}).get('parameterVersions',{})}
                if model['result'].get('experimentBinding')!=expected or model['result'].get('experimentBindingSha256')!=digest(expected):
                    fail('模型未绑定所选实验、脉冲配置及原始曲线，请按该记录重新仿真','SCIENCE_CONDITIONS_INCOMPATIBLE')
                if model['result'].get('observationCalibration'):
                    calibration=self._load(rt,model['result']['observationCalibration']['recordId'])
                    context=self._readout_context(rt,measured,measured_trace)
                    probabilities=next(s for s in model['result']['series'] if s['id']=='p1')['values']
                    prediction=forward_observation(probabilities,calibration,context,measured['result']['acquiredAt'])
                    model_trace=b if left['dataKind']=='real' else a
                    if model_trace['id']!='observation' or model_trace['values']!=prediction:
                        fail('读出模型曲线与标定来源不一致','SCIENCE_CALIBRATION_CHANGED')
            xs=sorted(set(ax+bx));ma=dict(zip(ax,a['values']));mb=dict(zip(bx,b['values']))
            excluded=excluded_points(body.get('excludedLeftPoints'),ax)
            original_left=deepcopy(ma)
            for x in excluded:ma[x]=None
            av=[ma.get(x) for x in xs];bv=[mb.get(x) for x in xs]
            differences=[u-v if u is not None and v is not None else None for u,v in zip(av,bv)]
            valid=[x for x in differences if x is not None]
            if not valid and not any(original_left.get(x) is not None and mb.get(x) is not None for x in xs):
                fail('记录没有可比的重叠采样点','SCIENCE_NO_OVERLAP')
            return {'kind':'comparison','source':'derived','dataKind':'analysis','dispatched':False,
                    'modelVersion':MODEL_VERSION,'axes':{'x':{'name':left['result']['axes']['x']['name'],'unit':au,'values':xs}},
                    'series':[series('left',left['label'],av,a['quantity'],a['unit']),
                              series('right',right['label'],bv,b['quantity'],b['unit']),
                              series('difference','逐点差值',differences,a['quantity'],a['unit'])],
                    'metrics':{'comparedPoints':len(valid),'missingPoints':len(differences)-len(valid),'rmse':math.sqrt(sum(v*v for v in valid)/len(valid)) if valid else None},
                    'sources':[left['id'],right['id']],
                    'observationCalibration':deepcopy(left['result'].get('observationCalibration') or right['result'].get('observationCalibration')),
                    'view':{'excludedLeftPoints':sorted(excluded),'originalLeftValues':[original_left.get(x) for x in xs],
                            'sourceRecordsUnchanged':True},
                    'interpolation':'none; union of exact sample points; missing points preserved'}
