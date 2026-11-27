"""Admission of an explicit manual experiment, before any task is persisted."""
from copy import deepcopy
from .common import APIError, digest


def manual_selection(controller, data, nodes, selected):
    from .catalog import SCIENCE_METHODS, SCIENCE_IRB_METHODS, SCIENCE_FLUX_METHODS
    if 'scienceDependencies' in data or 'fluxPointEvidence' in data or 'biasCommandAuthorization' in data:
        raise APIError('SCIENCE_CLIENT_PROOF_FORBIDDEN', '科学执行证明只能由服务器重新解析')
    procedure = data.get('manualProcedureId')
    values = data.get('manualParameters', {})
    if not procedure:
        if values:
            raise APIError('MANUAL_METHOD_REQUIRED', '请先选择实验类型')
        return None
    if data.get('mode') != 'real' or data.get('policy') != 'strict' or len(selected) != 1:
        raise APIError('MANUAL_POLICY_REQUIRED', '实验类型选择适用于单比特人工逐步任务')
    target = next((node for node in nodes if node['procedureId'] == procedure), None)
    if not target or target['capability'] != 'available':
        raise APIError('UNSUPPORTED_METHOD', '当前执行器未接入所选实验', 409)
    if not isinstance(values, dict) or any(not isinstance(key, str) for key in values):
        raise APIError('INVALID_PARAMETERS', '实验参数必须是名称与数值')
    schema = controller._parameter_schema(procedure)
    for key, value in values.items():
        controller._validate_parameter(key, value, schema)
    normalized = controller._normalize_changes(procedure, values, target['parameters'])
    for key, value in normalized.items():
        controller._validate_parameter(key, value, schema)
    combined = {**target['parameters'], **normalized}
    if procedure=='sq_flux_point_recheck':
        point_id=data.get('manualWorkingPointId')
        if not isinstance(point_id,str) or not 1<=len(point_id)<=256:
            raise APIError('FLUX_POINT_REQUIRED','请选择由原生偏置扫描保存的工作点或闲置点')
        target['workingPointId']=point_id
    if procedure in SCIENCE_METHODS:
        refs = data.get('manualScienceReferences', [])
        from .science_dependencies import execution_roles
        roles = execution_roles(procedure,combined)
        if (not isinstance(refs, list) or len(refs) > 16 or any(not isinstance(ref, dict)
                or set(ref) != {'role', 'recordId'} or ref['role'] not in roles
                or not isinstance(ref['recordId'], str) or not 1 <= len(ref['recordId']) <= 256 for ref in refs)
                or len({ref['role'] for ref in refs}) != len(refs)):
            raise APIError('SCIENCE_DEPENDENCY_INVALID', '来源只允许唯一角色和服务器记录编号')
        target['scienceReferences'] = deepcopy(refs)
        target['scienceSourceRoles'] = sorted(roles)
    for begin, end in (('start_s', 'stop_s'), ('amp_start', 'amp_stop'), ('start_hz', 'stop_hz')):
        if begin in combined and end in combined and combined[end] <= combined[begin]:
            raise APIError('INVALID_SCAN', '扫描终值必须大于起值')
    # Pure native planning catches generated grids and aggregate acquisition
    # limits before a draft exists. RB has an additional current-run proof and
    # remains admitted by its existing per-execution prerequisite check.
    if controller._physical_adapter and procedure in SCIENCE_METHODS:
        # Static scalar/grid/budget validation only. Source eligibility is freshly
        # resolved against the persisted task before run_step can create an effect.
        from adapters.science_v3.plans import BOUNDS, build_rows
        allowed=set(controller.adapter.validation_parameter_keys()) if procedure in SCIENCE_IRB_METHODS or procedure in SCIENCE_FLUX_METHODS else set(BOUNDS)
        defaults = {key: value for key, value in controller.profile['resources'][selected[0]].items() if key in allowed}
        try:
            if procedure in SCIENCE_FLUX_METHODS:
                from adapters.science_flux.plans import build_campaign
                point=None
                if procedure=='sq_flux_point_recheck':
                    from .flux_points import resolve_point
                    with controller.store.connect() as rt:
                        point=resolve_point(controller,rt,data.get('manualWorkingPointId'),resource_id=selected[0])
                    target['workingPointId']=data['manualWorkingPointId']
                build_campaign(procedure,{**defaults,**combined},controller.profile.get('biasRoutes',{}).get(selected[0]),point=point)
            elif procedure=='sq_drag_half_scan':
                from adapters.science_v3_irb.half_scan import build_half_scan, SCAN_FIELDS
                build_half_scan({k:v for k,v in {**defaults,**combined}.items() if k in SCAN_FIELDS})
            elif procedure=='sq_drag_irb':
                from adapters.science_v3_irb.plans import normalize_parameters, FIELDS, DEFAULTS
                normalize_parameters({k:v for k,v in {**defaults,**combined}.items() if k in FIELDS|set(DEFAULTS)|{'pulse_compatibility'}})
            elif procedure=='sq_shape_rabi_amplitude_repeat5':
                from adapters.science_shape_repeat5.plans import build_repeat5
                build_repeat5({**defaults,**combined})
            else:build_rows(procedure, {**defaults, **combined})
        except (ValueError, TypeError, KeyError) as exc:
            raise APIError('SCIENCE_PLAN_REJECTED', str(exc)) from None
    elif controller._physical_adapter and procedure != 'sq_rb':
        from adapters.f8 import AdapterError
        try:
            controller.adapter.build_plan({'operationId': 'manual-preview-' + digest(data)[:24],
                'resourceId': selected[0], 'procedureId': procedure, 'parameters': combined})
        except AdapterError as exc:
            raise APIError(exc.code, str(exc), 400) from None
    target['parameters'] = deepcopy(combined)
    target['parameterOverrides'] = deepcopy(normalized)
    target['parameterVersions'].update({key: 0 for key in normalized})
    return {'procedureId': procedure, 'nodeId': target['id'], 'parameters': deepcopy(normalized)}
