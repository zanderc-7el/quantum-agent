"""Checkpoint restoration tests with synthetic files and temporary SQLite only."""
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from adapters.arclight import ArcLightAdapter
from backend.common import ROOT, APIError, dumps
from backend.controller import Controller
from backend.prior_failure_context import PROFILE_FILES, ENTRY_FILES, audited_bindings


class ReadoutWithdrawalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qcal-withdrawal-offline-')
        self.root = Path(self.temp.name)
        profiles = {k: (ROOT / name).read_bytes() for k, (name, _) in PROFILE_FILES.items()}
        manifest = (ROOT / 'adapters/arclight_runtime/source_manifest.json').read_bytes()
        for identity, (name, _) in PROFILE_FILES.items():
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(profiles[identity])
        (self.root / 'adapters/arclight_runtime').mkdir(parents=True)
        (self.root / 'adapters/arclight_runtime/source_manifest.json').write_bytes(manifest)
        imports = self.root / 'data/imports'; imports.mkdir(parents=True)
        (imports / 'history.json').write_text(dumps({'version': 1, 'resources': [{'id': 'Q11', 'label': 'Q11'}], 'records': []}))
        self.binding = audited_bindings('adapters.arclight.ArcLightAdapter', profiles, manifest)['arclight-private-f8-q11-v2']
        self.adapter = ArcLightAdapter(self.root, self.root / 'adapters/arclight_profile_v2.json')
        self.controller = Controller(self.root, adapter=self.adapter, start_worker=False)
        self.controller._brain = lambda *a, **kw: self.fail('No model allowed')
        self.adapter.execute = lambda *a, **kw: self.fail('No hardware allowed')
        self.adapter.hardware_ownership = lambda *a, **kw: self.fail('No hardware discovery allowed')
        self.wf = self.controller.create({'name': 'SYNTHETIC OFFLINE', 'mode': 'real', 'resourceIds': ['Q11'],
                                          'experiment': 'single', 'coverage': 'full', 'policy': 'autopilot'})
        self.opt = next(n for n in self.wf['nodes'] if n['procedureId'] == 'ro_optimize')
        self.iq = next(n for n in self.wf['nodes'] if n['procedureId'] == 'ro_iq01')
        self.params = {k: v for k, v in self.adapter.profile['resources']['Q11'].items() if k not in ('q', 'calibrationStatus')}
        self.params['drive_length_s'] = 330.5e-9
        self.versions = {k: int(k == 'drive_length_s') for k in self.params}
        self.iq_attempt = self._pair('OFFLINE-IQ', self.iq, '2026-11-18T00:01:00Z', '2026-11-18T00:02:00Z')
        self.snapshot = {'version': 1, 'runId': self.wf['id'], 'resourceId': 'Q11', 'parameters': self.params,
            'parameterVersions': self.versions, 'acceptedEvidence': [{'procedureId': 'ro_iq01', 'status': 'passed',
                'ruleVersion': self.wf['gatesVersion'], 'selectionOperationId': 'OFFLINE-IQ/measure', 'verificationOperationId': 'OFFLINE-IQ/verify'}]}
        self.opt_attempt = self._pair('OFFLINE-RO', self.opt, '2026-11-18T00:03:00Z', '2026-11-18T00:04:00Z', optimizer=True)
        with self.controller.store.transaction() as rt:
            wf = self.controller.get(self.wf['id'], rt)
            for n in wf['nodes']:
                n.update(engineState='verified', status='success', mainProcedureComplete=True, attempt=1,
                    result={'quality': {'status': 'passed'}, 'metrics': {'SYNTHETIC': 1}})
            for n, attempt in ((self.iq, self.iq_attempt), (self.opt, self.opt_attempt)):
                current = next(x for x in wf['nodes'] if x['id'] == n['id'])
                current.update(latestAttemptId=attempt['id'], result=deepcopy(attempt['result']))
            first = next(n for n in wf['nodes'] if n['procedureId'] == 'sq_rabi_amplitude'); first['attempt'] = 4
            prefix = wf['id'] + ':Q11:'
            rt.db.execute('INSERT INTO parameters VALUES(?,?,?)', (prefix + 'drive_length_s', dumps(330.5e-9), 1))
            rt.db.execute('INSERT INTO parameters VALUES(?,?,?)', (prefix + 'readout_amp', dumps(.63125), 1))
            wf.update(status='paused', engineState='paused', iterations=9, reservedShots=16120000)
            wf['readoutRevalidations'] = {'Q11': {'id': 'old-marker', 'state': 'active', 'ruleVersion': 'readout-revalidation/1',
                'resourceId': 'Q11', 'optimizerNodeId': self.opt['id'], 'sourceAttemptId': self.opt_attempt['id'],
                'rabiNodeId': first['id'], 'values': {'readout_hz': self.params['readout_hz'], 'readout_amp': .63125},
                'parameterVersions': {'readout_hz': 0, 'readout_amp': 1}}}
            self.controller.store.save(rt, wf, 'OFFLINE_FIXTURE')
        self.wf = self.controller.get(self.wf['id'])
        self.payload = {'operationId': 'OFFLINE-WITHDRAW', 'expectedRevision': self.wf['revision'],
            'revalidationId': 'old-marker', 'sourceAttemptId': self.opt_attempt['id'], 'reason': 'Offline boundary test'}

    def tearDown(self):
        self.controller.close(); self.temp.cleanup()

    def _pair(self, name, node, started, ended, optimizer=False):
        results = []
        attempt = {'id': name, 'nodeId': node['id'], 'number': 1, 'procedureId': node['procedureId'], 'mode': 'real',
            'state': 'verified', 'startedAt': started, 'endedAt': ended, 'parameters': deepcopy(self.params),
            'parameterVersions': deepcopy(self.versions), 'operationId': name + '/measure', 'verificationOperationId': name + '/verify'}
        for role in ('measure', 'verify'):
            operation = name + '/' + role; parameters = deepcopy(self.params)
            if optimizer and role == 'verify': parameters.update(readout_amp=.63125, amplitudes=[.63125])
            parameters.update(shots=2000, capture_repeats=2, acquisitions_per_point=1, points=9)
            request = {'runId': self.wf['id'], 'nodeId': node['id'], 'attemptId': name, 'operationId': operation,
                'resourceId': 'Q11', 'procedureId': node['procedureId'], 'adapterId': self.wf['adapterId'],
                'adapterProfileHash': self.wf['adapterProfileHash'], 'parameterVersions': deepcopy(self.versions), 'parameters': parameters}
            if optimizer: request['validationSnapshot'] = deepcopy(self.snapshot)
            directory = self.root / 'data/runs/arclight_operations' / hashlib.sha256(operation.encode()).hexdigest()
            folder = directory / 'results/offline'; folder.mkdir(parents=True)
            job = {'name': operation.replace('/', '_'), 'kind': 'readout', 'SYNTHETIC': True}
            raw = ('SYNTHETIC TEST BYTES; NOT HARDWARE DATA ' + operation).encode(); raw_sha = hashlib.sha256(raw).hexdigest()
            native = {'accepted': True, 'procedureId': node['procedureId'], 'reasons': [], 'raw_sha256': raw_sha,
                      'source_sha256': self.binding['manifest']['platform_analysis.py']}
            analysis = {'analysis': native, 'metrics': {'assignment_fidelity': .55}, 'series': [],
                        'candidateParameters': {'readout_amp': .63125} if optimizer else {}}
            documents = {'raw_scan.npz': raw, 'job.json': job,
                'status.json': {'state': 'acquired', 'mode': 'real_hardware', 'job': job['name'], 'points_expected': 4,
                    'points_completed': 4, 'shutdown_ok': True, 'hardware_connected': False, 'driver_close_result': {'ok': True}},
                'source_hashes.json': {k: v for k, v in self.binding['manifest'].items() if k.startswith('work/server/core/') and k.endswith('.py')},
                'entry_source_hashes.json': {k: self.binding['manifest'][k] for k in ENTRY_FILES}, 'platform_analysis.json': analysis,
                'plan.json': {'operationId': operation, 'resourceId': 'Q11', 'procedureId': node['procedureId'], 'parameters': parameters,
                    'requestIdentity': {k: request[k] for k in ('runId', 'nodeId', 'attemptId', 'operationId', 'resourceId', 'procedureId', 'parameterVersions')},
                    'jobs': [job], 'expectedJobRows': [4]}}
            artifacts = []
            with self.controller.store.transaction() as rt:
                for filename, doc in documents.items():
                    data = doc if isinstance(doc, bytes) else dumps(doc).encode(); sha = hashlib.sha256(data).hexdigest()
                    path = (directory if filename == 'plan.json' else folder) / filename; path.write_bytes(data)
                    artifact = {'id': 'real-' + sha[:24], 'name': filename, 'kind': 'raw' if filename == 'raw_scan.npz' else 'json',
                                'sha256': sha, 'sourcePath': str(path)}
                    rt.db.execute('INSERT OR REPLACE INTO platform_artifacts VALUES(?,?,?,?)', (artifact['id'], str(path), sha, dumps(artifact)))
                    artifacts.append(artifact)
                result = {'dataKind': 'real', 'sourceRunId': operation, 'acquiredAt': ended, 'parameters': parameters,
                    'analysisQuality': native, 'metrics': analysis['metrics'], 'series': [], 'candidateParameters': analysis['candidateParameters'],
                    'artifacts': artifacts, 'quality': {'status': 'passed' if role == 'verify' else 'unverified',
                        'criteria': self.wf['gates'][node['procedureId']], 'ruleVersion': self.wf['gatesVersion'], 'reasons': []},
                    'provenance': {'freshAcquisition': True, 'shutdownCommandsCompleted': True, 'operationId': operation,
                        'cleanupReceipt': str(directory / 'completion.json'), 'runnerSha256': self.binding['profile']['runnerSha256'],
                        'profileHash': self.binding['executionProfileHashes'][1], 'sourceManifest': self.binding['profile']['sourceManifest'],
                        'parameterVersions': deepcopy(self.versions)}}
                (directory / 'completion.json').write_text(dumps({'state': 'completed', 'shutdown_ok': True,
                    'files': [a['sourcePath'] for a in artifacts]}), encoding='utf8')
                rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,?)',
                    (operation, self.wf['id'], name, 'completed', dumps(request), dumps(result)))
            results.append(result)
        attempt['result'] = deepcopy(results[1]); attempt['result']['selectionResult'] = results[0]
        with self.controller.store.transaction() as rt: self.controller.store.attempt(rt, self.wf['id'], attempt)
        return attempt

    def _change_wf(self, change):
        with self.controller.store.transaction() as rt:
            wf = self.controller.get(self.wf['id'], rt); change(wf)
            rt.db.execute('UPDATE platform_workflows SET payload=? WHERE id=?', (dumps(wf), wf['id']))

    def _call(self, **changes):
        return self.controller.withdraw_readout_candidate(self.wf['id'], {**self.payload, **changes})

    def _unchanged_rejection(self, code, **changes):
        with self.controller.store.connect() as rt:
            before = list(map(tuple, rt.db.execute('SELECT * FROM parameters ORDER BY name')))
            attempts = list(map(tuple, rt.db.execute('SELECT * FROM platform_attempts ORDER BY id')))
        with self.assertRaises(APIError) as raised: self._call(**changes)
        self.assertEqual(raised.exception.code, code)
        with self.controller.store.connect() as rt:
            self.assertEqual(before, list(map(tuple, rt.db.execute('SELECT * FROM parameters ORDER BY name'))))
            self.assertEqual(attempts, list(map(tuple, rt.db.execute('SELECT * FROM platform_attempts ORDER BY id'))))
            self.assertIsNone(rt.db.execute('SELECT 1 FROM platform_commands WHERE id=?', (self.payload['operationId'],)).fetchone())

    def test_success_preserves_history_budgets_and_fixed_baseline(self):
        with self.controller.store.connect() as rt: attempts = list(map(tuple, rt.db.execute('SELECT * FROM platform_attempts ORDER BY id')))
        response = self._call(); record = response['lastReadoutWithdrawal']; marker = response['readoutRevalidations']['Q11']
        self.assertEqual(response['status'], 'paused'); self.assertEqual(response['engineState'], 'readout_withdrawal_pending')
        self.assertEqual(record['after']['values']['readout_amp'], .8)
        self.assertEqual(record['after']['parameterVersions'], {'readout_amp': 2, 'readout_hz': 1})
        self.assertEqual(record['validationStatus'], 'unverified'); self.assertFalse(response['workflowComplete'])
        self.assertEqual(response['reservedShots'], 16120000); self.assertEqual(response['iterations'], 9)
        self.assertEqual([n['attempt'] for n in response['nodes']], [n['attempt'] for n in self.wf['nodes']])
        self.assertEqual(len(record['invalidatedNodes']), 10)
        for n in response['nodes']:
            if n['id'] in record['invalidatedNodes']:
                self.assertEqual(n['engineState'], 'stale'); self.assertTrue(n['result']['stale'])
                self.assertEqual(n['result']['quality']['status'], 'passed')  # history preserved, not newly passed
                self.assertEqual(n['parameters']['readout_amp'], .8)
                self.assertEqual(n['parameterVersions']['readout_amp'], 2)
        self.assertEqual(marker['origin'], 'operator_withdrawal'); self.assertEqual(marker['values']['readout_amp'], .8)
        with self.controller.store.connect() as rt:
            opt = next(n for n in response['nodes'] if n['procedureId'] == 'ro_optimize')
            p, v = self.controller._parameters(rt, response, opt)
            self.assertEqual(p['drive_length_s'], 330.5e-9)
            fixed = self.controller._readout_revalidation_parameters(opt, p, marker)
            self.assertEqual(fixed['amplitudes'], [.8])
            self.assertEqual(attempts, list(map(tuple, rt.db.execute('SELECT * FROM platform_attempts ORDER BY id'))))
        self.assertEqual(self._call(), response)  # old revision accepted only for identical command replay
        self.assertFalse(self.controller.wake.is_set())
        with self.assertRaises(APIError) as raised:
            self._call(operationId='OTHER', expectedRevision=response['revision'], revalidationId=marker['id'])
        self.assertEqual(raised.exception.code, 'READOUT_WITHDRAWAL_MARKER')

    def test_state_revision_marker_values_and_idempotency_boundaries(self):
        self._unchanged_rejection('STALE_REVISION', expectedRevision=0)
        self._unchanged_rejection('READOUT_WITHDRAWAL_MARKER', sourceAttemptId='OTHER')
        self._unchanged_rejection('READOUT_WITHDRAWAL_MARKER', revalidationId='OTHER')
        self._unchanged_rejection('INVALID_READOUT_WITHDRAWAL', readout_amp=.8)
        self._unchanged_rejection('INVALID_READOUT_WITHDRAWAL', reason=' ')
        for state in ('running', 'queued', 'draft', 'completed', 'failed', 'aborted'):
            with self.subTest(state=state):
                self._change_wf(lambda w: w.update(status=state)); self._unchanged_rejection('INVALID_STATE')
        self._change_wf(lambda w: w.update(status='paused')); self._call()
        with self.assertRaises(APIError) as raised: self._call(reason='Different')
        self.assertEqual(raised.exception.code, 'IDEMPOTENCY_CONFLICT')

    def test_active_lease_and_unknown_effect_reject_without_write(self):
        with self.controller.store.transaction() as rt:
            rt.db.execute('INSERT INTO platform_leases VALUES(?,?,?,?,?)', ('OFFLINE', self.wf['id'], 'OTHER', 'active', 'TOKEN'))
        self._unchanged_rejection('RECONCILIATION_REQUIRED')
        with self.controller.store.transaction() as rt:
            rt.db.execute('DELETE FROM platform_leases')
            rt.db.execute('INSERT INTO platform_effects VALUES(?,?,?,?,?,NULL)', ('OTHER', self.wf['id'], 'OTHER', 'unknown', '{}'))
        self._unchanged_rejection('RECONCILIATION_REQUIRED')

    def test_fixed_failed_allowed_but_changed_marker_owner_and_physical_context_reject(self):
        self._change_wf(lambda w: w['readoutRevalidations']['Q11'].update(state='completed'))
        self._unchanged_rejection('READOUT_WITHDRAWAL_MARKER')
        self._change_wf(lambda w: w['readoutRevalidations']['Q11'].update(state='fixed_failed'))
        with self.controller.store.transaction() as rt:
            rt.db.execute('UPDATE parameters SET value=?,revision=2 WHERE name=?', (dumps(331e-9), self.wf['id'] + ':Q11:drive_length_s'))
        self._unchanged_rejection('READOUT_WITHDRAWAL_SOURCE_INVALID')
        with self.controller.store.transaction() as rt:
            rt.db.execute('UPDATE parameters SET value=?,revision=1 WHERE name=?', (dumps(330.5e-9), self.wf['id'] + ':Q11:drive_length_s'))
        response = self._call(); self.assertEqual(response['lastReadoutWithdrawal']['after']['values']['readout_amp'], .8)

    def test_owner_graph_and_adapter_binding_rejected(self):
        self._change_wf(lambda w: next(n for n in w['nodes'] if n['id'] == self.opt['id']).update(ownerId='s04_readout_fidelity'))
        self._unchanged_rejection('READOUT_WITHDRAWAL_OWNER')
        self._change_wf(lambda w: next(n for n in w['nodes'] if n['id'] == self.opt['id']).update(ownerId='s05_readout_optimization', dependsOn=[]))
        self._unchanged_rejection('READOUT_WITHDRAWAL_OWNER')
        self._change_wf(lambda w: w.update(adapterProfileHash='wrong'))
        self._unchanged_rejection('ADAPTER_CHANGED')

    def test_changed_raw_registry_and_cleanup_fail_closed(self):
        artifact = self.iq_attempt['result']['artifacts'][0]; path = Path(artifact['sourcePath']); original = path.read_bytes()
        path.write_bytes(b'TAMPERED'); self._unchanged_rejection('READOUT_WITHDRAWAL_SOURCE_INVALID'); path.write_bytes(original)
        with self.controller.store.transaction() as rt:
            rt.db.execute('UPDATE platform_artifacts SET sha256=? WHERE id=?', ('0' * 64, artifact['id']))
        self._unchanged_rejection('READOUT_WITHDRAWAL_SOURCE_INVALID')
        with self.controller.store.transaction() as rt:
            rt.db.execute('UPDATE platform_artifacts SET sha256=? WHERE id=?', (artifact['sha256'], artifact['id']))
        receipt = Path(self.iq_attempt['result']['provenance']['cleanupReceipt']); doc = json.loads(receipt.read_text())
        doc['shutdown_ok'] = False; receipt.write_text(dumps(doc)); self._unchanged_rejection('READOUT_WITHDRAWAL_SOURCE_INVALID')

    def test_iq_partial_unaccepted_wrong_version_and_snapshot_reference_reject(self):
        with self.controller.store.transaction() as rt:
            original = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', ('OFFLINE-IQ',)).fetchone()[0]
        for change in (lambda a: a['result'].update(partial=True), lambda a: a['result']['quality'].update(status='failed'),
                       lambda a: a['parameterVersions'].update(readout_amp=2), lambda a: a.update(endedAt='2026-11-18T01:00:00Z')):
            a = json.loads(original); change(a)
            with self.controller.store.transaction() as rt: rt.db.execute('UPDATE platform_attempts SET payload=? WHERE id=?', (dumps(a), 'OFFLINE-IQ'))
            self._unchanged_rejection('READOUT_WITHDRAWAL_SOURCE_INVALID')
        with self.controller.store.transaction() as rt:
            rt.db.execute('UPDATE platform_attempts SET payload=? WHERE id=?', (original, 'OFFLINE-IQ'))
            e = rt.db.execute('SELECT request FROM platform_effects WHERE operation_id=?', ('OFFLINE-RO/measure',)).fetchone()[0]
            req = json.loads(e); req['validationSnapshot']['acceptedEvidence'][0]['selectionOperationId'] = 'OTHER/measure'
            rt.db.execute('UPDATE platform_effects SET request=? WHERE operation_id=?', (dumps(req), 'OFFLINE-RO/measure'))
        self._unchanged_rejection('READOUT_WITHDRAWAL_SOURCE_INVALID')

    def test_http_route_requires_session_origin_and_preserves_payload(self):
        from backend.server import Server
        server = Server(('127.0.0.1', 0), self.controller)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
            try:
                conn.request(method, path, body=None if body is None else json.dumps(body),
                    headers={'Connection': 'close', 'Content-Type': 'application/json', **(headers or {})})
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()
        try:
            path = '/api/workflows/' + self.wf['id'] + '/readout-withdrawal'
            self.assertEqual(request('POST', path, self.payload)[0], 401)
            token = request('GET', '/api/session')[1]['token']
            self.assertEqual(request('POST', path, self.payload,
                {'X-Qcal-Token': token, 'Origin': 'http://untrusted.example'})[0], 403)
            status, first = request('POST', path, self.payload, {'X-Qcal-Token': token})
            self.assertEqual(status, 200); self.assertEqual(first['status'], 'paused')
            self.assertEqual(request('POST', path, self.payload, {'X-Qcal-Token': token})[1], first)
            self.assertEqual(first['lastReadoutWithdrawal']['after']['values']['readout_amp'], .8)
        finally:
            server.shutdown(); server.server_close(); thread.join(3)

    def test_aggregate_candidate_cannot_impersonate_registered_native_choice(self):
        with self.controller.store.transaction() as rt:
            row = rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?', ('OFFLINE-RO',)).fetchone()
            attempt = json.loads(row[0]); attempt['result']['candidateParameters']['readout_amp'] = .7
            rt.db.execute('UPDATE platform_attempts SET payload=? WHERE id=?', (dumps(attempt), 'OFFLINE-RO'))
            rt.db.execute('UPDATE parameters SET value=? WHERE name=?', (dumps(.7), self.wf['id'] + ':Q11:readout_amp'))
        self._change_wf(lambda w: w['readoutRevalidations']['Q11']['values'].update(readout_amp=.7))
        self._unchanged_rejection('READOUT_WITHDRAWAL_SOURCE_INVALID')


if __name__ == '__main__':
    unittest.main()
