"""External readout calibration contract and non-destructive comparison masks.

Imported calibration remains user-provided evidence. It never becomes an
accepted platform calibration, a new acquisition, or a gate-fidelity result.
"""
from copy import deepcopy
from datetime import datetime
import base64
import hashlib
import json
import math

from .common import digest, number
from .science_models import fail

SCHEMA = 'qcal-external-readout-calibration/1'
VERSION = 'qcal-readout-observation/1'
READOUT_KEYS = ('readout_hz', 'readout_amp', 'readout_width_s', 'xy_delay_s')


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None: raise ValueError()
        return result
    except (AttributeError, ValueError, TypeError):
        fail('标定时间须为带时区的 ISO 日期', 'SCIENCE_CALIBRATION_INVALID')


def projection_context(value):
    if not isinstance(value, dict):
        fail('原始记录没有可核对的 I/Q 投影', 'SCIENCE_CALIBRATION_CONTEXT_MISSING')
    direction, origin = value.get('direction'), value.get('origin')
    if any(not isinstance(v, list) or len(v) != 2 or not all(number(x) for x in v)
           for v in (direction, origin)) or not math.isclose(sum(x*x for x in direction), 1., rel_tol=1e-8, abs_tol=1e-8):
        fail('原始投影方向或偏移不完整', 'SCIENCE_CALIBRATION_CONTEXT_MISSING')
    return {'kind': 'linear_iq_projection', 'direction': deepcopy(direction), 'origin': deepcopy(origin),
            'formula': '(I-originI)*directionI+(Q-originQ)*directionQ'}


def calibration_template(context):
    return {'schema': SCHEMA, 'appliesTo': deepcopy(context),
            'source': {'document': '', 'origin': '', 'measuredAt': None, 'validFrom': None, 'validUntil': None},
            'statePreparation': {'kind': 'independently_validated_states', 'evidence': ''},
            'response': {'kind': 'linear_state_response', 'groundResponse': None, 'excitedResponse': None},
            'usage': '填写独立标定的两态响应和来源；空值不可用于比较。制备标签/PCA/拟合峰谷不是纯态标定。'}


def import_calibration(inputs, context, acquired_at):
    text, filename = inputs.get('documentText'), inputs.get('fileName')
    try:
        if 'documentBase64' in inputs:
            encoded=inputs['documentBase64']
            if not isinstance(encoded,str) or len(encoded)>349528:raise ValueError()
            original=base64.b64decode(encoded,validate=True);text=original.decode('utf-8-sig')
        else:original=text.encode('utf-8')
    except (AttributeError,ValueError,UnicodeError):
        fail('标定文件须为 UTF-8 JSON', 'SCIENCE_CALIBRATION_INVALID')
    if not isinstance(text, str) or not 1 <= len(original) <= 262144 or not isinstance(filename, str) or not 1 <= len(filename) <= 256:
        fail('请选择不超过 256 KiB 的标定 JSON 文件', 'SCIENCE_CALIBRATION_INVALID')
    try:
        document = json.loads(text)
    except (ValueError, TypeError):
        fail('标定文件不是有效 JSON', 'SCIENCE_CALIBRATION_INVALID')
    if not isinstance(document, dict) or document.get('schema') != SCHEMA or document.get('appliesTo') != context:
        fail('标定的比特、读出条件、单位或投影与所选采集不一致', 'SCIENCE_CALIBRATION_CONTEXT_MISMATCH')
    origin = document.get('source', {})
    if not isinstance(origin, dict) or any(not isinstance(origin.get(k), str) or not origin[k].strip() for k in ('document', 'origin')):
        fail('标定须注明来源文件和提供方', 'SCIENCE_CALIBRATION_INVALID')
    measured, start, end, acquisition = [timestamp(v) for v in (origin.get('measuredAt'), origin.get('validFrom'), origin.get('validUntil'), acquired_at)]
    if not start <= measured <= end or not start <= acquisition <= end:
        fail('标定有效时间未覆盖标定采集或当前实测', 'SCIENCE_CALIBRATION_EXPIRED')
    state = document.get('statePreparation', {})
    if not isinstance(state, dict) or state.get('kind') != 'independently_validated_states' or not isinstance(state.get('evidence'), str) or not state['evidence'].strip():
        fail('需提供独立两态制备验证来源；制备标签不等同纯态标定', 'SCIENCE_CALIBRATION_STATE_EVIDENCE')
    response = document.get('response', {})
    if not isinstance(response, dict) or response.get('kind') != 'linear_state_response' or any(not number(response.get(k)) for k in ('groundResponse', 'excitedResponse')) or response['groundResponse'] == response['excitedResponse']:
        fail('两态响应须为不同的有限数值，且沿用采集单位', 'SCIENCE_CALIBRATION_INVALID')
    return {'kind': 'readout_calibration', 'modelVersion': VERSION, 'dataKind': 'provided_data',
            'source': 'external_calibration', 'dispatched': False, 'acceptedCalibration': False,
            'physicalAccepted': False, 'document': document, 'fileName': filename,
            'documentSha256': hashlib.sha256(original).hexdigest(),
            'contextSha256': digest(context), 'qualification': 'external_claim_not_platform_verified',
            'meaning': '用户提供的外部标定；来源声明未被平台独立验收。数值比较以此标定有效为条件。'}


def forward_observation(probabilities, calibration, context, acquired_at):
    # Revalidate the immutable original import against the currently checked
    # source context; loading a file once must not authorize another projection.
    output = import_calibration(calibration['input'], context, acquired_at)
    if output != calibration.get('result'):
        fail('标定内容与已保存版本不一致', 'SCIENCE_CALIBRATION_CHANGED')
    response = output['document']['response']
    ground, excited = response['groundResponse'], response['excitedResponse']
    return [ground + (excited-ground)*p if p is not None else None for p in probabilities]


def excluded_points(value, axis):
    value = [] if value is None else value
    if not isinstance(value, list) or len(value) > len(axis) or any(not number(x) or x not in axis for x in value) or len(set(value)) != len(value):
        fail('暂不比较的点必须来自当前实测横轴', 'SCIENCE_COMPARISON_MASK_INVALID')
    return set(value)
