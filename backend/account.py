"""Same-origin account gateway. Authentication secrets stay in the agent vault."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .common import APIError, dumps

ALLOWED = {
    'status': ('GET', set()),
    'models': ('GET', set()),
    'send-code': ('POST', {'phone', 'operationId'}),
    'login': ('POST', {'phone', 'code', 'operationId', 'useForAgent'}),
    'connect': ('POST', {'model', 'operationId'}),
    'logout': ('POST', {'operationId'}),
    'test': ('POST', {'operationId'}),
}

def public_account(value):
    """Explicit response schema prevents a future provider from leaking app_key."""
    if not isinstance(value, dict):
        raise APIError('ACCOUNT_BAD_RESPONSE', '账户服务返回格式不正确', 502)
    result = {k: value[k] for k in ('provider', 'configured', 'authenticated', 'status', 'retryAfter', 'message', 'verified', 'source', 'defaultModelId') if k in value}
    schemas = {'user': {'name', 'phoneMasked'}, 'balance': {'remainingCalls', 'label', 'updatedAt'},
               'modelConnection': {'provider', 'model', 'active', 'verified', 'status', 'source', 'lastVerifiedAt', 'selection', 'note'},
               'capabilities': {'sendCode', 'login', 'connect', 'test'}, 'error': {'code', 'message'}}
    for key, fields in schemas.items():
        if isinstance(value.get(key), dict):
            result[key] = {k: v for k, v in value[key].items() if k in fields}
        elif key in value and value[key] is None:
            result[key] = None
    if isinstance(value.get('models'), list):
        result['models'] = [{k: v for k, v in item.items() if k in ('id', 'label', 'provider', 'description', 'recommended', 'available', 'default')}
                            for item in value['models'] if isinstance(item, dict) and isinstance(item.get('id'), str)]
    # A successful account mutation may include its fresh public snapshot.
    if isinstance(value.get('account'), dict):
        result['account'] = public_account(value['account'])
    return result

def account_request(base_url, action, payload=None, timeout=35):
    if action not in ALLOWED:
        raise APIError('ACCOUNT_ACTION_NOT_FOUND', '账户操作不存在', 404)
    method, fields = ALLOWED[action]
    if method == 'POST':
        if not isinstance(payload, dict) or set(payload) - fields:
            raise APIError('ACCOUNT_INVALID_INPUT', '账户请求包含不支持的字段')
        operation = payload.get('operationId')
        if not isinstance(operation, str) or not 1 <= len(operation) <= 256:
            raise APIError('OPERATION_ID_REQUIRED', '账户写操作需要唯一操作编号')
    data = None if method == 'GET' else dumps(payload).encode('utf-8')
    request = urllib.request.Request(base_url + '/account/' + action, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return public_account(json.loads(response.read(1024 * 1024)))
    except urllib.error.HTTPError as exc:
        # The local account service supplies public error codes; raw upstream
        # payloads and credential-bearing data must never be reflected here.
        try:
            error = public_account(json.loads(exc.read(16384))).get('error', {})
        except (ValueError, APIError):
            error = {}
        raise APIError(error.get('code', 'ACCOUNT_REQUEST_FAILED'), error.get('message', '账户请求未完成'), exc.code) from None
    except (OSError, ValueError) as exc:
        raise APIError('ACCOUNT_SERVICE_UNAVAILABLE', '账户服务暂不可用，请检查本地智能体服务', 503) from None
