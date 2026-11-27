from __future__ import annotations

from copy import deepcopy, copy
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request

from .common import APIError, ROOT, checked_path, digest, dumps, file_hash, ident, now, number
from .chat_acquisition_context import read_chat_snapshot, acquisition_context, bound_chat_observations
from .curve_context import attach_curve_summary
from .capture_diagnostics import (PROCEDURES as CAPTURE_DIAGNOSTIC_PROCEDURES,
                                  resolve_sources, calculate_diagnostics, attach_diagnostics)
from . import workflow_budget as task_budget, shot_accounting
from .acquisition_budget import verification_probe, selection_lookahead, verify_admission, sampling_context
from .backtrack_admission import admit_backtrack, eligible_backtracks
from .accepted_upstream_context import resolve_accepted_upstream, attach_accepted_upstream
from .rb_diagnostic import build_proof as rb_diagnostic_proof, VERSION as RB_DIAGNOSTIC_VERSION, require as rb_require
from .model_input_projection import project_model_input, MAX_INPUT_UNITS as MODEL_INPUT_MAX_UNITS
from .readout_withdrawal import restoration_source, VERSION as READOUT_WITHDRAWAL_VERSION, READOUT_KEYS
from .catalog import Catalog, VERSION, PARAMETERS, WORKING_POINT_VERSION, WORKING_POINT_OMITTED, SCIENCE_METHODS, SCIENCE_IRB_METHODS, SCIENCE_FLUX_METHODS
from .working_point import build_working_point_seed
from .quality import GATES, SCIENCE_GATES, SCIENCE_GATES_VERSION, science_rules, evaluate, validate_proposal
from .decision_context import build_decision_context
from .model_parameter_semantics import build_model_parameter_contract
from .prior_failure_context import (CHECKED_JSON_NAMES, MAX_CASES, MAX_CANDIDATES,
                                    load_audited_bindings, eligible_header, project_failure_case, bounded_context, attach_prior_context,
                                    javascript_json_units_upper_bound)
from .store import Store, StepSpec, Budget, AccountingBudget
from .result_archive import ResultArchive, record_effect
from .experience import ExperienceLedger
from .workspace import device_key, device_fault_blocks, DEVICE_STATES
from .manual_steps import ManualSteps, is_manual, awaiting_acceptance
from .stopped_receipts import stopped_summary
from . import model_recovery
from .ownership_diagnostics import OwnershipRefusal, record_rollback_refusal

TERMINAL = {'completed', 'failed', 'aborted'}
PARAMETER_ALIASES = {'readout_frequency_hz': 'readout_hz', 'drive_frequency_hz': 'drive_hz',
                     'drive_amplitude': 'drive_amp', 'pi_duration_s': 'drive_length_s',
                     'readout_amplitude': 'readout_amp'}
DEFAULT_ADAPTER = 'arclight-native-cy8'
CALIBRATION_KEYS = ('readout_hz', 'readout_amp', 'drive_hz', 'drive_amp', 'drive_length_s', 'half_pi_amp', 'xy_delay_s')


def detached_science_profile(root,base,name):
    profile=deepcopy(base)
    if profile.get('scienceFluxBaseLocks'):
        profile['hardwareLocks']=profile.pop('scienceFluxBaseLocks')
    for key in ('sciencePolicy','scienceIRBPolicy','scienceFluxPolicy','scienceRepeat5Policy'):profile.pop(key,None)
    if name=='science-shape-repeat5':
        from adapters.science_shape_repeat5.adapter import build_repeat5_profile
        return build_repeat5_profile(root,profile)
    if name=='science-flux':
        from adapters.science_flux_adapter import build_flux_profile
        return build_flux_profile(root,profile)
    if name=='science-v3-irb':
        from adapters.science_v3_irb.adapter import build_irb_profile
        return build_irb_profile(root,profile)
    if name=='science-v3':
        from adapters.science_v3_adapter import build_science_profile
        return build_science_profile(root,profile)
    raise APIError('UNKNOWN_ADAPTER','未知科学执行配置')


def configured_adapter(root):
    """Load one explicitly allowlisted adapter without importing instrument code."""
    root = checked_path(root)
    local = root / 'config' / 'runtime.local.json'
    settings = json.loads(local.read_text(encoding='utf-8-sig')) if local.is_file() else {}
    selected = settings.get('adapter', DEFAULT_ADAPTER)
    profiles = {'arclight-native-cy8': 'adapters/arclight_profile.json', 'f8-native-cy8': 'adapters/f8_profile.json',
                'science-v3': 'config/science-v3.local.json','science-v3-irb':'config/science-v3-irb.local.json',
                'science-flux':'config/science-flux.local.json','science-shape-repeat5':'config/science-shape-repeat5.local.json'}
    if not isinstance(selected, str) or selected not in profiles:
        raise APIError('UNKNOWN_ADAPTER', '运行配置必须选择 arclight-native-cy8、f8-native-cy8 或 science-v3')
    profile_value = settings.get('profile', profiles[selected])
    if selected == 'f8-native-cy8' and 'profile' not in settings and (root / 'config' / 'f8.local.json').is_file():
        profile_value = 'config/f8.local.json'
    if not isinstance(profile_value, str):
        raise APIError('INVALID_ADAPTER_PROFILE', '执行器 profile 必须是项目内文件路径')
    profile = checked_path(root / profile_value, parent=root)
    if not profile.is_file():
        raise APIError('ADAPTER_PROFILE_MISSING', '执行器配置尚未准备完成：' + profile.name, 503)
    if selected == 'arclight-native-cy8':
        from adapters.arclight import ArcLightAdapter
        return ArcLightAdapter(root, profile)
    if selected=='science-flux':
        from adapters.science_flux_adapter import ScienceFluxAdapter
        from adapters.science_flux.contracts import POLICY
        value=json.loads(profile.read_text(encoding='utf-8-sig'))
        if value.get('scienceFluxPolicy')!=POLICY or value.get('enabled') is not False or value.get('sciencePolicy') or value.get('scienceIRBPolicy'):
            raise APIError('INVALID_ADAPTER_PROFILE','偏置执行须显式选择独立且默认禁用的配置')
        return ScienceFluxAdapter(root,value)
    if selected in ('science-v3','science-v3-irb','science-shape-repeat5'):
        from adapters.science_v3_adapter import ScienceV3Adapter
        from adapters.science_v3.plans import POLICY
        value = json.loads(profile.read_text(encoding='utf-8-sig'))
        if value.get('sciencePolicy') != POLICY or value.get('enabled') is not False:
            raise APIError('INVALID_ADAPTER_PROFILE', 'Science v3 必须显式选择独立且默认禁用的配置')
        if selected=='science-shape-repeat5':
            from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter
            from adapters.science_shape_repeat5.plans import PROFILE_POLICY
            if value.get('scienceRepeat5Policy')!=PROFILE_POLICY:raise APIError('INVALID_ADAPTER_PROFILE','五门 Rabi 来源策略未声明')
            return ScienceRepeat5Adapter(root,value)
        if value.get('scienceRepeat5Policy'):raise APIError('INVALID_ADAPTER_PROFILE','五门来源策略须显式选择新执行配置')
        if selected=='science-v3-irb':
            from adapters.science_v3_irb.adapter import ScienceIRBAdapter
            from adapters.science_v3_irb.plans import POLICY as IRB_POLICY
            if value.get('scienceIRBPolicy')!=IRB_POLICY:raise APIError('INVALID_ADAPTER_PROFILE','IRB 独立策略未声明')
            return ScienceIRBAdapter(root,value)
        if value.get('scienceIRBPolicy'):raise APIError('INVALID_ADAPTER_PROFILE','IRB 配置必须显式选择对应执行器')
        return ScienceV3Adapter(root, value)
    from adapters.f8 import F8Adapter
    return F8Adapter(root, profile)

class Controller(ManualSteps):
    def __init__(self, root=ROOT, *, adapter=None, db_path=None, start_worker=True, brain_url='http://127.0.0.1:8766'):
        self.root = Path(root)
        self.store = Store(db_path or self.root / 'data' / 'platform.sqlite', root=self.root)
        self.archive = ResultArchive(self.store, self.root)
        self.catalog = Catalog(self.root)
        self.brain_url = brain_url
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.worker = None
        if adapter is None:
            adapter = configured_adapter(self.root)
        self.adapter = adapter
        self.profile = getattr(adapter, 'profile', {})
        self._physical_adapter = getattr(adapter, 'physical_hardware', None) is True
        if not self._physical_adapter and getattr(adapter, 'test_adapter', None) is not True:
            raise APIError('ADAPTER_CONTRACT_REQUIRED', '执行器必须明确声明物理设备或测试替身，不能默认绕过设备独占')
        required = ['capabilities', 'preflight', 'execute', 'reconcile']
        if self._physical_adapter:
            required += ['with_execution_enabled', 'hardware_ownership', 'build_plan']
        if any(not callable(getattr(adapter, method, None)) for method in required):
            raise APIError('ADAPTER_CONTRACT_REQUIRED', '执行器缺少必需的独占、执行或回执接口')
        self.adapter_id = self.profile.get('id') or ('explicit-test-adapter' if not self._physical_adapter else None)
        if not self.adapter_id:
            raise APIError('ADAPTER_ID_REQUIRED', '物理执行器必须有稳定的配置编号')
        self.adapter_profile_hash = self._adapter_fingerprint()
        self._ownership_cache = None
        self.experience = ExperienceLedger(parameter_bounds=self._bounds())
        with self.store.transaction() as rt:
            self.experience.ensure_schema(rt)
        self.history = self.catalog.history()
        self.records = self.history['records']
        self._bound_workflow_id = None
        self._workspace_service = None
        self._stability_service = None
        self._stability_error = None
        self._report_exports_service = None
        self._report_exports_lock = threading.RLock()
        self._register_imports()
        self._recover_interrupted()
        # Initialize recovery before the one controller worker can dispatch.
        from .stability import StabilityService
        self._stability_service = StabilityService(self)
        if start_worker:
            self.worker = threading.Thread(target=self._loop, name='qcal-experiment-owner', daemon=True)
            self.worker.start()

    def close(self):
        self.stop_event.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=25)
        if self._report_exports_service is not None:
            self._report_exports_service.shutdown()

    @property
    def report_exports(self):
        with self._report_exports_lock:
            if self._report_exports_service is None:
                from .report_exports import ReportExportService
                self._report_exports_service = ReportExportService(self)
            return self._report_exports_service

    @property
    def workspace(self):
        with self.store.lock:
            if self._workspace_service is None:
                from .workspace import Workspace
                self._workspace_service = Workspace(self)
            return self._workspace_service

    @property
    def stability(self):
        return self._stability_service

    def for_workflow(self, workflow_id, rt=None):
        if getattr(self, '_execution_base', None) is not None:
            return self._execution_base.for_workflow(workflow_id, rt)
        if self._bound_workflow_id == workflow_id:
            return self
        wf = self.get(workflow_id, rt)
        binding = wf.get('configurationBinding')
        if not binding:
            from adapters.arclight import ArcLightAdapter
            # Keep an already exact current profile (including a deliberately
            # configured local snapshot); version routing must not replace it
            # merely because another file uses the same human-readable ID.
            if (type(self.adapter) is ArcLightAdapter and wf.get('mode') == 'real'
                    and wf.get('adapterId') == self.adapter_id
                    and wf.get('adapterProfileHash') == self.adapter_profile_hash
                    and self._adapter_fingerprint() == self.adapter_profile_hash):
                return self
            from adapters.arclight_dynamic import DynamicArcLightAdapter
            from adapters.science_v3_adapter import ScienceV3Adapter
            from adapters.science_v3_irb.adapter import ScienceIRBAdapter
            from adapters.science_flux_adapter import ScienceFluxAdapter
            from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter
            if type(self.adapter) in (ArcLightAdapter, DynamicArcLightAdapter, ScienceV3Adapter, ScienceIRBAdapter, ScienceFluxAdapter, ScienceRepeat5Adapter):
                from .arclight_versions import profile_for_workflow
                profile = profile_for_workflow(self.adapter.root, wf)
                if profile is not None:
                    bound = copy(self)
                    bound.adapter = ArcLightAdapter(self.adapter.root, profile)
                    bound.profile = bound.adapter.profile
                    bound.adapter_id = profile['id']
                    bound.adapter_profile_hash = bound._adapter_fingerprint()
                    bound._ownership_cache = None
                    bound._bound_workflow_id = workflow_id
                    return bound
            return self
        bound = self.with_binding(binding)
        bound._bound_workflow_id = workflow_id
        return bound

    def for_node(self, workflow_id, node_id, rt=None, *, purpose='next', attempt=None):
        from .attempt_execution import for_node
        return for_node(self, workflow_id, node_id, rt, purpose=purpose, attempt=attempt)

    def for_attempt(self, workflow_id, attempt, rt=None):
        return self.for_node(workflow_id, attempt['nodeId'], rt, attempt=attempt) if attempt.get('executionBinding') else self.for_workflow(workflow_id, rt)

    def select_execution_version(self, workflow_id, node_id, payload):
        from .attempt_execution import select
        return select(self, workflow_id, node_id, payload)

    def with_binding(self, binding):
        from adapters.arclight_dynamic import DynamicArcLightAdapter
        bound = copy(self)
        adapter_class = DynamicArcLightAdapter
        if binding['profile'].get('scienceFluxPolicy'):
            from adapters.science_flux_adapter import ScienceFluxAdapter
            from adapters.science_flux.contracts import POLICY as FLUX_POLICY
            if binding['profile']['scienceFluxPolicy']!=FLUX_POLICY or binding['profile'].get('sciencePolicy') or binding['profile'].get('scienceIRBPolicy'):
                raise APIError('INVALID_ADAPTER_PROFILE','未知或混用的偏置执行策略')
            adapter_class=ScienceFluxAdapter
        elif binding['profile'].get('sciencePolicy'):
            from adapters.science_v3_adapter import ScienceV3Adapter
            from adapters.science_v3.plans import POLICY
            if binding['profile']['sciencePolicy'] != POLICY:
                raise APIError('INVALID_ADAPTER_PROFILE', '未知科学执行策略')
            adapter_class = ScienceV3Adapter
            if binding['profile'].get('scienceIRBPolicy'):
                from adapters.science_v3_irb.adapter import ScienceIRBAdapter
                from adapters.science_v3_irb.plans import POLICY as IRB_POLICY
                if binding['profile']['scienceIRBPolicy']!=IRB_POLICY:raise APIError('INVALID_ADAPTER_PROFILE','未知 IRB 执行策略')
                adapter_class=ScienceIRBAdapter
            if binding['profile'].get('scienceRepeat5Policy'):
                from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter
                from adapters.science_shape_repeat5.plans import PROFILE_POLICY
                if binding['profile']['scienceRepeat5Policy']!=PROFILE_POLICY:raise APIError('INVALID_ADAPTER_PROFILE','未知五门来源策略')
                adapter_class=ScienceRepeat5Adapter
        bound.adapter = adapter_class(self.root, deepcopy(binding['profile']))
        bound.profile = bound.adapter.profile
        bound.adapter_id = bound.profile['id']
        bound._physical_adapter = True
        bound.adapter_profile_hash = bound._adapter_fingerprint()
        bound._ownership_cache = None
        bound.history = {**self.history, 'resources': deepcopy(binding['resources']), 'records': []}
        bound.records = []
        bound.catalog = copy(self.catalog)
        bound.catalog.history = lambda: deepcopy(bound.history)
        return bound

    def _observed_science_binding(self, source_workflow_id, resource_id, *, rt=None):
        """Freeze an actually measured resource without inventing a design identity."""
        if any(not isinstance(value,str) or not 1 <= len(value) <= 256 for value in (source_workflow_id,resource_id)):
            raise APIError('SCIENCE_SOURCE_SCOPE', '请选择唯一来源任务及实测资源编号', 409)
        if rt is None:
            with self.store.connect() as reader:
                return self._observed_science_binding(source_workflow_id, resource_id, rt=reader)
        source = self.get(source_workflow_id, rt)
        if source.get('mode') != 'real' or resource_id not in source.get('resourceIds', []):
            raise APIError('SCIENCE_SOURCE_SCOPE', '来源必须是同一实测资源的真机任务', 409)
        bound = self.for_workflow(source_workflow_id, rt)
        bound._assert_adapter_binding(source)
        from adapters.arclight import ArcLightAdapter
        from adapters.arclight_dynamic import DynamicArcLightAdapter
        from adapters.science_v3_adapter import ScienceV3Adapter, build_science_profile
        from adapters.arclight_configurable_runtime.routing import validate_route
        from adapters.science_v3_irb.adapter import ScienceIRBAdapter
        from adapters.science_flux_adapter import ScienceFluxAdapter
        from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter
        if type(bound.adapter) not in (ArcLightAdapter, DynamicArcLightAdapter, ScienceV3Adapter, ScienceIRBAdapter,ScienceFluxAdapter,ScienceRepeat5Adapter):
            raise APIError('SCIENCE_SOURCE_SCOPE', '实测资源绑定只接受已审计的原生执行器', 409)
        if source.get('observedResourceBinding'):
            self._validate_observed_resource_binding(source, rt)
            binding = deepcopy(source['configurationBinding'])
            binding['resources'] = [item for item in binding['resources'] if item['id'] == resource_id]
            node = next(node for node in source['nodes'] if node['resourceId'] == resource_id)
            current, _ = bound._parameters(rt,source,{**node,'parameterOverrides':{}})
            binding['profile']['resources'][resource_id].update(current)
            binding['profile']['id'] = 'science-v3-observed-' + digest(binding['profile'])[:20]
            binding['digest'] = digest({key:value for key,value in binding.items() if key != 'digest'})
            return binding
        if source.get('chipVersionId') or source.get('deploymentVersionId'):
            raise APIError('SCIENCE_SOURCE_SCOPE', '已有发布配置的来源应选择对应芯片和接线版本', 409)
        preflight = bound.adapter.preflight()
        if not preflight.get('staticReady', preflight.get('ready', False)):
            raise APIError('SCIENCE_SOURCE_PREFLIGHT', '来源执行器静态校验失败', 409)
        rank = {'ro_optimize':3, 'ro_iq01':2, 'ro_resonator_survey':1}
        candidates = sorted((node for node in source['nodes'] if node['resourceId'] == resource_id
            and node['procedureId'] in rank and node['engineState'] == 'verified'
            and node.get('result',{}).get('quality',{}).get('status') == 'passed'
            and not node.get('result',{}).get('stale')), key=lambda node:rank[node['procedureId']], reverse=True)
        if not candidates:
            raise APIError('SCIENCE_RESOURCE_UNOBSERVED', '来源尚无当前已验收的真机读出指纹', 409)
        node = candidates[0]
        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=? AND id=?',
            (source['id'],node.get('latestAttemptId'))).fetchone()
        attempt = json.loads(row[0]) if row else {}
        result = node['result']
        if (attempt.get('state') != 'verified' or attempt.get('result') != result or not attempt.get('verificationOperationId')
                or result.get('dataKind') != 'real' or result.get('partial')):
            raise APIError('SCIENCE_RESOURCE_UNOBSERVED', '实测资源指纹缺少当前独立验收记录', 409)
        raw = [item for item in result.get('artifacts',[]) if item.get('kind') == 'raw']
        if not raw:
            raise APIError('SCIENCE_RESOURCE_UNOBSERVED', '实测资源指纹没有原始采集文件', 409)
        for item in raw:
            path, metadata = self.artifact(item['id'])
            if not path.is_relative_to(self.root.resolve()) or metadata.get('sha256') != item.get('sha256'):
                raise APIError('SCIENCE_RESOURCE_UNOBSERVED', '实测资源指纹的原始文件不属于冻结来源', 409)
        snapshot = {key:deepcopy(value) for key,value in bound.profile.items() if key not in ('enabled','connectionConfirmedAt')}
        route = validate_route(snapshot.get('routingByResource',{}).get(resource_id,snapshot['instrument']))
        anchor = {'nodeId':node['id'], 'attemptId':attempt['id'], 'selectionOperationId':attempt['operationId'],
            'verificationOperationId':attempt['verificationOperationId'], 'resultSha256':digest(result),
            'readoutHz':result.get('candidateParameters',{}).get('readout_hz',result.get('parameters',{}).get('readout_hz')),
            'rawArtifacts':[{'id':item['id'],'sha256':item['sha256']} for item in raw]}
        if not number(anchor['readoutHz']):
            raise APIError('SCIENCE_RESOURCE_UNOBSERVED', '实测资源缺少已采集读出频率', 409)
        observed = {'schema':'qcal-observed-resource/1','kind':'observed_resource','resourceId':resource_id,
            'sourceWorkflowId':source['id'],'sourceAdapterId':source['adapterId'],
            'sourceAdapterProfileHash':source['adapterProfileHash'],'sourceProfileSnapshotSha256':digest(snapshot),
            'routeSha256':digest(route),'deviceKey':device_key(snapshot),'physicalDesignId':None,
            'identityClaim':'measured_resource_not_design_identity','readoutAnchor':anchor}
        observed['sha256'] = digest(observed)
        profile = build_science_profile(self.root, snapshot)
        profile['observedResourceBinding'] = deepcopy(observed)
        # Parameters are initialization only. Each selected scientific source is
        # independently authenticated at execution; this binding accepts none.
        current, _ = bound._parameters(rt, source, {**node,'parameterOverrides':{}})
        profile['resources'][resource_id].update(current)
        profile['id'] = 'science-v3-observed-' + digest({'binding':observed,'profile':profile})[:20]
        profile['enabled'] = False
        binding = {'resources':[{'id':resource_id,'label':resource_id,'identityStatus':'observed_resource',
            'physicalId':None,'identityLabel':'实测资源 '+resource_id+'／版图未确认'}],
            'profile':profile,'sourceProfile':snapshot,'observedResourceBinding':observed,
            'source':{'kind':'observed_resource','sourceWorkflowId':source['id'],'acceptedCalibration':False}}
        binding['digest'] = digest(binding)
        return binding

    def _validate_observed_resource_binding(self, wf, rt):
        observed = wf.get('observedResourceBinding')
        if not observed:
            return
        if (not isinstance(observed,dict) or observed.get('schema') != 'qcal-observed-resource/1'
                or observed.get('physicalDesignId') is not None
                or digest({key:value for key,value in observed.items() if key != 'sha256'}) != observed.get('sha256')):
            raise APIError('SCIENCE_RESOURCE_BINDING_CHANGED', '实测资源身份摘要不一致', 409)
        origin_workflow = self.get(observed.get('sourceWorkflowId'), rt)
        if origin_workflow.get('observedResourceBinding'):
            raise APIError('SCIENCE_RESOURCE_BINDING_CHANGED', '实测资源根来源不能形成继承循环', 409)
        from adapters.arclight import ArcLightAdapter
        snapshot = wf.get('configurationBinding',{}).get('sourceProfile',{})
        origin_controller = copy(self)
        origin_controller.adapter = ArcLightAdapter(self.root, snapshot)
        origin_controller.profile = origin_controller.adapter.profile
        origin_controller.adapter_id = snapshot.get('id')
        origin_controller.adapter_profile_hash = origin_controller._adapter_fingerprint()
        origin_controller._bound_workflow_id = None
        original = origin_controller._observed_science_binding(observed['sourceWorkflowId'], observed['resourceId'], rt=rt)
        if (original['observedResourceBinding'] != observed
                or wf.get('configurationBinding',{}).get('observedResourceBinding') != observed
                or wf.get('configurationBinding',{}).get('profile',{}).get('observedResourceBinding') != observed
                or digest(wf.get('configurationBinding',{}).get('sourceProfile',{})) != observed['sourceProfileSnapshotSha256']):
            raise APIError('SCIENCE_RESOURCE_BINDING_CHANGED', '来源、读出指纹或冻结设备路由已改变', 409)

    def science_catalog(self, *, chip_version_id=None, deployment_version_id=None, resource_id, source_workflow_id=None, execution_profile="science-v3", bias_deployment_version_id=None):
        """Read-only capability view for an explicitly selected detached profile."""
        if execution_profile not in ('science-v3','science-v3-irb','science-flux','science-shape-repeat5'):raise APIError('UNKNOWN_ADAPTER','未知科学执行配置')
        if source_workflow_id and (chip_version_id or deployment_version_id):
            raise APIError('SCIENCE_CONFIGURATION_AMBIGUOUS', '实测来源与已发布配置只能选择一种', 409)
        if not source_workflow_id and any(not isinstance(value, str) or not value for value in
               (chip_version_id, deployment_version_id, resource_id)):
            raise APIError('SCIENCE_CONFIGURATION_REQUIRED', '请选择已发布芯片、接线版本及单个比特', 409)
        if source_workflow_id:
            binding = self._observed_science_binding(source_workflow_id, resource_id)
        else:
            binding = deepcopy(self.workspace.chips.resolve_binding(chip_version_id, deployment_version_id, [resource_id]))
        if bias_deployment_version_id:
            if execution_profile!='science-flux' or not source_workflow_id:raise APIError('BIAS_DEPLOYMENT_SCOPE','独立偏置接线仅用于实测资源的偏置任务',409)
            from .flux_binding import published_bias_binding
            binding=published_bias_binding(self.workspace.chips,binding,bias_deployment_version_id,resource_id)
        if execution_profile=='science-flux':
            from .flux_binding import scope_flux_binding
            binding=scope_flux_binding(binding,resource_id)
        binding['profile']=detached_science_profile(self.root,binding['profile'],execution_profile)
        binding['digest'] = digest({key: value for key, value in binding.items() if key != 'digest'})
        bound = self.with_binding(binding)
        preflight = bound.adapter.preflight()
        capabilities = deepcopy(bound.adapter.capabilities())
        if not preflight.get('staticReady'):
            for procedure in capabilities['procedures'].values():
                procedure.update(available=False, status='static_preflight_failed', reason=preflight.get('reason'))
        result = bound.catalog.describe(capabilities, bound._bounds())
        from .science_dependencies import ROLES as SCIENCE_SOURCE_METHODS, execution_roles
        for method in result['manualMethods']:
            method['executionProfile'] = execution_profile
            method['parameterBounds'] = bound._parameter_schema(method['procedureId'])
            method['scienceSourceMethods'] = {role: sorted(SCIENCE_SOURCE_METHODS.get(role, ()))
                                              for role in method.get('scienceSourceRoles', [])}
            defaults = getattr(bound.adapter, 'parameter_defaults', None)
            if callable(defaults):
                method['parameterDefaults'] = defaults(method['procedureId'])
            if method['procedureId'] in SCIENCE_IRB_METHODS:
                method['scienceSourceRolesByMode'] = {mode: sorted(execution_roles(method['procedureId'], {'drag_mode':mode}))
                    for mode in ('normalized_alpha','empirical_beta')}
                mode_keys = getattr(bound.adapter, 'parameter_keys_by_mode', None)
                if callable(mode_keys):method['parameterKeysByMode'] = mode_keys(method['procedureId'])
        result.update(executionProfile=execution_profile, preflight=preflight,
            configurationBinding={'chipVersionId': chip_version_id, 'deploymentVersionId': deployment_version_id,
                                  'resourceId': resource_id, 'adapterProfileHash': bound.adapter_profile_hash},
            requiresManualReferences=True, hardwareConnected=False)
        if binding.get('observedResourceBinding'):
            result['observedResourceBinding'] = deepcopy(binding['observedResourceBinding'])
            result['configurationBinding'].update(sourceWorkflowId=source_workflow_id, identityStatus='observed_resource',
                identityLabel='实测资源 '+resource_id+'／版图未确认', observedResourceBinding=deepcopy(binding['observedResourceBinding']))
        if binding.get('biasDeploymentVersionId'):
            result['configurationBinding']['biasDeploymentVersionId']=binding['biasDeploymentVersionId']
            result['biasDeploymentBinding']=deepcopy(binding['biasDeploymentBinding'])
        return result

    def _register_imports(self):
        with self.store.transaction() as rt:
            for record in self.records:
                for artifact in record.get('result', {}).get('artifacts', []):
                    self._artifact(rt, artifact, verify=False)

    def _artifact(self, rt, artifact, verify=True):
        path_value = artifact.get('localPath') or artifact.get('sourcePath')
        if not path_value:
            registered = rt.db.execute('SELECT path,sha256 FROM platform_artifacts WHERE id=?', (artifact.get('id'),)).fetchone()
            if not registered or registered['sha256'] != artifact.get('sha256'):
                raise APIError('MISSING_ARTIFACT', '产物未提供实际文件路径且尚未登记')
            path_value = registered['path']
        path = checked_path(path_value)
        permitted = path.is_relative_to(self.root.resolve()) or str(path).replace('\\', '/').lower().startswith('d:/总体-8号机测试/')
        if not permitted:
            raise APIError('ARTIFACT_OUTSIDE_SOURCE', '产物不属于平台或已登记实验档案', 403)
        if not path.is_file():
            raise APIError('MISSING_ARTIFACT', f'产物不存在：{path.name}')
        expected = artifact.get('sha256')
        if not expected or (verify and file_hash(path) != expected):
            raise APIError('ARTIFACT_HASH_MISMATCH', '产物哈希不一致')
        old = rt.db.execute('SELECT sha256 FROM platform_artifacts WHERE id=?', (artifact['id'],)).fetchone()
        if old and old[0] != expected:
            raise APIError('ARTIFACT_ID_CONFLICT', '产物编号对应不同内容')
        clean = {k: v for k, v in artifact.items() if k != 'localPath'}
        clean['url'] = '/api/artifacts/' + artifact['id']
        rt.db.execute('INSERT OR REPLACE INTO platform_artifacts VALUES(?,?,?,?)',
                      (artifact['id'], str(path), expected, dumps(clean)))
        return clean

    def _recover_interrupted(self):
        with self.store.transaction() as rt:
            for wf in self.store.workflows(rt):
                # Older manual handovers cleared the workflow wait but left its
                # selected node waiting forever. Repair only an already-confirmed,
                # idle handover; never reopen a permit or clear unknown effects.
                if (is_manual(wf) and wf.get('hardwareConfirmed') and wf['status']=='paused'
                        and wf['engineState']=='manual_ready' and not wf.get('cancelRequested')
                        and not self._has_lease(rt,wf['id'])
                        and not rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')",(wf['id'],)).fetchone()
                        and not any(n['engineState'] in ('needs_reconciliation','unknown','acquiring','verifying') for n in wf['nodes'])
                        and self._clear_confirmed_hardware_wait(wf)):
                    self.store.save(rt,wf,'manual_handover_repaired',{'message':'已同步接线确认状态；仍需明确提交本步测量'})
                manual = wf.get('manualStep') or {}
                if manual.get('state') in ('queued', 'running'):
                    manual.update(state='interrupted', finishedAt=now())
                    if wf['status'] == 'queued':
                        wf.update(status='paused', engineState='manual_ready')
                    self.store.save(rt, wf, 'manual_step_interrupted', {'message': '服务重启：逐步许可已关闭，未自动重发'})
                diagnostic = wf.get('rbDiagnostic') or {}
                if diagnostic.get('state') in ('queued', 'running'):
                    diagnostic.update(state='interrupted', finishedAt=now(), resultStatus='unverified')
                    wf['workflowComplete'] = False
                    if wf['status'] == 'queued':
                        wf.update(status='paused', engineState='paused')
                        self.store.save(rt, wf, 'rb_diagnostic_interrupted', {'message': '服务重启：单次诊断许可已关闭，未自动重发'})
                        continue
                    if wf['status'] != 'running':
                        # Preflight/ownership/reconciliation may have put the
                        # run in attention while its one-shot permit was open.
                        # Persist closure without clearing existing quarantine.
                        self.store.save(rt, wf, 'rb_diagnostic_interrupted', {'message': '服务重启：关闭未结束的单次诊断许可，保留原停机及隔离状态'})
                        continue
                from .smart_main_checkpoint import recover as recover_smart_main
                main_recovery = recover_smart_main(self, rt, wf)
                if main_recovery in ('valid', 'restored'):
                    if main_recovery == 'restored':
                        self.store.save(rt, wf, 'smart_main_checkpoint_recovered',
                            {'message': '完整采集回执已保存；恢复下一步规划，未重发已完成采集'})
                    continue
                if main_recovery == 'unsafe':
                    # Reuse the normal quarantine below, including a queued
                    # checkpoint whose receipts became unresolved or changed.
                    wf['status'] = 'running'
                if wf['status'] != 'running':
                    continue
                if wf.get('smartRun', {}).get('schema') == 'q11-full-same-gate/1':
                    from .smart_science import recover_parent
                    if recover_parent(self, rt, wf):
                        continue
                if wf['mode'] == 'replay':
                    wf['status'], wf['engineState'] = 'paused', 'replay_interrupted'
                else:
                    wf['status'], wf['engineState'] = 'attention', 'needs_reconciliation'
                    rt.db.execute("UPDATE platform_leases SET state='quarantined' WHERE workflow_id=?", (wf['id'],))
                    rt.db.execute("UPDATE platform_effects SET state='unknown' WHERE workflow_id=? AND state='pending'", (wf['id'],))
                self.store.save(rt, wf, 'reconciliation_required', {'message': '服务重启：保留执行记录，未自动重发仪器命令'})

    @staticmethod
    def _clear_confirmed_hardware_wait(wf):
        node=next((n for n in wf['nodes'] if n['id']==wf.get('currentNodeId')),None)
        if node and node['engineState']=='waiting_hardware':
            node.update(status='pending',engineState='pending',detail='接线已确认，等待明确提交本步测量')
            return True
        return False

    def get(self, workflow_id, rt=None):
        if rt is None:
            with self.store.connect() as conn:
                return self.get(workflow_id, conn)
        wf = self.store.get(rt, workflow_id)
        if wf is None:
            raise APIError('NOT_FOUND', '任务不存在', 404)
        return self._with_timing(rt, wf)

    def list(self):
        with self.store.connect() as rt:
            return [self._with_timing(rt, wf) for wf in self.store.workflows(rt)]

    def snapshot(self):
        """Read the event cursor and timing-enriched workflows without writes."""
        with self.store.lock, self.store.connect() as rt:
            seq = rt.db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0]
            return {'version': 1, 'seq': seq,
                    'workflows': [self._with_timing(rt, wf) for wf in self.store.workflows(rt)]}

    def present_workflows(self, workflows):
        from .receipt_presentation import read_receipts, workflow_view
        from .flux_execution import present_start_requirements
        with self.store.connect() as rt:
            return [present_start_requirements(workflow_view(shot_accounting.view(rt.db,wf), [json.loads(row[0]) for row in rt.db.execute(
                'SELECT payload FROM platform_attempts WHERE workflow_id=?', (wf['id'],))],
                read_receipts(rt.db, wf['id'])),wf.get('configurationBinding',{}).get('profile',self.profile)) if isinstance(wf, dict) and isinstance(wf.get('id'), str)
                and isinstance(wf.get('nodes'), list) else deepcopy(wf) for wf in workflows]

    def present_detail(self, workflow_id, detail):
        from .receipt_presentation import read_receipts, detail_view
        with self.store.connect() as rt:
            return detail_view(workflow_id, detail, read_receipts(rt.db, workflow_id))

    @staticmethod
    def _timestamp(value):
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed if parsed.tzinfo is not None else None
        except ValueError:
            return None

    def _with_timing(self, rt, wf):
        # Legacy records are enriched in memory only. Task creation is never
        # used as a substitute for a real acquisition attempt's start.
        start = wf.get('firstAcquisitionAt') if wf.get('mode') == 'real' else None
        if self._timestamp(start) is None:
            starts = []
            if wf.get('mode') == 'real':
                for row in rt.db.execute('SELECT payload FROM platform_attempts WHERE workflow_id=?', (wf['id'],)):
                    attempt = json.loads(row[0])
                    value = attempt.get('startedAt')
                    if attempt.get('mode') == 'real' and self._timestamp(value) is not None:
                        starts.append(value)
            start = min(starts, key=self._timestamp) if starts else None
        finish = wf.get('finishedAt') if wf.get('status') in TERMINAL else None
        if wf.get('status') in TERMINAL and self._timestamp(finish) is None:
            # Prefer the immutable terminal event for legacy tasks; updatedAt
            # is a stable fallback when an older event ledger is unavailable.
            for row in rt.db.execute("SELECT event,at,payload FROM events WHERE run_id=? AND event IN ('completed','aborted','model_decision') ORDER BY seq DESC", (wf['id'],)):
                if row['event'] == 'model_decision' and json.loads(row['payload']).get('action') != 'stop':
                    continue
                finish = datetime.fromtimestamp(row['at'], timezone.utc).isoformat()
                break
            if self._timestamp(finish) is None:
                finish = wf.get('updatedAt') if self._timestamp(wf.get('updatedAt')) is not None else None
        wf['firstAcquisitionAt'], wf['finishedAt'] = start, finish
        end = self._timestamp(finish) if wf.get('status') in TERMINAL else self._timestamp(now())
        wf['elapsed'] = max(0.0, (end - self._timestamp(start)).total_seconds()) if start and end else 0
        return wf

    @staticmethod
    def _finish_timing(wf):
        if wf.get('status') in TERMINAL and not wf.get('finishedAt'):
            wf['finishedAt'] = now()

    def _node(self, wf, node_id=None):
        node_id = node_id or wf.get('currentNodeId')
        node = next((n for n in wf['nodes'] if n['id'] == node_id), None)
        if node is None:
            raise APIError('NODE_NOT_FOUND', '任务中不存在该节点', 404)
        return node

    def create(self, data, *, _rt=None, _binding=None, _campaign_id=None, _stability=None, _budget_parent=None):
        if '_budget_parent' in data or 'accountingParent' in data:
            raise APIError('INVALID_BUDGET_SCOPE', '子任务计量授权由控制器从父任务绑定', 409)
        if _budget_parent is not None and (_rt is None or not _rt.db.in_transaction):
            raise APIError('INVALID_BUDGET_SCOPE', '子任务计量授权缺少原事务', 409)
        # Replaying an already-created draft must not depend on later source
        # freshness. No new execution is authorized by this idempotent response.
        if _rt is None and 'operationId' in data:
            operation = data['operationId']
            if not isinstance(operation,str) or not 1 <= len(operation) <= 256:
                raise APIError('OPERATION_ID_REQUIRED', '创建操作编号无效')
            try:
                request_hash = digest({'kind':'create_workflow','body':data})
            except (ValueError,TypeError):
                raise APIError('INVALID_PARAMETERS', '参数必须是有限数值或有效文本') from None
            with self.store.connect() as reader:
                previous = reader.db.execute('SELECT request_hash,response FROM platform_commands WHERE id=?',(operation,)).fetchone()
            if previous:
                if previous['request_hash'] != request_hash:
                    raise APIError('IDEMPOTENCY_CONFLICT', '同一操作编号不能提交不同请求', 409)
                return json.loads(previous['response'])
        if data.get('executionProfile') not in (None, 'science-v3', 'science-v3-irb', 'science-flux','science-shape-repeat5'):
            raise APIError('UNKNOWN_ADAPTER', '未知新任务执行配置')
        if data.get('biasDeploymentVersionId') and (data.get('executionProfile')!='science-flux' or not data.get('sourceWorkflowId')):
            raise APIError('BIAS_DEPLOYMENT_SCOPE','独立偏置接线仅用于实测资源的偏置任务',409)
        if data.get('sourceWorkflowId'):
            if data.get('executionProfile') not in ('science-v3','science-v3-irb','science-flux','science-shape-repeat5') or data.get('chipVersionId') or data.get('deploymentVersionId'):
                raise APIError('SCIENCE_CONFIGURATION_AMBIGUOUS', '实测来源只用于独立科学配置，不能混选已发布版本', 409)
            if _binding is None:
                ids = data.get('resourceIds')
                if not isinstance(ids,list) or len(ids) != 1:
                    raise APIError('SCIENCE_SOURCE_SCOPE', '实测来源绑定只能选择一个资源', 409)
                binding = self._observed_science_binding(data['sourceWorkflowId'],ids[0],rt=_rt)
                if data.get('biasDeploymentVersionId'):
                    from .flux_binding import published_bias_binding
                    binding=published_bias_binding(self.workspace.chips,binding,data['biasDeploymentVersionId'],ids[0])
                if data.get('executionProfile')=='science-flux':
                    from .flux_binding import scope_flux_binding
                    binding=scope_flux_binding(binding,ids[0])
                return self.with_binding(binding).create(data,_rt=_rt,_binding=binding,
                    _campaign_id=_campaign_id,_stability=_stability, _budget_parent=_budget_parent)
        if _binding is None and (data.get('chipVersionId') or data.get('deploymentVersionId')):
            _binding = self.workspace.chips.resolve_binding(data.get('chipVersionId'), data.get('deploymentVersionId'), data.get('resourceIds'))
            return self.with_binding(_binding).create(data, _rt=_rt, _binding=_binding, _campaign_id=_campaign_id, _stability=_stability, _budget_parent=_budget_parent)
        current_profile='science-shape-repeat5' if self.profile.get('scienceRepeat5Policy') else 'science-flux' if self.profile.get('scienceFluxPolicy') else 'science-v3-irb' if self.profile.get('scienceIRBPolicy') else 'science-v3' if self.profile.get('sciencePolicy') else None
        if data.get('executionProfile') in ('science-v3','science-v3-irb','science-flux','science-shape-repeat5') and data['executionProfile']!=current_profile:
            if not _binding:
                raise APIError('SCIENCE_CONFIGURATION_REQUIRED', '科学实验须选择已发布的芯片和接线版本', 409)
            detached = deepcopy(_binding)
            detached['profile']=detached_science_profile(self.root,detached['profile'],data['executionProfile'])
            detached['digest'] = digest({key: value for key, value in detached.items() if key != 'digest'})
            return self.with_binding(detached).create(data, _rt=_rt, _binding=detached,
                _campaign_id=_campaign_id, _stability=_stability, _budget_parent=_budget_parent)
        if _rt is None and 'operationId' in data:
            operation = data['operationId']
            if not isinstance(operation, str) or not 1 <= len(operation) <= 256:
                raise APIError('OPERATION_ID_REQUIRED', '创建操作编号无效')
            try:
                request_hash = digest({'kind': 'create_workflow', 'body': data})
            except (ValueError, TypeError):
                raise APIError('INVALID_PARAMETERS', '参数必须是有限数值或有效文本') from None
            with self.store.transaction() as rt:
                prior = rt.db.execute('SELECT request_hash,response FROM platform_commands WHERE id=?', (operation,)).fetchone()
                if prior:
                    if prior['request_hash'] != request_hash:
                        raise APIError('IDEMPOTENCY_CONFLICT', '同一操作编号不能提交不同请求', 409)
                    return json.loads(prior['response'])
                result = self.create(data, _rt=rt, _binding=_binding, _campaign_id=_campaign_id, _stability=_stability, _budget_parent=_budget_parent)
                rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (operation, request_hash, dumps(result)))
                return result
        mode = data.get('mode', 'replay')
        if mode not in ('replay', 'real'):
            raise APIError('INVALID_MODE', '请选择历史回放或真机测量')
        name = str(data.get('name', '')).strip()
        if not 1 <= len(name) <= 200:
            raise APIError('INVALID_NAME', '任务名称长度须为 1–200')
        resources = {r['id']: r for r in self.history['resources']}
        selected = data.get('resourceIds')
        if not selected:
            target = str(data.get('target', '')).strip()
            selected = [target] if target in resources else []
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or any(r not in resources for r in selected):
            raise APIError('UNKNOWN_RESOURCE', '请选择当前芯片版本中的比特')
        supported_resources = self.adapter.capabilities().get('resources')
        if mode == 'real' and self._physical_adapter and isinstance(supported_resources, list) and any(r not in supported_resources for r in selected):
            raise APIError('UNSUPPORTED_RESOURCE', '当前执行器尚未绑定所选对象的真机映射；请选择已支持资源')
        experiment = data.get('experiment', 'single')
        if experiment not in ('single', 'two'):
            raise APIError('INVALID_EXPERIMENT', '未知实验类型')
        policy = data.get('policy', 'threshold')
        if policy not in ('strict', 'threshold', 'autopilot'):
            raise APIError('INVALID_POLICY', '未知运行策略')
        science = bool(self.profile.get('sciencePolicy') or self.profile.get('scienceFluxPolicy'))
        if self.profile.get('scienceFluxPolicy') and data.get('executionProfile')!='science-flux':
            raise APIError('SCIENCE_PROFILE_REQUIRED','偏置扫描与点位调用必须显式选择独立执行配置',409)
        if self.profile.get('scienceRepeat5Policy') and data.get('executionProfile')!='science-shape-repeat5':
            raise APIError('SCIENCE_PROFILE_REQUIRED','五门 Rabi 来源链必须显式选择独立执行配置',409)
        if self.profile.get('scienceIRBPolicy') and not self.profile.get('scienceRepeat5Policy') and data.get('executionProfile')!='science-v3-irb':
            raise APIError('SCIENCE_PROFILE_REQUIRED','X/2 DRAG 与 IRB 必须显式选择独立执行配置',409)
        gates_version,science_gates=science_rules(self.profile)
        if science and (mode != 'real' or policy != 'strict' or len(selected) != 1
                        or data.get('manualProcedureId') not in SCIENCE_METHODS or _stability is not None):
            raise APIError('SCIENCE_MANUAL_REQUIRED', '科学执行仅允许单比特人工逐步实验，并须明确选择测项', 409)
        coverage = data.get('coverage', 'full' if mode == 'real' else 'main')
        if coverage not in ('main', 'full', 'working_point'):
            raise APIError('INVALID_COVERAGE', '测量范围必须是 main、full 或 working_point')
        from .arclight_versions import fresh_lineage
        analysis_lineage = fresh_lineage(self, data, mode, policy, coverage, selected, _rt)
        seed = None
        if coverage == 'working_point':
            if mode != 'real' or experiment != 'single' or selected != ['Q11']:
                raise APIError('WORKING_POINT_SCOPE', '复刻工作点仅支持新建 Q11 真机单比特任务', 409)
            seed = build_working_point_seed(self.adapter)
        nodes, edges = self.catalog.nodes([resources[r] for r in selected], mode, self.adapter.capabilities(), experiment, coverage)
        if _stability is not None:
            nodes = [node for node in nodes if node['procedureId'] in ('sq_spectroscopy', 'sq_t1')]
            if len(nodes) != 2 or any(node['capability'] != 'available' for node in nodes):
                raise APIError('UNSUPPORTED_METHOD', '周期任务需要定频和 T1 执行能力', 409)
            nodes.sort(key=lambda node: node['procedureId'] != 'sq_spectroscopy')
            nodes[0]['dependsOn'] = []
            nodes[0]['parameterOverrides'] = {'span_hz': _stability['spanHz'], 'points': _stability['points']}
            nodes[1]['dependsOn'] = [nodes[0]['id']]
            edges = [{'from': nodes[0]['id'], 'to': nodes[1]['id']}]
        if seed:
            for node in nodes:
                node['parameters'] = deepcopy(seed['parameters'])
                node['parameterVersions'] = {key: 0 for key in seed['parameters']}
        from .experiment_setup import manual_selection
        manual_experiment = manual_selection(self, data, nodes, selected)
        if science:
            nodes = [node for node in nodes if node['id'] == manual_experiment['nodeId']]
            edges = []
        shot_budget = (30000000 if mode == 'real' and coverage in ('full', 'working_point') else 8000000) * len(selected)
        initial_budget = data.get('budget', {})
        ceilings = task_budget.CEILINGS
        accounting_parent = None
        accounting_only = initial_budget == {'mode':task_budget.MODE}
        if _budget_parent is not None:
            if _stability is not None or initial_budget not in ({},{'mode':task_budget.MODE}):
                raise APIError('INVALID_BUDGET_SCOPE', '子任务计量许可与请求预算不一致', 409)
            accounting_parent = task_budget.inherit_smart_budget(self, _rt, _budget_parent, data, _binding)
            accounting_only = True
            initial_budget = dict.fromkeys(ceilings)
        elif accounting_only:
            initial_budget = task_budget.limits_for_accounting(mode,selected,experiment,policy,_stability is not None,self.adapter_id)
        elif not isinstance(initial_budget,dict) or set(initial_budget)-set(ceilings) or any(type(v) is not int or not 1 <= v <= ceilings[k] for k,v in initial_budget.items()):
            raise APIError('INVALID_BUDGET','初始预算必须为范围内的正整数')
        shot_budget = initial_budget.get('maxTotalShots',shot_budget)
        node_attempts = initial_budget.get('maxAttemptsPerNode',3)
        workflow_attempts = initial_budget.get('maxWorkflowAttempts',40)
        wf = {'id': ident('wf'), 'revision': 0, 'mode': mode, 'name': name, 'machine': '八号机',
              'target': '、'.join(selected), 'resourceIds': selected, 'qubits': len(selected),
              'experiment': experiment, 'coverage': coverage, 'type': data.get('type', 'adaptive'), 'policy': policy,
              'scenario': 'normal', 'status': 'draft', 'engineState': 'draft', 'progress': 0,
              'owner': '现场操作员', 'ownerId': 'local-operator', 'createdAt': now(),
              'elapsed': 0, 'firstAcquisitionAt': None, 'finishedAt': None,
              'iterations': 0, 'approved': False, 'events': [], 'nodes': nodes, 'edges': edges,
              'blueprintVersion': 'science-manual/3' if science else WORKING_POINT_VERSION if seed else VERSION,
              'gates': deepcopy(science_gates if science else GATES),
              'gatesVersion': gates_version if science else 'diagnostic-gates/1',
              'adapterId': self.adapter_id, 'adapterProfileHash': self.adapter_profile_hash,
              'maxAttemptsPerNode': node_attempts, 'maxWorkflowAttempts': workflow_attempts, 'hardwareConfirmed': False,
              'maxTotalShots': shot_budget, 'reservedShots': 0,
              'budget': {'maxTotalShots': shot_budget, 'maxAttemptsPerNode': node_attempts, 'maxWorkflowAttempts': workflow_attempts,
                         'resourceCount': len(selected), 'basis': 'working-point-pulse-30M-per-resource' if seed else 'full-single-qubit-30M-per-resource' if coverage == 'full' else 'main-8M-per-resource'},
              'cancelRequested': None, 'processedRecordIds': [], 'workflowComplete': False}
        if accounting_only:
            wf['budget'].update(mode=task_budget.MODE, basis='operator_selected_accounting')
            if accounting_parent:
                wf['budget'].update(basis='smart_parent_accounting', parent=accounting_parent)
        if analysis_lineage:
            wf['analysisLineage'] = analysis_lineage
        if manual_experiment:
            wf.update(manualExperiment=manual_experiment, currentNodeId=manual_experiment['nodeId'])
        if science:
            wf.update(executionProfile='science-shape-repeat5' if self.profile.get('scienceRepeat5Policy') else 'science-flux' if self.profile.get('scienceFluxPolicy') else 'science-v3-irb' if self.profile.get('scienceIRBPolicy') else 'science-v3', gatesSha256=digest(wf['gates']),
                      completionScope={'selectedNodeCount': 1, 'meaning': '仅指所选科学测项首测、独立复测并经人工确认'})
        if _stability is not None:
            wf.update(stabilityBinding=deepcopy(_stability), initialParameters=deepcopy(_stability['parameters']),
                      completionScope={'selectedNodeCount': 2, 'full13Fresh': False,
                          'omittedProcedures': [], 'meaning': '本轮定频与 T1；门与读出使用所关联的既有标定'})
        if seed:
            wf['seedReference'] = seed
            wf['completionScope'] = {'selectedNodeCount': len(nodes), 'omittedProcedures': list(WORKING_POINT_OMITTED),
                'full13Fresh': False, 'meaning': '仅指所选10项脉冲测量完成本轮独立验收；不代表原13项全流程均已新测。'}
        if _binding:
            wf.update(configurationBinding=deepcopy(_binding), campaignId=_campaign_id,
                      initializationSource=deepcopy(_binding.get('source', {})))
            wf.update({key:_binding[key] for key in ('chipVersionId','deploymentVersionId') if _binding.get(key)})
            if _binding.get('biasDeploymentVersionId'):wf['biasDeploymentVersionId']=_binding['biasDeploymentVersionId']
            if _binding.get('observedResourceBinding'):
                wf['observedResourceBinding'] = deepcopy(_binding['observedResourceBinding'])
            wf['machine'] = self.profile.get('machine', '八号机')
        with (nullcontext(_rt) if _rt is not None else self.store.transaction()) as rt:
            for rule in self.experience.published_rules(rt):
                scope = rule['scope']
                if _binding and scope.get('chipVersionId') != _binding['chipVersionId']:
                    continue
                for node in nodes:
                    if scope.get('resourceId') == node['resourceId'] and scope.get('ownerId') == node['ownerId'] and scope.get('procedureId', node['procedureId']) == node['procedureId']:
                        node['rules'].append(deepcopy(rule))
            if accounting_only:
                registered_budget = AccountingBudget(shots=500, timeout_s=3600)
                parent_id = (wf['budget'].get('parent') or {}).get('parentWorkflowId')
                rt.db.execute('INSERT INTO platform_accounting_workflows VALUES(?,?)', (wf['id'], parent_id))
            else:
                registered_budget = Budget(shots=500, max_attempts=node_attempts, timeout_s=3600, max_total_shots=shot_budget)
            for node in nodes:
                reads, writes = PARAMETERS.get(node['procedureId'], ([], []))
                prefix = wf['id'] + ':' + node['resourceId'] + ':'
                norm = lambda k: prefix + PARAMETER_ALIASES.get(k, k)
                rt.register(StepSpec(wf['id'] + '/' + node['id'],
                                     depends_on=tuple(wf['id'] + '/' + n for n in node['dependsOn']),
                                     reads=tuple(norm(k) for k in reads), writes=tuple(norm(k) for k in writes),
                                     resources=(device_key(self.profile),), budget=registered_budget,
                                     criteria=deepcopy(wf['gates'].get(node['procedureId'], {}))))
            self.store.save(rt, wf, 'created', {'message': ('历史回放' if mode == 'replay' else '真机测量') + '任务已创建'})
        return wf

    def _command(self, rt, wf, payload, kind, *, stop_intent=False):
        op = payload.get('operationId')
        if not isinstance(op, str) or not 1 <= len(op) <= 256:
            raise APIError('OPERATION_ID_REQUIRED', '写操作必须包含唯一 operationId')
        request_hash = digest({'workflowId': wf['id'], 'kind': kind, 'body': payload})
        previous = rt.db.execute('SELECT * FROM platform_commands WHERE id=?', (op,)).fetchone()
        if previous:
            if previous['request_hash'] != request_hash:
                raise APIError('IDEMPOTENCY_CONFLICT', '同一操作编号不能提交不同请求', 409)
            return json.loads(previous['response']), request_hash
        expected = payload.get('expectedRevision')
        # Progress and a stop click can cross in flight. A workflow-scoped stop
        # still applies to that workflow; parameter edits and execution remain CAS.
        older_stop = (stop_intent and isinstance(expected, int) and not isinstance(expected, bool)
                      and 1 <= expected < wf['revision'])
        invalid_stop = stop_intent and (not isinstance(expected, int) or isinstance(expected, bool) or expected < 1)
        if invalid_stop or (expected != wf['revision'] and not older_stop):
            raise APIError('STALE_REVISION', '任务已更新，请刷新后按当前状态操作', 409)
        # Stops apply to this workflow, even if the displayed attempt or current
        # node has advanced since the click. All other actions retain that CAS.
        if payload.get('nodeId') and not stop_intent:
            node = self._node(wf, payload['nodeId'])
            if payload.get('attemptId') and payload['attemptId'] != node.get('latestAttemptId'):
                raise APIError('STALE_ATTEMPT', '该操作对应的尝试已不是当前尝试', 409)
        return None, request_hash

    def _rb_diagnostic_proof(self, rt, wf):
        self._assert_adapter_binding(wf)
        self._assert_supplemental_snapshot(wf)
        target = next((n for n in wf['nodes'] if n['procedureId'] == 'sq_rb'), None)
        rb_require(target is not None, 'RB_DIAGNOSTIC_SCOPE', '任务没有原登记RB节点')
        parameters, versions = self._parameters(rt, wf, target)
        kind = type(self.adapter)
        proof = rb_diagnostic_proof(checked_path(self.adapter.root), rt.db, wf, parameters, versions,
            implementation_id=kind.__module__ + '.' + kind.__qualname__)
        return proof, target, parameters, versions

    def _rb_diagnostic_admission(self, rt, wf):
        rb_require(wf['status'] in ('paused', 'attention') and wf.get('engineState') not in ('deciding', 'decision_queued')
                   and not wf.get('cancelRequested'), 'RB_DIAGNOSTIC_CHECKPOINT', '请在无活动采集和决策的检查点准备RB诊断')
        group = device_key(self.profile)
        rb_require(not rt.db.execute('SELECT 1 FROM platform_leases WHERE workflow_id=? OR resource=?', (wf['id'], group)).fetchone()
                   and not rt.db.execute("SELECT 1 FROM platform_effects WHERE state IN ('pending','unknown')").fetchone(),
                   'RECONCILIATION_REQUIRED', '采集占用或未知回执尚未清空')
        old = wf.get('rbDiagnostic') or {}
        rb_require(old.get('state') not in ('queued', 'running'), 'RB_DIAGNOSTIC_ACTIVE', '已有单次RB诊断许可')
        proof, target, parameters, versions = self._rb_diagnostic_proof(rt, wf)
        rb_require(target.get('engineState') != 'verified', 'RB_DIAGNOSTIC_ALREADY_VERIFIED', '当前RB已经独立验证，不重复创建诊断许可')
        admit_backtrack(wf, target)
        budget = self._validate_plan(wf, target, parameters, versions, 'rb-diagnostic-inspection', joint_budget=True, rt=rt)
        return proof, budget

    def inspect_rb_diagnostic(self, workflow_id):
        scoped = self.for_workflow(workflow_id)
        if scoped is not self:
            return scoped.inspect_rb_diagnostic(workflow_id)
        with read_chat_snapshot(self.store.path) as db:
            rt = types.SimpleNamespace(db=db, get_parameters=lambda: {
                r['name']: {'value': json.loads(r['value']), 'revision': r['revision']}
                for r in db.execute('SELECT * FROM parameters')})
            wf = self.get(workflow_id, rt)
            output = {'schema': 'qcal-rb-diagnostic-inspection/1', 'workflowId': workflow_id,
                      'revision': wf['revision'], 'policyVersion': RB_DIAGNOSTIC_VERSION,
                      'eligible': False, 'reasons': [], 'marker': deepcopy(wf.get('rbDiagnostic'))}
            try:
                proof, budget = self._rb_diagnostic_admission(rt, wf)
                output.update(eligible=True, budgetLookahead=budget, **{k: proof[k] for k in
                    ('sourceNodeId', 'sourceAttemptId', 'targetNodeId', 'sourceProofSha256', 'physicalParameterVersions')})
            except APIError as exc:
                output['reasons'] = [{'code': exc.code, 'message': exc.message}]
            return output

    def _queue_rb_diagnostic(self, rt, wf, payload):
        rb_require(set(payload) == {'action', 'operationId', 'expectedRevision', 'nodeId', 'attemptId', 'policyVersion', 'sourceProofSha256'}
                   and payload.get('policyVersion') == RB_DIAGNOSTIC_VERSION,
                   'RB_DIAGNOSTIC_CONTRACT', 'RB诊断请求须使用固定版本及完整来源字段')
        proof, budget = self._rb_diagnostic_admission(rt, wf)
        rb_require(payload.get('nodeId') == proof['sourceNodeId'] and payload.get('attemptId') == proof['sourceAttemptId']
                   and payload.get('sourceProofSha256') == proof['sourceProofSha256'],
                   'RB_DIAGNOSTIC_SOURCE_CHANGED', '来源或参数已变化，请重新检查诊断条件')
        if wf.get('rbDiagnostic'):
            wf.setdefault('rbDiagnosticHistory', []).append(deepcopy(wf['rbDiagnostic']))
        target = self._node(wf, proof['targetNodeId'])
        wf['rbDiagnostic'] = {**proof, 'id': ident('rb-diagnostic'), 'commandOperationId': payload['operationId'],
            'state': 'queued', 'attemptId': None, 'targetAttemptBefore': target['attempt'],
            'requestedAt': now(), 'budgetLookahead': budget, 'publication': 'disabled_diagnostic_only'}
        wf.update(status='queued', engineState='diagnostic_queued', currentNodeId=target['id'], workflowComplete=False)
        self._record_decision(rt, wf, proof['sourceNodeId'], {'action': 'queue_rb_diagnostic',
            'reason': '保留CPMG未通过与原图依赖，仅授权一套RB首测及独立复测；不发布完整校准',
            'evidenceIds': [], 'ruleId': RB_DIAGNOSTIC_VERSION, 'diagnosticId': wf['rbDiagnostic']['id'],
            'sourceAttemptId': proof['sourceAttemptId'], 'sourceProofSha256': proof['sourceProofSha256'],
            'budgetLookahead': budget}, 'operator')

    def _assert_rb_diagnostic_dispatch(self, rt, wf, node, branch_id, *, attempt_id=None, allow_cancel=False):
        marker = wf.get('rbDiagnostic') or {}
        rb_require(marker.get('id') == branch_id and marker.get('policyVersion') == RB_DIAGNOSTIC_VERSION
                   and marker.get('targetNodeId') == node['id'] and (allow_cancel or not wf.get('cancelRequested')),
                   'RB_DIAGNOSTIC_SCOPE', '单次诊断许可身份或状态不一致')
        if attempt_id is None:
            rb_require(marker.get('state') == 'queued' and marker.get('attemptId') is None
                       and node['attempt'] == marker.get('targetAttemptBefore'),
                       'RB_DIAGNOSTIC_CONSUMED', '本次诊断许可已消费或被改变')
            admit_backtrack(wf, node)
        else:
            rb_require(marker.get('state') == 'running' and marker.get('attemptId') == attempt_id
                       and node.get('latestAttemptId') == attempt_id,
                       'RB_DIAGNOSTIC_CONSUMED', '复测不属于本次单次诊断许可')
        proof, _, _, _ = self._rb_diagnostic_proof(rt, wf)
        rb_require(proof['sourceProofSha256'] == marker.get('sourceProofSha256'),
                   'RB_DIAGNOSTIC_SOURCE_CHANGED', '参数、图、源码或来源已改变，单次诊断许可失效')
        return marker

    def _run_rb_diagnostic(self, workflow_id, branch_id):
        try:
            wf = self.get(workflow_id)
            marker = wf['rbDiagnostic']
            self._execute_node(workflow_id, marker['targetNodeId'], diagnostic_branch_id=branch_id)
        except APIError as exc:
            with self.store.transaction() as rt:
                current = self.get(workflow_id, rt)
                marker = current.get('rbDiagnostic') or {}
                if marker.get('id') == branch_id:
                    stopping = (current['status'] in ('paused', 'aborted') or current.get('cancelRequested')
                                or current.get('engineState') == 'needs_reconciliation')
                    marker.update(state='interrupted' if stopping else 'invalidated', errorCode=exc.code)
                    if not stopping:
                        current.update(status='attention', engineState='diagnostic_invalidated')
                    self.store.save(rt, current, 'rb_diagnostic_interrupted' if stopping else 'rb_diagnostic_invalidated',
                                    {'message': exc.message, 'errorCode': exc.code})
        finally:
            with self.store.transaction() as rt:
                current = self.get(workflow_id, rt)
                marker = current.get('rbDiagnostic') or {}
                if marker.get('id') != branch_id:
                    return
                node = self._node(current, marker['targetNodeId'])
                uncertain = self._has_lease(rt, workflow_id) or rt.db.execute(
                    "SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone()
                passed = (node.get('latestAttemptId') == marker.get('attemptId') and node.get('engineState') == 'verified'
                          and node.get('result', {}).get('quality', {}).get('status') == 'passed')
                interrupted = (current['status'] in ('paused', 'aborted') or uncertain or current.get('cancelRequested')
                               or current.get('engineState') == 'needs_reconciliation')
                invalidated = marker.get('state') == 'invalidated'
                marker.update(state='interrupted' if interrupted else 'invalidated' if invalidated else 'completed' if passed else 'failed', finishedAt=now(),
                              resultStatus='passed' if passed else node.get('result', {}).get('quality', {}).get('status', 'unverified'))
                current['workflowComplete'] = False
                if not interrupted:
                    current.update(status='attention', engineState='diagnostic_invalidated' if invalidated else 'diagnostic_complete' if passed else 'diagnostic_failed')
                current['resultSummary'] = {'verifiedNodes': sum(n['engineState'] == 'verified' for n in current['nodes']),
                    'totalNodes': len(current['nodes']), 'coverage': current['coverage'], 'workflowComplete': False,
                    'meaning': 'RB诊断已结束；CPMG原结果与原流程依赖保留，原10项流程尚未全部通过'}
                self._finish_timing(current)
                self.store.save(rt, current, 'rb_diagnostic_finished', {'message': current['resultSummary']['meaning'],
                    'diagnosticId': branch_id, 'attemptId': marker.get('attemptId'), 'state': marker['state']})

    @staticmethod
    def _rb_diagnostic_candidate_contract(result, stage):
        if result.get('candidateParameters', {}) != {}:
            result['quality']['status'] = 'failed'
            result['quality']['reasons'].append('RB诊断回执必须没有待提交校准参数；已保留采集但拒绝该候选')
            result['diagnosticContract'] = {'accepted': False, 'code': 'RB_DIAGNOSTIC_CANDIDATE_NOT_EMPTY',
                'stage': stage, 'rawCandidateParameters': deepcopy(result.get('candidateParameters'))}

    def action(self, workflow_id, payload, *, _rt=None):
        scoped = self.for_workflow(workflow_id, _rt)
        if scoped is not self:
            return scoped.action(workflow_id, payload, _rt=_rt)
        with (nullcontext(_rt) if _rt is not None else self.store.transaction()) as rt:
            wf = self.get(workflow_id, rt)
            action = payload.get('action')
            existing, hash_value = self._command(rt, wf, payload, 'action', stop_intent=action in ('pause', 'abort'))
            if existing:
                return existing
            if wf.get('configurationIssue') and action not in ('pause', 'abort'):
                raise APIError('CONFIGURATION_REQUIRED', '请补齐并发布配置后，为此比特建立新任务', 409)
            if wf['status'] in TERMINAL and action not in ('pause', 'abort'):
                raise APIError('TERMINAL_RUN', '已结束任务不能再次下发；请创建新任务', 409)
            if is_manual(wf) and action in ('start', 'resume', 'retry', 'queue_rb_diagnostic'):
                raise APIError('MANUAL_STEP_REQUIRED', '人工逐步任务须明确选择步骤并运行本步', 409)
            diagnostic = wf.get('rbDiagnostic') or {}
            if diagnostic.get('state') in ('queued', 'running') and action not in ('pause', 'abort', 'reconcile'):
                raise APIError('RB_DIAGNOSTIC_ACTIVE', '单次RB诊断期间仅允许暂停、终止或回执核查', 409)
            if (diagnostic and wf.get('currentNodeId') == diagnostic.get('targetNodeId')
                    and action in ('resume', 'retry', 'approve', 'decide', 'confirm_hardware')):
                raise APIError('RB_DIAGNOSTIC_CLOSED', 'RB诊断不通过普通继续操作推进；请显式重新检查诊断条件或回退原前置步骤', 409)
            if action in ('retry', 'backtrack'):
                target_id = payload.get('targetNodeId') if action == 'backtrack' else payload.get('nodeId')
                target = self._node(wf, target_id)
                if target['procedureId'] == 'sq_rb' and any(self._node(wf, d)['engineState'] != 'verified' for d in target['dependsOn']):
                    raise APIError('PREREQUISITES_MISSING', '普通RB操作必须满足原图全部依赖；诊断须使用独立版本化命令', 409)
            if action == 'run_step':
                self._queue_manual_step(rt, wf, payload)
            elif action == 'start':
                if wf['status'] != 'draft':
                    raise APIError('INVALID_STATE', '仅草稿可开始', 409)
                wf['status'], wf['engineState'] = 'queued', 'queued'
                if wf.get('configurationBinding'):
                    wf.update(hardwareConfirmed=True, hardwareConfirmedAt=now())
            elif action == 'queue_rb_diagnostic':
                self._queue_rb_diagnostic(rt, wf, payload)
            elif action in ('pause', 'abort'):
                if action == 'pause':
                    from .smart_main_checkpoint import remember_planning
                    remember_planning(self, rt, wf)
                before_stop = (wf['status'], wf['engineState'], wf.get('cancelRequested'))
                pending_effect = rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (wf['id'],)).fetchone()
                unknown_effect = rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state='unknown'", (wf['id'],)).fetchone()
                quarantined = rt.db.execute("SELECT 1 FROM platform_leases WHERE workflow_id=? AND state='quarantined'", (wf['id'],)).fetchone()
                # An abort is stronger than pause and must survive a second click.
                stop = 'abort' if action == 'abort' or wf.get('cancelRequested') == 'abort' else 'pause'
                if (wf['engineState'] == 'needs_reconciliation' or unknown_effect or quarantined
                        or (wf['status'] != 'running' and (self._has_lease(rt, wf['id']) or pending_effect))):
                    wf.update(status='attention', engineState='needs_reconciliation', cancelRequested=stop)
                elif wf['status'] == 'running':
                    wf.update(cancelRequested=stop, engineState='stopping_at_checkpoint' if stop == 'abort' else 'pausing_at_checkpoint')
                elif wf['status'] not in TERMINAL and not (action == 'pause' and wf['status'] in ('draft', 'paused')):
                    wf.update(status='aborted' if stop == 'abort' else 'paused',
                              engineState='aborted' if stop == 'abort' else 'paused', cancelRequested=None)
                if before_stop == (wf['status'], wf['engineState'], wf.get('cancelRequested')):
                    # A fresh stop operation against a stopped task is also a
                    # receipt, not a new experiment or an artificial revision.
                    rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], hash_value, dumps(wf)))
                    return wf
            elif action in ('resume', 'retry'):
                if wf['status'] not in ('paused', 'attention', 'failed'):
                    raise APIError('INVALID_STATE', '当前状态不能继续或重试', 409)
                if self._has_lease(rt, wf['id']):
                    raise APIError('RECONCILIATION_REQUIRED', '上次设备操作状态未知，先核查回执', 409)
                if action == 'retry':
                    node = self._node(wf, payload.get('nodeId'))
                    admission_node = next((n for n in wf['nodes'] if n['id'] == wf.get('currentNodeId')
                                           and n['engineState'] == wf['engineState'] and n.get('lastAdmissionError')), None)
                    if (wf['engineState'] in ('plan_rejected', 'budget_exhausted') and admission_node
                            and node['id'] != admission_node['id']):
                        raise APIError('ADMISSION_NODE_CHANGED', '请重试本次参数预检未通过的节点', 409)
                    if task_budget.exhausted(wf, 'maxAttemptsPerNode', node['attempt']):
                        raise APIError('ATTEMPT_BUDGET', '该节点已达到本轮重试预算', 409)
                    node['status'], node['engineState'] = 'pending', 'retry_queued'
                from .smart_main_checkpoint import resume_planning
                planning = action == 'resume' and resume_planning(self, rt, wf)
                if action == 'retry' and wf.get('smartRun'):
                    wf['smartRun'].pop('pausedPlanning', None)
                wf['status'], wf['engineState'], wf['cancelRequested'] = 'queued', 'decision_queued' if planning else 'queued', None
            elif action == 'decide':
                legacy_decision_error = self._legacy_decision_error(rt, wf, payload.get('nodeId'))
                if wf['mode'] != 'real' or wf['policy'] != 'autopilot' or (wf['engineState'] not in ('quality_attention', 'waiting_model', 'budget_updated') and not legacy_decision_error):
                    raise APIError('INVALID_STATE', '仅自主策略下待处理的质量结果或模型等待状态可重新请求决策', 409)
                if self._has_lease(rt, wf['id']) or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (wf['id'],)).fetchone():
                    raise APIError('RECONCILIATION_REQUIRED', '采集状态尚待核查，不能请求新决策', 409)
                node = self._node(wf, payload.get('nodeId'))
                if not node.get('result') or node['result'].get('stale'):
                    raise APIError('CURRENT_EVIDENCE_REQUIRED', '需要当前未失效的采集结果才能重新决策', 409)
                if wf['engineState'] == 'budget_updated' and (node['id'] != wf.get('currentNodeId')
                        or node['result'].get('quality', {}).get('status') not in ('failed', 'unverified')):
                    raise APIError('CURRENT_EVIDENCE_REQUIRED', '预算调整后仅可基于当前步骤未通过或待验证的结果重新决策', 409)
                wf['currentNodeId'] = node['id']
                wf['status'], wf['engineState'] = 'queued', 'decision_queued'
                node.update(status='attention', engineState='decision_queued', detail='等待基于当前实测结果重新请求模型决策')
                if legacy_decision_error:
                    self.store.save(rt, wf, 'legacy_model_decision_recovery', {
                        'message': '旧版本模型证据引用错误已进入重新决策；原始采集与尝试历史保持不变',
                        'nodeId': node['id'], 'attemptId': node.get('latestAttemptId'), 'errorCode': 'BAD_EVIDENCE'})
            elif action == 'approve':
                if is_manual(wf):
                    self._approve_manual_step(rt, wf, payload)
                else:
                    node = self._node(wf, payload.get('nodeId'))
                    if node.get('result', {}).get('quality', {}).get('status') != 'passed' or node.get('result', {}).get('stale'):
                        raise APIError('GATE_NOT_PASSED', '确认操作不能将未通过或未验证的指标变为合格', 409)
                    node['status'], node['engineState'] = 'success', 'verified'
                    wf['status'], wf['engineState'], wf['approved'] = 'queued', 'queued', True
            elif action == 'backtrack':
                if is_manual(wf):
                    self._manual_idle(rt, wf)
                if wf['status'] == 'running' or self._has_lease(rt, wf['id']):
                    raise APIError('RESOURCE_BUSY', '等待当前采集结束后再回退', 409)
                target = payload.get('targetNodeId')
                target_node = self._node(wf, target)
                if (is_manual(wf) and awaiting_acceptance(wf) and target != wf.get('currentNodeId')
                        and target not in self._ancestors(wf, self._node(wf))):
                    raise APIError('ACCEPTANCE_REQUIRED', '请确认当前结果，或明确回退当前步骤及其上游', 409)
                admit_backtrack(wf, target_node)
                if self._physical_adapter:
                    self._assert_adapter_binding(wf)
                    parameters, versions = self._parameters(rt, wf, target_node)
                    trial, _ = self._readout_retry_parameters(rt, wf, target_node, parameters, versions, {})
                    self._validate_plan(wf, target_node, trial, versions,
                        ident('operator-backtrack-plan'), joint_budget=True, rt=rt)
                origin = payload.get('nodeId') or wf.get('currentNodeId') or target
                affected = self._invalidate(wf, target, include_target=True)
                wf['currentNodeId'], wf['status'], wf['engineState'] = target, 'queued', 'backtrack_queued'
                if is_manual(wf):
                    wf.update(status='paused', engineState='manual_ready', approved=False, workflowComplete=False)
                self._record_decision(rt, wf, origin, {'action': 'backtrack', 'reason': '操作员请求回到前置步骤', 'evidenceIds': [],
                                                     'targetNodeId': target, 'invalidatedNodes': affected}, 'operator')
            elif action == 'confirm_hardware':
                if wf['mode'] != 'real' or wf['engineState'] != 'waiting_hardware':
                    raise APIError('INVALID_STATE', '当前任务没有等待硬件交接', 409)
                ownership = self.hardware_ownership(refresh=True)
                if not ownership['verified'] or ownership['owners']:
                    raise OwnershipRefusal(wf, next((n for n in wf['nodes'] if n['id']==wf.get('currentNodeId')), {}), ownership, 'confirmation')
                wf['hardwareConfirmed'], wf['hardwareConfirmedAt'] = True, now()
                wf.pop('sharedDeviceFault', None)
                wf['status'], wf['engineState'] = 'queued', 'queued'
                if is_manual(wf):
                    wf.update(status='paused', engineState='manual_ready')
                    self._clear_confirmed_hardware_wait(wf)
                    if wf.get('manualStep'):
                        wf['manualStep'].update(state='interrupted', finishedAt=now())
            elif action == 'reconcile':
                if wf['engineState'] != 'needs_reconciliation':
                    raise APIError('INVALID_STATE', '当前任务不需要执行回执核查', 409)
                unknown = list(rt.db.execute("SELECT * FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)))
                remaining = []
                for effect in unknown:
                    source = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=?',
                        (effect['attempt_id'],workflow_id)).fetchone()
                    execution = self.for_attempt(workflow_id,json.loads(source[0]),rt) if source else self
                    if json.loads(effect['request']).get('executionBinding'):
                        from .attempt_execution import validate_request
                        execution=validate_request(self,rt,json.loads(effect['request']))
                    receipt = execution.adapter.reconcile(effect['operation_id'])
                    if receipt.get('status') == 'completed' and isinstance(receipt.get('result'), dict):
                        recovered = deepcopy(receipt['result'])
                        recovered['receiptQuality'] = recovered.get('quality')
                        recovered['quality'] = {'status': 'unverified', 'reasons': ['已找回采集回执，尚未重新完成本轮独立复测和冻结规则验收']}
                        recovered['artifacts'] = [self._artifact(rt, a) for a in recovered.get('artifacts', [])]
                        rt.db.execute("UPDATE platform_effects SET state='completed',result=? WHERE operation_id=?", (dumps(recovered), effect['operation_id']))
                        record_effect(rt.db, effect['operation_id'], self.root)
                        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', (effect['attempt_id'],)).fetchone()
                        if row:
                            attempt = json.loads(row[0])
                            selection = self._completed_selection_for_reconciliation(rt, workflow_id, attempt, effect)
                            if selection is not None:
                                recovered['selectionResult'] = selection
                            attempt.update(result=recovered, state='reconciled_unverified', endedAt=now())
                            self.store.attempt(rt, workflow_id, attempt)
                            node = self._node(wf, attempt['nodeId'])
                            node.update(result=recovered, status='attention', engineState='reconciled_unverified',
                                        detail='已找回完成回执；尚未重新满足本轮独立验收')
                    elif receipt.get('status') == 'not_started':
                        rt.db.execute("UPDATE platform_effects SET state='not_started',result=? WHERE operation_id=?", (dumps(receipt), effect['operation_id']))
                    elif receipt.get('status') in ('cancelled', 'failed_after_shutdown') and receipt.get('shutdownConfirmed') is True:
                        receipt['partialArtifacts'] = [self._artifact(rt, artifact) for artifact in receipt.get('partialArtifacts', [])]
                        rt.db.execute('UPDATE platform_effects SET state=?,result=? WHERE operation_id=?', (receipt['status'], dumps(receipt), effect['operation_id']))
                        record_effect(rt.db, effect['operation_id'], self.root)
                        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', (effect['attempt_id'],)).fetchone()
                        if row:
                            attempt = json.loads(row[0])
                            selection = self._completed_selection_for_reconciliation(rt, workflow_id, attempt, effect)
                            partial_result = self._partial_result(receipt['partialArtifacts'], [receipt], selection_retained=selection is not None)
                            if selection is not None:
                                partial_result['selectionResult'] = selection
                            attempt.update(state=receipt['status'], endedAt=now(), result=partial_result)
                            self.store.attempt(rt, workflow_id, attempt)
                            self._node(wf, attempt['nodeId']).update(result=attempt['result'], status='attention', engineState=receipt['status'], detail=partial_result['executionSummary']['message'])
                    else:
                        remaining.append(effect['operation_id'])
                if remaining:
                    wf['status'], wf['engineState'] = 'attention', 'needs_reconciliation'
                else:
                    rt.db.execute('DELETE FROM platform_leases WHERE workflow_id=?', (workflow_id,))
                    if wf.get('cancelRequested'):
                        self._finish_cancel_locked(rt, wf)
                    else:
                        wf['status'], wf['engineState'] = 'paused', 'reconciled'
            else:
                raise APIError('INVALID_ACTION', '未知操作')
            if diagnostic.get('state') in ('queued', 'running') and wf['status'] in ('paused', 'aborted'):
                diagnostic.update(state='interrupted', finishedAt=now(), resultStatus='unverified')
            if action in ('pause', 'abort') and wf['status'] in ('paused', 'aborted') and wf.get('manualStep', {}).get('state') == 'queued':
                wf['manualStep'].update(state='interrupted', finishedAt=now())
            if action == 'pause' and is_manual(wf) and awaiting_acceptance(wf):
                wf['engineState'] = 'awaiting_acceptance'
            self._finish_timing(wf)
            stop_message = None
            if action in ('pause', 'abort'):
                if wf.get('cancelRequested'):
                    stop_message = '已收到' + ('终止' if wf['cancelRequested'] == 'abort' else '暂停') + '请求'
                    stop_message += '，设备停止回执尚待核查' if wf['engineState'] == 'needs_reconciliation' else '，等待采集停止检查点'
                else:
                    stop_message = '任务已终止' if wf['status'] == 'aborted' else '任务已暂停'
            action_event = {'message': stop_message or '操作：' + action}
            if wf['status'] == 'queued':
                # Queue order follows this authorization, not the age of its task.
                wf['queueEnteredAt'] = now()
            if wf.get('sharedDeviceFault') and wf['status'] == 'queued' and action in ('resume', 'retry', 'run_step'):
                # This is a new explicit retry permit, not a claim that the
                # hardware is healthy. Original fault events/receipts remain.
                wf.pop('sharedDeviceFault', None)
                action_event.update(sharedDeviceFaultRetryAuthorized=True, operationId=payload['operationId'],
                    message='已授权重试共享设备故障任务；执行前仍须通过设备预检和独占检查')
            self.store.save(rt, wf, action, action_event)
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], hash_value, dumps(wf)))
        self.wake.set()
        return wf

    def withdraw_readout_candidate(self, workflow_id, payload):
        """Explicit operator restoration, never an experimental calibration commit."""
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, request_hash = self._command(rt, wf, payload, 'readout_withdrawal')
            if existing:
                return existing
            if set(payload) != {'operationId', 'expectedRevision', 'revalidationId', 'sourceAttemptId', 'reason'}:
                raise APIError('INVALID_READOUT_WITHDRAWAL', '撤回请求只接受操作编号、版本、重建编号、来源尝试及原因')
            reason = payload.get('reason')
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
                raise APIError('INVALID_READOUT_WITHDRAWAL', '请填写 1–2000 字的撤回原因')
            if wf['mode'] != 'real' or wf['status'] not in ('paused', 'attention') or wf.get('cancelRequested'):
                raise APIError('INVALID_STATE', '只能在真机任务已暂停或待处理的检查点撤回候选', 409)
            if (self._has_lease(rt, workflow_id) or rt.db.execute(
                    "SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone()):
                raise APIError('RECONCILIATION_REQUIRED', '采集租约或未知回执尚未释放，不能撤回读出候选', 409)
            self._assert_adapter_binding(wf)
            markers = [m for m in wf.get('readoutRevalidations', {}).values()
                       if m.get('id') == payload['revalidationId']]
            if (len(markers) != 1 or markers[0].get('state') not in ('active', 'fixed_failed')
                    or markers[0].get('sourceAttemptId') != payload['sourceAttemptId']
                    or markers[0].get('origin') == 'operator_withdrawal'):
                raise APIError('READOUT_WITHDRAWAL_MARKER', '读出重建或原候选来源已变化，不能重复撤回', 409)
            marker = markers[0]
            optimizer = self._node(wf, marker['optimizerNodeId'])
            if (optimizer.get('ownerId') != 's05_readout_optimization' or optimizer.get('procedureId') != 'ro_optimize'
                    or optimizer.get('resourceId') != marker['resourceId']
                    or optimizer['id'] != marker['resourceId'] + ':s05_readout_optimization'):
                raise APIError('READOUT_WITHDRAWAL_OWNER', '读出优化归属不匹配', 409)
            # Verify the immutable owner contract and the complete dependency graph.
            for node in wf['nodes']:
                spec = rt.steps.get(workflow_id + '/' + node['id'])
                reads, writes = PARAMETERS.get(node['procedureId'], ([], []))
                prefix = workflow_id + ':' + node['resourceId'] + ':'
                norm = lambda keys: tuple(prefix + PARAMETER_ALIASES.get(k, k) for k in keys)
                if (spec is None or tuple(spec.depends_on) != tuple(workflow_id + '/' + d for d in node['dependsOn'])
                        or tuple(spec.reads) != norm(reads) or tuple(spec.writes) != norm(writes)
                        or spec.criteria != wf['gates'].get(node['procedureId'], {})):
                    raise APIError('READOUT_WITHDRAWAL_OWNER', '已登记步骤的读写或依赖契约不匹配', 409)
            parameters, versions = self._parameters(rt, wf, {**optimizer, 'parameterOverrides': {}})
            source = restoration_source(rt.db, self.adapter, wf, marker, parameters, versions)
            before = {'values': {k: parameters[k] for k in READOUT_KEYS},
                      'parameterVersions': {k: versions[k] for k in READOUT_KEYS}}
            after = {'values': deepcopy(source['values']),
                     'parameterVersions': {k: versions[k] + 1 for k in READOUT_KEYS}}
            prefix = workflow_id + ':' + marker['resourceId'] + ':'
            for key in READOUT_KEYS:
                # Both readout components get a new version, including an equal
                # frequency: this is a new unvalidated working-point restoration.
                rt.db.execute('INSERT OR REPLACE INTO parameters VALUES(?,?,?)',
                              (prefix + key, dumps(after['values'][key]), after['parameterVersions'][key]))
                optimizer['parameterVersions'][key] = after['parameterVersions'][key]
            rt._invalidate([prefix + k for k in READOUT_KEYS])
            marker.update(state='withdrawn', endedAt=now(), withdrawalOperationId=payload['operationId'])
            self._start_readout_revalidation(rt, wf, optimizer)
            restored = wf['readoutRevalidations'][optimizer['resourceId']]
            restored.update(origin='operator_withdrawal', validationStatus='unverified',
                withdrawalOperationId=payload['operationId'], sourceAttemptId=payload['sourceAttemptId'],
                previousRevalidationId=payload['revalidationId'])
            for node in wf['nodes']:
                if node['id'] in restored['lockedNodeIds']:
                    # Current configuration display only. Historical result and
                    # attempt parameter snapshots retain their acquired values.
                    node.setdefault('parameters', {}).update(after['values'])
                    node.setdefault('parameterVersions', {}).update(after['parameterVersions'])
            # _start_readout_revalidation invalidates the entire pulse chain and
            # fixes subsequent ROopt to the restored value. Never auto-start it.
            wf.update(status='paused', engineState='readout_withdrawal_pending', cancelRequested=None,
                      workflowComplete=False, approved=False)
            record = {'ruleVersion': READOUT_WITHDRAWAL_VERSION, 'operationId': payload['operationId'],
                'source': 'operator_withdrawal', 'validationStatus': 'unverified', 'reason': reason.strip(),
                'createdAt': now(), 'before': before, 'after': after, 'sourceEvidence': source['sourceEvidence'],
                'invalidatedNodes': deepcopy(restored['lockedNodeIds']), 'revalidationId': restored['id'],
                'previousRevalidationId': payload['revalidationId'],
                'meaning': '恢复历史读出参数作为待复测工作点；不恢复任何旧验收，不启动采集。'}
            wf.setdefault('readoutWithdrawals', []).append(deepcopy(record))
            wf['lastReadoutWithdrawal'] = record
            self._record_decision(rt, wf, optimizer['id'], {'action': 'operator_withdrawal',
                'reason': record['meaning'] + ' ' + reason.strip(), 'evidenceIds': [],
                'sourceEvidence': source['sourceEvidence'], 'parameterChanges': {'before': before, 'after': after},
                'invalidatedNodes': record['invalidatedNodes'], 'ruleId': READOUT_WITHDRAWAL_VERSION}, 'operator')
            self.store.save(rt, wf, 'readout_candidate_withdrawn', {**record,
                'message': '已撤回读出候选并恢复原基准；全部受影响校准待复测，保持暂停'})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], request_hash, dumps(wf)))
        return wf

    def set_policy(self, workflow_id, payload):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, hash_value = self._command(rt, wf, payload, 'policy')
            if existing:
                return existing
            if payload.get('policy') not in ('strict', 'threshold', 'autopilot'):
                raise APIError('INVALID_POLICY', '未知策略')
            if task_budget.only_accounting(wf) and payload['policy'] != wf['policy']:
                raise APIError('INVALID_BUDGET_SCOPE', '仅记账任务保留创建时的运行策略', 409)
            if wf['status'] not in ('draft', 'paused', 'attention'):
                raise APIError('INVALID_STATE', '请在采集检查点暂停后更改运行策略', 409)
            if self._has_lease(rt, wf['id']) or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (wf['id'],)).fetchone():
                raise APIError('RECONCILIATION_REQUIRED', '存在活动或未知采集，先核查设备回执', 409)
            if awaiting_acceptance(wf) and payload['policy'] != wf['policy']:
                raise APIError('ACCEPTANCE_REQUIRED', '当前结果仍待确认，不能通过切换策略绕过验收', 409)
            wf['policy'] = payload['policy']
            self.store.save(rt, wf, 'policy_changed', {'message': '运行策略更新为 ' + wf['policy']})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], hash_value, dumps(wf)))
        return wf

    def set_budget(self, workflow_id, payload):
        ceilings = task_budget.CEILINGS
        try:
            dumps(payload)
        except (ValueError, TypeError):
            raise APIError('INVALID_BUDGET', '预算请求必须为有限的 JSON 值') from None
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, request_hash = self._command(rt, wf, payload, 'budget')
            if existing:
                return existing
            if wf['mode'] != 'real' or wf['status'] not in ('draft', 'paused', 'attention'):
                raise APIError('INVALID_STATE', '只能在真机任务草稿、暂停或待处理检查点调整预算', 409)
            pending = rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone()
            if self._has_lease(rt, workflow_id) or pending:
                raise APIError('RECONCILIATION_REQUIRED', '采集租约或执行回执尚未确认，不能调整预算', 409)
            if set(payload) - {'operationId', 'expectedRevision', 'budget', 'reason'}:
                raise APIError('INVALID_BUDGET', '预算请求包含不允许的字段')
            proposed, reason = payload.get('budget'), payload.get('reason')
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
                raise APIError('INVALID_BUDGET_REASON', '请填写 1–2000 字的预算调整原因')
            previous = {key: wf[key] for key in ceilings}
            updated = task_budget.revised_limits(wf, proposed)
            wf.update(updated)
            wf.setdefault('budget', {}).update(updated)
            if proposed == {'mode':task_budget.MODE}:
                wf['budget'].update(mode=task_budget.MODE, basis='operator_selected_accounting')
                rt.db.execute('INSERT OR IGNORE INTO platform_accounting_workflows VALUES(?,NULL)', (workflow_id,))
                shot_accounting.import_existing(rt.db, wf, self.root)
            update = {'operationId': payload['operationId'], 'source': 'operator', 'before': previous,
                      'after': updated, 'reason': reason.strip(), 'ceilings': ceilings, 'updatedAt': now()}
            wf['lastBudgetUpdate'] = deepcopy(update)
            if wf['engineState'] == 'budget_exhausted':
                wf['engineState'] = 'budget_updated'
                if wf.get('currentNodeId'):
                    node = self._node(wf)
                    if node['engineState'] == 'budget_exhausted':
                        node.update(engineState='budget_updated', detail='操作员已调整预算；等待明确继续')
            self.store.save(rt, wf, 'budget_updated', {**update, 'message': '操作员调整任务预算：' + reason.strip() + '；未自动启动采集'})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], request_hash, dumps(wf)))
        return wf

    def _loaded_supplemental_module(self):
        # Compile exactly the bytes whose hash is frozen, independent of any
        # older sys.modules cache. This fixed source path is not user input.
        path = Path(__file__).with_name('supplemental_quality.py')
        source = path.read_bytes()
        source_hash = hashlib.sha256(source).hexdigest()
        cached = getattr(self, '_supplemental_loaded_module', None)
        if cached is not None:
            if cached._qcal_source_sha256 != source_hash:
                raise APIError('SUPPLEMENTAL_SOURCE_CHANGED', '补充验收源码已变化；请在检查点重新加载服务，不能混用已加载版本', 409)
            return cached
        module = types.ModuleType('backend._supplemental_quality_' + source_hash[:16])
        module.__file__ = str(path)
        exec(compile(source, str(path), 'exec'), module.__dict__)
        module._qcal_source_sha256 = source_hash
        self._supplemental_loaded_module = module
        return module

    def _supplemental_sources(self):
        if getattr(self, '_execution_base', None) is not None:
            # The extension verifies and executes the exact frozen v3 analysis;
            # the workflow's pre-existing quality policy remains that version.
            return self._execution_base._supplemental_sources()
        module = self._loaded_supplemental_module()
        root = checked_path(getattr(self.adapter, 'root', self.root))
        manifest_name, runtime_name = self.profile.get('sourceManifest'), self.profile.get('runtimeRoot')
        if not isinstance(manifest_name, str) or not isinstance(runtime_name, str):
            raise APIError('SUPPLEMENTAL_SOURCE_UNAVAILABLE', '当前执行器没有可冻结的原生分析源码清单')
        manifest = checked_path(root / manifest_name, parent=root)
        analysis_name = (self.profile.get('analysisSource') if self.profile.get('id') == 'arclight-private-f8-q11-v3'
                         else runtime_name + '/platform_analysis.py')
        if not isinstance(analysis_name, str):
            raise APIError('SUPPLEMENTAL_SOURCE_UNAVAILABLE', '当前版本缺少明确的原生分析来源')
        analysis_source = checked_path(root / analysis_name, parent=root)
        records = json.loads(manifest.read_text(encoding='utf-8'))
        entries = [entry for entry in records if isinstance(entry, dict) and isinstance(entry.get('path'), str)
                   and entry['path'].replace('\\', '/').endswith('/platform_analysis.py')
                   and checked_path(root / entry['path'], parent=root) == analysis_source]
        if len(entries) != 1 or file_hash(analysis_source) != entries[0].get('sha256'):
            raise APIError('SUPPLEMENTAL_SOURCE_MISMATCH', '原生 platform_analysis.py 不匹配冻结清单')
        return module, {'moduleSha256': module._qcal_source_sha256,
                        'sourceManifestSha256': file_hash(manifest),
                        'nativeAnalysisSha256': entries[0]['sha256']}

    def install_supplemental_acceptance(self, workflow_id, payload):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, request_hash = self._command(rt, wf, payload, 'supplemental_acceptance')
            if existing:
                return existing
            if set(payload) - {'operationId', 'expectedRevision', 'policyVersion', 'reason'}:
                raise APIError('INVALID_SUPPLEMENTAL_POLICY', '补充验收请求包含不允许的字段')
            if wf['mode'] != 'real' or wf['status'] not in ('draft', 'paused', 'attention'):
                raise APIError('INVALID_STATE', '补充验收只能在非终态真机任务的检查点显式安装', 409)
            if self._has_lease(rt, workflow_id) or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone():
                raise APIError('RECONCILIATION_REQUIRED', '存在活动采集或未知回执，不能安装补充验收', 409)
            if wf.get('supplementalAcceptance'):
                raise APIError('SUPPLEMENTAL_ALREADY_INSTALLED', '该任务已经冻结补充验收；不能重复安装或静默替换', 409)
            reason = payload.get('reason')
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
                raise APIError('INVALID_SUPPLEMENTAL_REASON', '请填写 1–2000 字的补充验收安装原因')
            module = self._loaded_supplemental_module()
            if payload.get('policyVersion') != module.POLICY_VERSION:
                raise APIError('INVALID_SUPPLEMENTAL_POLICY', '未知补充验收版本')
            nodes = [n for n in wf['nodes'] if n['procedureId'] in module.SUPPORTED_PROCEDURES]
            if not nodes:
                raise APIError('SUPPLEMENTAL_NOT_APPLICABLE', '当前蓝图没有适用的测量方法')
            for node in nodes:
                if node['attempt'] != 0 or rt.db.execute('SELECT 1 FROM platform_attempts WHERE workflow_id=? AND node_id=?', (workflow_id, node['id'])).fetchone():
                    raise APIError('SUPPLEMENTAL_ALREADY_STARTED', '适用测量已有执行记录，不能追溯改变旧验收：' + node['label'], 409)
            self._assert_adapter_binding(wf)
            module, sources = self._supplemental_sources()
            policy = module.policy_snapshot()
            snapshot = {'policyVersion': module.POLICY_VERSION, 'policySnapshot': policy, 'policyHash': digest(policy),
                        **sources, 'baseGatesVersion': wf['gatesVersion'], 'baseGatesSha256': digest(wf['gates']),
                        'adapterProfileHash': wf['adapterProfileHash'], 'installedAt': now(), 'reason': reason.strip(),
                        'operationId': payload['operationId'], 'source': 'operator',
                        'applicableProcedures': [p for p in module.SUPPORTED_PROCEDURES if any(n['procedureId'] == p for n in nodes)],
                        'applicableNodeIds': [n['id'] for n in nodes]}
            wf['supplementalAcceptance'] = snapshot
            self.store.save(rt, wf, 'supplemental_acceptance_installed', {
                **snapshot, 'message': '操作员显式安装补充验收 ' + module.POLICY_VERSION + '：' + reason.strip() + '；原验收门槛与测量源码不变，未自动启动'})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], request_hash, dumps(wf)))
        return wf

    def _assert_supplemental_snapshot(self, wf):
        snapshot = wf.get('supplementalAcceptance')
        if not snapshot:
            return None
        module, sources = self._supplemental_sources()
        nodes = [n for n in wf['nodes'] if n['procedureId'] in module.SUPPORTED_PROCEDURES]
        expected = {'policyVersion': module.POLICY_VERSION, 'policyHash': digest(module.policy_snapshot()),
                    **sources, 'baseGatesVersion': wf['gatesVersion'], 'baseGatesSha256': digest(wf['gates']),
                    'adapterProfileHash': wf['adapterProfileHash'], 'applicableNodeIds': [n['id'] for n in nodes],
                    'applicableProcedures': [p for p in module.SUPPORTED_PROCEDURES if any(n['procedureId'] == p for n in nodes)]}
        if any(snapshot.get(k) != v for k, v in expected.items()) or digest(snapshot.get('policySnapshot')) != snapshot['policyHash']:
            raise APIError('SUPPLEMENTAL_BINDING_MISMATCH', '补充验收快照、源码或原验收绑定已变化；不能静默应用另一套规则', 409)
        return snapshot

    def _supplemental_evidence(self, request, result, snapshot):
        """Read only registered, hash-bound native analysis and acquisition receipts."""
        operation = request['operationId']
        expected_policy = {k: snapshot[k] for k in ('policyVersion', 'policyHash', 'moduleSha256', 'sourceManifestSha256', 'baseGatesSha256')}
        if request.get('supplementalAcceptance') != expected_policy:
            raise APIError('SUPPLEMENTAL_BINDING_MISMATCH', '采集请求未绑定当前显式安装的补充验收')
        root = checked_path(getattr(self.adapter, 'root', self.root))
        operations = checked_path(getattr(self.adapter, 'operations', root / 'data/runs/arclight_operations'), parent=root)
        directory = checked_path(operations / hashlib.sha256(operation.encode()).hexdigest(), parent=operations)
        with self.store.connect() as rt:
            effect = rt.db.execute('SELECT workflow_id,attempt_id,state,request FROM platform_effects WHERE operation_id=?', (operation,)).fetchone()
            if (not effect or effect['state'] != 'completed' or effect['workflow_id'] != request['runId']
                    or effect['attempt_id'] != request['attemptId'] or digest(json.loads(effect['request'])) != digest(request)):
                raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '采集回执与本任务请求不符')
            artifacts = []
            for item in result.get('artifacts', []):
                if item.get('kind') != 'raw' and item.get('name') not in ('plan.json', 'platform_analysis.json', 'job.json', 'status.json'):
                    continue
                row = rt.db.execute('SELECT path,sha256,payload FROM platform_artifacts WHERE id=?', (item.get('id'),)).fetchone()
                if not row or row['sha256'] != item.get('sha256'):
                    raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '分析产物没有匹配的已登记 SHA256')
                metadata = json.loads(row['payload'])
                artifacts.append((metadata, checked_path(row['path'], parent=directory)))

        def registered(name, parent=None):
            matches = [(a, p) for a, p in artifacts if a.get('name') == name and (parent is None or p.parent == parent)]
            if len(matches) != 1:
                raise APIError('SUPPLEMENTAL_EVIDENCE_MISSING', '缺少唯一已登记产物：' + name)
            return matches[0]

        def read_json(item):
            metadata, path = item
            if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
                raise APIError('SUPPLEMENTAL_EVIDENCE_MISSING', '分析 JSON 缺失或超过大小限制')
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != metadata['sha256']:
                raise APIError('SUPPLEMENTAL_EVIDENCE_HASH', '已登记分析 JSON 的内容已改变')
            def invalid_constant(value):
                raise ValueError('Nonfinite JSON constant: ' + value)
            return json.loads(content.decode('utf-8-sig'), parse_constant=invalid_constant)

        plan_item = registered('plan.json', directory)
        plan = read_json(plan_item)
        identity = {k: request.get(k) for k in ('runId', 'nodeId', 'attemptId', 'operationId', 'resourceId', 'procedureId', 'parameterVersions')}
        if (plan.get('adapter') != 'arclight' or plan.get('compileOnlyEvidence') or plan.get('operationId') != operation
                or plan.get('resourceId') != request['resourceId'] or plan.get('procedureId') != request['procedureId']
                or plan.get('requestIdentity') != identity or result.get('dataKind') != 'real'
                or result.get('provenance', {}).get('freshAcquisition') is not True
                or result.get('provenance', {}).get('operationId') != operation):
            raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '原生计划没有绑定本次真实任务、对象和操作')
        actual = plan.get('parameters')
        if (not isinstance(actual, dict) or actual != result.get('parameters')
                or any(actual.get(k) != v for k, v in request['parameters'].items())):
            raise APIError('SUPPLEMENTAL_CONTEXT_MISMATCH', '原生计划与请求、结果的实际参数不一致')
        keys = self.adapter.validation_parameter_keys() if callable(getattr(self.adapter, 'validation_parameter_keys', None)) else CALIBRATION_KEYS
        if any(k not in actual or k not in request['parameterVersions'] for k in keys):
            raise APIError('SUPPLEMENTAL_CONTEXT_MISSING', '缺少完整校准工作点或参数版本')
        analysis_item = registered('platform_analysis.json')
        folder = analysis_item[1].parent
        if not folder.is_relative_to(directory / 'results'):
            raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '分析报告不属于本次原生采集目录')
        report, job, state = read_json(analysis_item), read_json(registered('job.json', folder)), read_json(registered('status.json', folder))
        planned_job = plan['jobs'][-1]
        expected_rows = plan['expectedJobRows'][-1]
        execution_fields = ('xy_physical_channel', 'xy_filter_mhz', 'xy_attenuation_db', 'xy_delay_s',
                            'captures', 'acquisitions_per_point', 'randomize', 'order_seed')
        if (job.get('name') != planned_job.get('name') or job.get('target') != request['resourceId']
                or job.get('parameters') != actual or job.get('spec') != planned_job.get('spec')
                or not isinstance(job.get('config'), dict) or job['config'] != planned_job.get('config')
                or any((key in job) != (key in planned_job) or job.get(key) != planned_job.get(key)
                       for key in execution_fields)
                or state.get('job') != job.get('name') or state.get('target') != request['resourceId']
                or state.get('mode') != 'real_hardware' or state.get('state') != 'acquired'
                or state.get('shutdown_ok') is not True or state.get('points_completed') != expected_rows
                or type(expected_rows) is not int or expected_rows <= 0):
            raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '原生任务或完整采集回执不匹配')
        if (report.get('analysis', {}).get('procedureId') != request['procedureId']
                or report.get('analysis', {}).get('source_sha256') != snapshot['nativeAnalysisSha256']
                or report.get('analysis') != result.get('analysisQuality') or report.get('metrics') != result.get('metrics')):
            raise APIError('SUPPLEMENTAL_ANALYSIS_BINDING', '分析内容、源码 SHA 或结果指标不匹配原始 JSON')
        raw = []
        for metadata, path in artifacts:
            if metadata.get('kind') != 'raw':
                continue
            before = path.stat()
            actual_hash = file_hash(path) if before.st_size <= 512 * 1024 * 1024 else None
            after = path.stat()
            if actual_hash != metadata['sha256'] or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise APIError('SUPPLEMENTAL_EVIDENCE_HASH', '原始采集文件 SHA 或完整性不匹配')
            raw.append(metadata['sha256'])
        main_raw = registered('raw_scan.npz', folder)[0]['sha256']
        if not raw or report['analysis'].get('raw_sha256') != main_raw or main_raw not in raw:
            raise APIError('SUPPLEMENTAL_EVIDENCE_BINDING', '分析没有绑定同目录的原始采集数据')
        context_hash = digest({'values': {k: actual[k] for k in keys},
                               'versions': {k: request['parameterVersions'][k] for k in keys}})
        evidence = {'operationId': operation, 'rawSha256s': sorted(set(raw)),
                    'analysisArtifactSha256': analysis_item[0]['sha256'], 'calibrationContextSha256': context_hash,
                    'analysisArtifactId': analysis_item[0]['id'], 'planArtifactId': plan_item[0]['id']}
        return report, evidence

    def _apply_supplemental_acceptance(self, wf, request, result, stage, selection=None):
        snapshot = wf.get('supplementalAcceptance')
        if not snapshot or request['procedureId'] not in snapshot.get('applicableProcedures', []):
            return
        try:
            self._assert_supplemental_snapshot(wf)
            module = self._loaded_supplemental_module()
            bindings = {'selection': selection, 'verification': (request, result)} if stage == 'cross' else {stage: (request, result)}
            reports, evidence = {}, {}
            for role, pair in bindings.items():
                if not pair:
                    raise APIError('SUPPLEMENTAL_EVIDENCE_MISSING', '缺少独立采集配对')
                reports[role], evidence[role] = self._supplemental_evidence(pair[0], pair[1], snapshot)
            context = {'runId': request['runId'], 'resourceId': request['resourceId'],
                       **{k: snapshot[k] for k in ('baseGatesVersion', 'baseGatesSha256', 'sourceManifestSha256', 'moduleSha256')},
                       'policySha256': snapshot['policyHash']}
            receipt = module.evaluate_supplemental_quality(request['procedureId'], stage, reports,
                policy=snapshot['policySnapshot'], base_gates=wf['gates'], evidence=evidence, context=context)
            receipt['registeredEvidence'] = evidence
        except (APIError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            receipt = {'schema': 'qcal-supplemental-quality/1', 'ruleVersion': snapshot['policyVersion'],
                       'stage': stage, 'status': 'unverified', 'accepted': False, 'checks': [],
                       'reasons': [str(exc)], 'bindingError': getattr(exc, 'code', type(exc).__name__),
                       'policyHash': snapshot['policyHash'], 'evidenceHash': None,
                       'acquisitionClaim': 'derived_validation_only'}
        bundle = result.setdefault('supplementalQuality', {'policyVersion': snapshot['policyVersion'],
            'policyHash': snapshot['policyHash'], 'receipts': {}})
        bundle['receipts'][stage] = receipt
        bundle['accepted'] = all(bundle['receipts'].get(s, {}).get('accepted') is True for s in ('selection', 'verification', 'cross'))
        if receipt['accepted'] is not True:
            if result['quality']['status'] != 'failed':
                result['quality']['status'] = 'failed' if receipt['status'] == 'failed' else 'unverified'
            result['quality']['reasons'].extend('补充验收（' + stage + '）：' + reason for reason in receipt['reasons'] or ['证据不足'])

    def _legacy_decision_error(self, rt, workflow, node_id):
        """Recognize only the old post-acquisition BAD_EVIDENCE classification.

        No instrument/transport failure or partially acquired result is eligible.
        This does not rewrite the historical attempt; the operator may only ask
        the model for a new decision based on the retained completed evidence.
        """
        if (workflow.get('status') != 'attention' or workflow.get('engineState') != 'execution_failed'
                or not node_id or node_id != workflow.get('currentNodeId')):
            return False
        node = self._node(workflow, node_id)
        result = node.get('result') or {}
        messages = {'证据引用必须为编号列表', '决策引用了当前节点之外的证据'}
        if (node.get('detail') not in messages or result.get('dataKind') != 'real'
                or result.get('partial') or result.get('stale')
                or result.get('quality', {}).get('status') not in ('failed', 'unverified')):
            return False
        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
                            (node.get('latestAttemptId'), workflow['id'], node_id)).fetchone()
        if not row:
            return False
        attempt = json.loads(row['payload'])
        if attempt.get('state') != 'execution_failed' or attempt.get('error') != node['detail'] or attempt.get('result') != result:
            return False
        effects = list(rt.db.execute('SELECT state FROM platform_effects WHERE workflow_id=? AND attempt_id=?',
                                    (workflow['id'], attempt['id'])))
        return bool(effects) and all(row['state'] == 'completed' for row in effects)

    @staticmethod
    def _has_lease(rt, workflow_id):
        return rt.db.execute('SELECT 1 FROM platform_leases WHERE workflow_id=?', (workflow_id,)).fetchone() is not None

    def detail(self, workflow_id, node_id):
        scoped = self.for_node(workflow_id, node_id, purpose='view')
        if scoped is not self:
            return scoped.detail(workflow_id, node_id)
        wf = self.get(workflow_id)
        node = self._node(wf, node_id)
        attempts, decisions = self.store.detail(workflow_id, node_id)
        with self.store.connect() as rt:
            parameters, versions = self._parameters(rt, wf, node)
            resolved = {'status': 'unavailable', 'source': 'adapter.build_plan',
                        'reason': '当前执行器没有只读执行计划'}
            if wf['mode'] == 'real' and callable(getattr(self.adapter, 'build_plan', None)):
                try:
                    self._assert_adapter_binding(wf)
                    request = {'runId': wf['id'], 'nodeId': node_id, 'attemptId': 'detail-preview',
                        'operationId': 'detail-preview/' + digest({'workflowId':wf['id'],'nodeId':node_id,'revision':wf['revision']}),
                        'resourceId': node['resourceId'], 'procedureId': node['procedureId'],
                        'parameters': deepcopy(parameters), 'parameterVersions': versions,
                        'adapterId':self.adapter_id, 'adapterProfileHash':self.adapter_profile_hash,
                        'deadline':time.time() + self.profile.get('maximumRunSeconds',3600)}
                    if node['procedureId'] in ('sq_rb','sq_allxy'):
                        request['validationSnapshot'] = self._validation_snapshot(rt, wf, node, parameters, versions)
                    if node['procedureId'] in SCIENCE_METHODS:
                        request['configurationBinding'] = deepcopy(self.profile.get('configurationBinding', {}))
                        request['explicitParameterKeys'] = sorted(node.get('parameterOverrides', {}))
                        self._science_request_dependencies(rt, wf, node, request)
                    from .attempt_execution import attach_plan
                    attach_plan(self, request)
                    plan = self.adapter.build_plan(request)
                    if not isinstance(plan.get('parameters'), dict):
                        raise APIError('INVALID_PLAN', '执行计划未返回解析后的参数')
                    self._require_spectrum_drive_context(rt, wf, node, plan['parameters'])
                    resolved = {'status':'ready', 'source':'adapter.build_plan',
                        'parameters':deepcopy(plan['parameters']), 'estimatedTotalShots':plan.get('estimated_total_shots'),
                        'totalRows':plan.get('totalRows'), 'adapterProfileHash':self.adapter_profile_hash,
                        'scope':'当前参数的只读采集计划；不是历史实际执行参数或运行许可'}
                except Exception as exc:
                    resolved.update(code=getattr(exc,'code','INVALID_PLAN'), reason=str(exc))
        defaults = {key: value for key, value in resolved.get('parameters', {}).items() if key not in parameters}
        from .attempt_execution import summary
        return {'node': node, 'attempts': attempts, 'decisions': decisions, 'rules': node['rules'],
                'attemptExecutionVersion':summary(self,wf,node),
                'executionVersionOptions':([{'id':'rabi16-v1','label':'幅度 Rabi · 每点 16 块'}]
                    if wf.get('adapterId')=='arclight-private-f8-q11-v3' and wf.get('policy')=='strict'
                    and node.get('procedureId')=='sq_rabi_amplitude' and not wf.get('configurationBinding') else []),
                'parameters': parameters, 'parameterBounds': self._parameter_schema(node['procedureId']),
                'parameterVersions':versions,'workflowRevision':wf['revision'],
                'resolvedExecutionPlan':resolved, 'executionDefaults':defaults, 'executionDefaultsSource':'adapter.build_plan'}

    def pulse_preview(self, workflow_id, node_id, payload):
        """Read-only draft planning: no command, reservation, effect or acceptance."""
        scoped = self.for_node(workflow_id, node_id, purpose='view')
        if scoped is not self:
            return scoped.pulse_preview(workflow_id, node_id, payload)
        from .manual_pulse_preview import METHODS, summarize, unavailable
        if not isinstance(payload, dict) or set(payload)-{'expectedRevision','parameters','scanIndices'}:
            raise APIError('BAD_PARAMETERS','预览只接受当前参数草稿与扫描点')
        with self.store.connect() as rt:
            wf=self.get(workflow_id,rt);node=self._node(wf,node_id)
            if type(payload.get('expectedRevision')) is not int or payload['expectedRevision']!=wf['revision']:
                raise APIError('STALE_REVISION','任务版本已更新，请读取当前配置后预览',409)
            changes=payload.get('parameters',{})
            if not isinstance(changes,dict) or len(changes)>128:raise APIError('BAD_PARAMETERS','预览参数无效')
            self._assert_adapter_binding(wf)
            parameters,versions=self._parameters(rt,wf,node);schema=self._parameter_schema(node['procedureId'])
            for key,value in changes.items():self._validate_parameter(key,value,schema)
            normalized=self._normalize_changes(node['procedureId'],deepcopy(changes),parameters) if changes else {}
            for key,value in normalized.items():self._validate_parameter(key,value,schema)
            merged={**parameters,**normalized}
            # The mutation-path helper may persist an invalidation decision.
            # Preview only checks the same frozen marker and never calls it.
            marker=deepcopy(wf.get('readoutRevalidations',{}).get(node['resourceId']))
            if marker and marker['state'] in ('active','fixed_failed'):
                baseline,baseline_versions=self._parameters(rt,wf,{**node,'parameterOverrides':{}})
                if any(not self._same_parameter(value,baseline.get(k)) or marker['parameterVersions'][k]!=baseline_versions.get(k,0)
                       for k,value in marker['values'].items()):
                    raise APIError('PULSE_PREVIEW_REFERENCE_CHANGED','读出工作点或来源版本已改变；预览不修改核验状态',409)
            else:marker=None
            merged=self._readout_revalidation_parameters(node,merged,marker)
            context={'schema':'manual-pulse-preview/1','workflowId':workflow_id,'nodeId':node_id,
                'resourceId':node.get('resourceId'),'workflowRevision':wf['revision'],'parameterVersions':versions,
                'requestedChanges':deepcopy(changes),'normalizedChanges':normalized,
                'dispatched':False,'hardwareConnected':False,'scope':'当前版本参数草稿；不是执行许可或历史实际结果'}
            if wf['mode']!='real' or node['procedureId'] not in METHODS or not callable(getattr(self.adapter,'build_plan',None)):
                return {**context,**unavailable('此方法的完整原生时序尚未覆盖，未绘制替代模型。')}
            operation='pulse-preview/'+digest({'workflowId':workflow_id,'nodeId':node_id,'parameters':merged})
            request={'runId':workflow_id,'nodeId':node_id,'attemptId':'pulse-preview','operationId':operation,
                'resourceId':node['resourceId'],'procedureId':node['procedureId'],'parameters':merged,
                'parameterVersions':versions,'adapterId':self.adapter_id,'adapterProfileHash':self.adapter_profile_hash,
                'deadline':time.time()+self.profile.get('maximumRunSeconds',3600)}
            from .attempt_execution import attach_plan
            attach_plan(self, request)
            try:plan=self.adapter.build_plan(request)
            except Exception as exc:raise APIError(getattr(exc,'code','INVALID_PLAN'),str(exc)) from None
            self._require_spectrum_drive_context(rt, wf, node, plan.get('parameters') or {})
            return {**context,'effectiveParameters':deepcopy(plan.get('parameters',{})),
                'planSha256':digest(plan),**summarize(plan,payload.get('scanIndices'))}

    def set_parameters(self, workflow_id, node_id, payload):
        scoped = self.for_node(workflow_id, node_id)
        if scoped is not self:
            return scoped.set_parameters(workflow_id, node_id, payload)
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            existing, request_hash = self._command(rt, wf, {**payload, 'nodeId': node_id}, 'parameters')
            if existing:
                return existing
            if wf['mode'] != 'real' or wf['status'] not in ('draft', 'paused', 'attention'):
                raise APIError('INVALID_STATE', '参数只能在真机任务草稿、暂停或待处理状态修改', 409)
            if is_manual(wf) and awaiting_acceptance(wf):
                raise APIError('ACCEPTANCE_REQUIRED', '请先确认当前结果或明确回退后修改参数', 409)
            if self._has_lease(rt, workflow_id) or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone():
                raise APIError('RECONCILIATION_REQUIRED', '存在活动或未知采集，先完成回执核查', 409)
            self._assert_adapter_binding(wf)
            node = self._node(wf, node_id)
            changes = payload.get('parameters')
            clear_drive = payload.get('clearSpectrumDriveOverrides', False)
            if type(clear_drive) is not bool or not isinstance(changes, dict) or (clear_drive and changes) or (not clear_drive and not changes):
                raise APIError('BAD_PARAMETERS', '请提供需要修改的参数')
            parameters, versions = self._parameters(rt, wf, node)
            cleared = {}
            if clear_drive:
                if not self._spectrum_drive_conflicts(rt, wf, node, parameters):
                    raise APIError('NO_SPECTRUM_DRIVE_CONFLICT', '当前步骤没有需要清除的光谱驱动幅度冲突', 409)
                overrides = node.get('parameterOverrides', {})
                cleared = {k: overrides[k] for k in ('drive_amp', 'half_pi_amp') if k in overrides}
                if not cleared:
                    raise APIError('NO_SPECTRUM_DRIVE_OVERRIDE', '当前步骤没有已保存的驱动幅度覆盖', 409)
                node['parameterOverrides'] = {k:v for k,v in overrides.items() if k not in cleared}
                parameters, versions = self._parameters(rt, wf, node)
            schema = self._parameter_schema(node['procedureId'])
            for key, value in changes.items():
                self._validate_parameter(key, value, schema)
            requested_changes = deepcopy(changes)
            changes = {} if clear_drive else self._normalize_changes(node['procedureId'], changes, parameters)
            for key, value in changes.items():
                self._validate_parameter(key, value, schema)
            merged = {**parameters, **changes}
            if getattr(self, '_attempt_execution_authorization', None) and digest(merged) != self._attempt_execution_authorization['selectionParametersSha256']:
                raise APIError('EXECUTION_VERSION_PARAMETERS_CHANGED', '已冻结下一次尝试的参数；请先取消本次执行版本选择后修改', 409)
            marker = self._readout_revalidation(rt, wf, node)
            merged = self._readout_revalidation_parameters(node, merged, marker)
            self._validate_plan(wf, node, merged, versions, payload['operationId'])
            before = {key: parameters.get(key) for key in changes}
            node.setdefault('parameterOverrides', {}).update(changes)
            node['parameters'] = merged
            affected = self._invalidate(wf, node_id, include_target=True)
            if wf['status'] != 'draft':
                wf['status'], wf['engineState'] = 'paused', 'parameters_updated'
            self._record_decision(rt, wf, node_id, {'action': 'parameters_updated', 'reason': '操作员保存参数；未启动采集',
                'evidenceIds': [], 'parameterChanges': {key: {'from': before[key], 'to': value} for key, value in changes.items()},
                **({'clearedParameterOverrides': cleared, 'inheritedParameters': {k: parameters.get(k) for k in cleared}} if cleared else {}),
                **({'requestedParameterChanges': requested_changes, 'normalization': '执行器脉冲策略及采样网格规范化'} if changes != requested_changes else {}),
                'invalidatedNodes': affected}, 'operator')
            self.store.save(rt, wf, 'parameters_updated', {'nodeId': node_id, 'message': '参数已保存，相关结果已标记失效；等待明确启动或继续'})
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (payload['operationId'], request_hash, dumps(wf)))
        return wf

    def _stability_retry_frequency(self, rt, wf, node, *, operation_id, changes, reason):
        """Queue one bounded spectral retry inside the schedule transaction."""
        from .stability import RECOVERY_POLICY, _frequency_problem
        request_hash = digest({'kind': 'stability_frequency_retry', 'workflowId': wf['id'],
                               'nodeId': node['id'], 'changes': changes, 'reason': reason})
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 240:
            raise APIError('OPERATION_ID_REQUIRED', '频窗复核缺少有效操作编号')
        prior = rt.db.execute('SELECT request_hash,response FROM platform_commands WHERE id=?', (operation_id,)).fetchone()
        if prior:
            if prior[0] != request_hash:
                raise APIError('IDEMPOTENCY_CONFLICT', '同一频窗复核编号对应不同请求', 409)
            return json.loads(prior[1])
        current = self.get(wf['id'], rt)
        if current['revision'] != wf['revision']:
            raise APIError('STALE_REVISION', '周期子任务已更新', 409)
        wf = deepcopy(current)
        node = self._node(wf, node['id'])
        binding = wf.get('stabilityBinding') or {}
        policy = binding.get('frequencyRecovery') or {}
        schedule_row = rt.db.execute('SELECT payload FROM platform_stability WHERE id=?', (binding.get('scheduleId'),)).fetchone()
        schedule = json.loads(schedule_row[0]) if schedule_row else {}
        round_ = next((r for r in schedule.get('rounds', []) if r.get('workflowId') == wf['id'] and r.get('number') == binding.get('round')), None)
        if (policy.get('policy') != RECOVERY_POLICY or schedule.get('state') != 'running'
                or schedule.get('frequencyRecovery') != policy or round_ is None
                or binding.get('calibrationHash') != schedule.get('calibrationHash')
                or binding.get('frequencyNodeId') != node['id'] or node['procedureId'] != 'sq_spectroscopy'
                or node['resourceId'] != schedule.get('resourceId')
                or wf.get('mode') != 'real' or wf['status'] not in ('attention', 'failed')
                or wf.get('cancelRequested') or wf.get('sharedDeviceFault') or wf.get('engineState') == 'needs_reconciliation'):
            raise APIError('STABILITY_RECOVERY_BINDING', '当前任务没有有效的本轮频窗复核许可', 409)
        t1 = self._node(wf, binding.get('t1NodeId'))
        if t1['procedureId'] != 'sq_t1' or t1['resourceId'] != node['resourceId'] or t1.get('attempt', 0):
            raise APIError('STABILITY_RECOVERY_BINDING', '频窗复核须在本轮 T1 开始之前', 409)
        if (len(round_.get('recoveryEvents', [])) >= policy['maxExpansions']
                or node['attempt'] >= policy['maximumFrequencyAttempts']
                or task_budget.exhausted(wf, 'maxAttemptsPerNode', node['attempt'])):
            raise APIError('ATTEMPT_BUDGET', '本轮频窗复核已达到尝试预算', 409)
        if (rt.db.execute('SELECT 1 FROM platform_leases WHERE workflow_id=? OR resource=?', (wf['id'], device_key(self.profile))).fetchone()
                or rt.db.execute("SELECT 1 FROM platform_effects WHERE state IN ('pending','unknown')").fetchone()):
            raise APIError('RECONCILIATION_REQUIRED', '存在设备占用或未知采集，未创建频窗复核', 409)
        problem = _frequency_problem(node, binding)
        if (not problem or problem.get('code') not in ('frequency_peak_missing', 'frequency_peak_at_edge')
                or reason != problem or operation_id != f"{schedule['id']}/round/{round_['number']}/expand/{node['latestAttemptId']}"):
            raise APIError('CURRENT_EVIDENCE_REQUIRED', '频窗复核须对应本轮当前实测结果', 409)
        self._assert_adapter_binding(wf)
        schema = self._parameter_schema(node['procedureId'])
        if not isinstance(changes, dict) or set(changes) != {'span_hz', 'start_hz', 'stop_hz'}:
            raise APIError('BAD_PARAMETERS', '频窗复核只允许修改频率窗口')
        for key, value in changes.items():
            self._validate_parameter(key, value, schema)
        window = problem['window']
        expected_span = min(window['spanHz'] * policy['factor'], policy['maxSpanHz'], schema['span_hz']['max'])
        center = (window['startHz'] + window['stopHz']) / 2
        if (expected_span <= window['spanHz'] or changes != {'span_hz': expected_span,
                'start_hz': center - expected_span / 2, 'stop_hz': center + expected_span / 2}):
            raise APIError('FREQUENCY_WINDOW_LIMIT', '频窗复核必须保持中心并遵守冻结扩窗上限', 409)
        parameters, versions = self._parameters(rt, wf, node)
        if parameters.get('frequencies_hz'):
            raise APIError('FREQUENCY_WINDOW_FIXED_AXIS', '存在显式频率数组，不能自动替换扫描轴', 409)
        normalized = self._normalize_changes(node['procedureId'], changes, parameters)
        if normalized != changes:
            raise APIError('FREQUENCY_WINDOW_LIMIT', '执行器不能保持冻结频窗')
        merged = {**parameters, **changes}
        budget = self._validate_plan(wf, node, merged, versions, operation_id, joint_budget=True, rt=rt)
        rt.db.execute('SAVEPOINT stability_frequency_retry')
        try:
            node.setdefault('parameterOverrides', {}).update(changes)
            node['parameters'] = merged
            affected = self._invalidate(wf, node['id'], include_target=True)
            wf.update(currentNodeId=node['id'], status='attention', engineState='stability_frequency_retry')
            self._record_decision(rt, wf, node['id'], {'action': 'stability_frequency_retry', 'reason': deepcopy(reason),
                'evidenceIds': [], 'parameterChanges': {k: {'from': parameters.get(k), 'to': v} for k, v in changes.items()},
                'invalidatedNodes': affected, 'budgetLookahead': budget}, 'stability_policy')
            self.store.save(rt, wf, 'stability_frequency_retry', {'nodeId': node['id'], 'message': reason['message']})
            updated = self.action(wf['id'], {'action': 'retry', 'operationId': operation_id + '/queue',
                'expectedRevision': wf['revision'], 'nodeId': node['id'], 'attemptId': node['latestAttemptId']}, _rt=rt)
            rt.db.execute('INSERT INTO platform_commands VALUES(?,?,?)', (operation_id, request_hash, dumps(updated)))
            rt.db.execute('RELEASE SAVEPOINT stability_frequency_retry')
            return updated
        except Exception:
            rt.db.execute('ROLLBACK TO SAVEPOINT stability_frequency_retry')
            rt.db.execute('RELEASE SAVEPOINT stability_frequency_retry')
            raise

    def _validate_plan(self, wf, node, parameters, versions, operation_id, *, joint_budget=False, rt=None):
        scoped = self if getattr(self, '_attempt_execution_authorization', None) else self.for_node(wf['id'], node['id'], rt)
        if scoped is not self:
            return scoped._validate_plan(wf, node, parameters, versions, operation_id, joint_budget=joint_budget, rt=rt)
        if not callable(getattr(self.adapter, 'build_plan', None)):
            if joint_budget and self._physical_adapter:
                raise APIError('INVALID_BUDGET', '物理执行器缺少可核查的采集计划')
            return
        request = {'runId': wf['id'], 'nodeId': node['id'], 'attemptId': 'parameter-validation',
                   'operationId': 'validate/' + operation_id, 'resourceId': node['resourceId'],
                   'procedureId': node['procedureId'], 'parameters': parameters, 'parameterVersions': versions,
                   'adapterId': self.adapter_id, 'adapterProfileHash': self.adapter_profile_hash,
                   'deadline': time.time() + self.profile.get('maximumRunSeconds', 3600)}
        if node['procedureId'] in SCIENCE_METHODS:
            request['configurationBinding'] = deepcopy(self.profile.get('configurationBinding', {}))
            request['explicitParameterKeys'] = sorted(node.get('parameterOverrides', {}))
        if node['procedureId'] in ('sq_rb', 'sq_allxy'):
            with (nullcontext(rt) if rt is not None else self.store.connect()) as reader:
                request['validationSnapshot'] = self._validation_snapshot(reader, wf, node, parameters, versions)
        try:
            with (nullcontext(rt) if rt is not None else self.store.connect()) as reader:
                if joint_budget and self._physical_adapter:
                    from .gate_calibration_context import require_current_gate
                    require_current_gate(self, reader, wf, node, parameters)
                    from .smart_rabi_window import check_plan as check_rabi_window
                    check_rabi_window(self, reader, wf, node, parameters, versions)
                self._science_request_dependencies(reader, wf, node, request)
            from .attempt_execution import attach_plan
            attach_plan(self, request)
            plan = self.adapter.build_plan(request)
            if joint_budget and self._physical_adapter:
                if node['procedureId'] == 'sq_spectroscopy':
                    with (nullcontext(rt) if rt is not None else self.store.connect()) as reader:
                        self._require_spectrum_drive_context(reader, wf, node, plan.get('parameters') or {})
                return self._selection_budget(wf, request, plan, rt=rt)
        except Exception as exc:
            if isinstance(exc, APIError) and exc.code == 'SPECTRUM_DRIVE_CONTEXT_CONFLICT':
                raise
            raise APIError(getattr(exc, 'code', 'INVALID_PLAN'), str(exc)) from None

    def _science_request_dependencies(self, rt, wf, node, request):
        """Resolve immutable source IDs afresh, never accepting a client proof."""
        if node['procedureId'] not in SCIENCE_METHODS:
            return
        if wf.get('smartParent'):
            from .smart_science import validate_target
            validate_target(self, rt, wf, request['parameters'])
        if not (self.profile.get('sciencePolicy') or self.profile.get('scienceFluxPolicy')):
            raise APIError('SCIENCE_PROFILE_REQUIRED', '科学实验需要独立执行配置', 409)
        if wf.get('gatesVersion') != science_rules(self.profile)[0] or wf.get('gatesSha256') != digest(wf.get('gates', {})):
            raise APIError('SCIENCE_GATES_CHANGED', '冻结科学验收规则已改变', 409)
        if wf.get('observedResourceBinding'):
            self._validate_observed_resource_binding(wf, rt)
            request['observedResourceBinding'] = deepcopy(wf['observedResourceBinding'])
        if node['procedureId'] in SCIENCE_FLUX_METHODS:
            from .flux_binding import validate_bias_binding
            if wf.get('configurationBinding',{}).get('biasDeploymentBinding'):
                validate_bias_binding(self.workspace.chips,wf,self.profile)
            from .flux_execution import request_authorization
            request_authorization(self,rt,wf,node,request)
        from .science_dependencies import ScienceDependencyResolver, MAX_REQUEST_BYTES
        scope = {'workflowId': wf['id'], 'resourceId': node['resourceId']}
        scope.update({key: wf[key] for key in ('chipVersionId', 'deploymentVersionId') if wf.get(key)})
        if node['procedureId']=='sq_flux_point_recheck':
            from .flux_points import resolve_point
            point=resolve_point(self,rt,node.get('workingPointId'),resource_id=node['resourceId'],scope=scope)
            previous_point=request.get('fluxPointEvidence')
            if previous_point is not None and previous_point!=point:
                raise APIError('FLUX_POINT_CHANGED','工作点来源已偏离本次操作冻结证明',409)
            if {'role':'flux_scan','recordId':point['recordId']} not in node.get('scienceReferences',[]):
                raise APIError('FLUX_POINT_EVIDENCE','工作点与选择的偏置扫描来源不一致',409)
            request['fluxPointEvidence']=point
        resolver = ScienceDependencyResolver(self)
        previous = request.pop('scienceDependencies', None)
        proof = resolver.resolve(rt, kind='measurement', scope=scope,
            refs=deepcopy(node.get('scienceReferences', [])), inputs={}, use='execution', consumer=request)
        if previous is not None and previous.get('closureSha256') != proof['closureSha256']:
            raise APIError('SCIENCE_DEPENDENCY_CHANGED', '前置来源已偏离本次操作冻结证明', 409)
        request['scienceDependencies'] = proof
        request_bytes = len(dumps(request).encode('utf-8'))
        if request_bytes > MAX_REQUEST_BYTES:
            raise APIError('SCIENCE_REQUEST_LIMIT',
                f'冻结执行请求为 {request_bytes} 字节，超过 {MAX_REQUEST_BYTES} 字节的证据容量；未创建采集许可或下发仪器。', 413)

    def _selection_budget(self, wf, request, plan, *, rt=None):
        """Plan-only lookahead; actual per-operation reservation remains in _call_adapter."""
        adapter_type = type(self.adapter)
        if request['procedureId'] in SCIENCE_METHODS:
            from adapters.science_v3_adapter import ScienceV3Adapter
            from adapters.science_v3.plans import VERIFY
            from adapters.science_shape_repeat5.adapter import ScienceRepeat5Adapter
            if request['procedureId'] in SCIENCE_FLUX_METHODS:
                from adapters.science_flux_adapter import ScienceFluxAdapter
                from adapters.science_flux.contracts import policy_for
                expected=policy_for(self.profile.get('biasRoutes',{}).get(request['resourceId']))
                if type(self.adapter) is not ScienceFluxAdapter or plan.get('adapter')!='science-flux' or plan.get('policy')!=expected:
                    raise APIError('VERIFICATION_BUDGET_UNKNOWN','偏置复测容量只接受已审计执行器与冻结线路策略')
                parameters=self.adapter.build_verification_parameters(request['procedureId'],deepcopy(plan['parameters']),{})
                basis='science_flux_same_bias_axes_full_fresh_acquisition_and_'+('declared_end_command' if expected.endswith('_v2') else 'restore')
            elif request['procedureId'] in SCIENCE_IRB_METHODS:
                from adapters.science_v3_irb.adapter import ScienceIRBAdapter
                from adapters.science_v3_irb.beta import policy_for, candidate_key
                expected=policy_for(request['procedureId'],plan['parameters'])
                if (type(self.adapter),plan.get('adapter')) not in ((ScienceIRBAdapter,'science-v3-irb'),(ScienceRepeat5Adapter,'science-repeat5')) or plan.get('policy')!=expected:
                    raise APIError('VERIFICATION_BUDGET_UNKNOWN','X/2 科学复测只接受已审计执行器及对应方法策略')
                candidate={}
                if request['procedureId']=='sq_drag_half_scan':
                    axis=plan['parameters']['scan_axis_values'];candidate={candidate_key(plan['parameters']):(axis[0]+axis[-1])/2}
                parameters=self.adapter.build_verification_parameters(request['procedureId'],deepcopy(plan['parameters']),candidate)
                basis='science_irb_full_fresh_seed_campaign' if request['procedureId']=='sq_drag_irb' else 'science_half_drag_same_scan_plus_frozen_candidate_rows'
            else:
                if (type(self.adapter),plan.get('adapter')) not in ((ScienceV3Adapter,'science-v3'),(ScienceRepeat5Adapter,'science-repeat5')):
                    raise APIError('VERIFICATION_BUDGET_UNKNOWN', '科学复测容量只接受已核验的执行器实现')
                parameters = deepcopy(plan['parameters'])
                key = 'shape_pi_amp' if request['procedureId']=='sq_shape_rabi_amplitude_repeat5' else VERIFY.get(request['procedureId'])
                if key:
                    axis = parameters['scan_axis_values']
                    parameters['verify_' + key] = (axis[0] + axis[-1]) / 2
                basis = 'science_v3_same_scan_plus_frozen_candidate_rows'
        else:
            implementation_id = adapter_type.__module__ + '.' + adapter_type.__qualname__
            profile_id = self.profile.get('id')
            from adapters.arclight_dynamic import DynamicArcLightAdapter, RUNTIME as DYNAMIC_RUNTIME
            if type(self.adapter) is DynamicArcLightAdapter:
                # This exact subclass changes only resource routing around the
                # inherited ArcLight sampling/verification builders. Authenticate
                # that frozen binding before reusing the audited count semantics.
                binding = self.profile.get('configurationBinding', {})
                if (self.profile.get('runtimeRoot') != DYNAMIC_RUNTIME
                        or self.profile.get('runner') != DYNAMIC_RUNTIME + '/platform_worker.py'
                        or self.profile.get('sourceManifest') != DYNAMIC_RUNTIME + '/source_manifest.json'
                        or not all(binding.get(key) for key in ('chipVersionId','deploymentVersionId','chipHash','deploymentHash'))
                        or plan.get('configurationBinding') != binding
                        or plan.get('routing') != self.adapter._routing(request['resourceId'])):
                    raise APIError('VERIFICATION_BUDGET_UNKNOWN', '动态配置未满足已核验采样容量语义')
                implementation_id, profile_id = 'adapters.arclight.ArcLightAdapter', 'arclight-private-f8-q11-v2'
            parameters, basis = verification_probe(
                implementation_id=implementation_id, profile_id=profile_id, procedure_id=request['procedureId'],
                request_parameters=request['parameters'], selection_plan=plan)
        probe = deepcopy(request)
        probe['parameters'] = parameters
        probe['operationId'] = 'budget-verify/' + digest(request['operationId'])
        probe.pop('biasCommandAuthorization',None)
        if request['procedureId'] in SCIENCE_FLUX_METHODS:
            from adapters.science_flux.contracts import command_authorization,is_commanded
            route=self.profile.get('biasRoutes',{}).get(request['resourceId'],{})
            if is_commanded(route):probe['biasCommandAuthorization']=command_authorization(route,probe['operationId'],stage='plan_only')
        if request['procedureId'] in SCIENCE_METHODS:
            with (nullcontext(rt) if rt is not None else self.store.connect()) as reader:
                self._science_request_dependencies(reader, wf, self._node(wf, request['nodeId']), probe)
        from .attempt_execution import attach_plan
        attach_plan(self, probe)
        verification_plan = self.adapter.build_plan(probe)
        return selection_lookahead(wf, plan, verification_plan, basis=basis)

    def artifact(self, artifact_id):
        with self.store.connect() as rt:
            row = rt.db.execute('SELECT * FROM platform_artifacts WHERE id=?', (artifact_id,)).fetchone()
        if not row:
            raise APIError('NOT_FOUND', '产物未登记', 404)
        path = checked_path(row['path'])
        if not path.is_file() or file_hash(path) != row['sha256']:
            raise APIError('ARTIFACT_HASH_MISMATCH', '产物已丢失或内容变化', 409)
        return path, json.loads(row['payload'])

    def _loop(self):
        while not self.stop_event.is_set():
            if time.monotonic() >= getattr(self, '_smart_science_check', 0):
                self._smart_science_check = time.monotonic() + 1
                try:
                    from .smart_science import tick as check_smart_science
                    check_smart_science(self)
                except Exception as exc:
                    import sys
                    self._smart_science_check = time.monotonic() + 15
                    print('Q11 gate queue: '+type(exc).__name__, file=sys.stderr, flush=True)
            if time.monotonic() >= getattr(self, '_smart_report_check', 0):
                self._smart_report_check = time.monotonic() + 2
                try:
                    from .smart_q11_report_jobs import tick as check_smart_report
                    check_smart_report(self)
                except Exception as exc:
                    import sys
                    self._smart_report_check = time.monotonic() + 30
                    print('Q11 report queue: '+type(exc).__name__, file=sys.stderr, flush=True)
            if getattr(self, '_model_recovery_error', None) is None:
                try:
                    model_recovery.recover_due(self)
                except Exception as exc:
                    import sys
                    self._model_recovery_error = {'type': type(exc).__name__, 'at': now()}
                    print('Model recovery scheduler disabled: ' + type(exc).__name__, file=sys.stderr, flush=True)
            if self._stability_error is None:
                try:
                    self.stability.tick()
                except Exception as exc:
                    import sys
                    self._stability_error = {'type': type(exc).__name__, 'at': now(),
                        'message': '周期调度已暂停，请检查服务日志；普通测量队列继续受控制器管理'}
                    print('Stability scheduler paused: '+type(exc).__name__, file=sys.stderr, flush=True)
                    try:
                        self.stability.halt_after_error(type(exc).__name__)
                    except Exception:
                        # Do not dispatch periodic children if their stop could
                        # not be persisted. Their authorization remains frozen.
                        print('Stability permit persistence failed; periodic dispatch disabled', file=sys.stderr, flush=True)
            self._pause_shared_queue()
            queued = self._queued_workflows()
            if self._stability_error is not None:
                queued = [w for w in queued if not w.get('stabilityBinding')]
            if self._workspace_service is not None:
                queued = [w for w in queued if not self.workspace.queue_blocker(w)]
            if queued:
                try:
                    self.run_workflow(queued[0]['id'])
                except Exception as exc:
                    self._attention(queued[0]['id'], 'controller_error', str(exc))
            else:
                self.wake.wait(.5)
                self.wake.clear()

    def _queued_workflows(self, rt=None):
        """FIFO authorized turns; reads only and never grants a missing permit."""
        workflows = self.store.workflows(rt)
        eligible = [wf for wf in workflows if wf['status'] == 'queued']
        def key(wf):
            permit = wf.get('manualStep') or wf.get('rbDiagnostic') or {}
            at = wf.get('queueEnteredAt') or (permit.get('createdAt') if permit.get('state') == 'queued' else None)
            return (at or wf.get('updatedAt') or wf['createdAt'], wf['createdAt'], wf['id'])
        return sorted(eligible, key=key)

    def _yield_to_waiter(self, workflow_id, *, decision=False):
        """Hand over only after the complete attempt has released its lease.

        Selection and its independent verification are never separated. A known
        quality failure can defer its model request, but unknown effects or a
        quarantined lease never become a free-device claim.
        """
        with self.store.transaction() as rt:
            current = self.get(workflow_id, rt)
            if (current['mode'] != 'real' or current['policy'] != 'autopilot'
                    or current['status'] not in ('queued', 'running', 'attention')
                    or current.get('cancelRequested') or current.get('rbDiagnostic', {}).get('state') in ('queued','running')
                    or self._has_lease(rt, workflow_id)
                    or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (workflow_id,)).fetchone()):
                return False
            if not decision and all(n['engineState'] in ('verified','acknowledged','unsupported') for n in current['nodes']):
                return False
            workflows = self.store.workflows(rt)
            if self._shared_blocker(current, workflows):
                return False
            group = device_key(current.get('configurationBinding', {}).get('profile', self.profile))
            waiting = [w for w in self._queued_workflows(rt) if w['id'] != workflow_id and w['mode'] == 'real'
                       and not w.get('cancelRequested') and not w.get('configurationIssue')
                       and (not is_manual(w) or w.get('manualStep', {}).get('state') == 'queued')
                       and device_key(w.get('configurationBinding', {}).get('profile', self.profile)) == group
                       and not self._shared_blocker(w, workflows)
                       and not self._has_lease(rt, w['id'])
                       and not rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (w['id'],)).fetchone()]
            if not waiting:
                return False
            current.update(status='queued', engineState='decision_queued' if decision else 'queued', queueEnteredAt=now())
            self.store.save(rt, current, 'device_queue_yield', {
                'message': '本步采集回执已结束，先交接给已排队任务；稍后再处理本任务' if decision else '本步首测与独立复测已结束，先交接给已排队任务',
                'waitingWorkflowId': waiting[0]['id'], 'boundary': 'completed_attempt', 'decisionDeferred': decision})
        self.wake.set()
        return True

    def _shared_blocker(self, workflow, workflows):
        group = device_key(workflow.get('configurationBinding', {}).get('profile', self.profile))
        return next((w for w in workflows if w['id'] != workflow['id'] and w.get('mode') == 'real'
                     and device_fault_blocks(w)
                     and device_key(w.get('configurationBinding', {}).get('profile', self.profile)) == group), None)

    def _pause_shared_queue(self, *, _rt=None):
        with (nullcontext(_rt) if _rt is not None else self.store.transaction()) as rt:
            workflows = self.store.workflows(rt)
            for wf in workflows:
                if wf['mode'] != 'real' or wf['status'] != 'queued':
                    continue
                blocker = self._shared_blocker(wf, workflows)
                if blocker:
                    wf.update(status='paused', engineState='device_queue_paused', deviceBlockerId=blocker['id'])
                    self.store.save(rt, wf, 'device_queue_paused', {'message': '共用设备有待核查事项，队列已暂停', 'blockingWorkflowId':blocker['id']})

    def _device_lease(self, rt):
        group = device_key(self.profile)
        for row in rt.db.execute('SELECT * FROM platform_leases'):
            owner = self.store.get(rt, row['workflow_id'])
            profile = (owner or {}).get('configurationBinding', {}).get('profile', self.profile)
            if row['resource'] == group or device_key(profile) == group:
                return row
        return None

    def _attention(self, workflow_id, state, message, *, node_id=None, preserve_diagnostic_stop=False, shared_device_fault=False, ownership_check=None, decision_revision=None):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            if decision_revision is not None and (wf['revision'] != decision_revision or wf.get('cancelRequested')
                    or wf['status'] in TERMINAL or wf['status'] == 'paused' or wf['engineState'] == 'deciding'):
                return False
            if (preserve_diagnostic_stop and wf.get('rbDiagnostic')
                    and (wf['status'] in ('paused', 'aborted') or wf.get('cancelRequested')
                         or wf.get('engineState') == 'needs_reconciliation')):
                if ownership_check is not None:
                    rt._event(workflow_id,'hardware_ownership_rejected',{'message':message,'nodeId':wf.get('currentNodeId'),
                        'attemptId':next((n.get('latestAttemptId') for n in wf['nodes'] if n['id']==wf.get('currentNodeId')),None),
                        'ownershipCheck':ownership_check})
                return
            if wf['status'] in TERMINAL:
                return
            if node_id is not None:
                self._node(wf, node_id)
                wf['currentNodeId'] = node_id
            wf['status'], wf['engineState'] = 'attention', state
            if shared_device_fault:
                wf['sharedDeviceFault'] = True
            if ownership_check is not None and not wf.get('currentNodeId'):
                wf['ownershipCheck']=deepcopy(ownership_check)
            if wf.get('currentNodeId'):
                node = self._node(wf)
                node['status'], node['engineState'], node['detail'] = 'attention', state, message
                if ownership_check is not None:
                    node['ownershipCheck'] = deepcopy(ownership_check)
            self.store.save(rt, wf, 'attention', {'message': message, **({'ownershipCheck':ownership_check} if ownership_check is not None else {})})
            if device_fault_blocks(wf):
                self._pause_shared_queue(_rt=rt)

    def run_workflow(self, workflow_id):
        scoped = self.for_workflow(workflow_id)
        if scoped is not self:
            return scoped.run_workflow(workflow_id)
        wf = self.get(workflow_id)
        if wf['status'] != 'queued':
            return
        if wf.get('configurationIssue'):
            return self._attention(workflow_id, 'configuration_required', wf['configurationIssue']['message'])
        if self._shared_blocker(wf, self.list()):
            self._pause_shared_queue()
            return
        if self._workspace_service is not None and self.workspace.queue_blocker(wf):
            return
        if wf['mode'] == 'replay':
            return self._replay(workflow_id)
        if is_manual(wf) and wf.get('manualStep', {}).get('state') != 'queued':
            return self._attention(workflow_id, 'manual_ready', '请选择步骤、核对参数并明确运行本步')
        try:
            self._assert_adapter_binding(wf)
        except APIError as exc:
            return self._attention(workflow_id, 'adapter_changed', exc.message, preserve_diagnostic_stop=True)
        if wf['engineState'] == 'decision_queued':
            if not self._decide(workflow_id, wf['currentNodeId']):
                return
            wf = self.get(workflow_id)
        preflight = self.adapter.preflight()
        if not preflight.get('ready'):
            return self._attention(workflow_id, 'preflight_failed', '当前执行器静态预检未通过，未连接设备', preserve_diagnostic_stop=True)
        if not preflight.get('enabled', self.profile.get('enabled', False)) and not wf['hardwareConfirmed']:
            return self._attention(workflow_id, 'waiting_hardware', '当前执行器尚未启用；等待现场接线和设备独占交接', preserve_diagnostic_stop=True)
        if not (wf['hardwareConfirmed'] or self.profile.get('connectionConfirmedAt')):
            return self._attention(workflow_id, 'waiting_hardware', '请确认当前是主机读出/XY/采集卡接线', preserve_diagnostic_stop=True)
        ownership = self.hardware_ownership(refresh=True)
        if not ownership['verified'] or ownership['owners']:
            refusal=OwnershipRefusal(wf, next((n for n in wf['nodes'] if n['id']==wf.get('currentNodeId')), {}), ownership, 'schedule')
            return self._attention(workflow_id, 'waiting_hardware', refusal.message, preserve_diagnostic_stop=True, shared_device_fault=True, ownership_check=refusal.ownershipCheck)
        diagnostic = wf.get('rbDiagnostic') or {}
        if diagnostic.get('state') == 'queued':
            return self._run_rb_diagnostic(workflow_id, diagnostic['id'])
        if diagnostic and wf.get('currentNodeId') == diagnostic.get('targetNodeId'):
            return self._attention(workflow_id, 'diagnostic_closed', '单次RB诊断许可已关闭，未自动重发或推进原流程', preserve_diagnostic_stop=True)
        if is_manual(wf):
            return self._run_manual_step(workflow_id)
        while not self.stop_event.is_set():
            wf = self.get(workflow_id)
            if wf['status'] not in ('queued', 'running'):
                return
            if wf.get('cancelRequested'):
                return self._finish_cancel(workflow_id)
            pending = [n for n in wf['nodes'] if n['engineState'] not in ('verified', 'acknowledged', 'unsupported')]
            ready = [n for n in pending if all(self._node(wf, d)['engineState'] == 'verified' for d in n['dependsOn'])]
            if not pending:
                return self._complete(workflow_id)
            if not ready:
                return self._attention(workflow_id, 'prerequisites_missing', '前置校准未验证或已失效，等待复测')
            node = ready[0]
            if node['capability'] != 'available':
                return self._attention(workflow_id, 'unsupported_method', '当前方法尚未接入：' + node['label'], node_id=node['id'])
            if task_budget.exhausted(wf, 'maxWorkflowAttempts', wf['iterations']) or task_budget.exhausted(wf, 'maxAttemptsPerNode', node['attempt']):
                return self._attention(workflow_id, 'budget_exhausted', '达到本轮测量预算，保留全部候选与结果', node_id=node['id'])
            if not self._execute_node(workflow_id, node['id']):
                return
            if self._yield_to_waiter(workflow_id):
                return

    def _replay(self, workflow_id):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            wf['status'], wf['engineState'] = 'running', 'replaying'
            self.store.save(rt, wf, 'replay_started', {'message': '开始回放已登记的真实历史记录；不会连接仪器'})
        # Replay follows original chronology, preserving every procedure and attempt.
        records = sorted((r for r in self.records if r['resourceId'] in wf['resourceIds']),
                         key=lambda r: (r['result'].get('acquiredAt') or '', r['id']))
        for index, record in enumerate(records):
            if self.stop_event.is_set():
                return
            with self.store.transaction() as rt:
                wf = self.get(workflow_id, rt)
                if wf.get('cancelRequested'):
                    self._finish_cancel_locked(rt, wf)
                    return
                if record['id'] in wf['processedRecordIds']:
                    continue
                node_id = record['resourceId'] + ':' + record['ownerId']
                node = self._node(wf, node_id)
                node['attempt'] += 1
                result = evaluate(record['result'], record['procedureId'], 'replay')
                result['artifacts'] = [self._artifact(rt, a, verify=True) for a in result['artifacts']]
                attempt = {'id': ident('attempt'), 'nodeId': node_id, 'number': node['attempt'], 'state': 'historical',
                           'startedAt': now(), 'endedAt': now(), 'parameters': record['parameters'], 'parameterVersions': {},
                           'result': result, 'operationId': wf['id'] + '/replay/' + record['id'], 'mode': 'replay',
                           'procedureId': record['procedureId'], 'sourceRecordId': record['id']}
                self.store.attempt(rt, wf['id'], attempt)
                node.update(result=result, parameters=deepcopy(record['parameters']), latestAttemptId=attempt['id'],
                            status='attention', engineState='historical', progress=100,
                            detail='历史实测：' + result.get('methodLabel', record['procedureId']) + '；当前校准未验证')
                wf['currentNodeId'], wf['progress'] = node_id, round((index + 1) * 100 / max(1, len(records)))
                wf['iterations'] += 1
                wf['processedRecordIds'].append(record['id'])
                self.store.save(rt, wf, 'historical_record', {'message': f"回放 {record['resourceId']} · {result.get('methodLabel', record['procedureId'])}", 'recordId': record['id'], 'nodeId': node_id})
            self.stop_event.wait(.08)
        self._complete(workflow_id)

    def _complete(self, workflow_id):
        if self.get(workflow_id).get('smartRun', {}).get('schema') == 'q11-full-same-gate/1':
            from .smart_science import begin
            if begin(self, workflow_id):
                return
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            passed = [n for n in wf['nodes'] if n['engineState'] == 'verified']
            wf['workflowComplete'] = wf['mode'] == 'real' and len(passed) == len(wf['nodes'])
            if wf['mode'] == 'real' and not wf['workflowComplete']:
                wf['status'], wf['engineState'] = 'attention', 'coverage_incomplete'
                wf['progress'] = round(100 * len(passed) / max(1, len(wf['nodes'])))
            else:
                wf['status'], wf['engineState'], wf['progress'] = 'completed', ('verified' if wf['workflowComplete'] else 'replay_completed'), 100
            self._finish_timing(wf)
            wf['resultSummary'] = {'verifiedNodes': len(passed), 'totalNodes': len(wf['nodes']),
                                   'coverage': wf.get('coverage', 'main'),
                                   'recordCount': len(wf['processedRecordIds']), 'workflowComplete': wf['workflowComplete'],
                                   'meaning': '历史记录读取完成，不等同于本轮全部实验通过' if wf['mode'] == 'replay' else ('本轮所选测项全部独立验证完成' if wf['workflowComplete'] else '本轮测项尚未全部完成，详见能力和质量状态')}
            self.store.save(rt, wf, 'completed' if wf['status'] == 'completed' else 'coverage_incomplete', {'message': wf['resultSummary']['meaning']})

    def _finish_cancel_locked(self, rt, wf):
        action = wf.get('cancelRequested') or 'pause'
        wf['status'] = 'aborted' if action == 'abort' else 'paused'
        wf['engineState'], wf['cancelRequested'] = wf['status'], None
        if action != 'abort' and is_manual(wf) and awaiting_acceptance(wf):
            wf['engineState'] = 'awaiting_acceptance'
        diagnostic = wf.get('rbDiagnostic') or {}
        if diagnostic.get('state') in ('queued', 'running'):
            diagnostic.update(state='interrupted', finishedAt=now(), resultStatus='unverified')
            wf['workflowComplete'] = False
        self._finish_timing(wf)
        self.store.save(rt, wf, wf['status'], {'message': '已在无活动采集的检查点' + ('终止' if action == 'abort' else '暂停')})

    def _finish_cancel(self, workflow_id):
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            if self._has_lease(rt, workflow_id):
                wf['status'], wf['engineState'] = 'attention', 'needs_reconciliation'
                self.store.save(rt, wf, 'reconciliation_required', {'message': '停止状态尚未确认，保持设备隔离'})
            else:
                self._finish_cancel_locked(rt, wf)

    def _parameters(self, rt, wf, node):
        prefix = wf['id'] + ':' + node['resourceId'] + ':'
        values = {k: v for k, v in self.profile.get('resources', {}).get(node['resourceId'], {}).items()
                  if k not in ('q', 'calibrationStatus') and (number(v) or k in self.profile.get('parameterBounds', {}))}
        if wf.get('stabilityBinding'):
            values.update(wf.get('initialParameters', {}))
        if (wf.get('smartRun') or {}).get('schema') == 'q11-full-same-gate/1':
            initialization = wf['smartRun'].get('initialization', {})
            values.update(initialization.get('parameters', {}))
            from .smart_q11_launch import spectroscopy_initial_hints
            values.update(spectroscopy_initial_hints(wf, node, initialization, values))
        versions = {k: 0 for k in values}
        for key, entry in rt.get_parameters().items():
            if key.startswith(prefix):
                values[key[len(prefix):]], versions[key[len(prefix):]] = entry['value'], entry['revision']
        # Observed parameters are snapshots, not overrides. A retry/backtrack
        # must inherit freshly committed upstream values unless explicitly tuned.
        values.update(node.get('parameterOverrides', {}))
        if node['procedureId'] in SCIENCE_METHODS:
            from adapters.science_v3.plans import BOUNDS
            allowed=set(self.adapter.validation_parameter_keys()) if node['procedureId'] in SCIENCE_IRB_METHODS or node['procedureId'] in SCIENCE_FLUX_METHODS else set(BOUNDS)|{'scan_axis_values'}
            if wf.get('smartParent') and node['procedureId']=='sq_drag_half_scan':
                from adapters.science_v3_irb.half_scan import SCAN_FIELDS
                allowed.intersection_update(SCAN_FIELDS)
            elif wf.get('smartParent') and node['procedureId']=='sq_drag_irb':
                from adapters.science_v3_irb.plans import FIELDS, DEFAULTS
                allowed.intersection_update(FIELDS|set(DEFAULTS)|{'pulse_compatibility'})
            values = {key: value for key, value in values.items() if key in allowed}
            versions = {key: versions.get(key, 0) for key in values}
        return values, versions

    def _readout_revalidation(self, rt, wf, node, parameters=None, versions=None):
        marker = wf.get('readoutRevalidations', {}).get(node['resourceId'])
        if not marker or marker['state'] not in ('active', 'fixed_failed'):
            return None
        # An uncommitted override is not a new accepted working point and must
        # never release the fixed-point requirement.
        parameters, versions = self._parameters(rt, wf, {**node, 'parameterOverrides': {}})
        mismatches = [k for k, value in marker['values'].items()
                      if not self._same_parameter(value, parameters.get(k)) or marker['parameterVersions'][k] != versions.get(k, 0)]
        if mismatches:
            marker.update(state='invalidated', endedAt=now(), reason='读出工作点或版本已改变：' + ', '.join(mismatches))
            self._record_decision(rt, wf, node['id'], {'action': 'invalidate', 'reason': marker['reason'],
                'evidenceIds': [], 'revalidationId': marker['id'], 'ruleId': 'readout-revalidation/1'}, 'rule')
            return None
        return marker

    def _readout_revalidation_parameters(self, node, parameters, marker):
        if not marker:
            return parameters
        if node['id'] in marker.get('lockedNodeIds', [marker['optimizerNodeId']]):
            mismatches = [k for k, v in marker['values'].items() if not self._same_parameter(parameters.get(k), v)]
            if mismatches:
                raise APIError('READOUT_FIXED_WORKING_POINT', '当前读出重建不允许覆盖已提交工作点：' + ', '.join(mismatches))
        if node['id'] != marker['optimizerNodeId']:
            return parameters
        hook = getattr(self.adapter, 'build_verification_parameters', None)
        if not callable(hook):
            raise APIError('READOUT_REVALIDATION_UNSUPPORTED', '执行器尚未支持固定读出候选验证')
        return hook(node['procedureId'], deepcopy(parameters), deepcopy(marker['values']))

    def _readout_retry_parameters(self, rt, wf, node, parameters, versions, changes):
        marker = self._readout_revalidation(rt, wf, node, parameters, versions)
        trial = {**parameters, **changes}
        if not marker or node['id'] not in marker.get('lockedNodeIds', [marker['optimizerNodeId']]):
            return trial, None
        if any(k in changes and not self._same_parameter(changes[k], v) for k, v in marker['values'].items()):
            raise APIError('READOUT_FIXED_WORKING_POINT', '当前重建固定已提交读出工作点；需通过固定点验证失败后的范围重扫选择新候选')
        scan_keys = {'amp_start', 'amp_stop'}
        if node['id'] == marker['optimizerNodeId'] and scan_keys.intersection(changes):
            if not scan_keys.issubset(changes):
                raise APIError('READOUT_RESCAN_RANGE_REQUIRED', '读出重扫必须同时明确 amp_start 和 amp_stop')
            if (marker['state'] != 'fixed_failed' or marker.get('lastFixedAttemptId') != node.get('latestAttemptId')
                    or node.get('result', {}).get('quality', {}).get('status') != 'failed' or node.get('result', {}).get('stale')):
                raise APIError('READOUT_FIXED_VALIDATION_REQUIRED', '必须先完成当前候选的固定点验证并确认未通过，才能选择重新全扫')
            if changes['amp_stop'] <= changes['amp_start'] or not callable(getattr(self.adapter, 'build_plan', None)):
                raise APIError('READOUT_RESCAN_INVALID', '读出重扫需要递增范围及执行器网格校验')
            trial.pop('amplitudes', None)
            return trial, marker
        return self._readout_revalidation_parameters(node, trial, marker), None

    @staticmethod
    def _readout_revalidation_quality(request, result):
        marker = request.get('readoutRevalidation')
        if not marker or request['nodeId'] != marker['optimizerNodeId']:
            return
        reasons = []
        actual = result.get('parameters') or {}
        for key, value in marker['values'].items():
            if not Controller._same_parameter(actual.get(key), value):
                reasons.append('固定读出重建回执工作点不符：' + key)
        amplitudes = actual.get('amplitudes')
        if not isinstance(amplitudes, list) or len(amplitudes) != 1 or not Controller._same_parameter(amplitudes[0], marker['values']['readout_amp']):
            reasons.append('读出重建必须只验证已选幅度，不能重新全扫选优')
        candidate = result.get('candidateParameters')
        if not isinstance(candidate, dict) or not Controller._same_parameter(candidate.get('readout_amp'), marker['values']['readout_amp']):
            reasons.append('固定读出候选未被本轮采集确认')
        if isinstance(candidate, dict) and 'readout_hz' in candidate and not Controller._same_parameter(candidate['readout_hz'], marker['values']['readout_hz']):
            reasons.append('固定读出频率候选不符')
        if reasons:
            result['quality']['status'] = 'failed'
            result['quality']['reasons'].extend(reasons)

    def _start_readout_revalidation(self, rt, wf, optimizer):
        nodes = {n['procedureId']: n for n in wf['nodes'] if n['resourceId'] == optimizer['resourceId']}
        rabi, time_rabi, iq = (nodes.get(k) for k in ('sq_rabi_amplitude', 'sq_rabi_time', 'ro_iq01'))
        if (not all((rabi, time_rabi, iq)) or rabi['id'] not in time_rabi['dependsOn']
                or time_rabi['id'] not in iq['dependsOn'] or iq['id'] not in optimizer['dependsOn']):
            raise APIError('READOUT_REVALIDATION_UNSUPPORTED', '当前蓝图缺少完整 Rabi→时间 Rabi→IQ→读出优化依赖链，不能宣称完成新读出工作点重建')
        values, versions = self._parameters(rt, wf, {**optimizer, 'parameterOverrides': {}})
        required = ('readout_hz', 'readout_amp')
        if any(not number(values.get(k)) for k in required):
            raise APIError('READOUT_REVALIDATION_UNSUPPORTED', '读出重建缺少完整的频率和幅度工作点')
        markers = wf.setdefault('readoutRevalidations', {})
        if optimizer['resourceId'] in markers:
            wf.setdefault('readoutRevalidationHistory', []).append(deepcopy(markers[optimizer['resourceId']]))
        marker = {'id': ident('readout-revalidation'), 'ruleVersion': 'readout-revalidation/1',
                  'resourceId': optimizer['resourceId'], 'optimizerNodeId': optimizer['id'],
                  'rabiNodeId': rabi['id'], 'sourceAttemptId': optimizer.get('latestAttemptId'),
                  'values': {k: values[k] for k in required},
                  'parameterVersions': {k: versions.get(k, 0) for k in required},
                  'state': 'active', 'createdAt': now()}
        markers[optimizer['resourceId']] = marker
        affected = self._invalidate(wf, rabi['id'], include_target=True)
        marker['lockedNodeIds'] = list(affected)
        removed_overrides = {}
        for node in wf['nodes']:
            if node['id'] in affected:
                old = node.get('parameterOverrides', {})
                removed = {k: old.pop(k) for k in required if k in old}
                if removed:
                    removed_overrides[node['id']] = removed
        wf.update(currentNodeId=rabi['id'], status='paused' if wf['policy'] == 'strict' else 'queued',
                  engineState='readout_revalidation_pending' if wf['policy'] == 'strict' else 'backtrack_queued')
        wf['progress'] = round(100 * sum(n['engineState'] == 'verified' for n in wf['nodes']) / len(wf['nodes']))
        rabi['detail'] = '读出工作点已更新；从 Rabi 开始重建当前校准，随后固定候选复验读出'
        self._record_decision(rt, wf, optimizer['id'], {'action': 'backtrack', 'reason': rabi['detail'],
            'targetNodeId': rabi['id'], 'invalidatedNodes': affected, 'evidenceIds': [],
            'revalidationId': marker['id'], 'ruleId': marker['ruleVersion'],
            'removedReadoutOverrides': removed_overrides}, 'rule')
        return rabi['id']

    def _execute_node(self, workflow_id, node_id, *, diagnostic_branch_id=None):
        scoped = self.for_node(workflow_id, node_id)
        if scoped is not self:
            return scoped._execute_node(workflow_id, node_id, diagnostic_branch_id=diagnostic_branch_id)
        wf_before = self.get(workflow_id)
        if is_manual(wf_before):
            self._assert_manual_dispatch(wf_before, node_id)
        self._assert_adapter_binding(wf_before)
        try:
            supplemental = self._assert_supplemental_snapshot(wf_before)
        except (APIError, OSError, ValueError, TypeError) as exc:
            self._attention(workflow_id, 'supplemental_acceptance_mismatch', str(exc), node_id=node_id)
            return False
        node_before = self._node(wf_before, node_id)
        if wf_before.get('stabilityBinding'):
            from .stability import admit_t1
            if not admit_t1(self, wf_before, node_before):
                return False
        if node_before['procedureId'] == 'sq_rb' and diagnostic_branch_id is None:
            if not self._rb_prerequisites_current(workflow_id, node_id):
                return False
        with self.store.transaction() as rt:
            wf, attempt_id = self.get(workflow_id, rt), ident('attempt')
            node = self._node(wf, node_id)
            manual = is_manual(wf)
            if manual:
                self._assert_manual_dispatch(wf, node_id)
            if diagnostic_branch_id is not None:
                self._assert_rb_diagnostic_dispatch(rt, wf, node, diagnostic_branch_id)
            group = device_key(self.profile)
            if self._device_lease(rt):
                wf['status'], wf['engineState'] = 'attention', 'resource_busy'
                self.store.save(rt, wf, 'attention', {'message': '共享仪器仍占用或隔离中'})
                return False
            parameters, versions = self._parameters(rt, wf, node)
            marker = self._readout_revalidation(rt, wf, node, parameters, versions)
            parameters = self._readout_revalidation_parameters(node, parameters, marker)
            lookahead = None
            if self._physical_adapter:
                try:
                    lookahead = self._validate_plan(wf, node, parameters, versions, attempt_id,
                                                    joint_budget=True, rt=rt)
                except APIError as exc:
                    # Admission is before lease, effect, attempt count or result replacement.
                    wf.update(status='attention', currentNodeId=node_id,
                              engineState='budget_exhausted' if 'BUDGET' in exc.code else 'plan_rejected')
                    node.update(status='attention', engineState=wf['engineState'], detail=exc.message,
                                lastAdmissionError={'code': exc.code, 'message': exc.message, 'at': now()})
                    self.store.save(rt, wf, 'acquisition_admission_rejected',
                                    {'nodeId': node_id, 'errorCode': exc.code, 'message': exc.message})
                    return False
            token = ident('lease')
            if manual:
                wf['manualStep'].update(state='running', attemptId=attempt_id)
            rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)', (group, workflow_id, attempt_id, 'active', token))
            node['attempt'] += 1
            attempt = {'id': attempt_id, 'nodeId': node_id, 'number': node['attempt'], 'state': 'running', 'startedAt': now(),
                       'parameters': parameters, 'parameterVersions': versions, 'operationId': attempt_id + '/measure',
                       'mode': 'real', 'procedureId': node['procedureId']}
            request = {'runId': workflow_id, 'nodeId': node_id, 'attemptId': attempt_id,
                       'operationId': attempt['operationId'], 'resourceId': node['resourceId'], 'procedureId': node['procedureId'],
                       'parameters': parameters, 'parameterVersions': versions,
                       'adapterId': self.adapter_id, 'adapterProfileHash': self.adapter_profile_hash,
                       'deadline': time.time() + self.profile.get('maximumRunSeconds', 3600),
                       'outputDir': str(self.root / 'data' / 'runs' / workflow_id / attempt_id), 'leaseToken': token}
            from .attempt_execution import claim
            claim(self, rt, wf, node, attempt, request)
            from .smart_rabi_window import claim as claim_rabi_window
            claim_rabi_window(wf, node, attempt, request)
            if node['procedureId'] in SCIENCE_METHODS:
                request['configurationBinding'] = deepcopy(self.profile.get('configurationBinding', {}))
                request['explicitParameterKeys'] = sorted(node.get('parameterOverrides', {}))
            if diagnostic_branch_id is not None:
                wf['rbDiagnostic'].update(state='running', attemptId=attempt_id)
                branch = {k: wf['rbDiagnostic'][k] for k in ('id','policyVersion','commandOperationId',
                    'sourceNodeId','sourceAttemptId','sourceProofSha256','publication')}
                request['rbDiagnostic'] = deepcopy(branch)
                attempt['rbDiagnostic'] = deepcopy(branch)
            if lookahead is not None:
                request['budgetLookahead'] = deepcopy(lookahead)
                attempt['budgetLookahead'] = deepcopy(lookahead)
            if marker:
                request['readoutRevalidation'] = deepcopy(marker)
                attempt['readoutRevalidation'] = deepcopy(marker)
                if node_id == marker['optimizerNodeId']:
                    marker['state'] = 'active'
            request['validationSnapshot'] = self._validation_snapshot(rt, wf, node, parameters, versions)
            if supplemental and node['id'] in supplemental['applicableNodeIds']:
                request['supplementalAcceptance'] = {k: supplemental[k] for k in
                    ('policyVersion', 'policyHash', 'moduleSha256', 'sourceManifestSha256', 'baseGatesSha256')}
            self._science_request_dependencies(rt, wf, node, request)
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)',
                          (request['operationId'], workflow_id, attempt_id, 'pending', dumps(request)))
            self.store.attempt(rt, workflow_id, attempt)
            node['lastCompletedAttemptId'] = node.get('latestAttemptId')
            node.pop('result', None)
            node.pop('telemetry', None)
            # A prior refusal stays in its event/attempt; do not present it as
            # the ownership observation for this new acquisition attempt.
            node.pop('ownershipCheck', None)
            node.update(status='running', engineState='acquiring', latestAttemptId=attempt_id,
                        parameters=parameters, parameterVersions=versions, progress=0,
                        detail='正在采集本轮新数据')
            wf.update(status='running', engineState='acquiring', currentNodeId=node_id, iterations=wf['iterations'] + 1)
            if not wf.get('firstAcquisitionAt'):
                wf['firstAcquisitionAt'] = attempt['startedAt']
            self.store.save(rt, wf, 'acquisition_started', {'message': node['qubits'] + ' · ' + node['label'], 'attemptId': attempt_id})
        preliminary = verification = verify_request = None
        command_finalization_attempted=False
        try:
            result = self._call_adapter(request, token)
            if result is None:
                return False
            preliminary = evaluate(result, request['procedureId'], 'real', wf['gates'], independent=False,
                                   rule_version=wf['gatesVersion'])
            self._readout_revalidation_quality(request, preliminary)
            self._apply_supplemental_acceptance(wf, request, preliminary, 'selection')
            if diagnostic_branch_id is not None:
                self._rb_diagnostic_candidate_contract(preliminary, 'selection')
            candidate = self._validated_candidate(request['procedureId'], preliminary, 'selection')
            # Only start independent verification once all numerical gates are already satisfied.
            gate_reasons = [r for r in preliminary['quality']['reasons'] if r != '需要独立采集复测']
            independent = False
            verification = None
            if not gate_reasons and not self._cancel_requested(workflow_id):
                verify_request = deepcopy(request)
                verify_request['operationId'] = attempt_id + '/verify'
                if request['procedureId'] in SCIENCE_FLUX_METHODS:
                    from .flux_execution import verification_authorization
                    verification_authorization(self,verify_request,request['operationId'])
                # Each independent acquisition has its own operation time limit.
                verify_request['deadline'] = time.time() + self.profile.get('maximumRunSeconds', 3600)
                raw_candidate = deepcopy(result.get('candidateParameters', {}))
                for key, value in candidate.items():
                    verify_request['parameters'][key] = value
                verification_parameters = getattr(self.adapter, 'build_verification_parameters', None)
                if callable(verification_parameters):
                    verify_request['parameters'] = verification_parameters(request['procedureId'], verify_request['parameters'], candidate)
                with self.store.transaction() as rt:
                    current = self.get(workflow_id, rt)
                    if manual:
                        self._assert_manual_dispatch(current, node_id, attempt_id)
                    if diagnostic_branch_id is not None:
                        self._assert_rb_diagnostic_dispatch(rt, current, self._node(current, node_id), diagnostic_branch_id, attempt_id=attempt_id)
                    # The first acquisition is complete and registered. Publish it
                    # now, without implying that independent verification passed.
                    attempt['result'] = {'dataKind': 'real', 'partial': True, 'verificationPending': True,
                        'metrics': {}, 'artifacts': [],
                        'quality': {'status': 'unverified', 'reasons': ['首测已完成；独立复测尚未验收']},
                        'selectionResult': deepcopy(preliminary)}
                    self._node(current).update(engineState='verifying', detail='正在独立复测候选参数',
                        result=deepcopy(attempt['result']), mainProcedureComplete=False)
                    verify_request['validationSnapshot'] = self._validation_snapshot(rt, current, self._node(current), verify_request['parameters'], versions)
                    self._science_request_dependencies(rt, current, self._node(current), verify_request)
                    if self._physical_adapter:
                        # Candidate changes are now known. Check only verification's
                        # actual cost; selection has already consumed its reservation.
                        verify_request['verificationBudgetCheck'] = verify_admission(
                            current, self.adapter.build_plan(verify_request), request.get('budgetLookahead'))
                    self._science_request_dependencies(rt, current, self._node(current), verify_request)
                    rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)',
                                  (verify_request['operationId'], workflow_id, attempt_id, 'pending', dumps(verify_request)))
                    self.store.attempt(rt, workflow_id, attempt)
                    self.store.save(rt, current, 'verification_started', {'message': '开始新的独立采集验证候选参数'})
                verification = self._call_adapter(verify_request, token)
                if verification is None:
                    return False
                source_ids = {a['sha256'] for a in result.get('artifacts', []) if a['kind'] == 'raw'}
                verify_ids = {a['sha256'] for a in verification.get('artifacts', []) if a['kind'] == 'raw'}
                independent = bool(source_ids and verify_ids and source_ids.isdisjoint(verify_ids))
                result = evaluate(verification, request['procedureId'], 'real', wf['gates'], independent=independent,
                                  rule_version=wf['gatesVersion'])
                if request['procedureId'] in SCIENCE_FLUX_METHODS:
                    from .science_flux_quality import check_independent
                    cross_reasons=check_independent(preliminary,verification,request['procedureId'])
                    if cross_reasons:
                        result['quality'].update(status='failed',reasons=result['quality']['reasons']+cross_reasons)
                if request['procedureId']=='sq_drag_irb':
                    from .science_irb_quality import check_independent
                    cross_reasons=check_independent(preliminary,verification)
                    if cross_reasons:
                        result['quality'].update(status='failed',reasons=result['quality']['reasons']+cross_reasons)
                        result['diagnosticMetrics']={key:result['metrics'].pop(key) for key in ('gate_fidelity','clifford_fidelity') if key in result['metrics']}
                self._readout_revalidation_quality(verify_request, result)
                if preliminary.get('supplementalQuality'):
                    result['supplementalQuality'] = deepcopy(preliminary['supplementalQuality'])
                self._apply_supplemental_acceptance(wf, verify_request, result, 'verification')
                self._apply_supplemental_acceptance(wf, verify_request, result, 'cross', selection=(request, preliminary))
                if diagnostic_branch_id is not None:
                    self._rb_diagnostic_candidate_contract(result, 'verification')
                repeated_candidate = self._validated_candidate(request['procedureId'], result, 'verification')
                consistency = self._repeatability(candidate, repeated_candidate,
                                                  preliminary.get('parameters') or request['parameters']) if repeated_candidate is not None else []
                if consistency:
                    result['quality']['status'] = 'failed'
                    result['quality']['reasons'].extend(consistency)
                result['selectionResult'] = preliminary
                result['verificationCandidateParameters'] = deepcopy(verification.get('candidateParameters', {}))
                result['candidateParameters'] = candidate
                if candidate != raw_candidate:
                    result['candidateNormalization'] = {'requested': raw_candidate, 'applied': candidate,
                                                        'reason': '执行器脉冲策略及采样网格规范化', 'adapterId': self.adapter_id}
            else:
                result = preliminary
            from .flux_execution import finalize_lease
            command_finalization_attempted=True
            finalize_lease(self,request,token)
            with self.store.transaction() as rt:
                current = self.get(workflow_id, rt)
                current_node = self._node(current, node_id)
                if manual:
                    self._assert_manual_dispatch(current, node_id, attempt_id)
                if diagnostic_branch_id is not None:
                    self._assert_rb_diagnostic_dispatch(rt, current, current_node, diagnostic_branch_id, attempt_id=attempt_id, allow_cancel=True)
                    result['diagnosticScope'] = {**deepcopy(request['rbDiagnostic']),
                        'scope': 'rb_only_after_failed_cpmg', 'workflowComplete': False,
                        'sourceQualityStatus': 'failed', 'publication': 'disabled_diagnostic_only'}
                rebuild_target = None
                if result['quality']['status'] == 'passed':
                    context_reasons = self._calibration_context_reasons(rt, current, current_node, result)
                    if context_reasons:
                        result['quality']['status'] = 'failed'
                        result['quality']['reasons'].extend(context_reasons)
                if result.get('supplementalQuality') and result['quality']['status'] != 'passed':
                    # Already registered by _call_adapter. Preserve the original
                    # SHA metadata plus rejection receipt when a later integrity
                    # check fails; never replace the native file or its hash.
                    result['artifacts'] = deepcopy(result.get('artifacts', []))
                else:
                    result['artifacts'] = [self._artifact(rt, a) for a in result.get('artifacts', [])]
                attempt.update(state='verified' if result['quality']['status'] == 'passed' else 'attention', endedAt=now(), result=result,
                               verificationOperationId=attempt_id + '/verify' if verification else None)
                self.store.attempt(rt, workflow_id, attempt)
                passed = result['quality']['status'] == 'passed'
                if passed and manual:
                    attempt['state'] = 'awaiting_acceptance'
                    self.store.attempt(rt, workflow_id, attempt)
                current_node.update(result=result, progress=100, status='success' if passed else 'attention',
                                    engineState='verified' if passed else 'quality_attention', mainProcedureComplete=passed,
                                    detail='独立复测通过' if passed else '；'.join(result['quality']['reasons']))
                if manual:
                    current_node.update(status='attention', engineState='awaiting_acceptance' if passed else 'quality_attention',
                                        mainProcedureComplete=False)
                    current.update(status='attention', engineState=current_node['engineState'], approved=False)
                    current['manualStep'].update(state='finished', finishedAt=now(), resultStatus=result['quality']['status'])
                if diagnostic_branch_id is not None:
                    current_node['diagnosticScope'] = deepcopy(result['diagnosticScope'])
                    current_node['detail'] = ('RB诊断独立验收通过；CPMG仍未通过，不发布完整校准' if passed else
                                              'RB诊断未通过：' + '；'.join(result['quality']['reasons']))
                # Finish receipts are not claims that the physical RF output is proven OFF.
                rt.db.execute('DELETE FROM platform_leases WHERE workflow_id=? AND token=?', (workflow_id, token))
                if passed and not manual:
                    if diagnostic_branch_id is None:
                        rebuild_target = self._commit_candidate(rt, current, current_node, result.get('candidateParameters', {}), versions)
                    if diagnostic_branch_id is None and current_node['procedureId'] == 'sq_rb' and self._physical_adapter and callable(getattr(self.adapter, 'publish_calibration', None)):
                        snapshot = self._validation_snapshot(rt, current, current_node, parameters, versions)
                        snapshot['acceptedEvidence'].append({'procedureId': 'sq_rb', 'status': 'passed',
                            'selectionOperationId': attempt['operationId'], 'verificationOperationId': attempt['verificationOperationId'],
                            'ruleVersion': current['gatesVersion']})
                        calibration = self.adapter.publish_calibration(snapshot)
                        result['artifacts'].append(self._artifact(rt, calibration))
                        current_node['calibrationVersion'] = calibration['calibrationVersion']
                        attempt['result'] = result
                        self.store.attempt(rt, workflow_id, attempt)
                    previous = [json.loads(r[0]) for r in rt.db.execute('SELECT payload FROM platform_decisions WHERE workflow_id=? AND node_id=? ORDER BY rowid DESC',
                                                                       (workflow_id, node_id))]
                    prior_decision = next((d for d in previous if d.get('source') == 'model' and d.get('action') == 'retry'), None)
                    if prior_decision:
                        candidate_experience = self.experience.observe_verified(rt, current, current_node, attempt, prior_decision)
                        if candidate_experience:
                            current_node['experienceCandidateId'] = candidate_experience['id']
                current_marker = self._readout_revalidation(rt, current, current_node)
                if request.get('readoutRevalidation') and current_marker and node_id == current_marker['optimizerNodeId']:
                    current_marker.update(state=('active' if manual else 'completed') if passed else 'fixed_failed',
                                          lastFixedAttemptId=attempt_id)
                    if manual or current_marker.get('origin') == 'operator_withdrawal':
                        current_marker['validationStatus'] = ('awaiting_acceptance' if manual else 'verified') if passed else 'failed'
                    if passed and not manual:
                        current_marker['endedAt'] = now()
                current['progress'] = round(100 * sum(n['engineState'] == 'verified' for n in current['nodes']) / len(current['nodes']))
                from .smart_main_checkpoint import record as record_smart_main
                main_checkpointed = (not manual and diagnostic_branch_id is None
                    and record_smart_main(self, rt, current, node_id))
                self.store.save(rt, current, 'readout_revalidation_started' if rebuild_target else ('awaiting_acceptance' if manual else 'node_verified') if passed else 'quality_attention',
                                {'message': self._node(current)['detail'] if rebuild_target else current_node['label'] + '：' + current_node['detail'], 'attemptId': attempt_id})
            if self._cancel_requested(workflow_id):
                self._finish_cancel(workflow_id)
                return False
            if diagnostic_branch_id is not None:
                # One explicit permission ends here, on either scientific outcome.
                # It never enters normal approve/autopilot/publication scheduling.
                return False
            if manual:
                return False
            if main_checkpointed:
                # The durable queue owns the next transition. In particular a
                # failed result reaches _decide once through decision_queued.
                self.wake.set()
                return False
            if passed:
                if rebuild_target:
                    return current['status'] == 'queued'
                if current['policy'] == 'strict':
                    self._attention(workflow_id, 'awaiting_acceptance', '独立复测通过，等待确认后推进')
                    return False
                return True
            if self._yield_to_waiter(workflow_id, decision=True):
                return False
            return self._decide(workflow_id, node_id)
        except Exception as exc:
            ownership_refusal=exc if isinstance(exc,OwnershipRefusal) else None
            finalization_failed=isinstance(exc,APIError) and exc.code=='BIAS_LOCK_RECONCILIATION'
            if not command_finalization_attempted:
                try:
                    from .flux_execution import finalize_lease
                    finalize_lease(self,request,token)
                except Exception as release_exc:
                    finalization_failed=True
                    exc=APIError('BIAS_LOCK_RECONCILIATION',str(exc)+'；'+str(release_exc),409)
            with self.store.transaction() as rt:
                current = self.get(workflow_id, rt)
                if diagnostic_branch_id is not None and isinstance(exc, APIError) and exc.code.startswith('RB_DIAGNOSTIC_'):
                    diagnostic = current.get('rbDiagnostic') or {}
                    if diagnostic.get('id') == diagnostic_branch_id:
                        diagnostic.update(state='invalidated', errorCode=exc.code)
                stored_node = self._node(current, node_id)
                stored_result = stored_node.get('result') or {}
                if stored_node.get('latestAttemptId') == attempt_id and stored_result.get('verificationPending'):
                    # A failed final commit may roll back the database while the
                    # local attempt already holds a passed result. Use the durable
                    # unaccepted placeholder before clearing pending below.
                    attempt['result'] = deepcopy(stored_result)
                pending = finalization_failed or rt.db.execute("SELECT 1 FROM platform_effects WHERE attempt_id=? AND state IN ('pending','unknown')", (attempt_id,)).fetchone()
                state = 'needs_reconciliation' if pending else 'execution_failed'
                if not pending and isinstance(exc, APIError) and 'BUDGET' in exc.code:
                    state = 'budget_exhausted'
                if pending:
                    rt.db.execute("UPDATE platform_leases SET state='quarantined' WHERE token=?", (token,))
                else:
                    rt.db.execute('DELETE FROM platform_leases WHERE token=?', (token,))
                stopped = list(rt.db.execute("SELECT state,result FROM platform_effects WHERE attempt_id=? AND state IN ('cancelled','failed_after_shutdown')", (attempt_id,)))
                stopped_receipts = [json.loads(row['result']) for row in stopped]
                if stopped:
                    if not pending:
                        state = 'cancelled' if all(row['state'] == 'cancelled' for row in stopped) else 'failed_after_shutdown'
                    artifacts = {a['id']: a for row in stopped for a in json.loads(row['result']).get('partialArtifacts', [])}
                    attempt['result'] = self._partial_result(list(artifacts.values()), stopped_receipts)
                preserved_selection = bool(attempt.get('result', {}).pop('verificationPending', False))
                if preserved_selection:
                    attempt['result']['quality']['reasons'] = ['独立复测未完成验收；已完成的候选采集单独保留']
                if (verify_request is not None and verification is None and isinstance(preliminary, dict)
                        and preliminary.get('dataKind') == 'real' and not preliminary.get('partial')
                        and rt.db.execute("SELECT 1 FROM platform_effects WHERE operation_id=? AND state='completed'",
                                          (request['operationId'],)).fetchone()):
                    if not attempt.get('result'):
                        attempt['result'] = {'dataKind': 'real', 'partial': True, 'metrics': {}, 'artifacts': [],
                            'quality': {'status': 'unverified', 'reasons': ['独立复测尚未完成；已完成的候选采集单独保留']}}
                    attempt['result']['selectionResult'] = deepcopy(preliminary)
                    preserved_selection = True
                if verify_request is not None and verification is not None:
                    effect = rt.db.execute("SELECT * FROM platform_effects WHERE operation_id=? AND workflow_id=? AND attempt_id=? AND state='completed'",
                        (verify_request['operationId'], workflow_id, attempt_id)).fetchone()
                    if effect:
                        recovered = json.loads(effect['result'])
                        selection = self._completed_selection_for_reconciliation(rt, workflow_id, attempt, effect)
                        if (isinstance(recovered, dict) and recovered == verification and selection is not None
                                and recovered.get('dataKind') == 'real' and not recovered.get('partial')
                                and recovered.get('acquiredAt') and recovered.get('artifacts')):
                            recovered['quality'] = {'status': 'unverified',
                                'reasons': ['独立复测采集已完成，但验收或提交失败：' + str(exc)]}
                            recovered.pop('verificationPending', None)
                            recovered['selectionResult'] = selection
                            attempt['result'] = recovered
                            attempt['verificationOperationId'] = verify_request['operationId']
                            preserved_selection = True
                message = str(exc)
                if stopped and not pending:
                    summary = stopped_summary(stopped_receipts, selection_retained=bool(attempt.get('result', {}).get('selectionResult')))
                    if summary:
                        message = summary['message']
                        attempt['result']['executionSummary'] = summary
                        attempt['result']['quality']['reasons'] = [message]
                        if summary.get('reason') == 'connection_failed':
                            current['sharedDeviceFault'] = True
                attempt.update(state=state, endedAt=now(), error=str(exc))
                if ownership_refusal is not None:
                    attempt['ownershipCheck']=deepcopy(ownership_refusal.ownershipCheck)
                self.store.attempt(rt, workflow_id, attempt)
                node = self._node(current, node_id)
                node.update(status='attention', engineState=state, detail=message)
                if ownership_refusal is not None:
                    node['ownershipCheck']=deepcopy(ownership_refusal.ownershipCheck)
                if stopped or preserved_selection:
                    node['result'] = deepcopy(attempt['result'])
                current.update(status='attention', engineState=state)
                if manual and current.get('manualStep', {}).get('attemptId') == attempt_id:
                    current['manualStep'].update(state='finished', finishedAt=now(), resultStatus='unverified')
                self.store.save(rt, current, 'reconciliation_required' if pending else 'attention', {'message': message,
                    **({'ownershipCheck':ownership_refusal.ownershipCheck} if ownership_refusal is not None else {})})
                if device_fault_blocks(current):
                    self._pause_shared_queue(_rt=rt)
                if not pending and current.get('cancelRequested'):
                    self._finish_cancel_locked(rt, current)
            return False

    @staticmethod
    def _partial_result(artifacts, receipts=(), *, selection_retained=False):
        summary = stopped_summary(receipts, selection_retained=selection_retained)
        return {'dataKind': 'real', 'partial': True, 'metrics': {}, 'artifacts': artifacts,
                **({'executionSummary': summary} if summary else {}),
                'quality': {'status': 'unverified', 'reasons': [summary['message'] if summary else '采集已停止，测量数据与独立验证结果尚待核查']}}

    @staticmethod
    def _completed_selection_for_reconciliation(rt, workflow_id, attempt, effect):
        """Retain only the registered, complete first acquisition of this attempt."""
        attempt_id = attempt.get('id')
        if (not isinstance(attempt_id, str) or effect['attempt_id'] != attempt_id
                or effect['workflow_id'] != workflow_id or effect['operation_id'] != attempt_id + '/verify'
                or attempt.get('operationId') != attempt_id + '/measure'):
            return None
        selection = (attempt.get('result') or {}).get('selectionResult')
        if (not isinstance(selection, dict) or selection.get('dataKind') != 'real'
                or selection.get('partial') or 'selectionResult' in selection
                or selection.get('quality', {}).get('status') != 'unverified'):
            return None
        row = rt.db.execute("SELECT result FROM platform_effects WHERE operation_id=? AND workflow_id=? AND attempt_id=? AND state='completed'",
            (attempt['operationId'], workflow_id, attempt_id)).fetchone()
        if not row:
            return None
        try:
            source = json.loads(row[0])
        except (ValueError, TypeError):
            return None
        if (not isinstance(source, dict) or source.get('dataKind') != 'real' or source.get('partial')
                or not source.get('acquiredAt') or not source.get('artifacts')
                or any(selection.get(key) != source.get(key) for key in
                       ('acquiredAt', 'parameters', 'metrics', 'series', 'artifacts'))):
            return None
        return deepcopy(selection)

    def _call_adapter(self, request, lease_token):
        def emit(event):
            with self.store.transaction() as rt:
                lease = rt.db.execute('SELECT token FROM platform_leases WHERE attempt_id=?', (request['attemptId'],)).fetchone()
                if not lease or lease[0] != lease_token:
                    raise APIError('LEASE_LOST', '设备执行租约不匹配')
                wf = self.get(request['runId'], rt)
                node = self._node(wf, request['nodeId'])
                previous = node.get('telemetry', {})
                if previous.get('controllerOperationId', previous.get('operationId')) != request['operationId']:
                    previous = {}
                    node['progress'] = 0
                # Bind the callback without changing an adapter's own event ID.
                event = {**event, 'controllerOperationId': request['operationId'],
                         'controllerAttemptId': request['attemptId']}
                progress = event.get('progress')
                if number(progress):
                    node['progress'] = max(0, min(99, progress))
                node['telemetry'] = {**previous, **event}
                self.store.save(rt, wf, 'worker_progress', {'nodeId': node['id'], 'message': str(event.get('message') or event.get('event') or '采集进度'), 'telemetry': event})
        try:
            if self._physical_adapter:
                if request.get('rabiWindowAuthorization'):
                    from .smart_rabi_window import validate_live as validate_rabi_window
                    with self.store.connect() as reader:
                        validate_rabi_window(self, reader, request)
                if request.get('executionBinding'):
                    from .attempt_execution import validate_request
                    with self.store.connect() as reader:
                        validate_request(self, reader, request, live=True)
                wf = self.get(request['runId'])
                self._assert_adapter_binding(wf)
                if not (wf.get('hardwareConfirmed') or self.profile.get('connectionConfirmedAt')):
                    raise APIError('HARDWARE_HANDOFF_REQUIRED', '本次任务尚未确认设备独占交接，未启动采集', 409)
                ownership = self.hardware_ownership(refresh=True)
                from .flux_execution import owns_verification_reservation
                if not ownership['verified'] or (ownership['owners'] and not owns_verification_reservation(self,request,ownership)):
                    raise OwnershipRefusal(wf, self._node(wf,request['nodeId']), ownership, 'dispatch')
                # This instance is scoped to an accepted, exclusive run. Do not
                # persist an enabled flag that silently arms future workflows.
                execution_adapter = self.adapter.with_execution_enabled()
                if getattr(execution_adapter, 'physical_hardware', None) is not True or execution_adapter.profile.get('enabled') is not True:
                    raise APIError('ADAPTER_CONTRACT_REQUIRED', '执行器未提供有效的任务作用域启用实例')
                expected = {k: v for k, v in self.profile.items() if k != 'enabled'}
                actual = {k: v for k, v in execution_adapter.profile.items() if k != 'enabled'}
                if actual != expected:
                    raise APIError('ADAPTER_SCOPE_CHANGED', '任务作用域启用不得更换设备配置')
            else:
                execution_adapter = self.adapter
            if hasattr(execution_adapter, 'build_plan'):
                if request['procedureId'] in SCIENCE_METHODS:
                    with self.store.connect() as reader:
                        current = self.get(request['runId'], reader)
                        self._science_request_dependencies(reader, current, self._node(current, request['nodeId']), request)
                estimate = execution_adapter.build_plan(request).get('estimated_total_shots')
                if type(estimate) is not int or estimate <= 0:
                    raise APIError('INVALID_BUDGET', '执行器没有提供可核查的采集预算')
                with self.store.transaction() as rt:
                    wf = self.get(request['runId'], rt)
                    if request.get('rbDiagnostic'):
                        self._assert_rb_diagnostic_dispatch(rt, wf, self._node(wf, request['nodeId']),
                            request['rbDiagnostic']['id'], attempt_id=request['attemptId'])
                    remaining = task_budget.remaining(wf, 'maxTotalShots', wf.get('reservedShots', 0))
                    if remaining is not None and estimate > remaining:
                        raise APIError('TOTAL_SHOTS_BUDGET', '本任务累计采集预算不足，未下发本次采集')
                    if task_budget.only_accounting(wf):
                        shot_accounting.reserve(rt.db,wf['id'],request['operationId'],estimate)
                    wf['reservedShots'] = wf.get('reservedShots', 0) + estimate
                    self.store.save(rt, wf, 'budget_reserved', {'message': f'本次预留 {estimate} shots；未知执行不返还预算',
                                                             'operationId': request['operationId'], 'shots': estimate})
            result = execution_adapter.execute(request, emit, lambda: self._cancel_requested(request['runId']))
        except Exception as exc:
            if isinstance(exc,APIError) and exc.code=='SHOT_RESERVATION_EXISTS':
                raise  # The original invocation owns its receipt and device lease.
            if isinstance(exc,OwnershipRefusal):
                # Keep the observed refusal even if later reconciliation itself
                # fails; this event does not acknowledge or release any effect.
                with self.store.transaction() as rt:
                    record_rollback_refusal(rt,exc)
            receipt = self.adapter.reconcile(request['operationId'])
            with self.store.transaction() as rt:
                status = receipt.get('status')
                if status in ('not_started', 'rejected', 'failed_before_start'):
                    rt.db.execute("UPDATE platform_effects SET state='not_started',result=? WHERE operation_id=?", (dumps(receipt), request['operationId']))
                elif status in ('cancelled', 'failed_after_shutdown') and receipt.get('shutdownConfirmed') is True:
                    receipt['partialArtifacts'] = [self._artifact(rt, artifact) for artifact in receipt.get('partialArtifacts', [])]
                    rt.db.execute('UPDATE platform_effects SET state=?,result=? WHERE operation_id=?', (status, dumps(receipt), request['operationId']))
                    record_effect(rt.db, request['operationId'], self.root)
                else:
                    rt.db.execute("UPDATE platform_effects SET state='unknown',result=? WHERE operation_id=?", (dumps(receipt), request['operationId']))
            raise
        with self.store.transaction() as rt:
            result['artifacts'] = [self._artifact(rt, a) for a in result.get('artifacts', [])]
            rt.db.execute("UPDATE platform_effects SET state='completed',result=? WHERE operation_id=?", (dumps(result), request['operationId']))
            record_effect(rt.db, request['operationId'], self.root)
            rt._event(request['runId'], 'effect_completed', {'operationId': request['operationId'], 'attemptId': request['attemptId']})
        return result

    def _validation_snapshot(self, rt, wf, node, parameters, versions):
        accepted = []
        keys = self.adapter.validation_parameter_keys() if callable(getattr(self.adapter, 'validation_parameter_keys', None)) else CALIBRATION_KEYS
        for prior in wf['nodes']:
            if prior['resourceId'] != node['resourceId'] or prior['engineState'] != 'verified':
                continue
            if prior['procedureId'] not in ('sq_rabi_amplitude', 'sq_rabi_time', 'ro_iq01', 'ro_optimize'):
                continue
            row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', (prior.get('latestAttemptId'),)).fetchone()
            if not row:
                continue
            attempt = json.loads(row[0])
            if not attempt.get('verificationOperationId') or attempt.get('result', {}).get('quality', {}).get('status') != 'passed':
                continue
            # A later calibration may change the working point without deleting
            # an earlier successful diagnostic. Only current independently
            # verified evidence belongs in a downstream calibration snapshot.
            result = attempt['result']
            actual = result.get('parameters', {})
            if result.get('stale') or any(k not in actual or k not in parameters or not self._same_parameter(actual[k], parameters[k]) for k in keys):
                continue
            accepted.append({'procedureId': prior['procedureId'], 'status': 'passed',
                             'selectionOperationId': attempt['operationId'], 'verificationOperationId': attempt['verificationOperationId'],
                             'ruleVersion': wf['gatesVersion']})
        return {'version': 1, 'runId': wf['id'], 'resourceId': node['resourceId'], 'parameters': parameters,
                'parameterVersions': versions, 'acceptedEvidence': accepted}

    def _rb_prerequisites_current(self, workflow_id, node_id):
        if not self._physical_adapter:
            return True
        keys = self.adapter.validation_parameter_keys() if callable(getattr(self.adapter, 'validation_parameter_keys', None)) else CALIBRATION_KEYS
        with self.store.transaction() as rt:
            wf = self.get(workflow_id, rt)
            node = self._node(wf, node_id)
            parameters, versions = self._parameters(rt, wf, node)
            for procedures, fallback in ((('sq_rabi_amplitude', 'sq_rabi_time'), 's03_rabi_amplitude'), (('ro_iq01',), 's04_readout_fidelity')):
                candidates = [n for n in wf['nodes'] if n['resourceId'] == node['resourceId'] and n['procedureId'] in procedures]
                matched = False
                for prior in candidates:
                    actual = prior.get('result', {}).get('parameters', {})
                    mismatch = [k for k in keys if k not in actual or k not in parameters or not self._same_parameter(actual[k], parameters[k])]
                    if prior['engineState'] == 'verified' and not mismatch:
                        matched = True
                        break
                if not matched:
                    prior = self._node(wf, node['resourceId'] + ':' + fallback)
                    actual = prior.get('result', {}).get('parameters', {})
                    mismatch = [k for k in keys if k not in actual or k not in parameters or not self._same_parameter(actual[k], parameters[k])]
                    if is_manual(wf):
                        wf.update(status='attention', engineState='prerequisites_missing')
                        node.update(status='attention', engineState='prerequisites_missing',
                                    detail='RB 前置校准与当前工作点不一致；请明确回退并复测：' + prior['label'])
                        self.store.save(rt, wf, 'attention', {'message': node['detail']})
                        return False
                    affected = self._invalidate(wf, prior['id'], include_target=True)
                    wf['status'], wf['engineState'], wf['currentNodeId'] = 'queued', 'backtrack_queued', prior['id']
                    self._record_decision(rt, wf, node_id, {'action': 'backtrack', 'reason': 'RB 前置校准与当前工作点不同，需要在当前参数下重新验证：' + ','.join(mismatch),
                                                         'evidenceIds': [], 'ruleId': 'field.readout_change', 'targetNodeId': prior['id'], 'invalidatedNodes': affected}, 'rule')
                    self.store.save(rt, wf, 'backtrack', {'message': 'RB 前置检查返回 ' + prior['label']})
                    return False
        return True

    def _cancel_requested(self, workflow_id):
        wf = self.get(workflow_id)
        if wf.get('smartParent'):
            from .smart_science import cancelled
            if cancelled(self, wf):
                return True
        return self.stop_event.is_set() or bool(wf.get('cancelRequested'))

    def _calibration_context_reasons(self, rt, wf, node, result):
        if not self._physical_adapter:
            return []
        if node['procedureId'] in SCIENCE_METHODS:
            # V3 has optional shape/transition fields and generated sampling
            # defaults. Compare against the actual frozen operation plans rather
            # than requiring every possible schema field in committed calibration.
            try:
                rows = list(rt.db.execute('SELECT operation_id,state,request FROM platform_effects WHERE workflow_id=? AND attempt_id=?',
                    (wf['id'], node.get('latestAttemptId'))))
                effects = {row['operation_id'].rsplit('/',1)[-1]:row for row in rows}
                if any(stage not in effects or effects[stage]['state'] != 'completed' for stage in ('measure','verify')):
                    return ['科学验收缺少已完成的首测及独立复测执行记录']
                measure = json.loads(effects['measure']['request'])
                verification = json.loads(effects['verify']['request'])
                current, versions = self._parameters(rt, wf, node)
                if (current != measure['parameters'] or any(versions.get(key,0) != version
                        for key, version in measure.get('parameterVersions',{}).items())):
                    return ['科学采集后参数或版本已改变，必须重新采集']
                self._science_request_dependencies(rt, wf, node, verification)
                expected = self.adapter.build_plan(verification)['parameters']
                if result.get('parameters') != expected:
                    return ['科学采集参数与冻结的独立复测计划不一致']
                return []
            except Exception as exc:
                return ['科学来源或冻结采集上下文未通过重新核验：' + str(exc)]
        keys = self.adapter.validation_parameter_keys() if callable(getattr(self.adapter, 'validation_parameter_keys', None)) else CALIBRATION_KEYS
        actual = result.get('parameters')
        actual = actual if isinstance(actual, dict) else {}
        committed, _ = self._parameters(rt, wf, {**node, 'parameterOverrides': {}})
        missing = [k for k in keys if k not in actual or k not in committed]
        if missing:
            return ['实际采集回执缺少完整校准上下文，不能验收：' + ', '.join(missing)]
        changed = self._nonoutput_context_changes(node, actual, committed, keys)
        if changed:
            detail = ', '.join(f'{k}: {committed[k]} → {actual[k]}' for k in changed)
            return ['本轮实测工作点与已提交校准不同，当前方法不能提交这些上下文参数；请回到相应上游校准验证：' + detail]
        return []

    def _nonoutput_context_changes(self, node, actual, committed, keys):
        outputs = {PARAMETER_ALIASES.get(k, k) for k in PARAMETERS.get(node['procedureId'], ([], []))[1]}
        return [key for key in keys if key not in outputs and not self._same_parameter(actual[key], committed[key])]

    def _spectrum_drive_conflicts(self, rt, wf, node, planned):
        if not self._physical_adapter or wf.get('mode') != 'real' or node['procedureId'] != 'sq_spectroscopy':
            return {}
        committed, versions = self._parameters(rt, wf, {**node, 'parameterOverrides': {}})
        validated = self.adapter.validation_parameter_keys() if callable(getattr(self.adapter,'validation_parameter_keys',None)) else CALIBRATION_KEYS
        # A profile default (version zero) does not attest an accepted pi amplitude.
        keys = [k for k in ('drive_amp','half_pi_amp') if versions.get(k,0)>0 and k in validated and k in planned and k in committed]
        return {k:(committed[k],planned[k]) for k in self._nonoutput_context_changes(node, planned, committed, keys)}

    def _require_spectrum_drive_context(self, rt, wf, node, planned):
        conflicts = self._spectrum_drive_conflicts(rt, wf, node, planned)
        if conflicts:
            labels = {'drive_amp':'驱动幅度', 'half_pi_amp':'半门幅度'}
            message = '；'.join(f'{labels[k]}（{k}）已提交 {current!s}，拟下发 {proposed!s}' for k,(current,proposed) in conflicts.items())
            raise APIError('SPECTRUM_DRIVE_CONTEXT_CONFLICT', message+'。请在本步参数中恢复继承已提交幅度。', 409)

    def _commit_candidate(self, rt, wf, node, candidate, versions):
        prefix = wf['id'] + ':' + node['resourceId'] + ':'
        changed = []
        changed_readout = []
        baseline, _ = self._parameters(rt, wf, {**node, 'parameterOverrides': {}})
        allowed = {PARAMETER_ALIASES.get(k, k) for k in PARAMETERS.get(node['procedureId'], ([], []))[1]}
        if node['procedureId']=='sq_drag_half_scan':
            from adapters.science_v3_irb.beta import candidate_key
            allowed={candidate_key(node.get('result',{}).get('parameters',node.get('parameters',{})))}
        for key, value in candidate.items():
            key = PARAMETER_ALIASES.get(key, key)
            if key not in allowed or not number(value):
                continue
            row = rt.db.execute('SELECT revision,value FROM parameters WHERE name=?', (prefix + key,)).fetchone()
            version = row['revision'] if row else 0
            if version != versions.get(key, 0):
                raise APIError('STALE_REVISION', '候选基准已变化，不能提交')
            if row and json.loads(row['value']) == value:
                continue
            previous = json.loads(row['value']) if row else baseline.get(key)
            if key in ('readout_hz', 'readout_amp') and not self._same_parameter(previous, value):
                changed_readout.append(key)
            rt.db.execute('INSERT OR REPLACE INTO parameters VALUES(?,?,?)', (prefix + key, dumps(value), version + 1))
            node['parameterVersions'][key] = version + 1
            changed.append(prefix + key)
        if changed:
            rt._invalidate(changed, exclude=wf['id'] + '/' + node['id'])
            affected = self._invalidate(wf, node['id'], include_target=False)
            self._readout_revalidation(rt, wf, node)
            rebuild_target = None
            if node['procedureId'] == 'ro_optimize' and changed_readout:
                rebuild_target = self._start_readout_revalidation(rt, wf, node)
                affected = list(dict.fromkeys(affected + [n['id'] for n in wf['nodes'] if n['resourceId'] == node['resourceId'] and n['engineState'] == 'stale']))
            self._record_decision(rt, wf, node['id'], {'action': 'commit', 'reason': '独立复测通过后提交候选版本',
                'evidenceIds': [a['id'] for a in node['result']['artifacts']], 'parameterChanges': candidate,
                'invalidatedNodes': affected}, 'rule')
            return rebuild_target

    @staticmethod
    def _repeatability(selection, verification, parameters):
        reasons = []
        for key, selected in selection.items():
            repeated = verification.get(key)
            if not number(selected) or not number(repeated):
                reasons.append('独立复测缺少同一候选参数：' + key)
                continue
            if key in ('readout_hz', 'drive_hz'):
                span = parameters.get('span_hz', 4e6 if key == 'readout_hz' else 20e6)
                tolerance = max(5e4, 2 * span / max(1, parameters.get('points', 81) - 1))
            else:
                tolerance = max(abs(selected) * .15, 1e-12)
            if abs(repeated - selected) > tolerance:
                reasons.append(f'候选重复性不足：{key} 从 {selected:g} 变为 {repeated:g}，容差 {tolerance:g}')
        return reasons

    @staticmethod
    def _invalidate(wf, node_id, include_target=False):
        affected = {node_id}
        while True:
            expanded = affected | {n['id'] for n in wf['nodes'] if set(n['dependsOn']) & affected}
            if expanded == affected:
                break
            affected = expanded
        if not include_target:
            affected.discard(node_id)
        diagnostic = wf.get('rbDiagnostic') or {}
        if diagnostic and affected:
            diagnostic.update(state='invalidated', finishedAt=now(), resultStatus='unverified')
        for node in wf['nodes']:
            if node['id'] in affected:
                node.pop('humanAcceptedAttemptId', None)
                node.pop('humanAcceptedAt', None)
                for accepted_key in ('agentAcceptedAttemptId','agentAcceptedAt','agentAcceptedByParentId'):
                    node.pop(accepted_key, None)
                node['status'], node['engineState'], node['progress'], node['mainProcedureComplete'] = 'pending', 'stale', 0, False
                node['detail'] = '前置参数已变更，历史结果保留，等待重新测量'
                if node.get('result'):
                    node['result']['stale'] = True
        return sorted(affected)

    def _record_decision(self, rt, wf, node_id, decision, source):
        record = {**decision, 'id': ident('decision'), 'nodeId': node_id, 'createdAt': now(), 'source': source,
                  'attemptId': self._node(wf, node_id).get('latestAttemptId')}
        if decision.get('action') == 'backtrack' and decision.get('targetNodeId'):
            wf['edges'] = [edge for edge in wf['edges'] if not (edge.get('kind') == 'backtrack' and edge['from'] == node_id and edge['to'] == decision['targetNodeId'])]
            wf['edges'].append({'from': node_id, 'to': decision['targetNodeId'], 'kind': 'backtrack', 'decisionId': record['id']})
        self.store.decision(rt, wf['id'], record)
        return record

    def _bounds(self):
        return {k: (deepcopy(v) if isinstance(v, dict) else {'min': v[0], 'max': v[1],
                    **({'integer': True} if k in ('shots', 'capture_repeats', 'points', 'pi_pulses') else {})})
                for k, v in self.profile.get('parameterBounds', {}).items()}

    def _parameter_schema(self, procedure):
        hook = getattr(self.adapter, 'parameter_schema', None)
        return deepcopy(hook(procedure)) if callable(hook) else self._bounds()

    def _normalize_candidate(self, procedure, candidate):
        hook = getattr(self.adapter, 'normalize_candidate', None)
        return deepcopy(hook(procedure, candidate)) if callable(hook) else deepcopy(candidate)

    def _validated_candidate(self, procedure, result, stage):
        """Reject unusable fit proposals without discarding their acquired evidence.

        Only expected proposal validation failures are scientific gate failures.
        Instrument, adapter, and normalization implementation exceptions still
        follow the execution/reconciliation error path.
        """
        raw = deepcopy(result.get('candidateParameters', {}))
        normalized = None
        schema = self._bounds()

        def validate(candidate):
            if not isinstance(candidate, dict) or any(not isinstance(k, str) for k in candidate):
                raise APIError('INVALID_CANDIDATE_PARAMETERS', '拟合候选参数必须是字符串键的对象')
            for key, value in candidate.items():
                try:
                    self._validate_parameter(key, value, schema)
                except APIError as exc:
                    if exc.code not in ('UNKNOWN_PARAMETER', 'PARAMETER_OUT_OF_RANGE'):
                        raise
                    raise APIError(exc.code, f'{exc.message}：{key}={value!r}；允许范围 {schema.get(key)}') from exc

        try:
            validate(raw)
            normalized = self._normalize_candidate(procedure, deepcopy(raw))
            validate(normalized)
            return normalized
        except APIError as exc:
            if exc.code not in ('INVALID_CANDIDATE_PARAMETERS', 'UNKNOWN_PARAMETER', 'PARAMETER_OUT_OF_RANGE'):
                raise
            result['quality']['status'] = 'failed'
            result['quality']['reasons'].append('拟合候选未通过参数校验：' + str(exc))
            result['candidateValidation'] = {'accepted': False, 'stage': stage,
                'raw': raw, 'normalized': normalized, 'code': exc.code, 'message': str(exc)}
            return None

    def _normalize_changes(self, procedure, changes, parameters):
        from .smart_q11_launch import SHORT_HALF_POLICY
        if (procedure == 'sq_spectroscopy' and parameters.get('pulse_compatibility') == SHORT_HALF_POLICY
                and 'drive_amp' in changes and 'half_pi_amp' in changes
                and not self._same_parameter(changes['drive_amp'], changes['half_pi_amp'])):
            raise APIError('PULSE_POLICY_MISMATCH', '当前短半脉冲策略要求 π/2 与 π 同幅度，π/2 使用半时长', 409)
        if procedure in SCIENCE_IRB_METHODS:
            mode_keys = getattr(self.adapter, 'parameter_keys_by_mode', None)
            if callable(mode_keys):
                from adapters.science_v3_irb.beta import mode_of
                try:allowed=set(mode_keys(procedure)[mode_of({**parameters,**changes})])
                except (ValueError,KeyError,TypeError) as exc:
                    raise APIError('SCIENCE_DRAG_MODE_INVALID',str(exc),409) from None
                if set(changes)-allowed:
                    raise APIError('SCIENCE_DRAG_MODE_PARAMETERS','参数不属于所选 DRAG 系数模式：'+', '.join(sorted(set(changes)-allowed)))
        hook = getattr(self.adapter, 'normalize_parameter_changes', None)
        result = hook(procedure, deepcopy(changes), deepcopy(parameters)) if callable(hook) else changes
        if not isinstance(result, dict):
            raise APIError('INVALID_PARAMETER_NORMALIZATION', '执行器参数规范化必须返回键值对象')
        return deepcopy(result)

    @staticmethod
    def _validate_parameter(key, value, schema):
        rule = schema.get(key)
        if not isinstance(rule, dict):
            raise APIError('UNKNOWN_PARAMETER', '当前方法不允许调整参数：' + key)
        if 'enum' in rule:
            if value not in rule['enum'] or isinstance(value, (dict, list)):
                raise APIError('PARAMETER_OUT_OF_RANGE', key + ' 不在允许选项内')
        elif rule.get('type')=='integerArray':
            if (not isinstance(value,list) or not rule['minItems']<=len(value)<=rule['maxItems']
                    or any(type(item) is not int or not rule['min']<=item<=rule['max'] for item in value)
                    or rule.get('strictlyIncreasing') and any(b<=a for a,b in zip(value,value[1:]))):
                raise APIError('PARAMETER_OUT_OF_RANGE',key+' 必须为范围内严格递增的整数数组')
        elif rule.get('type')=='numberArray':
            if (not isinstance(value,list) or not rule['minItems']<=len(value)<=rule['maxItems']
                    or any(not number(item) or not rule['min']<=item<=rule['max'] for item in value)
                    or rule.get('unique') and len(set(value))!=len(value)
                    or rule.get('strictlyIncreasing') and any(b<=a for a,b in zip(value,value[1:]))):
                raise APIError('PARAMETER_OUT_OF_RANGE',key+' 必须为明确、有限且符合线路范围的数值数组')
        elif not number(value) or not rule['min'] <= value <= rule['max'] or (rule.get('integer') and int(value) != value):
            raise APIError('PARAMETER_OUT_OF_RANGE', key + ' 超出允许范围或类型')

    @staticmethod
    def _same_parameter(left, right):
        if number(left) and number(right):
            return abs(left - right) <= max(abs(right) * 1e-12, 1e-12)
        return type(left) is type(right) and left == right

    def _assert_adapter_binding(self, wf):
        if getattr(self, '_execution_base', None) is not None:
            from .attempt_execution import assert_binding
            return assert_binding(self, wf)
        if wf['mode'] == 'real' and (wf.get('adapterId') != self.adapter_id or wf.get('adapterProfileHash') != self.adapter_profile_hash
                                    or self._adapter_fingerprint() != self.adapter_profile_hash):
            raise APIError('ADAPTER_CHANGED', '任务绑定的执行器或配置已变化，请创建与当前配置绑定的新任务', 409)

    def _adapter_fingerprint(self):
        profile = {k: v for k, v in self.profile.items() if k not in ('enabled', 'connectionConfirmedAt')}
        identity = {'profile': profile}
        if self._physical_adapter and self.profile.get('sourceManifest'):
            root = checked_path(getattr(self.adapter, 'root', self.root))
            manifest = checked_path(root / self.profile['sourceManifest'], parent=root)
            if not manifest.is_file():
                raise APIError('ADAPTER_MANIFEST_MISSING', '执行器源码清单不存在，不能冻结当前任务')
            identity['sourceManifestSha256'] = file_hash(manifest)
        return digest(identity)

    def _brain(self, path, body=None, timeout=180):
        if path == '/decide' and javascript_json_units_upper_bound(body) > MODEL_INPUT_MAX_UNITS:
            return {'status': 'waiting_model', 'error': {'code': 'context_budget_exceeded', 'source': 'controller_preflight'},
                    'message': '模型上下文仍超出输入预算；已保留完整采集记录，未发送模型请求。'}
        data = None if body is None else dumps(body).encode()
        request = urllib.request.Request(self.brain_url + path, data=data, headers={'Content-Type': 'application/json'})
        # Local services bypass ambient HTTP proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # Only recognize this local service's bounded, known validation
            # codes. Never echo arbitrary upstream bodies or account details.
            if path == '/decide' and exc.code == 400:
                try:
                    error = json.loads(exc.read(4097)).get('error', {})
                    reason = error.get('message')
                    if error.get('code') == 'invalid_request' and reason in ('input_too_large', 'input_too_deep'):
                        return {'status': 'waiting_model', 'error': {'code': reason, 'source': 'agent_input_validation'},
                            'message': '模型请求未通过本地输入校验：' + reason + '；尚未调用会员模型。'}
                except (ValueError, TypeError, AttributeError):
                    pass
            return {'status': 'waiting_model', 'message': '模型服务当前不可用：' + str(exc), 'configured': False}
        except (OSError, ValueError) as exc:
            return {'status': 'waiting_model', 'message': '模型服务当前不可用：' + str(exc), 'configured': False}

    def _model_readout_rescan_context(self, rt, wf, node, effective_parameters):
        """Read-only attestation for model bounds; never release or mutate a pin."""
        marker = wf.get('readoutRevalidations', {}).get(node['resourceId'])
        context = {'state': None, 'eligible': False, 'reason': '当前没有固定读出重建'}
        if node['procedureId'] != 'ro_optimize' or not isinstance(marker, dict) or marker.get('state') not in ('active', 'fixed_failed'):
            return context
        context.update(state=marker['state'], reason='固定候选尚未满足当前失败尝试及工作点核验；保留采样参数，未开放重扫')
        if (marker.get('state') != 'fixed_failed' or marker.get('optimizerNodeId') != node['id']
                or wf.get('currentNodeId') != node['id'] or not marker.get('id')):
            return context
        values, versions = marker.get('values'), marker.get('parameterVersions')
        committed, current_versions = self._parameters(rt, wf, {**node, 'parameterOverrides': {}})
        keys = ('readout_hz', 'readout_amp')
        if (not isinstance(values, dict) or not isinstance(versions, dict)
                or any(k not in values or not number(values[k]) or type(versions.get(k)) is not int for k in keys)
                or any(not self._same_parameter(value, committed.get(k))
                       or not self._same_parameter(value, effective_parameters.get(k))
                       or versions.get(k) != current_versions.get(k, 0) for k, value in values.items())):
            return context
        result = node.get('result') or {}
        attempt_id = node.get('latestAttemptId')
        actual = result.get('parameters') or {}
        if (not isinstance(attempt_id, str) or not attempt_id or marker.get('lastFixedAttemptId') != attempt_id
                or result.get('dataKind') != 'real' or result.get('partial') or result.get('stale')
                or result.get('quality', {}).get('status') != 'failed'
                or not isinstance(actual, dict)
                or any(not self._same_parameter(actual.get(k), values[k]) for k in keys)
                or actual.get('amplitudes') != [values['readout_amp']]):
            return context
        row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
                            (attempt_id, wf['id'], node['id'])).fetchone()
        if not row:
            return context
        try:
            attempt = json.loads(row['payload'])
        except (TypeError, ValueError):
            return context
        if not isinstance(attempt, dict):
            return context
        recorded_marker = attempt.get('readoutRevalidation') or {}
        if (attempt.get('mode') != 'real' or attempt.get('procedureId') != node['procedureId']
                or attempt.get('state') != 'attention' or not attempt.get('endedAt') or attempt.get('result') != result
                or not isinstance(recorded_marker, dict)
                or recorded_marker.get('id') != marker['id'] or recorded_marker.get('values') != values
                or recorded_marker.get('parameterVersions') != versions
                or any(attempt.get('parameterVersions', {}).get(k, 0) != current_versions.get(k, 0) for k in keys)):
            return context
        effects = list(rt.db.execute('SELECT operation_id,state,request,result FROM platform_effects WHERE workflow_id=? AND attempt_id=?',
                                    (wf['id'], attempt_id)))
        if (not isinstance(attempt.get('operationId'), str) or not attempt['operationId']
                or (attempt.get('verificationOperationId') is not None
                    and not isinstance(attempt['verificationOperationId'], str))):
            return context
        expected_operations = {attempt['operationId']}
        if attempt.get('verificationOperationId'):
            expected_operations.add(attempt['verificationOperationId'])
        if (not effects or any(e['state'] != 'completed' for e in effects) or self._has_lease(rt, wf['id'])
                or not all(isinstance(op, str) and op for op in expected_operations)
                or {e['operation_id'] for e in effects} != expected_operations
                or rt.db.execute("SELECT 1 FROM platform_effects WHERE workflow_id=? AND state IN ('pending','unknown')", (wf['id'],)).fetchone()):
            return context
        for effect in effects:
            try:
                request, receipt = json.loads(effect['request']), json.loads(effect['result'])
            except (TypeError, ValueError):
                return context
            if not isinstance(request, dict) or not isinstance(receipt, dict):
                return context
            if (any(request.get(k) != value for k, value in {'runId': wf['id'], 'nodeId': node['id'],
                    'resourceId': node['resourceId'], 'procedureId': node['procedureId'], 'attemptId': attempt_id,
                    'operationId': effect['operation_id']}.items()) or receipt.get('dataKind') != 'real' or receipt.get('partial')
                    or not isinstance(request.get('readoutRevalidation'), dict)
                    or request['readoutRevalidation'].get('id') != marker['id']):
                return context
            for evidence_parameters in (request.get('parameters'), receipt.get('parameters')):
                if (not isinstance(evidence_parameters, dict)
                        or any(not self._same_parameter(evidence_parameters.get(k), values[k]) for k in keys)
                        or evidence_parameters.get('amplitudes') != [values['readout_amp']]):
                    return context
        context.update(eligible=True, reason='当前固定点失败记录、实际工作点与已提交读出版本一致；只允许明确成对范围请求，仍需原控制器和计划复核')
        return context

    def _decision_execution_constraints(self, wf, node, payload):
        """Explain existing limits and saved scan geometry; never plan or acquire."""
        constraints = {
            'maximumTotalShots': self.profile.get('maximumTotalShots'),
            'maximumPoints': self.profile.get('maximumPoints'),
            'scope': 'per_operation',
            'maximumWorkflowShots': wf['maxTotalShots'],
            'reservedWorkflowShots': wf.get('reservedShots', 0),
            'remainingWorkflowShots': task_budget.remaining(wf, 'maxTotalShots', wf.get('reservedShots', 0)),
            'selectionAndVerificationBudgetedSeparately': True,
            'selectionAdmissionRequiresVerificationCapacity': self._physical_adapter,
            'budgetSemantics': 'maximumTotalShots 和 maximumPoints 分别限制一次原生操作。启动 selection 前必须有首测与后续独立 verification 的联合容量；该预检不扣预算，实际每个 operation 仍只预留一次。verification 只检查自身剩额，不再为首测计费；失败或取消不退还已预留预算。'}
        if task_budget.only_accounting(wf):
            constraints.update(budgetMode=task_budget.MODE, budgetSemantics='逐次记录真实操作及预留 shots。任务不设累计额度，单次原生上限与首复测要求仍须满足。')
        adapter_type = type(self.adapter)
        semantics = sampling_context(implementation_id=adapter_type.__module__ + '.' + adapter_type.__qualname__,
            profile_id=self.profile.get('id'), procedure_id=node['procedureId'])
        if semantics:
            constraints['samplingSemantics'] = semantics
        if node['procedureId'] != 'ro_power_shift':
            return constraints
        constraints['scanSemantics'] = {
            'formulaShotsPerOperation': 'frequencyPoints * powerPoints * capture_repeats * acquisitions_per_point * shots',
            'formulaPointsPerOperation': 'frequencyPoints * powerPoints * capture_repeats',
            'powerAxis': '当前 s21_power 使用已记录的 powers 数组；未显式提供时原生默认 [0.1,0.2,0.4,0.6,0.8]。',
            'ineffectivePowerAxisParameters': ['amp_start', 'amp_stop', 'readout_amp'],
            'notice': 'amp_start/amp_stop/readout_amp 不改变此方法的固定功率扫描轴，不能据此删去 0.1 档。模型仅可调整 shots、capture_repeats、acquisitions_per_point、points、span_hz；修改 readout_hz 必须回到上游读出定频并重新验证。'}
        result = node.get('result') or {}
        actual = result.get('parameters') or {}
        groups = payload.get('observations', {}).get('powerSeriesDiagnostics', {}).get('groups', [])
        series = [s for s in result.get('series', []) if isinstance(s, dict) and s.get('fit') is not True]
        values = {key: actual.get(key) for key in ('points', 'capture_repeats', 'acquisitions_per_point', 'shots')}
        recorded_powers = result.get('analysisQuality', {}).get('powers_waveform_scale')
        confirmed = (all(type(value) is int and value > 0 for value in values.values()) and bool(groups) and bool(series)
                     and isinstance(recorded_powers, list) and all(number(v) for v in recorded_powers)
                     and sorted(recorded_powers) == [g['powerWaveformScale'] for g in groups]
                     and len(series) == len(groups) * values['capture_repeats']
                     and all(g['captureCount'] == values['capture_repeats'] and g['sameFrequencyGrid'] and g['uniqueCaptureLabels'] for g in groups)
                     and all(len(s.get('x', [])) == values['points'] and len(s.get('y', [])) == values['points'] for s in series))
        constraints['currentScan'] = {'status': 'confirmed' if confirmed else 'unconfirmed',
            'source': '当前 result.parameters、analysisQuality.powers_waveform_scale 与保存的逐 capture series 交叉核对；未新采集或读取原始文件'}
        if confirmed:
            base = values['points'] * len(groups)
            per_capture = base * values['shots']
            total = per_capture * values['capture_repeats'] * values['acquisitions_per_point']
            constraints['currentScan'].update(frequencyPoints=values['points'], powerPoints=len(groups),
                powersWaveformScale=sorted(recorded_powers), pointsPerCapture=base,
                captureRepeats=values['capture_repeats'], acquisitionsPerPoint=values['acquisitions_per_point'],
                shotsPerAcquisition=values['shots'], pointsPerOperation=base * values['capture_repeats'],
                shotsPerOperation=total, selectionPlusVerificationShots=2 * total)
            limit = constraints['maximumTotalShots']
            if type(limit) is int and limit > 0:
                constraints['currentScan']['maximumCaptureAcquisitionProductAtCurrentGridAndShots'] = limit // per_capture
                constraints['illustrativeCostsAtCurrentGridAndShots'] = [
                    {'capture_repeats': captures, 'acquisitions_per_point': acquisitions,
                     'shotsPerOperation': per_capture * captures * acquisitions,
                     'selectionPlusVerificationShots': 2 * per_capture * captures * acquisitions,
                     'withinPerOperationShotLimit': per_capture * captures * acquisitions <= limit}
                    for captures, acquisitions in ((4, 4), (2, 4), (6, 4))]
        return constraints

    def _prior_failure_context(self, rt, wf, node):
        """Read-only archive projection; no adapter execution or DB writes.

        Staged after deployment SHA 6ab47e7785cb68405e8b88d86373d9354818b8e72d6dd7c4e6d4f740a7766a27.
        Root owns checkpoint deployment. Frozen native source/profile is unchanged.
        """
        cases, examined, excluded = [], 0, 0
        try:
            adapter_type = type(self.adapter)
            implementation = adapter_type.__module__ + '.' + adapter_type.__qualname__
            if implementation != 'adapters.arclight.ArcLightAdapter':
                return bounded_context([], wf, status='unsupported_identity')
            root = checked_path(self.adapter.root)
            bindings = load_audited_bindings(root, implementation)
            if (wf.get('adapterId') not in bindings
                    or wf.get('adapterProfileHash') != bindings[wf['adapterId']]['fingerprint']):
                return bounded_context([], wf, status='unaudited_binding')
            rows = rt.db.execute(
                'SELECT a.payload AS attempt_payload,w.payload AS workflow_payload '
                'FROM platform_attempts a JOIN platform_workflows w ON w.id=a.workflow_id '
                'WHERE a.node_id=? ORDER BY a.rowid DESC LIMIT ?', (node['id'], MAX_CANDIDATES))
            # Bounds archive verification I/O as well as prompt size. Large or
            # unverifiable files are excluded, never counted as trusted cases.
            remaining_bytes = 2 * 1024**3
            for row in rows:
                examined += 1
                try:
                    old_wf, attempt = json.loads(row['workflow_payload']), json.loads(row['attempt_payload'])
                    if not eligible_header(old_wf, attempt, wf, node, bindings):
                        excluded += 1
                        continue
                    effects = []
                    files_by_operation = {}
                    for stored in rt.db.execute('SELECT * FROM platform_effects WHERE attempt_id=?', (attempt['id'],)):
                        effect = dict(stored)
                        if effect['state'] != 'completed' or not effect['result']:
                            raise ValueError('incomplete_effect')
                        effect['request'], effect['result'] = json.loads(effect['request']), json.loads(effect['result'])
                        if not isinstance(effect['request'], dict) or not isinstance(effect['result'], dict):
                            raise ValueError('malformed_effect')
                        effects.append(effect)
                        operation = effect['operation_id']
                        op_root = checked_path(root / 'data/runs/arclight_operations' / hashlib.sha256(operation.encode()).hexdigest(), parent=root)
                        checked_files = []
                        inventory = effect['result'].get('artifacts')
                        if not isinstance(inventory, list) or not all(isinstance(a, dict) for a in inventory):
                            raise ValueError('malformed_artifacts')
                        needed = [a for a in inventory
                                  if a.get('kind') == 'raw' or a.get('name') in CHECKED_JSON_NAMES]
                        if not 1 <= len(needed) <= 128:
                            raise ValueError('artifact_inventory_limit')
                        for artifact in needed:
                            registered = rt.db.execute('SELECT * FROM platform_artifacts WHERE id=?', (artifact['id'],)).fetchone()
                            if (not registered or registered['sha256'] != artifact['sha256']
                                    or artifact['id'] != 'real-' + artifact['sha256'][:24]):
                                raise ValueError('artifact_not_registered')
                            registered_payload = json.loads(registered['payload'])
                            if not isinstance(registered_payload, dict) or registered_payload.get('kind') != artifact['kind']:
                                raise ValueError('artifact_kind_mismatch')
                            path = checked_path(artifact['sourcePath'], parent=op_root)
                            size = path.stat().st_size
                            if size > remaining_bytes or (artifact['kind'] != 'raw' and size > 2 * 1024**2):
                                raise ValueError('archive_read_budget')
                            remaining_bytes -= size
                            document_bytes = None
                            if artifact['kind'] != 'raw':
                                with path.open('rb') as stream:
                                    document_bytes = stream.read(2 * 1024**2 + 1)
                            actual_sha = file_hash(path) if document_bytes is None else hashlib.sha256(document_bytes).hexdigest()
                            if (document_bytes is not None and len(document_bytes) > 2 * 1024**2
                                    or path.name != artifact['name'] or actual_sha != registered['sha256']):
                                raise ValueError('artifact_hash_mismatch')
                            entry = {'artifactRef': artifact['id'], 'sha256': artifact['sha256'],
                                     'name': artifact['name'], 'kind': artifact['kind'],
                                     'folder': path.parent.relative_to(op_root).as_posix(), 'registryHashVerified': True}
                            if artifact['kind'] != 'raw':
                                entry['document'] = json.loads(document_bytes.decode('utf-8-sig'))
                            checked_files.append(entry)
                        files_by_operation[operation] = checked_files
                    case = project_failure_case({'workflow': old_wf, 'attempt': attempt, 'effects': effects,
                        'files': files_by_operation}, wf, node, bindings)
                    if case is None:
                        excluded += 1
                    else:
                        cases.append(case)
                    if len(cases) >= MAX_CASES:
                        break
                except (APIError, OSError, ValueError, TypeError, KeyError):
                    excluded += 1
            return bounded_context(cases, wf, examined=examined, excluded=excluded)
        except (APIError, OSError, ValueError, TypeError, KeyError):
            return bounded_context([], wf, examined=examined, excluded=excluded, status='archive_unavailable')

    def _decide(self, workflow_id, node_id, *, _smart_parent=None):
        if getattr(self, '_attempt_execution_authorization', {}).get('executionVersion') == 'smart-rabi16-v1':
            # Replanning belongs to the unchanged parent controller; the
            # completed attempt's adapter is only its immutable evidence reader.
            return self._execution_base._decide(workflow_id, node_id, _smart_parent=_smart_parent)
        wf = self.get(workflow_id)
        node = self._node(wf, node_id)
        if _smart_parent is not None:
            from .smart_science import assert_authorized
            assert_authorized(self, wf, _smart_parent)
        if wf['policy'] != 'autopilot' and _smart_parent is None:
            self._attention(workflow_id, 'quality_attention', '本轮结果待处理；可查看质量依据后复测或选择自主策略')
            return False
        try:
            self._assert_adapter_binding(wf)
        except APIError as exc:
            self._attention(workflow_id, 'adapter_changed', exc.message, node_id=node_id)
            return False
        from .smart_main_checkpoint import is_smart_main
        decision_revision = wf['revision'] if is_smart_main(wf) else None
        if self._attention(workflow_id, 'deciding', '智能体正在根据实测证据选择下一步',
                           decision_revision=decision_revision) is False:
            return False
        wf = self.get(workflow_id)
        node = self._node(wf, node_id)
        with self.store.connect() as rt:
            effective_parameters, _ = self._parameters(rt, wf, node)
            rescan_context = self._model_readout_rescan_context(rt, wf, node, effective_parameters)
            prior_failure_context = self._prior_failure_context(rt, wf, node)
        attempts, history = self.store.detail(workflow_id, node_id)
        backtrack_admission = eligible_backtracks(wf, self._ancestors(wf, node))
        payload = {'runId': workflow_id, 'nodeId': node_id, 'revision': wf['revision'], 'mode': 'real',
                   'goal': wf['name'], 'observations': {'result': node.get('result'), 'parameters': effective_parameters,
                   'attempt': node['attempt'], 'remainingAttempts': task_budget.remaining(wf, 'maxAttemptsPerNode', node['attempt'])},
                   'allowedActions': ['retry', 'backtrack', 'wait', 'stop'], 'allowedBacktrackTargets': backtrack_admission['allowed'],
                   'backtrackAdmission': backtrack_admission,
                   'parameterBounds': self._parameter_schema(node['procedureId']), 'rules': node['rules'], 'history': history[-8:]}
        physical_parameter_bounds = deepcopy(payload['parameterBounds'])
        marker = wf.get('readoutRevalidations', {}).get(node['resourceId'])
        if marker:
            payload['observations']['readoutRevalidation'] = {**deepcopy(marker),
                'retrySemantics': '仅改 shots 等采集参数保持固定读出候选。当前优化节点固定点验证失败后，retry 同时指定 amp_start/amp_stop 才申请重新全扫；预算不增加。'}
            if marker['state'] in ('active', 'fixed_failed') and node_id in marker.get('lockedNodeIds', [marker['optimizerNodeId']]):
                for key, value in marker['values'].items():
                    payload['parameterBounds'][key] = {'min': value, 'max': value}
        payload = build_decision_context(payload, procedure_id=node['procedureId'])
        if model_recovery.enabled(wf):
            from .smart_evidence_projection import compact_block_evidence
            payload = compact_block_evidence(payload)
        # Separate from observations/history: historical IDs can never become
        # the current attempt's evidence allowlist, including in legacy agents.
        if node['procedureId'] == 'ro_power_shift':
            allowed = {'shots', 'capture_repeats', 'acquisitions_per_point', 'points', 'span_hz'}
            payload['parameterBounds'] = {k: v for k, v in payload['parameterBounds'].items() if k in allowed}
        adapter_type = type(self.adapter)
        semantics = build_model_parameter_contract(
            implementation_id=adapter_type.__module__ + '.' + adapter_type.__qualname__,
            profile_id=self.profile.get('id'), procedure_id=node['procedureId'],
            parameter_bounds=physical_parameter_bounds, effective_parameters=effective_parameters,
            fixed_readout_state=rescan_context['state'], fixed_rescan_eligible=rescan_context['eligible'])
        if semantics['applied']:
            payload['parameterBounds'] = deepcopy(semantics['parameterBounds'])
            payload['modelParameterSemantics'] = semantics
            if rescan_context['state'] is not None:
                payload['modelParameterSemantics']['fixedReadoutContext'] = rescan_context
        if _smart_parent is not None:
            from .smart_science import decision_bounds as science_decision_bounds
            payload['parameterBounds'] = science_decision_bounds(wf, payload['parameterBounds'])
            payload['allowedActions'] = ['retry', 'wait', 'stop']
            payload['allowedBacktrackTargets'] = []
            payload['smartScienceScope'] = {'parentWorkflowId': _smart_parent,
                'meaning': '仅可调整给出的扫描和采集参数；门与读出来源保持冻结，失败结果不能批准'}
        from . import smart_sampling
        payload['parameterBounds'] = smart_sampling.model_bounds(self, wf, node, payload['parameterBounds'])
        from . import smart_rabi_window
        payload['parameterBounds'], window_scope = smart_rabi_window.model_scope(self, wf, node, payload['parameterBounds'])
        if window_scope is not None:
            payload['smartRabiWindowScope'] = window_scope
            # This is an evidence-bound addition to the ordinary method
            # semantics, not a change to the general method allowlist.
            scoped_semantics = payload.get('modelParameterSemantics')
            if scoped_semantics is not None:
                scoped_semantics['parameterBounds'] = deepcopy(payload['parameterBounds'])
                scoped_semantics['excludedParameters'] = [key for key in scoped_semantics['excludedParameters'] if key != 'drive_length_s']
                scoped_semantics['notes'].append(window_scope['meaning'])
        if (payload['parameterBounds'].get('acquisitions_per_point', {}).get('max') == 16
                and smart_sampling.eligible(wf, node)):
            payload['smartSamplingScope'] = {'policy': 'q11-smart-rabi-independent-blocks16/1',
                'meaning': '已核对当前完整弱响应；可申请16个独立2000-shot块，保持驱动/门长/读出工作点。9至15块不属于此版本。首复测独立且正常计量。'}
        decision_bounds = deepcopy(payload['parameterBounds'])
        semantic_conditions = deepcopy(semantics['conditions']) if semantics['applied'] else []
        payload['executionConstraints'] = self._decision_execution_constraints(wf, node, payload)
        payload = attach_prior_context(payload, prior_failure_context)
        payload = attach_curve_summary(payload, procedure_id=node['procedureId'])
        payload = project_model_input(payload)
        if node['procedureId'] in CAPTURE_DIAGNOSTIC_PROCEDURES:
            sources = resolve_sources(self.root, self.store.path, wf, node,
                implementation_id=adapter_type.__module__ + '.' + adapter_type.__qualname__)
            payload = attach_diagnostics(payload, calculate_diagnostics(sources))
        upstream_context = resolve_accepted_upstream(self.root, self.store.path, wf, node,
            implementation_id=adapter_type.__module__ + '.' + adapter_type.__qualname__)
        payload = attach_accepted_upstream(payload, upstream_context)
        from .smart_q11_launch import attach_rabi_history
        payload = attach_rabi_history(payload, wf, node['procedureId'])
        payload = model_recovery.attach_feedback(payload, wf)
        payload = model_recovery.prepare_call(self, payload)
        if payload is None:
            return False
        from .historical_measurements import capability as historical_search_capability
        history_capability = historical_search_capability({**wf, 'revision': payload['revision']}, node)
        if history_capability:
            payload['historicalEvidenceSearch'] = history_capability
        if model_recovery.enabled(wf):
            from .smart_evidence_projection import fit_final_input
            payload = fit_final_input(payload)
        if javascript_json_units_upper_bound(payload) > MODEL_INPUT_MAX_UNITS:
            payload.pop('historicalEvidenceSearch', None)  # Optional reference tool cannot crowd out current evidence.
        response = model_recovery.call_model(self, payload)
        with self.store.transaction() as rt:
            current = self.get(workflow_id, rt)
            if current['revision'] != payload['revision']:
                return False  # A user action superseded this model call.
            if _smart_parent is not None:
                try:
                    assert_authorized(self, current, _smart_parent, rt)
                except APIError:
                    return False
            current_node = self._node(current, node_id)
            if not isinstance(response, dict) or response.get('status') != 'ok':
                message = str(response.get('message') or response.get('error') or '模型尚未返回有效决策') if isinstance(response, dict) else '模型尚未返回有效决策'
                current['status'], current['engineState'] = 'attention', 'waiting_model'
                current_node.update(status='attention', engineState='waiting_model', detail=message)
                error = response.get('error', {}) if isinstance(response, dict) else {}
                code = error.get('code', 'MODEL_UNAVAILABLE') if isinstance(error, dict) else 'MODEL_UNAVAILABLE'
                model_recovery.record_failure(self, rt, current, current_node, payload,
                    code=code, message=message, transient=True)
                self.store.save(rt, current, 'attention', {'message': message})
                return False
            try:
                binding = response.get('binding')
                if binding and binding != {k: payload[k] for k in ('runId', 'nodeId', 'revision')}:
                    raise APIError('STALE_MODEL_DECISION', '模型响应绑定了不同运行版本')
                if _smart_parent is not None:
                    from .smart_science import validate_decision
                    decision = validate_decision(self, current, current_node, response.get('decision'), decision_bounds)
                else:
                    decision = validate_proposal(response.get('decision'), current, current_node, decision_bounds)
                if payload.get('modelEvidenceCatalog'):
                    from .smart_evidence_projection import check_shown_evidence
                    check_shown_evidence(payload, decision)
                if _smart_parent is not None and decision['action'] not in ('retry','wait','stop'):
                    raise APIError('SMART_SCIENCE_DECISION', '科学子项仅能调整当前扫描，不能跳过或更换门来源', 409)
                model_recovery.guard_proposal(current, decision, controller=self, rt=rt, node=current_node,
                                              bounds=decision_bounds, targets=payload['allowedBacktrackTargets'])
                release_readout = None
                window_authorization = None
                sampling_plan_controller = self
                if decision['action'] == 'retry':
                    requested_changes = {k: v.get('to') if isinstance(v, dict) else v for k, v in decision.get('parameterChanges', {}).items()}
                    parameters, versions = self._parameters(rt, current, current_node)
                    changes = self._normalize_changes(current_node['procedureId'], requested_changes, parameters)
                    for condition in semantic_conditions:
                        if (condition.get('requiresCallerSemanticCheck') and set(condition['parameters']).intersection(changes)
                                and not set(condition.get('requires', [])).issubset(changes)):
                            raise APIError('INEFFECTIVE_PARAMETER_CHANGE', '固定读出点的 points 只有与 amp_start、amp_stop 成对重扫请求同时提供才生效')
                    trial, release_readout = self._readout_retry_parameters(rt, current, current_node, parameters, versions, changes)
                    model_recovery.guard_retry(self, rt, current, current_node, trial)
                    if smart_sampling.eligible(current, current_node):
                        window_authorization = smart_rabi_window.prepare_retry(self, rt, current, current_node, trial, versions, decision)
                    sampling_plan_controller = smart_sampling.authorize_retry(self, rt, current, current_node, trial, versions, decision)
                    for key, value in changes.items():
                        sampling_plan_controller._validate_parameter(key, value, sampling_plan_controller._parameter_schema(current_node['procedureId']))
                    lookahead = sampling_plan_controller._validate_plan(current, current_node, trial, versions, ident('model-plan'),
                                                                        joint_budget=True, rt=rt)
                    if lookahead is not None:
                        decision['budgetLookahead'] = lookahead
                    decision['parameterChanges'] = changes
                    if release_readout:
                        decision['readoutRevalidation'] = {'id': release_readout['id'], 'mode': 'full_scan',
                            'failedFixedAttemptId': release_readout['lastFixedAttemptId'],
                            'validatedRange': {k: changes[k] for k in ('amp_start', 'amp_stop')}}
                    if changes != requested_changes:
                        decision['requestedParameterChanges'] = requested_changes
                        decision['normalization'] = '执行器脉冲策略及采样网格规范化'
                elif decision['action'] == 'backtrack':
                    target = self._node(current, decision['targetNodeId'])
                    decision['attemptAdmission'] = admit_backtrack(current, target)
                    if self._physical_adapter:
                        parameters, versions = self._parameters(rt, current, target)
                        trial, _ = self._readout_retry_parameters(rt, current, target, parameters, versions, {})
                        decision['budgetLookahead'] = self._validate_plan(current, target, trial, versions,
                            ident('model-backtrack-plan'), joint_budget=True, rt=rt)
            except APIError as exc:
                # Proposal validation happens after the acquisition has already
                # been recorded. It must never relabel that acquisition as an
                # execution failure or replace its quality/evidence.
                rejection = {'action': 'reject', 'reason': exc.message, 'errorCode': exc.code,
                             'evidenceIds': [], 'rejectedProposal': self._chat_summary_value(response.get('decision'))}
                self._record_decision(rt, current, node_id, rejection, 'controller')
                current['status'], current['engineState'] = 'attention', 'waiting_model'
                current_node.update(status='attention', engineState='waiting_model',
                                    detail='模型决策未执行：' + exc.message,
                                    lastDecisionError={'code': exc.code, 'message': exc.message, 'at': now()})
                model_recovery.record_failure(self, rt, current, current_node, payload,
                    code=exc.code, message=current_node['detail'], proposal=response.get('decision'))
                self.store.save(rt, current, 'model_decision_rejected', {
                    'nodeId': node_id, 'attemptId': current_node.get('latestAttemptId'),
                    'errorCode': exc.code, 'message': current_node['detail']})
                return False
            model_recovery.record_resolution(current, decision)
            self._record_decision(rt, current, node_id, decision, 'model')
            action = decision['action']
            if action == 'retry':
                if task_budget.exhausted(current, 'maxAttemptsPerNode', current_node['attempt']):
                    current['status'], current['engineState'] = 'attention', 'budget_exhausted'
                    current_node.update(status='attention', engineState='budget_exhausted',
                                        detail='当前步骤尝试次数已达上限；' + decision['reason'])
                else:
                    changes = {k: v.get('to') if isinstance(v, dict) else v for k, v in decision.get('parameterChanges', {}).items()}
                    if release_readout:
                        release_readout.update(state='released', endedAt=now(), reason=decision['reason'],
                            rescanRequest=deepcopy(decision['readoutRevalidation']))
                        current_node.get('parameterOverrides', {}).pop('amplitudes', None)
                        current_node['parameters'].pop('amplitudes', None)
                    current_node.setdefault('parameterOverrides', {}).update(changes)
                    current_node['parameters'].update(changes)
                    smart_sampling.commit_retry(current, current_node, sampling_plan_controller)
                    smart_rabi_window.commit_retry(current_node, window_authorization)
                    current_node['engineState'], current_node['status'] = 'retry_queued', 'pending'
                    current_node['detail'] = decision['reason']
                    current['status'], current['engineState'] = 'queued', 'queued'
            elif action == 'backtrack':
                affected = self._invalidate(current, decision['targetNodeId'], include_target=True)
                current['currentNodeId'] = decision['targetNodeId']
                current['status'], current['engineState'] = 'queued', 'backtrack_queued'
            elif action == 'stop':
                current['status'], current['engineState'] = 'failed', 'budget_or_evidence_stop'
                current_node.update(status='attention', engineState='budget_or_evidence_stop', detail=decision['reason'])
            else:
                current['status'], current['engineState'] = 'attention', 'waiting_operator'
                current_node.update(status='attention', engineState='waiting_operator', detail=decision['reason'])
            self._finish_timing(current)
            self.store.save(rt, current, 'model_decision', {'message': decision['reason'], 'action': action})
        return current['status'] == 'queued'

    @staticmethod
    def _ancestors(workflow, node):
        ancestors = set(node['dependsOn'])
        while True:
            expanded = ancestors | {d for n in workflow['nodes'] if n['id'] in ancestors for d in n['dependsOn']}
            if expanded == ancestors:
                return sorted(ancestors)
            ancestors = expanded

    @staticmethod
    def _chat_summary_value(value, depth=0):
        if depth > 7:
            return None
        if value is None or isinstance(value, bool) or number(value):
            return value
        if isinstance(value, str):
            return value[:1000]
        if isinstance(value, list):
            return [Controller._chat_summary_value(v, depth + 1) for v in value[:32]]
        if isinstance(value, dict):
            denied = ('secret', 'token', 'password', 'credential', 'authorization', 'cookie', 'apikey', 'privatekey')
            return {k: Controller._chat_summary_value(v, depth + 1) for k, v in list(value.items())[:64]
                    if isinstance(k, str) and len(k) <= 128 and not any(word in k.lower().replace('_', '').replace('-', '') for word in denied)
                    and k not in ('__proto__', 'prototype', 'constructor', 'fileContents', 'rawData', 'rawText')}
        return None

    def _chat_observations(self, workflow, budget, db=None, operation_facts=None):
        """Bounded registered evidence metadata; never open an acquisition file."""
        if not workflow:
            return [], 0
        provenance_keys = {'freshAcquisition', 'operationId', 'parameterVersions', 'runnerSha256',
            'sourceManifest', 'profileHash', 'nativeHalfPolicy', 'shutdownCommandsCompleted',
            'shutdownReadbackVerified', 'adapterId', 'adapterVersion', 'methodId', 'sourceRunId',
            'ruleVersion', 'codeVersion', 'recordedAt'}
        parameter_keys = set(self._bounds()) | {'frequencies_hz', 'amplitudes', 'powers', 'start_hz',
            'stop_hz', 'span_hz', 'rb_lengths', 'rb_sequences', 'rb_master_seed',
            'rb_bootstrap_replicates', 'order_seed'}

        observations, seen, omitted, used = [], set(), 0, 0
        nodes = sorted(workflow['nodes'], key=lambda n: n['id'] != workflow.get('currentNodeId'))
        with (read_chat_snapshot(self.store.path) if db is None else nullcontext(db)) as connection:
            for node in nodes:
                latest = node.get('result') or {}
                for stage, result in (('result', latest), ('selection', latest.get('selectionResult') or {})):
                    for artifact in result.get('artifacts', []):
                        artifact_id = artifact.get('id')
                        if not isinstance(artifact_id, str) or artifact_id in seen:
                            continue
                        seen.add(artifact_id)
                        registered = connection.execute('SELECT sha256,payload FROM platform_artifacts WHERE id=?', (artifact_id,)).fetchone()
                        if not registered or registered['sha256'] != artifact.get('sha256'):
                            omitted += 1
                            continue
                        metadata = json.loads(registered['payload'])
                        summary = {'id': artifact_id, 'workflowId': workflow['id'], 'workflowMode': workflow['mode'],
                            'nodeId': node['id'], 'procedureId': node['procedureId'], 'resourceId': node['resourceId'],
                            'attemptId': node.get('latestAttemptId'), 'evidenceStage': stage,
                            'artifact': {k: metadata[k] for k in ('id', 'name', 'kind', 'sha256', 'url', 'size', 'mimeType') if k in metadata},
                            'dataKind': result.get('dataKind'), 'metrics': result.get('metrics', {}),
                            'quality': {k: result.get('quality', {}).get(k) for k in ('status', 'reasons', 'ruleVersion', 'criteria')},
                            'acquiredAt': result.get('acquiredAt'),
                            'parameters': {k: v for k, v in result.get('parameters', {}).items() if k in parameter_keys},
                            'provenance': {k: v for k, v in result.get('provenance', {}).items() if k in provenance_keys},
                            'summaryOnly': True, 'rawFileRead': False}
                        facts = (operation_facts or {}).get((result.get('provenance') or {}).get('operationId'))
                        if facts:
                            summary['parameterEvidence'] = facts['parameterEvidence']
                            summary['acquiredAtBeijing'] = facts.get('acquiredAtBeijing')
                            summary['displayTimeZone'] = 'Asia/Shanghai (UTC+08:00)'
                        summary = self._chat_summary_value(summary)
                        size = len(dumps(summary))
                        if size > 12000 or used + size > budget or len(observations) >= 128:
                            omitted += 1
                            continue
                        observations.append(summary)
                        used += size
        return observations, omitted

    def chat(self, payload):
        message = payload.get('message')
        if not isinstance(message, str) or not message.strip() or len(message) > 20000:
            raise APIError('INVALID_MESSAGE', '消息不能为空且不能超过 20000 字符')
        selected_node_id = payload.get('nodeId')
        if selected_node_id is not None and (not isinstance(selected_node_id, str) or not selected_node_id or not payload.get('workflowId')):
            raise APIError('INVALID_CHAT_NODE', '所选节点必须属于指定实验任务')
        session_id = payload.get('sessionId') or ident('chat')
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 256:
            raise APIError('INVALID_SESSION', '对话编号无效')
        scope = {'kind':'workflow','id':payload['workflowId']} if payload.get('workflowId') else {'kind':'workspace'}
        # Pin workflow, operations and artifact registry to one read-only snapshot.
        # Close it before waiting on the model so acquisition writes remain free.
        with read_chat_snapshot(self.store.path) as db:
            wf = self.get(payload['workflowId'], types.SimpleNamespace(db=db)) if payload.get('workflowId') else None
            model_session_id = 'legacy_' + digest({'scope':scope,'sessionId':session_id})[:40]
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_conversations'").fetchone():
                conversation = db.execute('SELECT payload FROM platform_conversations WHERE id=?',(session_id,)).fetchone()
                if conversation:
                    if json.loads(conversation[0])['scope'] != scope:
                        raise APIError('INVALID_SCOPE','该对话属于其他范围',409)
                    model_session_id = session_id
            selected_node = self._node(wf, selected_node_id) if selected_node_id is not None else None
            brief = None
            if wf:
                brief = {k: wf[k] for k in ('id', 'name', 'mode', 'status', 'engineState', 'revision', 'resourceIds', 'workflowComplete')}
                brief['currentNodeId'] = wf.get('currentNodeId')
                brief['nodes'] = [{k: n.get(k) for k in ('id', 'label', 'procedureId', 'engineState', 'attempt', 'latestAttemptId', 'parameters', 'parameterVersions', 'dependsOn')}
                                  | {'result': {k: n.get('result', {}).get(k) for k in ('dataKind', 'methodLabel', 'metrics', 'quality', 'acquiredAt', 'provenance', 'partial', 'verificationPending', 'stale')},
                                     'evidenceIds': [a['id'] for a in n.get('result', {}).get('artifacts', [])]}
                                  for n in wf['nodes']]
                brief = self._chat_summary_value(brief)
            bound = self.with_binding(wf['configurationBinding']) if wf and wf.get('configurationBinding') else self
            context = {'workflow': brief, 'catalog': bound.catalog.describe(bound.adapter.capabilities(), bound._bounds()),
                       'notice': '聊天建议不会直接下发设备；实验动作经过Python控制器校验。inspect_observation 仅核查本任务已登记的证据摘要，不读取原始文件；以 dataKind 和 acquiredAt 区分本轮采集与历史记录。采集状态截至 acquisitionState 的快照；扫描几何仅来自已登记且核对过哈希的 plan 元数据。'}
            if wf:
                context['acquisitionState'] = acquisition_context(db, wf, self.root, now())
            if selected_node is not None:
                context['selectedNode'] = self._chat_summary_value({
                    'workflowId': wf['id'], 'workflowRevision': wf['revision'], 'readOnly': True,
                    **{k: selected_node.get(k) for k in ('id', 'label', 'procedureId', 'engineState', 'attempt',
                        'latestAttemptId', 'parameters', 'parameterVersions', 'dependsOn')},
                    'result': {k: (selected_node.get('result') or {}).get(k) for k in
                        ('dataKind', 'metrics', 'quality', 'acquiredAt', 'partial', 'verificationPending', 'stale')}})
            observation_budget = max(0, min(60000, 170000 - len(dumps(context)) - len(message)))
            operation_facts = {operation['operationId']: operation
                for node in context.get('acquisitionState', {}).get('nodes', []) for operation in node['operations']}
            context['observations'], omitted = self._chat_observations(wf, observation_budget, db, operation_facts)
            context['observationSummary'] = {'provided': len(context['observations']), 'omitted': omitted,
                                             'source': 'registered_workflow_evidence_metadata', 'rawFileRead': False}
            request = bound_chat_observations({'message': message, 'sessionId': model_session_id, 'context': context})
        result = self._brain('/chat', request)
        return {'sessionId': session_id, 'message': result.get('message') or result.get('error') or '模型暂时不可用',
                'status': result.get('status', 'error'), 'model': result.get('model')}

    def health(self):
        brain_address = urllib.parse.urlsplit(self.brain_url)
        agent_port = brain_address.port or (443 if brain_address.scheme == 'https' else 80)
        return {'status': 'ok', 'version': '0.2.0', 'projectId': 'qcal-local-f8-platform', 'projectRoot': str(self.root.resolve()), 'agentPort': agent_port, 'agent': self._brain('/health', timeout=3),
                'stabilityError': self._stability_error,
                'hardware': {**self.adapter.preflight(), 'ownership': self.hardware_ownership()}, 'capabilities': self.adapter.capabilities()}

    def hardware_ownership(self, refresh=False):
        if not self._physical_adapter:
            return {'verified': True, 'owners': [], 'reason': 'Explicit test adapter; no physical endpoint'}
        if refresh or self._ownership_cache is None or time.time() - self._ownership_cache.get('checkedAt', 0) > 15:
            try:
                value = self.adapter.hardware_ownership(refresh=refresh)
            except Exception:
                value = {'verified': False, 'owners': [], 'check':{'schema':'qcal-socket-check/1','status':'adapter_error'}}
            if not isinstance(value, dict):value={}
            if value.get('verified') is not True or not isinstance(value.get('owners'), list):
                value = {**value, 'verified': False, 'owners':value.get('owners') if isinstance(value.get('owners'),list) else []}
            value.setdefault('checkedAt',time.time())
            self._ownership_cache = value
        return self._ownership_cache

    def account(self, action, payload=None):
        from .account import account_request
        # Account mutations and model tests are only exposed by explicit UI
        # actions. Model conversations cannot send SMS or log accounts in/out.
        # Includes official login/profile/catalog calls and encrypted receipt writes.
        timeout = 125 if action == 'test' else 90 if payload is not None else 25
        return account_request(self.brain_url, action, payload, timeout=timeout)

    def report(self, workflow_id):
        # One read-only WAL snapshot: a concurrent measurement cannot mix old
        # workflow quality with new attempts or events in the same report.
        with read_chat_snapshot(self.store.path) as db:
            wf = self.get(workflow_id, types.SimpleNamespace(db=db))
            if (wf.get('smartRun') or {}).get('schema') == 'q11-full-same-gate/1':
                from scripts.export_report_snapshot import _read_report, attach_smart_children
                stamp, root = now(), self.root / 'data'
                report = _read_report(db, workflow_id, root, stamp)
                attach_smart_children(db, report, root, stamp)
                report['events'] = [{**dict(row), 'payload': json.loads(row['payload'])} for row in db.execute(
                    'SELECT * FROM events WHERE run_id=? ORDER BY seq', (workflow_id,))]
                return report
            nodes = []
            for node in wf['nodes']:
                attempts = [json.loads(row[0]) for row in db.execute(
                    'SELECT payload FROM platform_attempts WHERE workflow_id=? AND node_id=? ORDER BY number', (workflow_id, node['id']))]
                decisions = [json.loads(row[0]) for row in db.execute(
                    'SELECT payload FROM platform_decisions WHERE workflow_id=? AND node_id=? ORDER BY rowid', (workflow_id, node['id']))]
                nodes.append({'node': node, 'attempts': attempts, 'decisions': decisions})
            events = [{**dict(row), 'payload': json.loads(row['payload'])} for row in db.execute(
                'SELECT * FROM events WHERE run_id=? ORDER BY seq', (workflow_id,))]
            from .receipt_presentation import read_receipts, report_view
            return report_view({'schema': 'qcal-report/1', 'generatedAt': now(), 'workflow': wf, 'nodes': nodes,
                    'events': events, 'source': 'recorded_data_only', 'workflowComplete': wf['workflowComplete'],
                    'stoppedReceipts': read_receipts(db, workflow_id)})

    def archive_status(self):
        return self.archive.status()

    def archive_list(self, workflow_id=None, limit=30, cursor=None):
        if workflow_id:
            self.get(workflow_id)
        return self.archive.list(workflow_id, limit, cursor)

    def archive_item(self, archive_id):
        return self.archive.item(archive_id)

    def knowledge(self):
        with self.store.connect() as rt:
            return {'rules': self.catalog.rules(), 'candidates': self.experience.list(rt),
                    'published': self.experience.published_rules(rt),
                    'semantics': '已发布经验仅供新任务引用；当前任务的门槛和规则快照保持冻结'}

    def publish_experience(self, candidate_id, operation_id):
        with self.store.transaction() as rt:
            rule = self.experience.publish(rt, candidate_id, operation_id)
            rt._event(None, 'experience_published', {'ruleId': rule['id'], 'candidateId': candidate_id})
            return rule
