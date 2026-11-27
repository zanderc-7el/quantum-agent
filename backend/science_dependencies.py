"""Server-resolved scientific dependencies; no acquisition or calibration writes.

Immutable content and live qualification are deliberately separate. Model and
provided-data records can be saved unbound. An explicit reference must resolve
to the declared resource/method. Execution additionally requires the physical
adapter's authenticated native receipts; controller quality flags alone do not
constitute physical attestation.
"""
from copy import deepcopy
import json

from .common import APIError, checked_path, digest, file_hash, number
from .quality import criteria_for, SCIENCE_GATES, SCIENCE_IRB_GATES, SCIENCE_FLUX_GATES

SCHEMA='qcal-science-dependencies/1'
POLICY='science-dependency-policy/1'
MAX_ROOTS,MAX_NODES,MAX_DEPTH=16,128,16
# Native maximum: 808 rows times (16 ADC blocks + three row files), plus nine.
# These finite bounds preserve all files for default multi-stage calibrations.
MAX_OPERATION_ARTIFACTS=16_384
MAX_ARTIFACTS=65_536
MAX_PROOF_BYTES=32*1024*1024
MAX_REQUEST_BYTES=40*1024*1024
KINDS={'pulse','drag','predistortion','bloch','simulation','shelve','flux','working_point','measurement','readout_calibration','predistortion_comparison'}
ROLES={
    'cavity_seed':{'ro_resonator_survey','ro_power_shift'},'flux_scan':{'ro_flux_scan'},
    'f01':{'sq_spectroscopy'}, 'readout':{'ro_iq01','ro_optimize'},
    'rabi_amplitude':{'sq_rabi_amplitude','sq_shape_rabi_amplitude','sq_shape_rabi_amplitude_repeat5'},
    'rabi_time':{'sq_rabi_time'},
    'pi01':{'sq_rabi_amplitude','sq_rabi_time','sq_shape_rabi_amplitude','sq_shape_rabi_amplitude_repeat5'},
    'half01':{'sq_shape_half_verify'},
    'coefficient_scan':{'sq_drag_scan','sq_drag_half_scan'}, 'candidate_verification':{'sq_drag_scan','sq_drag_verify','sq_drag_half_scan'},
    'drag':{'sq_drag_half_scan'},
    'gate_characterization':{'sq_rb','sq_drag_irb'},
    'anharmonicity':{'sq_shelve_spectrum12'},
    'spectrum12':{'sq_shelve_spectrum12','sq_shelve_frequency_verify'},
    'pi12':{'sq_shelve_rabi12','sq_shelve_pi12_verify'},
    'readout_comparison':{'sq_shelve_readout_compare'},
    'transfer_calibration':{'channel_impulse_response','channel_transfer_response'},
    'baseline_response':{'channel_response'}, 'compensated_response':{'channel_response'},
}
LEGACY={
    'rabiSourceRecordId':'rabi_auto','coefficientScanRecordId':'coefficient_scan',
    'independentRBRecordId':'gate_characterization','transferCalibrationRecordId':'transfer_calibration',
    'spectroscopyRecordId':'spectrum12','pi12CalibrationRecordId':'pi12',
    'sourceRecordId':'source_record',
}
REQUIRED={
    'drag':{'rabi_amplitude','rabi_time'},'shelve':{'f01','spectrum12','pi12'},
    'predistortion':{'transfer_calibration'},'simulation':{'source_record'},'working_point':{'source_record'},
}
EXECUTION_ROLES={
    'ro_flux_scan':{'cavity_seed'},'sq_flux_point_recheck':{'cavity_seed','flux_scan'},
    'sq_shape_rabi_amplitude':{'f01','readout'},'sq_shape_rabi_amplitude_repeat5':{'f01','readout'},
    'sq_shape_half_verify':{'pi01','readout'},
    'sq_drag_scan':{'pi01','half01','readout','anharmonicity'},
    'sq_drag_half_scan':{'pi01','half01','rabi_amplitude','rabi_time','readout','anharmonicity'},
    'sq_drag_irb':{'pi01','half01','drag','readout','anharmonicity'},
    'sq_shelve_spectrum12':{'pi01','f01','readout'},
    'sq_shelve_rabi12':{'pi01','spectrum12','readout'},
    'sq_shelve_readout_compare':{'pi01','spectrum12','pi12','readout'},
}
BINDING_KEYS=('schema','policyVersion','kind','scope','inputs','rootReferences','nodes','edges')
CONTENT_KEYS=('dataKind','acquiredAt','sourceRunId','parameters','metrics','candidateParameters',
              'series','analysisQuality','provenance','pulseIdentities','targetPulseIdentity','comparison','classifierProtocol')


def execution_roles(procedure, parameters=None):
    """Resolve the frozen experiment mode, without weakening other methods."""
    if procedure in ('sq_drag_half_scan','sq_drag_irb'):
        from adapters.science_v3_irb.beta import required_roles
        try:return set(required_roles(procedure, parameters or {}))
        except (TypeError,ValueError) as exc:
            raise APIError('SCIENCE_DRAG_MODE_INVALID',str(exc),409) from None
    return set(EXECUTION_ROLES.get(procedure,()))


def _require(value,code,message):
    if not value:raise APIError(code,message,409)


def _identifier(value):
    return isinstance(value,str) and 0<len(value.strip())<=256


def _check_metadata_size(value):
    encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')
    _require(len(encoded)<=MAX_PROOF_BYTES,'SCIENCE_DEPENDENCY_LIMIT','完整证据元数据超过32 MiB上限，请检查实验容量；未丢弃任何来源')


def quality_policy_current(procedure,quality,gates,rule_version,*,independent):
    criteria=criteria_for(procedure,gates,independent=independent)
    hashes={'rulesSha256':digest(gates),'criteriaSha256':digest(criteria)}
    return bool(criteria) and quality.get('criteria')==criteria and quality.get('ruleVersion')==rule_version and all(
        quality.get(key,value if procedure!='sq_shape_rabi_amplitude_repeat5' and procedure not in SCIENCE_GATES and procedure not in SCIENCE_IRB_GATES and procedure not in SCIENCE_FLUX_GATES else None)==value for key,value in hashes.items())


def result_content(result):
    """Shared projection of adapter and controller results, excluding derived quality/URLs."""
    value={key:deepcopy(result[key]) for key in CONTENT_KEYS if key in result}
    value['artifacts']=sorted(({key:item.get(key) for key in ('id','name','kind','sha256')}
                               for item in result.get('artifacts',[])),key=lambda item:item['id'] or '')
    return value


def consumer_content(request):
    """Avoid a self-referential hash when the proof is attached to its request."""
    return {key:deepcopy(value) for key,value in request.items() if key!='scienceDependencies'}


def _pulse_identities(raw):
    identities=raw.get('pulseIdentities',{})
    _require(isinstance(identities,dict),'SCIENCE_DEPENDENCY_EVIDENCE','脉冲身份集合无效')
    bindings=list(identities.values())+([raw['targetPulseIdentity']] if raw.get('targetPulseIdentity') is not None else [])
    fields={'policy','transition','shape','durationS','sigmaS','amplitude','frequencyHz','phaseRad',
            'rotationRad','lambda1','anharmonicityHz','bufferS','xySampleRateHz','readoutContextHash'}
    for binding in bindings:
        _require(isinstance(binding,dict) and isinstance(binding.get('identity'),dict),
                 'SCIENCE_DEPENDENCY_EVIDENCE','脉冲身份不是完整参数对象')
        identity=binding['identity']
        _require(fields<=set(identity) and digest(identity)==binding.get('sha256'),
                 'SCIENCE_DEPENDENCY_EVIDENCE','脉冲身份字段缺失或摘要不一致')
        if 'dragMode' in identity or 'betaSeconds' in identity:
            from adapters.science_v3_irb.beta import MODE, PULSE_POLICY, finite_beta
            try:
                finite_beta(identity.get('betaSeconds'))
                coefficient_valid=(identity.get('dragMode')==MODE and identity['policy']==PULSE_POLICY
                    and identity['lambda1'] is None and identity['anharmonicityHz'] is None)
            except (TypeError,ValueError):coefficient_valid=False
        else:
            coefficient_valid=number(identity['lambda1']) and (number(identity['anharmonicityHz'])
                or identity['anharmonicityHz'] is None and identity['lambda1']==0)
        _require(all(_identifier(identity[k]) for k in ('policy','transition','shape'))
                 and all(number(identity[k]) for k in ('durationS','sigmaS','amplitude','frequencyHz','phaseRad','rotationRad','bufferS','xySampleRateHz'))
                 and identity['durationS']>0 and identity['frequencyHz']>0 and identity['xySampleRateHz']>0
                 and identity['sigmaS']>=0 and identity['bufferS']>=0
                 and coefficient_valid
                 and isinstance(identity['readoutContextHash'],str) and len(identity['readoutContextHash'])==64,
                 'SCIENCE_DEPENDENCY_EVIDENCE','脉冲身份物理参数无效')


def _compatible(left,right,observed_match=False):
    for key in ('resourceId','chipVersionId','deploymentVersionId'):
        _require(left.get(key)==right.get(key),'SCIENCE_SCOPE_MISMATCH','科学来源不是同一比特、芯片和接线版本')
    if not left.get('chipVersionId') or not left.get('deploymentVersionId'):
        _require(left.get('workflowId')==right.get('workflowId') or observed_match,'SCIENCE_SCOPE_MISMATCH','缺少完整配置身份的来源不能跨任务使用')


class ScienceDependencyResolver:
    def __init__(self,controller):
        self.c=controller

    def scope(self,rt,scope):
        """Only a controller-validated DB binding can extend a legacy scope."""
        result=deepcopy(scope)
        wf=self.c.store.get(rt,scope['workflowId']) if scope.get('workflowId') else None
        binding=(wf or {}).get('observedResourceBinding')
        if binding:
            _require(isinstance(binding,dict) and binding.get('schema')=='qcal-observed-resource/1'
                     and binding.get('kind')=='observed_resource' and binding.get('resourceId')==scope.get('resourceId')
                     and binding.get('identityClaim')=='measured_resource_not_design_identity' and binding.get('physicalDesignId') is None
                     and digest({k:v for k,v in binding.items() if k!='sha256'})==binding.get('sha256'),
                     'SCIENCE_SCOPE_MISMATCH','实测资源绑定内容或身份摘要不一致')
            validate=getattr(self.c,'_validate_observed_resource_binding',None)
            _require(callable(validate),'SCIENCE_SCOPE_MISMATCH','控制器尚未提供实测资源验证契约')
            validate(wf,rt)
            _require(scope.get('observedResourceBindingSha256',binding['sha256'])==binding['sha256'],
                     'SCIENCE_SCOPE_MISMATCH','实测资源来源绑定已改变')
            result['observedResourceBindingSha256']=binding['sha256']
        else:
            _require('observedResourceBindingSha256' not in scope,'SCIENCE_SCOPE_MISMATCH','所选任务没有服务器核验的实测资源绑定')
        return result

    def _compatible(self,rt,left,right):
        left=self.scope(rt,left);right=self.scope(rt,right)
        observed_match=False
        if left.get('workflowId')!=right.get('workflowId'):
            for selected,other in ((left,right),(right,left)):
                if not selected.get('observedResourceBindingSha256'):continue
                wf=self.c.store.get(rt,selected['workflowId']);binding=wf['observedResourceBinding']
                observed_match=observed_match or other.get('workflowId')==binding['sourceWorkflowId'] or other.get('observedResourceBindingSha256')==binding['sha256']
        _compatible(left,right,observed_match)

    def _record(self,rt,record_id,cache):
        _require(_identifier(record_id),'SCIENCE_DEPENDENCY_INVALID','来源记录编号无效')
        if record_id not in cache:
            _require(rt.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_science_records'").fetchone(),
                     'SCIENCE_DEPENDENCY_NOT_FOUND','尚无保存的科学来源记录')
            row=rt.db.execute('SELECT payload,payload_hash FROM platform_science_records WHERE id=?',(record_id,)).fetchone()
            _require(row is not None,'SCIENCE_DEPENDENCY_NOT_FOUND','引用的科学记录不存在：'+record_id)
            record=json.loads(row[0])
            _require(digest(record)==row[1] and record.get('id')==record_id,'SCIENCE_DEPENDENCY_CHANGED','来源记录内容摘要不一致')
            cache[record_id]=(record,row[1])
        return cache[record_id]

    def _references(self,rt,refs,inputs,cache,primary=None):
        _require(isinstance(refs,list) and len(refs)<=MAX_ROOTS,'SCIENCE_DEPENDENCY_INVALID','依赖列表最多16项')
        found={}
        def add(role,record_id,legacy=False):
            _require(isinstance(role,str) and (role in ROLES or role=='source_record'),'SCIENCE_DEPENDENCY_INVALID','未知依赖角色')
            _require(_identifier(record_id),'SCIENCE_DEPENDENCY_INVALID','依赖记录编号无效')
            if role in found:
                _require(legacy and found[role]==record_id,'SCIENCE_DEPENDENCY_INVALID','依赖角色重复或新旧来源字段冲突')
            found[role]=record_id
        for item in refs:
            _require(isinstance(item,dict) and set(item)=={'role','recordId'},'SCIENCE_DEPENDENCY_INVALID','依赖只接受 role 和 recordId')
            add(item['role'],item['recordId'])
        for key,role in LEGACY.items():
            if key not in inputs:continue
            value=inputs[key]
            _require(_identifier(value),'SCIENCE_DEPENDENCY_INVALID','显式来源字段不可为空：'+key)
            if role=='rabi_auto':
                record,_=self._record(rt,value,cache)
                procedure=record.get('result',{}).get('procedureId') or record.get('provenance',{}).get('procedureId')
                role='rabi_time' if procedure=='sq_rabi_time' else 'rabi_amplitude'
            add(role,value,True)
        if primary:add('source_record',primary,True)
        _require(len(found)<=MAX_ROOTS,'SCIENCE_DEPENDENCY_INVALID','依赖列表最多16项')
        return [{'role':role,'recordId':record_id} for role,record_id in sorted(found.items())]

    def _artifacts(self,rt,raw,files,hash_cache=None):
        _require(isinstance(raw.get('artifacts'),list) and len(raw['artifacts'])<=MAX_OPERATION_ARTIFACTS,
                 'SCIENCE_DEPENDENCY_LIMIT','单次采集产物超过16384项上限；未截断证据')
        output=[]
        for item in raw.get('artifacts',[]):
            _require(isinstance(item,dict) and all(_identifier(item.get(k)) for k in ('id','name','kind','sha256')),
                     'SCIENCE_DEPENDENCY_EVIDENCE','采集产物身份无效')
            row=rt.db.execute('SELECT path,sha256,payload FROM platform_artifacts WHERE id=?',(item['id'],)).fetchone()
            _require(row is not None and row['sha256']==item.get('sha256'),'SCIENCE_DEPENDENCY_EVIDENCE','来源产物未登记或摘要不一致')
            registered=json.loads(row['payload'])
            _require(all(registered.get(k)==item.get(k) for k in ('id','kind','name','sha256')),'SCIENCE_DEPENDENCY_EVIDENCE','产物登记身份与采集不一致')
            path=checked_path(row['path'])
            permitted=path.is_relative_to(self.c.root.resolve()) or str(path).replace('\\','/').lower().startswith('d:/总体-8号机测试/')
            _require(permitted,'SCIENCE_DEPENDENCY_EVIDENCE','来源文件不属于允许的实验目录')
            token=(str(path),row['sha256'])
            if token not in files:
                _require(len(files)<MAX_ARTIFACTS,'SCIENCE_DEPENDENCY_LIMIT','完整依赖闭包超过65536个文件上限；未截断证据')
                try:
                    actual=hash_cache[token] if hash_cache is not None and token in hash_cache else file_hash(path)
                except OSError as exc:raise APIError('SCIENCE_DEPENDENCY_EVIDENCE','来源文件不可读取',409) from exc
                _require(actual==row['sha256'],'SCIENCE_DEPENDENCY_EVIDENCE','来源原始文件或分析产物已改变')
                files[token]=actual
                if hash_cache is not None and len(hash_cache)<MAX_ARTIFACTS:hash_cache[token]=actual
            output.append({key:item.get(key) for key in ('id','name','kind','sha256')})
        _require(len({a['id'] for a in output})==len(output),'SCIENCE_DEPENDENCY_EVIDENCE','采集重复引用同一产物')
        return sorted(output,key=lambda item:item['id'])

    def _acquisition(self,rt,record,files,hash_cache=None):
        ref=record.get('reference')
        _require(isinstance(ref,dict) and set(ref)=={'workflowId','attemptId'} and all(_identifier(v) for v in ref.values()),
                 'SCIENCE_DEPENDENCY_EVIDENCE','实测依赖缺少任务及采集编号')
        wf=self.c.store.get(rt,ref['workflowId'])
        row=rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=?',(ref['attemptId'],ref['workflowId'])).fetchone()
        _require(wf is not None and row is not None,'SCIENCE_DEPENDENCY_EVIDENCE','来源任务或采集不存在')
        attempt=json.loads(row[0]);raw=attempt.get('result') or {}
        source_controller=self.c.for_attempt(wf['id'],attempt,rt)
        node=next((n for n in wf['nodes'] if n['id']==attempt.get('nodeId')),None)
        _require(node is not None and attempt.get('id')==ref['attemptId'] and attempt.get('procedureId')==node.get('procedureId'),
                 'SCIENCE_DEPENDENCY_EVIDENCE','来源采集节点或实验方法不一致')
        scope={'workflowId':wf['id'],'resourceId':node['resourceId']}
        scope.update({k:wf[k] for k in ('chipVersionId','deploymentVersionId') if wf.get(k)})
        scope=self.scope(rt,scope)
        self._compatible(rt,scope,record.get('scope',{}))
        _require(wf.get('mode')=='real' and attempt.get('mode')=='real' and raw.get('dataKind')=='real' and raw.get('acquiredAt'),
                 'SCIENCE_DEPENDENCY_ROLE_MISMATCH','引用不是控制器保存的采集记录')
        recorded=record.get('result',{})
        _require(recorded.get('procedureId')==node['procedureId'] and recorded.get('parameters',{})==raw.get('parameters',{}),
                 'SCIENCE_DEPENDENCY_EVIDENCE','冻结科学记录的实验参数与实际采集不一致')
        operation=attempt.get('operationId');verification=attempt.get('verificationOperationId')
        _require(operation==attempt['id']+'/measure' and (not verification or verification==attempt['id']+'/verify'),
                 'SCIENCE_DEPENDENCY_EVIDENCE','首测或独立复测操作身份错误')
        originals=[('selection',operation,raw.get('selectionResult')),
                   ('verification',verification,raw)] if verification else [('selection',operation,raw)]
        effects=rt.db.execute('SELECT * FROM platform_effects WHERE attempt_id=?',(attempt['id'],)).fetchall()
        _require(len(effects)==len(originals),'SCIENCE_DEPENDENCY_EVIDENCE','来源采集操作数量不一致')
        operations=[];raw_sets=[];embedded=[]
        for stage,operation,original in originals:
            _require(isinstance(original,dict),'SCIENCE_DEPENDENCY_EVIDENCE','缺少首测或复测的分离结果')
            _pulse_identities(original)
            effect=next((e for e in effects if e['operation_id']==operation),None)
            _require(effect is not None and effect['state']=='completed','SCIENCE_DEPENDENCY_EVIDENCE','来源操作尚未有完整回执')
            request=json.loads(effect['request']);result=json.loads(effect['result'])
            if attempt.get('executionBinding') or request.get('executionBinding'):
                from .attempt_execution import validate_request
                validate_request(self.c,rt,request)
            identity={'runId':wf['id'],'nodeId':node['id'],'attemptId':attempt['id'],'operationId':operation,
                      'resourceId':node['resourceId'],'procedureId':node['procedureId']}
            _require(all(request.get(k)==v for k,v in identity.items()),'SCIENCE_DEPENDENCY_EVIDENCE','操作请求与采集身份不一致')
            comparable=deepcopy(original)
            if stage=='verification' and 'verificationCandidateParameters' in raw:
                _require(isinstance(raw['verificationCandidateParameters'],dict),'SCIENCE_DEPENDENCY_EVIDENCE','保存的独立复测候选无效')
                comparable['candidateParameters']=deepcopy(raw['verificationCandidateParameters'])
            # Controller-derived display suppression preserves rejected/selection
            # estimates as diagnostics; compare the original acquisition values.
            if isinstance(comparable.get('diagnosticMetrics'),dict):
                comparable.setdefault('metrics',{}).update(comparable['diagnosticMetrics'])
            _require(result_content(result)==result_content(comparable),'SCIENCE_DEPENDENCY_EVIDENCE','采集结果与实际执行回执不一致')
            _require(all(result.get('parameters',{}).get(k)==v for k,v in request.get('parameters',{}).items()),
                     'SCIENCE_DEPENDENCY_EVIDENCE','实际采集参数不对应冻结请求')
            artifacts=self._artifacts(rt,result,files,hash_cache);raws=[a for a in artifacts if a['kind']=='raw']
            if attempt.get('executionBinding'):
                from .attempt_execution import sampling_evidence
                sampling_evidence(self.c,rt,request,result)
            _require(bool(raws),'SCIENCE_DEPENDENCY_EVIDENCE','来源缺少原始数据')
            raw_sets.append({a['sha256'] for a in raws})
            operations.append({'stage':stage,'operationId':operation,'requestSha256':digest(request),
                'adapterRequestSha256':digest({k:v for k,v in request.items() if k!='outputDir'}),
                'resultSha256':digest(result),'resultContentSha256':digest(result_content(result)),
                'rawArtifacts':raws,'registeredArtifacts':artifacts})
            if request.get('scienceDependencies'):embedded.append(request['scienceDependencies'])
        _require(len(raw_sets)==1 or raw_sets[0].isdisjoint(raw_sets[1]),'SCIENCE_DEPENDENCY_EVIDENCE','首测与独立复测复用了原始数据')
        if verification:
            selected=raw['selectionResult'].get('candidateParameters',{})
            expected=source_controller._normalize_candidate(node['procedureId'],deepcopy(selected))
            _require(raw.get('candidateParameters',{})==expected,'SCIENCE_DEPENDENCY_EVIDENCE','已提交候选不对应首测及执行器规范化结果')
            normalization=raw.get('candidateNormalization')
            if normalization is not None:
                _require(isinstance(normalization,dict) and normalization.get('requested')==selected and normalization.get('applied')==expected,
                    'SCIENCE_DEPENDENCY_EVIDENCE','候选规范化来源记录不一致')
        quality=raw.get('quality',{})
        policy_current=quality_policy_current(node['procedureId'],quality,wf.get('gates',{}),wf.get('gatesVersion'),independent=bool(verification))
        current=bool(node.get('latestAttemptId')==attempt['id'] and node.get('engineState')!='stale'
                     and not raw.get('stale') and not (node.get('result') or {}).get('stale') and policy_current)
        verified=bool(current and verification and node.get('engineState')=='verified'
                      and attempt.get('state')=='verified' and quality.get('status')=='passed'
                      and not any(raw.get(k) for k in ('partial','stale','verificationPending')))
        data={'reference':deepcopy(ref),'nodeId':node['id'],'procedureId':node['procedureId'],
              'attemptId':attempt['id'],'attemptReceiptSha256':digest(attempt),'operations':operations,
              'adapterId':wf.get('adapterId'),'adapterProfileHash':wf.get('adapterProfileHash'),
              'parameters':deepcopy(raw.get('parameters',{})),'parameterVersions':deepcopy(attempt.get('parameterVersions',{})),
              'pulseIdentities':deepcopy(raw.get('pulseIdentities',{})),
              'targetPulseIdentity':deepcopy(raw.get('targetPulseIdentity')),
              'resolvedOutputs':{'candidateParameters':deepcopy(raw.get('candidateParameters',{})),'metrics':deepcopy(raw.get('metrics',{}))},
              'quality':deepcopy(quality),'gatesSha256':digest({'version':quality.get('ruleVersion'),'criteria':quality.get('criteria')})}
        if attempt.get('executionBinding'):
            data['executionBinding']=deepcopy(attempt['executionBinding'])
            data.update(adapterId=source_controller.adapter_id,adapterProfileHash=source_controller.adapter_profile_hash)
        for proof in [raw.get('scienceDependencies'),raw.get('provenance',{}).get('scienceDependencies')]:
            if proof:embedded.append(proof)
        return data,{'current':current,'controllerVerified':verified},embedded

    @staticmethod
    def _check_proof(proof):
        _require(isinstance(proof,dict) and proof.get('schema')==SCHEMA and proof.get('policyVersion')==POLICY,
                 'SCIENCE_DEPENDENCY_PROOF_CHANGED','依赖证明结构或策略版本不受支持')
        _require(isinstance(proof.get('kind'),str) and proof['kind'] in KINDS
                 and isinstance(proof.get('scope'),dict) and isinstance(proof.get('inputs'),dict)
                 and isinstance(proof.get('rootReferences'),list) and len(proof['rootReferences'])<=MAX_ROOTS
                 and isinstance(proof.get('nodes'),list) and len(proof['nodes'])<=MAX_NODES
                 and all(isinstance(item,dict) and _identifier(item.get('recordId')) for item in proof['nodes'])
                 and isinstance(proof.get('edges'),list) and len(proof['edges'])<=MAX_NODES*MAX_ROOTS,
                 'SCIENCE_DEPENDENCY_PROOF_CHANGED','依赖证明结构或大小无效')
        _check_metadata_size(proof)
        try:expected=digest({key:proof[key] for key in BINDING_KEYS})
        except (KeyError,TypeError,ValueError) as exc:raise APIError('SCIENCE_DEPENDENCY_PROOF_CHANGED','依赖证明不完整',409) from exc
        _require(expected==proof.get('closureSha256'),'SCIENCE_DEPENDENCY_PROOF_CHANGED','冻结依赖闭包摘要不一致')

    def resolve(self,rt,*,kind,scope,refs,inputs,use='analysis',consumer=None,frozen_proofs=(),hash_cache=None):
        _require(isinstance(kind,str) and kind in KINDS and use in ('analysis','execution'),'SCIENCE_DEPENDENCY_INVALID','依赖类型或用途无效')
        _require(isinstance(inputs,dict) and isinstance(scope,dict) and set(scope)<= {'workflowId','resourceId','chipVersionId','deploymentVersionId','observedResourceBindingSha256'}
                 and all(_identifier(v) for v in scope.values()),'SCIENCE_DEPENDENCY_INVALID','依赖输入或范围无效')
        cache={};files={};nodes={};states={};edges=[];visiting=set();embedded_proofs=[]
        roots=self._references(rt,refs,inputs,cache)
        actual_scope=self.scope(rt,scope)
        # Only server-read native requests/results may supply these snapshots.
        # Merge their roots while preserving every immutable upstream node.
        for frozen in frozen_proofs:
            self._check_proof(frozen);embedded_proofs.append(frozen)
            if not actual_scope:actual_scope=self.scope(rt,frozen['scope'])
            else:self._compatible(rt,actual_scope,frozen['scope'])
            for reference in self._references(rt,frozen['rootReferences'],{},cache):
                previous=next((r for r in roots if r['role']==reference['role']),None)
                _require(previous is None or previous==reference,'SCIENCE_DEPENDENCY_CHANGED','采集与保存所绑定的前置来源冲突')
                if previous is None:roots.append(reference)
        _require(len(roots)<=MAX_ROOTS,'SCIENCE_DEPENDENCY_LIMIT','依赖根节点过多')
        roots.sort(key=lambda item:item['role'])
        def visit(reference,parent=None,depth=0):
            nonlocal actual_scope
            record_id=reference['recordId'];role=reference['role']
            _require(record_id not in visiting,'SCIENCE_DEPENDENCY_CYCLE','来源依赖存在循环')
            _require(depth<=MAX_DEPTH and (record_id in nodes or len(nodes)<MAX_NODES),'SCIENCE_DEPENDENCY_LIMIT','来源依赖链过深或过大')
            record,payload_hash=self._record(rt,record_id,cache)
            record_scope=self.scope(rt,record.get('scope',{}))
            if not actual_scope:actual_scope=deepcopy(record_scope)
            else:self._compatible(rt,actual_scope,record_scope)
            procedure=record.get('result',{}).get('procedureId') or record.get('provenance',{}).get('procedureId')
            if role!='source_record':
                _require(record.get('kind')=='measurement' and record.get('dataKind')=='real' and procedure in ROLES[role],
                         'SCIENCE_DEPENDENCY_ROLE_MISMATCH','来源记录的方法或数据类别不符合依赖角色：'+role)
            edge={'fromRecordId':parent,'role':role,'toRecordId':record_id}
            if edge not in edges:edges.append(edge)
            if record_id in nodes:return
            visiting.add(record_id)
            item={'recordId':record_id,'recordRevision':record.get('revision'),'payloadSha256':payload_hash,
                  'kind':record.get('kind'),'dataKind':record.get('dataKind'),'scope':deepcopy(record_scope)}
            embedded=[]
            if record.get('reference'):
                data,state,embedded=self._acquisition(rt,record,files,hash_cache);item.update(data);states[record_id]=state
            else:states[record_id]={'current':True,'controllerVerified':False}
            nodes[record_id]=item
            children=self._references(rt,[],record.get('input',{}),cache,record.get('sourceRecordId'))
            if record.get('dependencyProof'):embedded.append(record['dependencyProof'])
            for frozen in embedded:
                self._check_proof(frozen);embedded_proofs.append(frozen)
                children.extend(frozen['rootReferences'])
            for child in children:visit(child,record_id,depth+1)
            visiting.remove(record_id)
        for root in roots:visit(root)
        # Embedded proof is an immutable upstream snapshot, not permission to
        # silently bind a changed ancestor with the same record ID.
        for frozen in embedded_proofs:
            for previous in frozen['nodes']:
                _require(nodes.get(previous['recordId'])==previous,'SCIENCE_DEPENDENCY_CHANGED','祖先来源已偏离采集时冻结的依赖证明')
        binding={'schema':SCHEMA,'policyVersion':POLICY,'kind':kind,'scope':actual_scope,'inputs':deepcopy(inputs),
                 'rootReferences':roots,'nodes':[nodes[k] for k in sorted(nodes)],'edges':sorted(edges,key=digest)}
        _check_metadata_size(binding)
        current=all(s['current'] for s in states.values())
        missing=sorted(REQUIRED.get(kind,set())-{r['role'] for r in roots})
        status='unbound' if not roots else 'historical' if not current else 'incomplete' if missing else 'ready'
        proof={**binding,'closureSha256':digest(binding),'acceptedCalibration':False,'physicalAccepted':False,
               'assessment':{'status':status,'integrityIntact':True,'evidenceCurrent':current if roots else None,
                             'missingRoles':missing,'executionEligible':False,'nodes':states,'issues':[]}}
        if use=='execution':self._execution(rt,proof,consumer)
        _check_metadata_size(proof)
        return proof

    def _execution(self,rt,proof,consumer):
        code='SCIENCE_DEPENDENCY_NOT_EXECUTABLE'
        _require(isinstance(consumer,dict) and isinstance(consumer.get('procedureId'),str)
                 and consumer['procedureId'] in EXECUTION_ROLES,code,'缺少受支持的执行请求身份')
        consumer=consumer_content(consumer)
        required=execution_roles(consumer['procedureId'],consumer.get('parameters'))
        roles={r['role'] for r in proof['rootReferences']}
        _require(required<=roles,code,'缺少执行前置来源：'+','.join(sorted(required-roles)))
        _require(proof['assessment']['integrityIntact'] and proof['assessment']['evidenceCurrent'],code,'来源完整性或当前性未通过')
        _require(consumer.get('runId')==proof['scope'].get('workflowId') and consumer.get('resourceId')==proof['scope'].get('resourceId'),code,'执行必须使用同任务、同资源的证据')
        bound=self.c.for_workflow(consumer['runId'],rt)
        _require(getattr(bound,'_physical_adapter',False) is True,code,'测试替身不能授予物理执行资格')
        authenticate=getattr(bound.adapter,'authenticate_dependency',None)
        _require(callable(authenticate),code,'该执行器没有原生来源认证契约')
        attestations=[]
        for item in proof['nodes']:
            if not item.get('reference'):
                _require(False,code,'模型或提供数据不能成为物理执行前置')
            _require(proof['assessment']['nodes'][item['recordId']]['controllerVerified'],code,'来源尚未独立验收')
            # The physical adapter authenticates explicit legacy seed migrations;
            # profile equality alone cannot prove pulse/route compatibility.
            dependency={**deepcopy(item),'sourceRoles':sorted({edge['role'] for edge in proof['edges'] if edge['toRecordId']==item['recordId']})}
            try:attestation=authenticate(deepcopy(consumer),dependency)
            except (ValueError,OSError,KeyError,TypeError) as exc:
                raise APIError(code,'原生来源认证失败：'+str(exc),409) from exc
            _require(isinstance(attestation,dict) and attestation.get('status')=='valid'
                     and attestation.get('dependencySha256')==digest(dependency)
                     and attestation.get('consumerSha256')==digest(consumer)
                     and isinstance(attestation.get('evidenceSha256'),str) and len(attestation['evidenceSha256'])==64,
                     code,'原生来源认证未返回与本请求相符的证据摘要')
            attestations.append(deepcopy(attestation))
        proof['assessment'].update(status='eligible',executionEligible=True,attestations=attestations,
                                   consumerSha256=digest(consumer),missingRoles=[])

    def validate(self,rt,proof,*,use='analysis',consumer=None,hash_cache=None):
        self._check_proof(proof)
        try:
            current=self.resolve(rt,kind=proof['kind'],scope=proof['scope'],refs=proof['rootReferences'],inputs=proof['inputs'],hash_cache=hash_cache)
            _require(current['closureSha256']==proof['closureSha256'],'SCIENCE_DEPENDENCY_CHANGED','来源内容、参数或采集回执已改变')
        except APIError as exc:
            if use=='execution':raise APIError('SCIENCE_DEPENDENCY_NOT_EXECUTABLE',exc.message,409) from exc
            return {**deepcopy(proof),'acceptedCalibration':False,'physicalAccepted':False,
                    'assessment':{'status':'invalid','integrityIntact':False,'evidenceCurrent':False,
                                  'executionEligible':False,'issues':[{'code':exc.code,'message':exc.message}]}}
        if use=='execution':self._execution(rt,current,consumer)
        return current
