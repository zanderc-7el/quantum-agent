"""Separate smart-run admission; unchanged R31 physical sampling implementation."""
from copy import deepcopy
from pathlib import Path
import re
from adapters.arclight_rabi16_runtime.policy import (
    BASE_ID, BASE_PROFILE, BASE_PROFILE_SHA, BASE_FINGERPRINT,
    MAX_REGISTERED_ARTIFACTS, COMMON_ARTIFACT_RESERVE, POLICY,
    digest, intent_digest, file_sha, read, fingerprint, parameter_context_sha256)

ADAPTER_ID = 'arclight-private-f8-q11-smart-rabi16-v1'
EXECUTION_POLICY = 'q11-smart-rabi-independent-blocks16/1'
BINDING_SCHEMA = 'qcal-smart-attempt-execution-binding/1'
PROFILE = 'adapters/arclight_profile_smart_rabi16_v1.json'
RUNNER = 'adapters/arclight_smart_rabi16_runtime/execute_worker.py'
MANIFEST = 'adapters/arclight_smart_rabi16_runtime/source_manifest.json'
INHERITED_MANIFEST = 'adapters/arclight_rabi16_runtime/source_manifest.json'
INHERITED_SHA = 'ce2424fc1179c9953aed4660454dda47621d7bb87a2020da62d5c6851ce484a0'
NEW_FILES = {PROFILE, RUNNER, 'adapters/arclight_smart_rabi16_adapter.py',
             'adapters/arclight_smart_rabi16_runtime/policy.py',
             'adapters/arclight_smart_rabi16_runtime/__init__.py'}
BINDING_FIELDS = {'schema','stage','authorizationId','authorizationSha256','baseAdapterId','baseAdapterProfileHash',
    'adapterId','adapterProfileHash','workflowId','nodeId','procedureId','resourceId','attemptId','attemptNumber',
    'selectionOperationId','verificationOperationId','parametersSha256','smartRunSha256','sourceAttemptId','sourceEvidenceSha256'}


def verify_profile(root, profile):
    root = Path(root).resolve()
    if file_sha(root/BASE_PROFILE) != BASE_PROFILE_SHA or file_sha(root/INHERITED_MANIFEST) != INHERITED_SHA:
        raise ValueError('Frozen inherited sampling identity changed')
    expected = read(root/BASE_PROFILE)
    expected.update(id=ADAPTER_ID,runner=RUNNER,runnerSha256=file_sha(root/RUNNER),sourceManifest=MANIFEST)
    expected['parameterBounds']['acquisitions_per_point'] = [1,16]
    actual = {k:v for k,v in profile.items() if k != 'connectionConfirmedAt'}
    if type(actual.get('enabled')) is not bool or digest({**actual,'enabled':False}) != digest(expected):
        raise ValueError('Smart sampling profile differs from the exact audited delta')
    return deepcopy(profile)


def load_profile(root): return verify_profile(root,read(Path(root)/PROFILE))


def validate_binding(root, profile, request, *, executable=False):
    b = request.get('executionBinding')
    if not isinstance(b,dict) or set(b) != BINDING_FIELDS:
        raise ValueError('Exact smart attempt binding is required')
    if b['schema'] != BINDING_SCHEMA or b['stage'] not in ('plan_only','acquisition'):
        raise ValueError('Smart sampling binding schema/stage differs')
    if executable and b['stage'] != 'acquisition': raise ValueError('Plan-only authorization cannot execute')
    for k in ('authorizationId','workflowId','nodeId','attemptId','sourceAttemptId'):
        if not isinstance(b[k],str) or not 1 <= len(b[k]) <= 240: raise ValueError('Invalid binding identity: '+k)
    for k in ('authorizationSha256','baseAdapterProfileHash','adapterProfileHash','parametersSha256','smartRunSha256','sourceEvidenceSha256'):
        if not isinstance(b[k],str) or re.fullmatch('[0-9a-f]{64}',b[k]) is None: raise ValueError('Invalid binding hash: '+k)
    # No policy attempt ceiling: this is the actual positive ordinal stored by
    # the controller ledger, not a clamped/relabelled R31 attempt number.
    if type(b['attemptNumber']) is not int or b['attemptNumber'] < 1: raise ValueError('Invalid actual attempt ordinal')
    if b['baseAdapterId'] != BASE_ID or b['baseAdapterProfileHash'] != BASE_FINGERPRINT:
        raise ValueError('Smart sampling must retain its exact v3 parent identity')
    if b['adapterId'] != ADAPTER_ID or b['adapterProfileHash'] != fingerprint(root,profile):
        raise ValueError('Smart execution profile identity differs')
    for k,target in [('workflowId','runId'),('nodeId','nodeId'),('attemptId','attemptId'),
        ('resourceId','resourceId'),('procedureId','procedureId'),('adapterId','adapterId'),('adapterProfileHash','adapterProfileHash')]:
        if b[k] != request.get(target): raise ValueError('Smart request scope differs: '+k)
    if b['procedureId'] != 'sq_rabi_amplitude' or b['resourceId'] != 'Q11':
        raise ValueError('Smart extension only supports fixed Q11 amplitude Rabi')
    a = b['attemptId']
    if b['sourceAttemptId'] == a: raise ValueError('Sampling evidence must precede the current attempt')
    if b['selectionOperationId'] != a+'/measure' or b['verificationOperationId'] != a+'/verify':
        raise ValueError('Independent operation pair differs')
    if b['stage'] == 'acquisition' and request.get('operationId') not in (b['selectionOperationId'],b['verificationOperationId']):
        raise ValueError('Operation is outside the authorized pair')
    if b['parametersSha256'] != parameter_context_sha256(request.get('parameters')):
        raise ValueError('Physical/sampling context differs from authorization')
    return deepcopy(b)
