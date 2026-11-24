"""Worker-local cooperative cancellation and controller telemetry; no drivers."""
import json
import os
from pathlib import Path

def root():
    return Path(os.environ['QCAL_ARCLIGHT_RUN_DIR'])

def check_cancel():
    if (root()/'STOP_AFTER_POINT').exists():
        raise InterruptedError('Controller requested cooperative cancellation')

def emit(event):
    with (root()/'events.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')

def progress_event(status):
    total=status.get('points_expected',0); completed=status.get('points_completed',0)
    job_index=int(os.environ.get('QCAL_ARCLIGHT_JOB_INDEX','1'))
    job_count=int(os.environ.get('QCAL_ARCLIGHT_JOB_COUNT','1'))
    offset=int(os.environ.get('QCAL_ARCLIGHT_COMPLETED_ROWS','0'))
    suite_rows=int(os.environ.get('QCAL_ARCLIGHT_TOTAL_ROWS',str(total or 1)))
    if total:
        emit({'event':'row_acquired' if completed else 'job_start','row':completed,'rowNumber':completed,
              'completedRows':completed,'totalRows':total,'jobIndex':job_index,'jobCount':job_count,
              'progress':min(100,100*(offset+completed)/max(1,suite_rows)),
              'operationId':status.get('operationId'),'resourceId':status.get('resourceId'),'target':status.get('target'),
              'configuredRoute':status.get('configuredRoute'),'actualRoute':status.get('actualRoute'),
              'progressBasis':'actual-completed-native-scan-points', 'currentAxes':status.get('current_axes'),
              'currentPointBlocksCompleted':status.get('current_point_blocks_completed',0),
              'message':f"{status['job']}: {completed}/{total} 点；任务 {job_index}/{job_count}"})

class DelegatedLock:
    """The outer worker owns all chassis locks for the whole bounded suite."""
    owned=False
    def acquire(self,details):
        token=os.environ.get('QCAL_ARCLIGHT_LOCK_TOKEN')
        for value in json.loads(os.environ.get('QCAL_ARCLIGHT_LOCKS','[]')):
            record=json.loads(Path(value).read_text(encoding='utf-8'))
            if not token or record.get('token')!=token or record.get('pid')!=os.getpid():
                raise RuntimeError('Missing controller worker chassis lock ownership')
        if not token:raise RuntimeError('Hardware entry must be invoked through the platform worker')
        self.owned=True
    def release(self):self.owned=False
