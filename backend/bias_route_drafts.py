"""Convert the operator's verified wiring sheet into a frozen LF route."""
from .common import APIError, digest
from .workspace import device_key
from adapters.science_flux.command_contracts import (
    ContractError, NUMBERS, SCHEMA, validate_command_route,
)

NUMBER_FIELDS = {'board', 'channel', 'polarity', *NUMBERS}
REPLY_FIELDS = ('moduleReplies', 'channelReplies', 'offsetReplies')
NOTE_FIELDS = ('lineSource', 'staticDcSource', 'startSource', 'endSource', 'replySource')
FIELDS = {'lineId', 'stationIdentity', *NUMBER_FIELDS, *REPLY_FIELDS, *NOTE_FIELDS}
EVIDENCE = {
    'confirmationEvidenceSha256': 'lineSource',
    'preparedStaticDcEvidenceSha256': 'staticDcSource',
    'startSettingEvidenceSha256': 'startSource',
    'endSettingEvidenceSha256': 'endSource',
}


def _invalid(text):
    return APIError('BIAS_ROUTE_DRAFT_INVALID', text)


def _context(value, resource, fields):
    instrument = value.get('instrument', {})
    physical = value.get('routes', {}).get(resource, {})
    if not isinstance(physical, dict):
        raise _invalid(f'{resource} 的物理路由格式无效')
    return {'resourceId': resource, 'fields': fields, 'machine': value.get('machine', ''),
            'instrument': {k: instrument.get(k) for k in ('id', 'host', 'port', 'address')},
            'physicalRoute': {k: physical.get(k)
                              for k in ('xyPhysical', 'readoutPhysical', 'adcPhysical')}}


def _compile(resource, fields, deployment, context):
    missing = sorted(key for key in FIELDS if not fields.get(key, '').strip())
    if missing:
        raise _invalid(f'{resource} 的偏置接线、范围、回执和核实来源须填齐后再确认')
    if any(fields[key].strip().casefold() in ('待核实', '未知', 'unknown', 'tbd', '无', '不清楚') for key in NOTE_FIELDS):
        raise _invalid(f'{resource} 的现场来源仍待核实')
    inst = deployment.get('instrument', {})
    if not isinstance(inst.get('host'), str) or not inst['host'].strip() or type(inst.get('port')) is not int or not 1 <= inst['port'] <= 65535:
        raise _invalid('请先填写当前机箱的主机地址和端口')
    route = dict(schema=SCHEMA, resourceId=resource, deviceKey=device_key(deployment),
                 unit='V', endPolicy='return_declared_setting', lineId=fields['lineId'].strip(),
                 stationIdentity=fields['stationIdentity'].strip())
    try:
        for key in NUMBER_FIELDS:
            route[key] = int(fields[key]) if key in ('board','channel','polarity') else float(fields[key])
    except ValueError as exc:
        raise _invalid(f'{resource} 的槽位、通道、极性或电压参数无效') from exc
    for key, note in EVIDENCE.items():
        route[key] = digest({'context': context, 'sourceField': note, 'source': fields[note]})
    route['commandReplyContract'] = {'kind':'exact_tokens',
        **{key: [s.strip() for s in fields[key].splitlines() if s.strip()] for key in REPLY_FIELDS},
        'evidenceSha256': digest({'source': fields['replySource'], 'station': route['stationIdentity'],
                                  'responses': [fields[k] for k in REPLY_FIELDS]})}
    try:
        return validate_command_route(route)
    except ContractError as exc:
        raise _invalid(f'{resource} 的偏置线路无法启用。请核对板卡、起止值、允许范围、爬坡时间和已确认的成功回执。技术原因：{exc}') from exc


def prepare_bias_route_drafts(deployment):
    if 'biasRouteDrafts' not in deployment:
        return
    sheets = deployment['biasRouteDrafts']
    active = deployment.get('biasRoutes', {})
    if not isinstance(sheets, dict) or len(sheets) > 256 or not isinstance(active, dict):
        raise _invalid('偏置线路须按比特保存，最多 256 条')
    routes = dict(active)
    for resource, sheet in sheets.items():
        if not isinstance(resource, str) or not resource.strip() or len(resource)>128 or not isinstance(sheet, dict):
            raise _invalid('偏置线路缺少有效的比特编号')
        if set(sheet) - {'fields', 'confirmation'}:
            raise _invalid('偏置草稿字段无效')
        fields = sheet.get('fields')
        if not isinstance(fields, dict) or set(fields) - FIELDS or any(not isinstance(v,str) or len(v)>4096 for v in fields.values()):
            raise _invalid(f'{resource} 的偏置草稿内容无效')
        existing = routes.pop(resource, None)
        if existing is not None and (not isinstance(existing, dict) or existing.get('schema') != SCHEMA):
            raise _invalid(f'{resource} 已配置独立测压线路；请核对已有配置')
        context = _context(deployment, resource, fields)
        if sheet.get('confirmation') == context:
            routes[resource] = _compile(resource, fields, deployment, context)
        else:
            sheet.pop('confirmation', None)
    deployment['biasRoutes'] = routes

