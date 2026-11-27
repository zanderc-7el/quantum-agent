"""Read-only authentication for an explicit, unvalidated readout restoration.

No hardware, model, parameter writes, or scientific acceptance occurs here.
Only an IQ pair explicitly referenced by the original optimizer is eligible.
"""
from copy import deepcopy
import hashlib
import json

from .common import APIError, checked_path, digest, file_hash, number
from .prior_failure_context import (CHECKED_JSON_NAMES, load_audited_bindings,
                                    _validate_effect)
from .quality import evaluate

VERSION = 'readout-withdrawal/1'
READOUT_KEYS = ('readout_hz', 'readout_amp')


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _document(path):
    _need(path.stat().st_size <= 2 * 1024**2, 'receipt_size_limit')
    raw = path.read_bytes()
    _need(len(raw) <= 2 * 1024**2, 'receipt_size_limit')
    return json.loads(raw.decode('utf-8-sig')), hashlib.sha256(raw).hexdigest()


def _files(db, root, effect):
    operation = effect['operation_id']
    directory = checked_path(root / 'data/runs/arclight_operations' / hashlib.sha256(operation.encode()).hexdigest(), parent=root)
    result = effect['result']
    inventory = result.get('artifacts')
    _need(isinstance(inventory, list) and all(isinstance(a, dict) for a in inventory), 'artifact_inventory')
    needed = [a for a in inventory if a.get('kind') == 'raw' or a.get('name') in CHECKED_JSON_NAMES]
    _need(1 <= len(needed) <= 64, 'artifact_count_limit')
    files, remaining = [], 64 * 1024**2
    for artifact in needed:
        record = db.execute('SELECT * FROM platform_artifacts WHERE id=?', (artifact['id'],)).fetchone()
        _need(record is not None and record['sha256'] == artifact['sha256']
              and artifact['id'] == 'real-' + artifact['sha256'][:24], 'artifact_not_registered')
        registered = json.loads(record['payload'])
        _need(registered.get('kind') == artifact['kind'], 'artifact_kind_mismatch')
        path = checked_path(artifact['sourcePath'], parent=directory)
        before_stat = path.stat()
        size = before_stat.st_size
        _need(path.is_file() and path.name == artifact['name'] and size <= remaining, 'artifact_read_limit')
        remaining -= size
        document, actual = (None, file_hash(path)) if artifact['kind'] == 'raw' else _document(path)
        after_stat = path.stat()
        _need((before_stat.st_size, before_stat.st_mtime_ns) == (after_stat.st_size, after_stat.st_mtime_ns), 'artifact_changed_during_read')
        _need(actual == artifact['sha256'], 'artifact_hash_mismatch')
        entry = {'artifactRef': artifact['id'], 'sha256': actual, 'name': path.name,
                 'kind': artifact['kind'], 'folder': path.parent.relative_to(directory).as_posix(),
                 'registryHashVerified': True}
        if document is not None:
            entry['document'] = document
        files.append(entry)
    receipt_path = checked_path(result.get('provenance', {}).get('cleanupReceipt', ''), parent=directory)
    _need(receipt_path == directory / 'completion.json', 'completion_path')
    receipt, receipt_sha = _document(receipt_path)
    _need(receipt.get('state') == 'completed' and receipt.get('shutdown_ok') is True, 'incomplete_shutdown')
    listed = {checked_path(p, parent=directory) for p in receipt.get('files', [])}
    _need(all(checked_path(a['sourcePath'], parent=directory) in listed
              for a in needed if a['name'] != 'plan.json'), 'completion_inventory')
    for entry in files:
        if entry['name'] == 'status.json':
            status = entry['document']
            _need(status.get('shutdown_ok') is True and status.get('hardware_connected') is False
                  and status.get('driver_close_result', {}).get('ok') is True, 'shutdown_not_confirmed')
    return files, {'operationId': operation, 'receiptPath': str(receipt_path), 'receiptSha256': receipt_sha,
                   'rawSha256': [f['sha256'] for f in files if f['kind'] == 'raw'],
                   'analysisSha256': [f['sha256'] for f in files if f['name'] == 'platform_analysis.json'],
                   'acquiredAt': result.get('acquiredAt')}


def _attempt(db, wf, attempt_id, procedure, node_id, binding, root):
    row = db.execute('SELECT payload FROM platform_attempts WHERE id=? AND workflow_id=? AND node_id=?',
                     (attempt_id, wf['id'], node_id)).fetchone()
    _need(row is not None, 'attempt_missing')
    attempt = json.loads(row[0]); result = attempt.get('result', {})
    _need(attempt.get('id') == attempt_id and attempt.get('nodeId') == node_id
          and attempt.get('procedureId') == procedure and attempt.get('mode') == 'real'
          and attempt.get('state') == 'verified' and attempt.get('endedAt')
          and not attempt.get('error') and result.get('quality', {}).get('status') == 'passed'
          and result['quality'].get('criteria') == wf['gates'][procedure]
          and result['quality'].get('ruleVersion') == wf['gatesVersion'], 'attempt_not_independently_accepted')
    operations = [attempt_id + '/measure', attempt_id + '/verify']
    _need(attempt.get('operationId') == operations[0] and attempt.get('verificationOperationId') == operations[1], 'operation_identity')
    stored = list(db.execute('SELECT * FROM platform_effects WHERE attempt_id=?', (attempt_id,)))
    _need(len(stored) == 2, 'unexpected_effects')
    effects, receipts, raw_sets = [], [], []
    for operation, expected in zip(operations, (result.get('selectionResult'), result)):
        _need(isinstance(expected, dict) and not any(expected.get(k) for k in ('partial', 'stale', 'verificationPending')), 'incomplete_attempt')
        effect = dict(next((r for r in stored if r['operation_id'] == operation), {}))
        _need(effect.get('state') == 'completed' and effect.get('result'), 'incomplete_effect')
        effect['request'], effect['result'] = json.loads(effect['request']), json.loads(effect['result'])
        files, receipt = _files(db, root, effect)
        raw_sets.append(_validate_effect(effect, files, wf, attempt, binding, operation, expected))
        _need(expected.get('analysisQuality', {}).get('accepted') is True, 'native_not_accepted')
        _need(evaluate(expected, procedure, 'real', wf['gates'], independent=True)['quality']['status'] == 'passed', 'historical_gate_not_passed')
        effects.append(effect); receipts.append(receipt)
    _need(raw_sets[0] and raw_sets[1] and raw_sets[0].isdisjoint(raw_sets[1]), 'not_independent')
    return attempt, effects, receipts


def restoration_source(db, adapter, wf, marker, current_parameters, current_versions):
    """Return authenticated baseline values; caller must already enforce checkpoint/revision."""
    try:
        implementation = type(adapter).__module__ + '.' + type(adapter).__qualname__
        root = checked_path(adapter.root)
        bindings = load_audited_bindings(root, implementation)
        binding = bindings.get(wf.get('adapterId'))
        _need(binding and wf.get('adapterProfileHash') == binding['fingerprint'], 'unaudited_adapter')
        _need(marker.get('origin') != 'operator_withdrawal', 'candidate_already_withdrawn')
        resource = marker['resourceId']
        optimizer = next(n for n in wf['nodes'] if n['id'] == marker['optimizerNodeId'])
        _need(optimizer['resourceId'] == resource and optimizer['procedureId'] == 'ro_optimize', 'optimizer_binding')
        source, effects, receipts = _attempt(db, wf, marker['sourceAttemptId'], 'ro_optimize', optimizer['id'], binding, root)
        snapshot = effects[0]['request'].get('validationSnapshot') or {}
        keys = tuple(adapter.validation_parameter_keys())
        baseline, versions = snapshot.get('parameters', {}), snapshot.get('parameterVersions', {})
        _need(snapshot.get('version') == 1 and snapshot.get('runId') == wf['id']
              and snapshot.get('resourceId') == resource and set(keys) <= baseline.keys()
              and all(type(versions.get(k)) is int and versions[k] >= 0 for k in keys), 'baseline_snapshot')
        _need(all(baseline[k] == source['result']['selectionResult']['parameters'][k]
                  and versions[k] == source['parameterVersions'][k] for k in keys), 'baseline_source_mismatch')
        # Unlike drive duration/amplitude, these native readout candidates have
        # no controller normalization. Bind aggregate state to the registered
        # measurement analysis, then to the fixed independent acquisition.
        selected = effects[0]['result'].get('candidateParameters')
        _need(isinstance(selected, dict) and 'readout_amp' in selected
              and set(selected) <= set(READOUT_KEYS)
              and source['result'].get('candidateParameters') == selected
              and effects[1]['result'].get('candidateParameters') == selected, 'candidate_analysis_mismatch')
        for key in READOUT_KEYS:
            candidate = selected.get(key, baseline[key])
            bounds = binding['profile']['parameterBounds'][key]
            _need(number(baseline[key]) and number(candidate) and bounds[0] <= baseline[key] <= bounds[1]
                  and effects[1]['result']['parameters'].get(key) == candidate, 'candidate_fixed_validation_mismatch')
            _need(marker['values'][key] == candidate and current_parameters[key] == candidate
                  and marker['parameterVersions'][key] == current_versions[key]
                  and current_versions[key] == versions[key] + int(candidate != baseline[key]), 'marker_version_mismatch')
        _need(effects[1]['result']['parameters'].get('amplitudes') == [selected['readout_amp']], 'candidate_not_fixed')
        _need(any(baseline[k] != marker['values'][k] for k in READOUT_KEYS), 'no_readout_change')
        _need(all(current_parameters[k] == baseline[k] and current_versions[k] == versions[k]
                  for k in keys if k not in READOUT_KEYS), 'other_physical_context_changed')
        candidates = [e for e in snapshot.get('acceptedEvidence', []) if e.get('procedureId') == 'ro_iq01']
        _need(len(candidates) == 1 and candidates[0].get('status') == 'passed'
              and candidates[0].get('ruleVersion') == wf['gatesVersion'], 'iq_snapshot_missing')
        entry = candidates[0]; iq_id = entry['selectionOperationId'].removesuffix('/measure')
        _need(entry['selectionOperationId'] == iq_id + '/measure' and entry['verificationOperationId'] == iq_id + '/verify', 'iq_identity')
        iq_nodes = [n for n in wf['nodes'] if n['resourceId'] == resource and n['procedureId'] == 'ro_iq01']
        _need(len(iq_nodes) == 1 and iq_nodes[0]['id'] in optimizer['dependsOn'], 'iq_dependency')
        iq, iq_effects, iq_receipts = _attempt(db, wf, iq_id, 'ro_iq01', iq_nodes[0]['id'], binding, root)
        _need(iq['endedAt'] <= source['startedAt'], 'iq_not_prior')
        _need(all(iq['parameterVersions'][k] == versions[k]
                  and all(e['result']['parameters'][k] == baseline[k] for e in iq_effects) for k in keys), 'iq_baseline_mismatch')
        all_receipts = receipts + iq_receipts
        hashes = [h for r in all_receipts for h in r['rawSha256']]
        _need(len(hashes) == len(set(hashes)), 'reused_raw')
        return {'values': {k: baseline[k] for k in READOUT_KEYS},
                'parameterVersions': {k: versions[k] for k in READOUT_KEYS},
                'sourceEvidence': {'optimizerAttemptId': source['id'], 'baselineIqAttemptId': iq['id'],
                    'baselineSnapshotSha256': digest(snapshot), 'operations': all_receipts,
                    'meaning': 'Historical baseline values only; no calibration acceptance is restored.'}}
    except (ValueError, KeyError, TypeError, StopIteration, OSError, APIError) as exc:
        raise APIError('READOUT_WITHDRAWAL_SOURCE_INVALID', '无法核实可撤回的原读出工作点：' + str(exc), 409) from None
