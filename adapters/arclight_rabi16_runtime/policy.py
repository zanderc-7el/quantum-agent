"""Pure request identities and immutable profile loading for the Rabi-only extension."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

ADAPTER_ID = 'arclight-private-f8-q11-rabi16-v1'
POLICY = 'arclight-rabi-independent-blocks16/1'
MAX_REGISTERED_ARTIFACTS = 16384
COMMON_ARTIFACT_RESERVE = 32
BINDING_SCHEMA = 'qcal-attempt-execution-binding/1'
PROFILE = 'adapters/arclight_profile_rabi16_v1.json'
RUNNER = 'adapters/arclight_rabi16_runtime/execute_worker.py'
MANIFEST = 'adapters/arclight_rabi16_runtime/source_manifest.json'
BASE_ID = 'arclight-private-f8-q11-v3'
BASE_PROFILE = 'adapters/arclight_profile_v3.json'
BASE_PROFILE_SHA = 'dc3b91cdb75a1c21f622f142d61d56e63cb80336088725e8000916d6b808ecfa'
BASE_MANIFEST = 'adapters/arclight_analysis_v3/source_manifest.json'
BASE_MANIFEST_SHA = '96b3cf7cb560120f7a5f5babe01a637a99c680ac8b528a2ce26c43f3d2d83cfc'
BASE_FINGERPRINT = '856c796eb967131eaaa0d6659582774fdd5ebc73db663a53af1a5735328e7d7b'
ANALYSIS = 'adapters/arclight_analysis_v3/platform_analysis.py'
NEW_FILES = {PROFILE, RUNNER, 'adapters/arclight_rabi16_adapter.py',
             'adapters/arclight_rabi16_runtime/__init__.py',
             'adapters/arclight_rabi16_runtime/policy.py'}
BINDING_FIELDS = {'schema', 'stage', 'authorizationId', 'authorizationSha256',
                  'baseAdapterId', 'baseAdapterProfileHash', 'adapterId', 'adapterProfileHash',
                  'workflowId', 'nodeId', 'procedureId', 'resourceId', 'attemptId', 'attemptNumber',
                  'selectionOperationId', 'verificationOperationId', 'parametersSha256'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def intent_digest(value):
    """The unchanged ArcLight bridge uses default JSON separators for its intent."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode('utf-8')).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def fingerprint(root, profile):
    frozen = {k: v for k, v in profile.items() if k not in ('enabled', 'connectionConfirmedAt')}
    return digest({'profile': frozen, 'sourceManifestSha256': file_sha(Path(root) / profile['sourceManifest'])})


def verify_profile(root, profile):
    """Only this named version's enumerated sampling delta is permitted."""
    root = Path(root).resolve()
    if file_sha(root / BASE_PROFILE) != BASE_PROFILE_SHA or file_sha(root / BASE_MANIFEST) != BASE_MANIFEST_SHA:
        raise ValueError('Frozen base profile or source manifest changed')
    expected = read(root / BASE_PROFILE)
    expected.update(id=ADAPTER_ID, runner=RUNNER, runnerSha256=file_sha(root / RUNNER), sourceManifest=MANIFEST)
    expected['parameterBounds']['acquisitions_per_point'] = [1, 16]
    actual = {k: v for k, v in profile.items() if k != 'connectionConfirmedAt'}
    if type(actual.get('enabled')) is not bool:
        raise ValueError('Execution enable flag is invalid')
    actual = {**actual, 'enabled': False}
    if digest(actual) != digest(expected):
        raise ValueError('Rabi-only sampling profile differs from the audited delta')
    return deepcopy(profile)


def load_profile(root):
    profile = read(Path(root) / PROFILE)
    return verify_profile(root, profile)


def parameter_context_sha256(parameters):
    # Independent amplitude verification writes its normalized fitted drive/half
    # amplitude; the actual scan axis overrides both. Its physical window, axis,
    # readout and sampling remain frozen. Seed is the independently permuted order.
    if not isinstance(parameters, dict):
        raise ValueError('Sampling parameters must be an object')
    return digest({k: v for k, v in parameters.items() if k not in ('drive_amp', 'half_pi_amp', 'order_seed')})


def validate_binding(root, profile, request, *, executable=False):
    binding = request.get('executionBinding')
    if not isinstance(binding, dict) or set(binding) != BINDING_FIELDS:
        raise ValueError('Exact attempt execution binding is required')
    if binding['schema'] != BINDING_SCHEMA or binding['stage'] not in ('plan_only', 'acquisition'):
        raise ValueError('Sampling binding schema/stage differs')
    if executable and binding['stage'] != 'acquisition':
        raise ValueError('Plan-only sampling authorization cannot execute')
    for key in ('authorizationId', 'workflowId', 'nodeId', 'attemptId'):
        if not isinstance(binding[key], str) or not 1 <= len(binding[key]) <= 240:
            raise ValueError('Invalid sampling binding identity: ' + key)
    for key in ('authorizationSha256', 'baseAdapterProfileHash', 'adapterProfileHash', 'parametersSha256'):
        if not isinstance(binding[key], str) or re.fullmatch('[0-9a-f]{64}', binding[key]) is None:
            raise ValueError('Invalid sampling binding hash: ' + key)
    if type(binding['attemptNumber']) is not int or not 1 <= binding['attemptNumber'] <= 10:
        raise ValueError('Invalid next attempt number')
    if binding['baseAdapterId'] != BASE_ID or binding['baseAdapterProfileHash'] != BASE_FINGERPRINT:
        raise ValueError('Sampling extension must preserve its exact v3 base identity')
    if binding['adapterId'] != ADAPTER_ID or binding['adapterProfileHash'] != fingerprint(root, profile):
        raise ValueError('Sampling execution profile identity differs')
    for key, target in [('workflowId', 'runId'), ('nodeId', 'nodeId'), ('attemptId', 'attemptId'),
                        ('resourceId', 'resourceId'), ('procedureId', 'procedureId'),
                        ('adapterId', 'adapterId'), ('adapterProfileHash', 'adapterProfileHash')]:
        if binding[key] != request.get(target):
            raise ValueError('Sampling request scope differs: ' + key)
    if binding['procedureId'] != 'sq_rabi_amplitude' or binding['resourceId'] != 'Q11':
        raise ValueError('Sampling extension is restricted to fixed Q11 amplitude Rabi')
    attempt = binding['attemptId']
    if binding['selectionOperationId'] != attempt + '/measure' or binding['verificationOperationId'] != attempt + '/verify':
        raise ValueError('Sampling operation pair differs from the attempt')
    if binding['stage'] == 'acquisition' and request.get('operationId') not in (
            binding['selectionOperationId'], binding['verificationOperationId']):
        raise ValueError('Operation is outside the authorized independent pair')
    if binding['parametersSha256'] != parameter_context_sha256(request.get('parameters')):
        raise ValueError('Sampling/physical context differs from the authorized attempt')
    return deepcopy(binding)
