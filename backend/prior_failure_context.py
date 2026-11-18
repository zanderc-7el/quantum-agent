"""Pure projections of authenticated prior failures; never grants execution.

The controller supplies DB-bound receipts and files it has independently checked
against the artifact registry and their on-disk SHA256. This module does no I/O.
Historical references deliberately contain no current-evidence IDs. Missing,
partial, exceptional or unaudited records fail closed and remain in the archive.
"""
from copy import deepcopy
import hashlib
import json
import math

from .common import digest, dumps, number
from .decision_context import build_decision_context

IMPLEMENTATION = 'adapters.arclight.ArcLightAdapter'
MANIFEST_SHA = '1531ee2cd78774e6c6a5e089be54c3f35a98d8c0fdf03aa0547169106527a322'
PROFILE_FILES = {
    'arclight-private-f8-q11-v1': ('adapters/arclight_profile.json', '33094da60d34cb36355af6054fb412185b411980ebb3a802ef2177acfd6c7114'),
    'arclight-private-f8-q11-v2': ('adapters/arclight_profile_v2.json', '2a77d880ac6cfa5fdc93115653faffbd8e6a7d58311a7f6da1e34f6deb033f14'),
}
MAX_CASES, MAX_CANDIDATES, MAX_CONTEXT_BYTES = 6, 64, 32768
ENTRY_FILES = {'run_single_qubit.py', 'bootstrap.py', 'cavity_runtime.py', 'native_single_qubit_specs.py',
               'native_allxy_spec.py', 'paired_rb_support.py', 'repeated_iq_acquisition.py', 'validated_short_half_compat.py'}
CHECKED_JSON_NAMES = {'plan.json', 'status.json', 'job.json', 'platform_analysis.json',
                      'source_hashes.json', 'entry_source_hashes.json'}


def audited_bindings(implementation, profile_bytes, manifest_bytes):
    """Only the exact audited v1/v2 pair is eligible, including frozen sources."""
    if implementation != IMPLEMENTATION or hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA:
        return {}
    profiles = {}
    for identity, (_, expected) in PROFILE_FILES.items():
        raw = profile_bytes.get(identity, b'')
        if hashlib.sha256(raw).hexdigest() != expected:
            return {}
        profile = json.loads(raw.decode('utf-8-sig'))
        if profile.get('id') != identity:
            return {}
        profiles[identity] = profile
    old, new = (deepcopy(profiles[k]) for k in PROFILE_FILES)
    old['id'] = new['id']
    old['parameterBounds']['acquisitions_per_point'][1] = 8
    if old != new:
        return {}
    manifest = {item['path'].removeprefix('adapters/arclight_runtime/').replace('\\', '/'): item['sha256']
                for item in json.loads(manifest_bytes)}
    output = {}
    for identity, profile in profiles.items():
        frozen = {k: v for k, v in profile.items() if k not in ('enabled', 'connectionConfirmedAt')}
        fingerprint = digest({'profile': frozen, 'sourceManifestSha256': MANIFEST_SHA})
        execution_hash = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        output[identity] = {'fingerprint': fingerprint, 'profile': profile, 'manifest': manifest,
                            'executionProfileHashes': [execution_hash({**profile, 'enabled': flag}) for flag in (False, True)]}
    return output


def _controller_gate_failure(workflow, attempt):
    """Confirm a numeric gate failure against that run's unchanged frozen gates.

    Missing independent verification alone is not a scientific gate failure.
    Receipt/source authentication still runs in _validate_effect afterwards.
    """
    result = attempt.get('result') or {}
    quality, metrics = result.get('quality') or {}, result.get('metrics') or {}
    gates = workflow.get('gates') or {}
    criteria = gates.get(attempt.get('procedureId')) if isinstance(gates, dict) else None
    if (not isinstance(quality, dict) or not isinstance(metrics, dict) or not isinstance(criteria, dict) or not criteria
            or quality.get('status') != 'failed' or quality.get('criteria') != criteria
            or not isinstance(workflow.get('gatesVersion'), str)
            or quality.get('ruleVersion') != workflow['gatesVersion']
            or not isinstance(quality.get('reasons'), list) or not quality['reasons']
            or not all(isinstance(reason, str) for reason in quality['reasons'])):
        return None
    failures = []
    for name, rule in criteria.items():
        if (not isinstance(name, str) or not isinstance(rule, dict) or not rule
                or any(key not in ('min', 'max') or not number(value) for key, value in rule.items())):
            return None
        value = metrics.get(name)
        if number(value) and (('min' in rule and value < rule['min']) or ('max' in rule and value > rule['max'])):
            failures.append({'metric': name, 'value': value, 'criteria': deepcopy(rule)})
    return {'ruleVersion': quality['ruleVersion'], 'criteria': deepcopy(criteria),
            'failedCriteria': failures} if failures else None


def eligible_header(workflow, attempt, current_workflow, current_node, bindings):
    """Cheap filter before hashing raw files. No conclusion is made here."""
    if not all(isinstance(value, dict) for value in (workflow, attempt, current_workflow, current_node, bindings)):
        return False
    binding = bindings.get(workflow.get('adapterId'))
    current = bindings.get(current_workflow.get('adapterId'))
    result = attempt.get('result') or {}
    if (not isinstance(result, dict) or not isinstance(result.get('quality', {}), dict)
            or not isinstance(result.get('analysisQuality', {}), dict) or not isinstance(workflow.get('nodes'), list)):
        return False
    nodes = [n for n in workflow['nodes'] if isinstance(n, dict) and n.get('id') == attempt.get('nodeId')]
    return bool(binding and current
        and workflow.get('adapterProfileHash') == binding['fingerprint']
        and current_workflow.get('adapterProfileHash') == current['fingerprint']
        and workflow.get('mode') == attempt.get('mode') == 'real'
        and attempt.get('state') == 'attention' and attempt.get('endedAt')
        and not attempt.get('error') and attempt.get('id') != current_node.get('latestAttemptId')
        and len(nodes) == 1 and nodes[0].get('resourceId') == current_node.get('resourceId')
        and nodes[0].get('procedureId') == attempt.get('procedureId') == current_node.get('procedureId')
        and result.get('dataKind') == 'real' and result.get('quality', {}).get('status') == 'failed'
        and (result.get('analysisQuality', {}).get('accepted') is False
             or (result.get('analysisQuality', {}).get('accepted') is True and _controller_gate_failure(workflow, attempt) is not None))
        and not any(result.get(k) for k in ('partial', 'stale', 'verificationPending')))


def _one(files, name, folder=None):
    found = [f for f in files if f['name'] == name and (folder is None or f['folder'] == folder)]
    if len(found) != 1:
        raise ValueError('missing_or_ambiguous_registered_file')
    return found[0]['document']


def _validate_effect(effect, files, workflow, attempt, binding, operation, expected_result):
    if effect.get('state') != 'completed' or effect.get('workflow_id') != workflow['id'] or effect.get('attempt_id') != attempt['id']:
        raise ValueError('incomplete_effect')
    request, result = effect['request'], effect['result']
    identities = {'runId': workflow['id'], 'nodeId': attempt['nodeId'], 'attemptId': attempt['id'],
                  'operationId': operation, 'resourceId': next(n['resourceId'] for n in workflow['nodes'] if n['id'] == attempt['nodeId']),
                  'procedureId': attempt['procedureId'], 'adapterId': workflow['adapterId'], 'adapterProfileHash': binding['fingerprint']}
    if any(request.get(k) != v for k, v in identities.items()) or effect.get('operation_id') != operation:
        raise ValueError('effect_binding_mismatch')
    if any(result.get(k) for k in ('partial', 'stale', 'verificationPending')) or result.get('dataKind') != 'real' or not result.get('acquiredAt'):
        raise ValueError('not_complete_real_result')
    for key in ('parameters', 'metrics', 'series', 'analysisQuality', 'provenance', 'artifacts', 'acquiredAt', 'sourceRunId'):
        if result.get(key) != expected_result.get(key):
            raise ValueError('attempt_effect_mismatch')
    provenance = result.get('provenance') or {}
    physical_keys = set(binding['profile']['resources'][identities['resourceId']]) - {'q', 'calibrationStatus'}
    versions = request.get('parameterVersions') or {}
    if (not isinstance(versions, dict) or versions != attempt.get('parameterVersions')
            or any(type(versions.get(k)) is not int or versions[k] < 0 or k not in result.get('parameters', {}) for k in physical_keys)):
        raise ValueError('physical_context_incomplete')
    if (provenance.get('freshAcquisition') is not True or provenance.get('shutdownCommandsCompleted') is not True
            or provenance.get('operationId') != operation or result.get('sourceRunId') != operation
            or provenance.get('runnerSha256') != binding['profile']['runnerSha256']
            or provenance.get('profileHash') not in binding['executionProfileHashes']
            or provenance.get('sourceManifest') != binding['profile']['sourceManifest']
            or provenance.get('parameterVersions') != request.get('parameterVersions')):
        raise ValueError('provenance_mismatch')
    if any(a.get('name') == 'analysis_error.json' for a in result.get('artifacts', [])):
        raise ValueError('analysis_exception')
    artifacts = result.get('artifacts') or []
    needed = [a for a in artifacts if a.get('kind') == 'raw' or a.get('name') in CHECKED_JSON_NAMES]
    if len(files) != len(needed) or any(not f.get('registryHashVerified') for f in files):
        raise ValueError('unauthenticated_files')
    expected = {(a['id'], a['sha256'], a['name'], a['kind']) for a in needed}
    actual = {(f['artifactRef'], f['sha256'], f['name'], f['kind']) for f in files}
    if len(actual) != len(files) or actual != expected:
        raise ValueError('artifact_inventory_mismatch')
    plan = _one(files, 'plan.json')
    if (plan.get('operationId') != operation or plan.get('resourceId') != identities['resourceId']
            or plan.get('procedureId') != attempt['procedureId'] or plan.get('parameters') != result.get('parameters')
            or any(plan.get('requestIdentity', {}).get(k) != request.get(k)
                   for k in ('runId', 'nodeId', 'attemptId', 'operationId', 'resourceId', 'procedureId', 'parameterVersions'))):
        raise ValueError('plan_binding_mismatch')
    raw = [f for f in files if f['kind'] == 'raw']
    jobs, counts = plan.get('jobs', []), plan.get('expectedJobRows', [])
    if not raw or len(raw) != len(jobs) or len(counts) != len(jobs) or len({f['folder'] for f in raw}) != len(raw):
        raise ValueError('incomplete_job_inventory')
    core = {k: v for k, v in binding['manifest'].items() if k.startswith('work/server/core/') and k.endswith('.py')}
    entry = {k: binding['manifest'][k] for k in ENTRY_FILES}
    for raw_file, job, count in zip(sorted(raw, key=lambda f: f['folder']), jobs, counts):
        folder = raw_file['folder']
        status = _one(files, 'status.json', folder)
        if (status.get('state') != 'acquired' or status.get('mode') != 'real_hardware'
                or status.get('job') != job['name'] or status.get('points_completed') != count
                or status.get('points_expected') != count or _one(files, 'job.json', folder) != job):
            raise ValueError('incomplete_native_job')
        if {k.replace('\\', '/'): v for k, v in _one(files, 'source_hashes.json', folder).items()} != core:
            raise ValueError('native_source_mismatch')
        if _one(files, 'entry_source_hashes.json', folder) != entry:
            raise ValueError('entry_source_mismatch')
    # This first version supports platform_analysis receipts. RB's different
    # paired-report schema remains excluded until separately audited.
    analysis = _one(files, 'platform_analysis.json')
    if (analysis.get('analysis') != result.get('analysisQuality') or analysis.get('metrics') != result.get('metrics')
            or analysis.get('series') != result.get('series') or analysis.get('candidateParameters') != result.get('candidateParameters')):
        raise ValueError('analysis_receipt_mismatch')
    native = result.get('analysisQuality') or {}
    if (native.get('procedureId') != attempt['procedureId']
            or native.get('source_sha256') != binding['manifest']['platform_analysis.py']
            or native.get('raw_sha256') not in {f['sha256'] for f in raw}):
        raise ValueError('analysis_source_mismatch')
    return {f['sha256'] for f in raw}


def project_failure_case(record, current_workflow, current_node, bindings):
    """Return an explanatory case or None. Caller must authenticate every file."""
    try:
        workflow, attempt = record['workflow'], record['attempt']
        if not eligible_header(workflow, attempt, current_workflow, current_node, bindings):
            return None
        binding, result = bindings[workflow['adapterId']], attempt['result']
        operations = [attempt['operationId']]
        if attempt.get('verificationOperationId'):
            operations.append(attempt['verificationOperationId'])
        if len(set(operations)) != len(operations) or len(record['effects']) != len(operations):
            return None
        expected = [result['selectionResult'], result] if len(operations) == 2 else [result]
        if len(operations) == 1 and result.get('selectionResult'):
            return None
        raw_sets = []
        for operation, original in zip(operations, expected):
            effect = next(e for e in record['effects'] if e['operation_id'] == operation)
            raw_sets.append(_validate_effect(effect, record['files'][operation], workflow, attempt, binding, operation, original))
        if len(raw_sets) == 2 and raw_sets[0] & raw_sets[1]:
            return None
        native = result['analysisQuality']
        native_failed = native.get('accepted') is False
        controller_failure = _controller_gate_failure(workflow, attempt) if native.get('accepted') is True else None
        if (not isinstance(native.get('reasons'), list) or (native_failed and not native['reasons'])
                or not all(isinstance(r, str) for r in native['reasons'])
                or not native_failed and controller_failure is None):
            return None
        context = {'caseRef': 'prior-failure-' + digest([workflow['id'], attempt['id']])[:24],
            'sourceRun': workflow['id'], 'sourceAttempt': attempt['id'], 'sourceOperations': operations,
            'resource': current_node['resourceId'], 'procedure': attempt['procedureId'],
            'finishedAt': attempt['endedAt'], 'failedStage': 'independent_verification' if len(operations) == 2 else 'selection',
            'sourceAdapter': workflow['adapterId'], 'sourceProfileFingerprint': workflow['adapterProfileHash'],
            'sourceManifestSha256': MANIFEST_SHA, 'crossProfileCompatibility': 'audited_v1_v2_only_id_and_acquisition_cap_differ',
            'parameters': deepcopy(result['parameters']), 'parameterVersions': deepcopy(attempt.get('parameterVersions', {})),
            'failureClass': 'native_analysis_failed' if native_failed else 'controller_gate_failed',
            'nativeAccepted': native.get('accepted'),
            'nativeFailureReasons': [reason[:512] for reason in native['reasons'][:8]],
            'controllerFailureReasons': [reason[:512] for reason in result.get('quality', {}).get('reasons', [])[:8]
                                        if isinstance(reason, str)],
            'metrics': {k: v for k, v in list(result.get('metrics', {}).items())[:24] if number(v)},
            'registeredRawSha256': [sorted(items) for items in raw_sets],
            'attemptReceiptSha256': digest(attempt), 'nativeAnalysisSha256': digest(native),
            'meaning': 'Historical failed acquisition; reference only, not current calibration, acceptance evidence or proof that a setting can never work.'}
        if controller_failure is not None:
            context['controllerGateFailure'] = controller_failure
        if attempt['procedureId'] == 'ro_power_shift':
            explanation = build_decision_context({'observations': {'result': result}}, procedure_id='ro_power_shift')['observations']['powerSeriesDiagnostics']
            if explanation['status'] == 'available':
                context['powerRepeatDiagnostics'] = [{k: g[k] for k in ('powerWaveformScale', 'captureCount', 'spreadHz', 'frequencyStepHz', 'nativeRepeatThresholdHz', 'exceedsNativeRepeatThreshold')}
                    for g in explanation['groups'][:12]]
        # Parameters are generated by the authenticated native plan, but still
        # enforce the prompt budget and never transport a huge explicit axis.
        if len(dumps(context).encode('utf-8')) > 12000:
            return None
        return context
    except (KeyError, TypeError, ValueError, StopIteration, AttributeError):
        return None


def bounded_context(cases, workflow, *, examined=0, excluded=0, status='available'):
    output = {'schema': 'qcal-prior-failures/1', 'status': status,
        'usage': 'Reference only. Do not reuse calibration or relax gates. Historical case references are never valid evidenceIds for this decision.',
        'limits': {'maximumCases': MAX_CASES, 'maximumCandidates': MAX_CANDIDATES, 'maximumBytes': MAX_CONTEXT_BYTES},
        'examinedCandidates': min(examined, MAX_CANDIDATES), 'excludedCandidates': excluded, 'cases': []}
    budget = workflow.get('lastBudgetUpdate') or {}
    budget = budget if isinstance(budget, dict) else {}
    if budget.get('source') == 'operator' and isinstance(budget.get('reason'), str):
        output['operatorContext'] = {'kind': 'operator_budget_explanation_not_measurement',
            'reason': budget['reason'][:2000], 'updatedAt': budget.get('updatedAt'),
            'usage': 'Operator rationale only; neither a measurement nor permission to alter gates or exceed current limits.'}
    for case in cases[:MAX_CASES]:
        candidate = {**output, 'cases': output['cases'] + [case]}
        if len(dumps(candidate).encode('utf-8')) <= MAX_CONTEXT_BYTES:
            output = candidate
    return output


def javascript_json_units_upper_bound(value, depth=0):
    """Conservative length of JSON.stringify(JSON.parse(wire)) in UTF-16.

    Python's shortest float representation is NOT a length bound for JavaScript:
    1e-5 has five Python characters but becomes 0.00001 in JS. Finite binary64
    needs at most 17 significant decimal digits. JS fixed notation at its lower
    1e-6 boundary therefore uses at most 1 sign + 2 (0.) + 5 zeroes + 17 digits
    = 25 characters; exponential notation needs at most 24. Non-integer numbers
    use this safe bound instead of attempting to reproduce JS rounding.

    Strings count JSON escaping and UTF-16 units, including well-formed
    stringify's escaping of lone surrogates. Unsupported/deep/nonfinite values
    return infinity so this optional context is omitted, never repaired.
    """
    if depth > 20:
        return math.inf
    if value is None:
        return 4
    if isinstance(value, bool):
        return 4 if value else 5
    if isinstance(value, str):
        units = 2
        for char in value:
            point = ord(char)
            if char in ('"', '\\') or point in (8, 9, 10, 12, 13):
                units += 2
            elif point < 32 or 0xD800 <= point <= 0xDFFF:
                units += 6
            else:
                units += 2 if point > 0xFFFF else 1
        return units
    if type(value) is int:
        # Avoid float conversion of arbitrary Python integers. JSON.parse maps
        # integers outside binary64 to a Number (or Infinity -> null: 4 units).
        return len(str(value)) if abs(value) <= 2**53 - 1 else 25
    if type(value) is float:
        if not math.isfinite(value):
            return math.inf
        if value.is_integer() and abs(value) <= 2**53 - 1:
            return len(str(int(value)))  # Includes -0.0 -> "0".
        return 25
    if isinstance(value, (list, tuple)):
        return 2 + max(0, len(value) - 1) + sum(javascript_json_units_upper_bound(item, depth + 1) for item in value)
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return 2 + max(0, len(value) - 1) + sum(javascript_json_units_upper_bound(key, depth + 1)
            + 1 + javascript_json_units_upper_bound(item, depth + 1) for key, item in value.items())
    return math.inf


def attach_prior_context(payload, context):
    """Only omit optional history; preserve every current observation unchanged."""
    output = deepcopy(payload)
    candidate = deepcopy(context)
    while True:
        output['priorFailureContext'] = candidate
        if javascript_json_units_upper_bound(output) <= 198000:
            return output
        if candidate.get('cases'):
            candidate['cases'].pop()
            candidate['omittedForInputBudget'] = True
        elif 'operatorContext' in candidate:
            del candidate['operatorContext']
        else:
            output.pop('priorFailureContext', None)
            return output  # Never enlarge an already-full current observation.
