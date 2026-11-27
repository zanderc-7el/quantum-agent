"""Optional per-route XY capability; no defaults, measurement or device I/O."""
import math
import re
import unicodedata

MAX_SOURCE_CHARS = 2000
PLACEHOLDER_SOURCES = frozenset((
    'unknown', 'unconfirmed', 'pending', 'tbd', 'todo', 'n/a', 'na', 'none', 'null',
    'test', 'example', 'placeholder', '未提供', '暂无', '未知', '待确认', '待核实',
    '待填写', '待补充', '待定', '不详', '无', '无资料', '无来源', '已确认', '确认', '占位', '示例', '测试'))


def source_is_specific(value):
    if not isinstance(value, str) or not 0 < len(value) <= MAX_SOURCE_CHARS:
        return False
    normalized = re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).lower())
    # Whole-value placeholders only: a concrete report title can legitimately
    # contain these words. Text validation does not prove the source is true.
    return bool(normalized) and any(c.isalnum() for c in normalized) and normalized not in PLACEHOLDER_SOURCES


def _positive(value):
    if type(value) not in (int, float):return False
    try:return math.isfinite(value) and value > 0
    except OverflowError:return False


def validate_xy_passband(route, *, required=False):
    """Validate entered fields; only an explicit complete confirmation authorizes.

    Pending drafts may omit the band or use null endpoints/source. Existing
    standard methods do not call this optional-capability requirement.
    """
    if not isinstance(route, dict):
        raise ValueError('XY 通带须属于明确的资源路由')
    if 'xyPassbandConfirmed' in route and type(route['xyPassbandConfirmed']) is not bool:
        raise ValueError('XY 通带确认状态须为布尔值')
    source = route.get('xyPassbandSource')
    if source is not None and (not isinstance(source, str) or len(source) > MAX_SOURCE_CHARS):
        raise ValueError('XY 通带来源须为不超过 2000 字符的文字')
    band = route.get('xyPassbandHz')
    if band is not None:
        if not isinstance(band, list) or len(band) != 2:
            raise ValueError('XY 通带须为两个 Hz 端点，未填写端点可为 null')
        if any(value is not None and not _positive(value) for value in band):
            raise ValueError('XY 通带端点须为有限正数 Hz，不能使用布尔值')
        if all(value is not None for value in band) and band[0] >= band[1]:
            raise ValueError('XY 通带下限须小于上限')
    confirmed = route.get('xyPassbandConfirmed') is True
    if confirmed or required:
        if not confirmed or band is None or any(value is None for value in band) or not source_is_specific(source):
            raise ValueError('此测量需要已确认的完整 XY 通带及具体资料来源；不能使用空白或占位说明')
        return tuple(band)
    return None


def require_xy_frequencies(route, frequencies):
    band = validate_xy_passband(route, required=True)
    for frequency in frequencies:
        if not _positive(frequency) or not band[0] <= frequency <= band[1]:
            raise ValueError('实际激励频率超出该资源已确认的 XY 通带')
    return band
