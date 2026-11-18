"""Offline SQLite fixtures; never use a platform store or a live database."""
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    'bundle_report_snapshot', Path(__file__).resolve().parents[1] / 'scripts/export_report_snapshot.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def make_fixture(root, wal=False):
    db = root / 'fixture.sqlite'
    image = root / 'failed-image.png'
    image.write_bytes(b'offline fixture; image hash verification only')
    artifact = {'id': 'failed-image', 'kind': 'image',
                'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
    with closing(sqlite3.connect(db)) as connection:
        if wal:
            connection.execute('PRAGMA journal_mode=WAL')
        connection.executescript('''
            CREATE TABLE platform_workflows(id TEXT PRIMARY KEY,payload TEXT);
            CREATE TABLE platform_attempts(workflow_id TEXT,node_id TEXT,number INTEGER,payload TEXT);
            CREATE TABLE platform_decisions(workflow_id TEXT,node_id TEXT,payload TEXT);
            CREATE TABLE platform_artifacts(id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,payload TEXT);
        ''')
        for identity, profile, complete in [('old', 'profile-v1', True), ('new', 'profile-v2', False)]:
            workflow = {'id': identity, 'mode': 'real', 'revision': 1,
                        'adapterId': profile, 'adapterProfileHash': profile + '-hash',
                        'workflowComplete': complete,
                        'nodes': [{'id': 'same-q11-node', 'resourceId': 'Q11',
                                   'attempt': 2 if complete else 0,
                                   'engineState': 'verified' if complete else 'pending'}]}
            connection.execute('INSERT INTO platform_workflows VALUES (?,?)', (identity, json.dumps(workflow)))
            connection.execute('INSERT INTO platform_decisions VALUES (?,?,?)',
                               (identity, 'same-q11-node', json.dumps({'id': identity + '-decision'})))
        # Identical node IDs do not make an old acquisition part of the new task.
        connection.execute('INSERT INTO platform_attempts VALUES (?,?,?,?)',
                           ('old', 'same-q11-node', 1, json.dumps({
                               'id': 'old-failure', 'number': 1, 'state': 'execution_failed',
                               'result': {'analysisQuality': {'accepted': False},
                                          'quality': {'status': 'failed'}, 'artifacts': [artifact]}})))
        connection.execute('INSERT INTO platform_artifacts VALUES (?,?,?,?)',
                           (artifact['id'], str(image), artifact['sha256'], json.dumps(artifact)))
        connection.commit()
    return db


class ReportBundleSnapshotTests(unittest.TestCase):
    def test_profiles_attempts_decisions_and_failed_images_stay_with_their_task(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = make_fixture(root)
            before = db.read_bytes()
            bundle = MODULE.snapshot_many(db, ['old', 'new'], root)
            self.assertEqual(before, db.read_bytes())
            self.assertEqual(bundle['schema'], 'qcal-report-bundle/1')
            self.assertNotIn('workflowComplete', bundle)
            old, new = bundle['reports']
            self.assertEqual([item['workflow']['id'] for item in bundle['reports']], ['old', 'new'])
            self.assertEqual([item['workflow']['adapterProfileHash'] for item in bundle['reports']],
                             ['profile-v1-hash', 'profile-v2-hash'])
            self.assertEqual(old['generatedAt'], new['generatedAt'])
            self.assertTrue(old['workflowComplete'])
            self.assertFalse(new['workflowComplete'])
            self.assertEqual(old['nodes'][0]['attempts'][0]['result']['quality']['status'], 'failed')
            self.assertIn('failed-image', old['artifactIndex'])
            self.assertEqual(new['nodes'][0]['attempts'], [])
            self.assertEqual(new['artifactIndex'], {})
            self.assertEqual(old['nodes'][0]['decisions'], [{'id': 'old-decision'}])
            self.assertEqual(new['nodes'][0]['decisions'], [{'id': 'new-decision'}])
            single = MODULE.snapshot_many(db, ['new'], root)
            self.assertEqual(single['schema'], 'qcal-report/1')
            self.assertNotIn('reports', single)

    def test_two_tasks_share_one_snapshot_even_when_another_connection_commits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = make_fixture(root, wal=True)
            original = MODULE._read_report

            def read_then_commit(connection, workflow_id, artifact_root, generated_at):
                result = original(connection, workflow_id, artifact_root, generated_at)
                if workflow_id == 'old':
                    # WAL lets a writer commit while the export holds its read view.
                    with closing(sqlite3.connect(db)) as writer:
                        payload = json.loads(writer.execute(
                            'SELECT payload FROM platform_workflows WHERE id=?', ('new',)).fetchone()[0])
                        payload['revision'] = 2
                        writer.execute('UPDATE platform_workflows SET payload=? WHERE id=?',
                                       (json.dumps(payload), 'new'))
                        writer.commit()
                return result

            with patch.object(MODULE, '_read_report', side_effect=read_then_commit):
                bundle = MODULE.snapshot_many(db, ['old', 'new'], root)
            self.assertEqual(bundle['reports'][1]['workflow']['revision'], 1)
            self.assertEqual(MODULE.snapshot(db, 'new', root)['workflow']['revision'], 2)

    def test_unknown_duplicate_and_mismatched_ids_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = make_fixture(root)
            with self.assertRaisesRegex(ValueError, 'not found'):
                MODULE.snapshot_many(db, ['old', 'missing'], root)
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                MODULE.snapshot_many(db, ['old', 'old'], root)
            with self.assertRaisesRegex(ValueError, 'explicit'):
                MODULE.snapshot_many(db, [], root)
            with closing(sqlite3.connect(db)) as connection:
                payload = {'id': 'old', 'nodes': [], 'workflowComplete': True}
                connection.execute('UPDATE platform_workflows SET payload=? WHERE id=?',
                                   (json.dumps(payload), 'new'))
                connection.commit()
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                MODULE.snapshot_many(db, ['old', 'new'], root)

    def test_cli_additional_workflow_and_immutable_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = make_fixture(root)
            output = root / 'bundle.json'
            command = [sys.executable, '-X', 'utf8', str(Path(MODULE.__file__)), '--db', str(db),
                       '--workflow', 'old', '--additional-workflow', 'new',
                       '--artifact-root', str(root), '--output', str(output)]
            completed = subprocess.run(command, capture_output=True, encoding='utf-8', check=True)
            self.assertEqual([item['id'] for item in json.loads(completed.stdout)['workflows']], ['old', 'new'])
            before = output.read_bytes()
            self.assertEqual(json.loads(before)['schema'], 'qcal-report-bundle/1')
            repeated = subprocess.run(command, capture_output=True, encoding='utf-8')
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn('immutable', repeated.stderr)
            self.assertEqual(output.read_bytes(), before)
            missing = root / 'missing.json'
            invalid = [*command]
            invalid[invalid.index('--additional-workflow') + 1] = 'missing-task'
            invalid[-1] = str(missing)
            result = subprocess.run(invalid, capture_output=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
