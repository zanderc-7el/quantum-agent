"""Audited Q11 historical parameters for a distinct new pulse-only task.

No historical data are registered as current evidence. No calibration is copied.
"""
from copy import deepcopy
import hashlib
import json

from .common import APIError, checked_path, now
from .prior_failure_context import IMPLEMENTATION, PROFILE_FILES, V3_ID, load_audited_bindings

SEED_FILE = 'knowledge/working_point_q11_seed_v1.json'
SEED_SHA = 'c3bee40407ea8783f3eeb2baaf172e830cf171dda220ab3acdb4e79ccb7941d0'


def _read(path, sha256, maximum):
    path = checked_path(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise APIError('WORKING_POINT_SEED_UNAVAILABLE', '复刻工作点来源缺失或大小不符：' + path.name, 409)
    with path.open('rb') as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum or hashlib.sha256(content).hexdigest() != sha256:
        raise APIError('WORKING_POINT_SEED_CHANGED', '复刻工作点来源 SHA 不匹配：' + path.name, 409)
    return content


def build_working_point_seed(adapter):
    """Validate fixed audited identities and source bytes, with no device access."""
    implementation = type(adapter).__module__ + '.' + type(adapter).__qualname__
    profile = adapter.profile
    if (implementation != IMPLEMENTATION or getattr(adapter, 'physical_hardware', None) is not True
            or profile.get('id') not in (*PROFILE_FILES, V3_ID)):
        raise APIError('WORKING_POINT_ADAPTER_REQUIRED', '复刻工作点仅适用于已审计 Q11 ArcLight v1/v2/v3', 409)
    try:
        root = checked_path(adapter.root)
        bindings = load_audited_bindings(root, implementation)
        binding = bindings.get(profile['id'])
        frozen = lambda value: {k: v for k, v in value.items() if k not in ('enabled', 'connectionConfirmedAt')}
        if not binding or frozen(profile) != frozen(binding['profile']):
            raise APIError('WORKING_POINT_PROFILE_MISMATCH', '当前配置不同于已审计完整 profile，不能套用历史起点', 409)
        seed = json.loads(_read(root / SEED_FILE, SEED_SHA, 65536))
        if (seed.get('schema') != 'working-point-seed/1' or seed.get('status') != 'seed_only'
                or seed.get('resourceId') != 'Q11'
                or any(profile['resources']['Q11'].get(k) != v for k, v in seed['parameters'].items())
                or any(profile['instrument'].get(k) != v for k, v in seed['physicalRouting'].items())):
            raise APIError('WORKING_POINT_PROFILE_MISMATCH', '当前物理参数或路由不匹配历史候选点', 409)
        _read(seed['sourceReport']['path'], seed['sourceReport']['sha256'], 1024 * 1024)
        documents = {}
        for item in seed['sourceFiles']:
            content = _read(item['path'], item['sha256'], 16 * 1024 * 1024 if item['kind'] == 'raw' else 1024 * 1024)
            if len(content) != item['bytes']:
                raise APIError('WORKING_POINT_SEED_CHANGED', '历史文件大小与审计索引不同', 409)
            if item['kind'] != 'raw':
                documents[item['kind']] = json.loads(content)
        receipt, completion = documents['receipt'], documents['completion']
        raw = next(item for item in seed['sourceFiles'] if item['kind'] == 'raw')
        if (receipt.get('mode') != 'real_hardware' or receipt.get('state') != 'acquired'
                or receipt.get('job') != seed['sourceJob'] or receipt.get('target') != 'Q11'
                or receipt.get('points_completed') != 122 or receipt.get('points_expected') != 122
                or receipt.get('shutdown_ok') is not True
                or documents['analysis'].get('any_repeat_validated') is not True):
            raise APIError('WORKING_POINT_RECEIPT_INVALID', '历史 TimeRabi 完成回执未满足已审计条件', 409)
        matches = [item for item in completion.get('runs', []) if item.get('job') == seed['sourceJob']]
        if (len(matches) != 1 or matches[0].get('raw_sha256') != raw['sha256']
                or matches[0].get('folder') != receipt.get('folder') or matches[0].get('points') != 122
                or matches[0].get('effective_shots_per_point') != 2000 or matches[0].get('safe_shutdown') is not True):
            raise APIError('WORKING_POINT_RECEIPT_INVALID', '历史原始数据没有绑定完整采集和清理回执', 409)
        result = deepcopy(seed)
        result.update(checkedAt=now(), seedMetadataSha256=SEED_SHA, currentAdapter={
            'implementation': implementation, 'profileId': profile['id'],
            'profileFileSha256': binding['profileFileSha256'], 'sourceManifestSha256': binding['sourceManifestSha256']})
        return result
    except APIError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration) as exc:
        raise APIError('WORKING_POINT_SEED_UNAVAILABLE', '不能核验历史工作点来源：' + type(exc).__name__, 409) from None
