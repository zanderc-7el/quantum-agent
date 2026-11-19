"""One SQLite authority, extending the supplied experience Runtime's schema.

The original simulated Runtime remains unchanged. Its parameter/StepSpec/dependency
ledger is reused; real acquisitions use the separate guarded worker protocol.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import threading
import time

from .common import ROOT, dumps, now
from .result_archive import ensure_schema, backfill, record_attempt, SCHEMA_VERSION

sys.path.insert(0, str(ROOT / 'vendor' / 'experience'))
from quantum_agents.runtime import Runtime, StepSpec, Budget  # noqa: E402

class Store:
    def __init__(self, path, *, root=None):
        self.path = Path(path)
        self.root = Path(root) if root is not None else self.path.parent.parent
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        with self.connect() as rt:
            rt.db.executescript('''
            CREATE TABLE IF NOT EXISTS platform_workflows(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_attempts(id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, node_id TEXT NOT NULL, number INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(workflow_id,node_id,number));
            CREATE TABLE IF NOT EXISTS platform_decisions(id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, node_id TEXT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_commands(id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_artifacts(id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_leases(resource TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, attempt_id TEXT NOT NULL, state TEXT NOT NULL, token TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS platform_effects(operation_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, attempt_id TEXT NOT NULL, state TEXT NOT NULL, request TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS platform_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
            with rt._transaction():
                if ensure_schema(rt.db):
                    backfill(rt.db, self.root)
                    rt.db.execute('INSERT INTO platform_archive_migrations VALUES(?,?)', (SCHEMA_VERSION, now()))

    @contextmanager
    def connect(self):
        rt = Runtime(self.path)
        try:
            yield rt
        finally:
            rt.close()

    @contextmanager
    def transaction(self):
        with self.lock, self.connect() as rt:
            with rt._transaction():
                yield rt
            self.changed.notify_all()

    def workflows(self, rt=None):
        if rt is None:
            with self.connect() as current:
                return self.workflows(current)
        return [json.loads(row[0]) for row in rt.db.execute('SELECT payload FROM platform_workflows ORDER BY rowid DESC')]

    @staticmethod
    def get(rt, workflow_id):
        row = rt.db.execute('SELECT payload FROM platform_workflows WHERE id=?', (workflow_id,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def save(rt, workflow, event, payload=None):
        workflow['revision'] += 1
        workflow['updatedAt'] = now()
        message = (payload or {}).get('message', event)
        workflow['events'].append({'time': now(), 'message': message,
                                   'kind': 'warning' if event in ('attention', 'reconciliation_required', 'backtrack') else 'info'})
        # UI keeps a tail. The append-only SQLite event ledger is never truncated.
        workflow['events'] = workflow['events'][-250:]
        rt.db.execute('INSERT OR REPLACE INTO platform_workflows VALUES(?,?,?)',
                      (workflow['id'], workflow['revision'], dumps(workflow)))
        rt._event(workflow['id'], event, {'revision': workflow['revision'], **(payload or {})})

    def snapshot(self):
        with self.lock, self.connect() as rt:
            seq = rt.db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0]
            return {'version': 1, 'seq': seq, 'workflows': self.workflows(rt)}

    def detail(self, workflow_id, node_id):
        with self.connect() as rt:
            attempts = [json.loads(r[0]) for r in rt.db.execute(
                'SELECT payload FROM platform_attempts WHERE workflow_id=? AND node_id=? ORDER BY number', (workflow_id, node_id))]
            decisions = [json.loads(r[0]) for r in rt.db.execute(
                'SELECT payload FROM platform_decisions WHERE workflow_id=? AND node_id=? ORDER BY rowid', (workflow_id, node_id))]
            return attempts, decisions

    def attempt(self, rt, workflow_id, attempt):
        rt.db.execute('INSERT OR REPLACE INTO platform_attempts VALUES(?,?,?,?,?)',
                      (attempt['id'], workflow_id, attempt['nodeId'], attempt['number'], dumps(attempt)))
        record_attempt(rt.db, workflow_id, attempt, self.root)

    @staticmethod
    def decision(rt, workflow_id, decision):
        rt.db.execute('INSERT INTO platform_decisions VALUES(?,?,?,?)',
                      (decision['id'], workflow_id, decision.get('nodeId'), dumps(decision)))

    def events(self, workflow_id, after=0):
        with self.connect() as rt:
            return [{**dict(row), 'payload': json.loads(row['payload'])} for row in rt.db.execute(
                'SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq', (workflow_id, after))]
