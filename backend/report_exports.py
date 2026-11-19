"""Durable, idempotent local report jobs; does not connect to measurement hardware."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid

from .common import APIError, checked_path, dumps, file_hash, now
from .human_reports import reader_model, writing_audit
from scripts.export_report_snapshot import snapshot


class ReportExportService:
    def __init__(self, controller, *, runner=None):
        self.controller = controller
        self.root = checked_path(controller.root)
        self.db_path = checked_path(controller.store.path, parent=self.root)
        self.output_root = checked_path(self.root / 'data/report_exports', parent=self.root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='report-export')
        self.runner = runner or self._run_pipeline
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS platform_report_exports(id TEXT PRIMARY KEY,operation_id TEXT UNIQUE NOT NULL,request_hash TEXT NOT NULL,payload TEXT NOT NULL)')
            for row in db.execute('SELECT id,payload FROM platform_report_exports'):
                job = json.loads(row[1])
                if job['status'] in ('queued', 'processing'):
                    job.update(status='interrupted', endedAt=now(), error={'code': 'EXPORT_INTERRUPTED', 'message': '软件重启中断了报告生成，请重新导出。'})
                    self._save(db, job)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.execute('PRAGMA busy_timeout=30000')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _save(db, job):
        db.execute('UPDATE platform_report_exports SET payload=? WHERE id=?', (dumps(job), job['id']))

    @staticmethod
    def _public(job):
        return {key: value for key, value in job.items() if key not in ('directory', 'requestHash')}

    def get(self, job_id):
        with self._db() as db:
            row = db.execute('SELECT payload FROM platform_report_exports WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise APIError('NOT_FOUND', '未找到报告导出记录', 404)
        return self._public(json.loads(row[0]))

    def list(self, workflow_id, limit=20):
        with self._db() as db:
            rows = db.execute('SELECT payload FROM platform_report_exports ORDER BY rowid DESC').fetchall()
        return [self._public(j) for r in rows if (j := json.loads(r[0]))['workflowId'] == workflow_id][:min(100, max(1, limit))]

    def create(self, workflow_id, body):
        if set(body) - {'operationId', 'expectedRevision', 'format', 'appendixCount'}:
            raise APIError('INVALID_REPORT', '报告请求含不支持的字段')
        op = body.get('operationId')
        revision = body.get('expectedRevision')
        appendix = body.get('appendixCount', 16)
        if not isinstance(op, str) or not op.strip() or len(op) > 160 or type(revision) is not int or revision < 0 or type(appendix) is not int or appendix not in (16, 64) or body.get('format') != 'pptx':
            raise APIError('INVALID_REPORT', '请提供操作编号、当前任务版本及PPT格式')
        request_hash = hashlib.sha256(dumps({'workflowId': workflow_id, **body}).encode()).hexdigest()
        with self._lock, self._db() as db:
            prior = db.execute('SELECT request_hash,payload FROM platform_report_exports WHERE operation_id=?', (op,)).fetchone()
            if prior:
                if prior[0] != request_hash:
                    raise APIError('IDEMPOTENCY_CONFLICT', '同一导出操作编号不能用于不同请求', 409)
                return self._public(json.loads(prior[1]))
            row = db.execute('SELECT payload FROM platform_workflows WHERE id=?', (workflow_id,)).fetchone()
            if row is None:
                raise APIError('NOT_FOUND', '任务不存在', 404)
            wf = json.loads(row[0])
            if wf.get('revision') != revision:
                raise APIError('REVISION_CONFLICT', '实验状态已更新，请刷新后重新导出', 409)
            job_id = 'report-' + uuid.uuid4().hex
            directory = checked_path(self.output_root / job_id, parent=self.output_root)
            directory.mkdir()
            # Freeze synchronously before acknowledging: later acquisition progress cannot change this report.
            frozen = snapshot(self.db_path, workflow_id, self.root / 'data')
            if frozen['workflow']['revision'] != revision:
                raise APIError('REVISION_CONFLICT', '冻结时实验状态发生变化，请重新导出', 409)
            payload = json.dumps(frozen, ensure_ascii=False, indent=2).encode('utf-8')
            (directory / 'report.json').write_bytes(payload)
            job = {'id': job_id, 'workflowId': workflow_id, 'revision': revision, 'operationId': op,
                   'status': 'queued', 'createdAt': now(), 'format': 'pptx', 'appendixCount': appendix,
                   'snapshotSha256': hashlib.sha256(payload).hexdigest(), 'directory': str(directory.relative_to(self.root))}
            db.execute('INSERT INTO platform_report_exports VALUES(?,?,?,?)', (job_id, op, request_hash, dumps(job)))
        self.executor.submit(self._execute, job)
        return self._public(job)

    def _execute(self, job):
        job = dict(job)
        try:
            job.update(status='processing', startedAt=now())
            with self._db() as db: self._save(db, job)
            directory = checked_path(self.root / job['directory'], parent=self.output_root)
            report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
            model = reader_model(report)
            audit = writing_audit(model, self.root / 'knowledge/report-writing/defensive-language-audit')
            (directory / 'reader.json').write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding='utf-8')
            (directory / 'writing-audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
            output = checked_path(self.runner(directory, job), parent=directory)
            sha = file_hash(output)
            from .result_archive import register_report_archive
            archive_id = register_report_archive(self.db_path, job['workflowId'], job['id'], output, sha,
                job['snapshotSha256'], job['revision'], root=self.root, kind='pptx', audit={
                    'policyVersion': audit['policyVersion'], 'policyApplied': True, 'humanAccepted': False})
            job.update(status='completed', endedAt=now(), sha256=sha, bytes=output.stat().st_size,
                       filename='量子测控报告.pptx', relativePath=str(output.relative_to(directory)),
                       downloadUrl=f'/api/report-exports/{job["id"]}/file', archiveId=archive_id,
                       writingPolicy={'version': audit['policyVersion'], 'applied': True, 'humanAccepted': False})
        except Exception as exc:
            job.update(status='failed', endedAt=now(), error={'code': 'REPORT_EXPORT_FAILED', 'message': '报告生成未完成，请查看导出诊断后重试。'})
            directory = checked_path(self.root / job['directory'], parent=self.output_root)
            (directory / 'error.txt').write_text(type(exc).__name__ + ': ' + str(exc), encoding='utf-8')
        with self._db() as db: self._save(db, job)

    def file(self, job_id):
        job = self.get(job_id)
        if job['status'] != 'completed':
            raise APIError('REPORT_NOT_READY', '报告尚未生成完成', 409)
        path = checked_path(self.output_root / job_id / job['relativePath'], parent=self.output_root / job_id)
        if file_hash(path) != job['sha256']:
            raise APIError('ARTIFACT_CHANGED', '报告文件校验未通过', 409)
        return path, 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

    def _run_pipeline(self, directory, job):
        # A local dependency is required; never silently fetch code or upload measurements.
        config_path = self.root / 'config/reporting.local.json'
        config = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
        modules = config.get('runtimeNodeModules') or os.environ.get('RUNTIME_NODE_MODULES')
        node = config.get('nodeExecutable')
        if not modules:
            candidates = sorted(Path('C:/Program Files/WindowsApps').glob('OpenAI.CodexPrimaryRuntime.*/dependencies/node/node_modules'), reverse=True)
            modules = str(candidates[0]) if candidates else None
        if not modules:
            raise RuntimeError('未安装本地 PowerPoint 生成依赖，请配置 reporting.local.json')
        if not node:
            candidate = Path(modules).parent / 'bin/node.exe'
            node = str(candidate) if candidate.is_file() else shutil.which('node')
        env = {**os.environ, 'RUNTIME_NODE_MODULES': str(checked_path(modules)), 'PYTHONIOENCODING': 'utf-8'}
        command = [str(node), str(self.root / 'scripts/export_reader_pptx.mjs'), '--snapshot', str(directory / 'report.json'),
                   '--reader', str(directory / 'reader.json'), '--template', str(self.root / 'scripts/report_assets/quantum_red_six_plot_template.pptx'),
                   '--build-dir', str(directory / 'build'), '--appendix-count', str(job['appendixCount'])]
        with (directory / 'generation.log').open('wb') as log:
            subprocess.run(command, cwd=self.root, env=env, stdout=log, stderr=subprocess.STDOUT,
                           timeout=1800, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            subprocess.run([sys.executable, '-B', '-X', 'utf8', str(self.root / 'scripts/validate_reader_pptx.py'),
                            '--build-dir', str(directory / 'build')], cwd=self.root, env=env, stdout=log, stderr=subprocess.STDOUT,
                           timeout=120, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return directory / 'build/report.pptx'

    def shutdown(self):
        self.executor.shutdown(wait=True)

    def has_active(self):
        with self._db() as db:
            return any(json.loads(row[0])['status'] in ('queued', 'processing') for row in db.execute('SELECT payload FROM platform_report_exports'))
