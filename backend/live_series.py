"""On-demand, read-only live point summaries with bounded durable cursors.

Controller/HTTP integration must first authorize the operation from the selected
workflow's persisted attempt/effect. This module never accepts a request path,
opens an instrument, starts a measurement, or promotes a quality result.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time

from .common import checked_path


class LiveSeries:
    def __init__(self, project_root, python, *, database=None, reader=None, poll_seconds=1.5):
        self.root = checked_path(project_root)
        self.python = str(python)
        self.helper = checked_path(self.root/'backend/live_scan_reader.py', parent=self.root)
        self.database = checked_path(database or self.root/'data/live_series.sqlite', parent=self.root)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.reader = reader
        self.poll_seconds = max(0., float(poll_seconds))
        self._mutex = threading.RLock()
        self._locks = {}
        self._last_poll = {}
        with self._connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS live_points (
                cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL, job_id TEXT NOT NULL,
                point_index INTEGER NOT NULL, source_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(operation_id, job_id, point_index));
            CREATE INDEX IF NOT EXISTS live_points_cursor ON live_points(operation_id, cursor);
            CREATE TABLE IF NOT EXISTS live_operation_state (
                operation_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _dump(value):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))

    def _invoke(self, request):
        if self.reader is not None:
            return self.reader(request)
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
        result = subprocess.run([self.python, '-I', '-B', str(self.helper)], input=self._dump(request),
            capture_output=True, text=True, encoding='utf-8', env=env, cwd=self.root, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0:
            raise ValueError('Live point reader failed: ' + result.stderr[:500])
        if len(result.stdout) > 4*1024*1024:
            raise ValueError('Live point reader output exceeded bound')
        return json.loads(result.stdout)

    def read(self, operation_id, *, after=0, limit=256):
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 256:
            raise ValueError('Invalid operation ID')
        if type(after) is not int or not 0 <= after <= 2**63-1 or type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError('Invalid live series cursor or limit')
        # Only the operation ID is accepted; its SHA-derived folder is fixed.
        directory = self.root/'data/runs/arclight_operations'/hashlib.sha256(operation_id.encode()).hexdigest()
        checked_path(directory, parent=self.root/'data/runs/arclight_operations')
        with self._mutex:
            lock = self._locks.setdefault(operation_id, threading.Lock())
        with lock:
            with self._connect() as db:
                saved = db.execute('SELECT payload FROM live_operation_state WHERE operation_id=?', (operation_id,)).fetchone()
                state = json.loads(saved['payload']) if saved else {'state': 'not_started', 'complete': False, 'terminal': False, 'availablePoints': 0}
                offsets = {r['job_id']: r['last_point'] for r in db.execute(
                    'SELECT job_id,MAX(point_index) AS last_point FROM live_points WHERE operation_id=? GROUP BY job_id', (operation_id,))}
                count = db.execute('SELECT COUNT(*) FROM live_points WHERE operation_id=?', (operation_id,)).fetchone()[0]
            should_poll = count < 15000 and (not state.get('terminal') or count < state.get('availablePoints', 0) or state.get('pending'))
            if should_poll and time.monotonic()-self._last_poll.get(operation_id, -float('inf')) >= self.poll_seconds:
                batch = self._invoke({'operationId': operation_id, 'afterByJob': offsets, 'limit': min(256, 15000-count)})
                if batch.get('operationId') != operation_id or not isinstance(batch.get('points'), list) or len(batch['points']) > 256:
                    raise ValueError('Live reader identity or batch mismatch')
                state = {k: v for k, v in batch.items() if k != 'points'}
                with self._connect() as db:
                    for point in batch['points']:
                        if point.get('operationId') != operation_id:
                            raise ValueError('Cross-operation live point')
                        old = db.execute('SELECT source_hash FROM live_points WHERE operation_id=? AND job_id=? AND point_index=?',
                            (operation_id, point['jobId'], point['pointIndex'])).fetchone()
                        if old and old['source_hash'] != point['pointSha256']:
                            raise ValueError('Previously observed point has changed')
                        db.execute('INSERT OR IGNORE INTO live_points(operation_id,job_id,point_index,source_hash,payload) VALUES(?,?,?,?,?)',
                            (operation_id, point['jobId'], point['pointIndex'], point['pointSha256'], self._dump(point)))
                    db.execute('INSERT OR REPLACE INTO live_operation_state VALUES(?,?)', (operation_id, self._dump(state)))
                self._last_poll[operation_id] = time.monotonic()
            with self._connect() as db:
                rows = db.execute('SELECT cursor,payload FROM live_points WHERE operation_id=? AND cursor>? ORDER BY cursor LIMIT ?',
                    (operation_id, after, limit+1)).fetchall()
                cached = db.execute('SELECT COUNT(*) FROM live_points WHERE operation_id=?', (operation_id,)).fetchone()[0]
            points = [{**json.loads(row['payload']), 'cursor': row['cursor']} for row in rows[:limit]]
            return {'operationId': operation_id, 'cursor': points[-1]['cursor'] if points else after,
                'points': points, 'hasMore': len(rows)>limit or cached < state.get('availablePoints', 0),
                'cachedPoints': cached, 'availablePoints': state.get('availablePoints', 0),
                'complete': bool(state.get('complete')), 'terminal': bool(state.get('terminal')),
                'state': state.get('state', 'not_started'), 'pending': bool(state.get('pending')),
                'source': 'live_acquired_points', 'quality': 'unverified',
                'meaning': 'Read-only summaries of confirmed acquired point files; not independent validation or accepted calibration'}
