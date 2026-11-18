"""Audited offline compile entry: all sockets and VISA creation/open forbidden."""
import sys,socket,runpy,os,json,hashlib
from pathlib import Path
calls=[]
def forbidden(*args,**kwargs):
    calls.append('forbidden-network-or-visa')
    raise RuntimeError('OFFLINE GUARD: network/VISA is forbidden during compile')
socket.create_connection=forbidden
socket.socket.connect=forbidden
socket.socket.connect_ex=forbidden
socket.socket.sendto=forbidden
import pyvisa
pyvisa.ResourceManager=forbidden
from pyvisa.resources import Resource
Resource.open=forbidden
source=Path(__file__).absolute().parent
plan=Path(sys.argv[1]).absolute()
os.environ['QCAL_ARCLIGHT_RUN_DIR']=str(plan.parent)
sys.argv=[str(source/'platform_worker.py'),str(plan),'--compile-only']
completed=False
try:
    runpy.run_path(str(source/'platform_worker.py'),run_name='__main__')
    if calls:raise AssertionError('Offline compilation attempted forbidden IO')
    completed=True
finally:
    completion_path=plan.parent/'completion.json'
    completion=json.loads(completion_path.read_text(encoding='utf8')) if completion_path.exists() else {}
    raw=list((plan.parent/'results').glob('*/raw_scan.npz'))
    report={'kind':'offline_compile_only','passed':completed and not calls and not raw,
        'forbidden_io_attempts':calls,'real_acquisition_files':len(raw),
        'jobs_compiled':len(completion.get('folders',[])),
        'worker_state':completion.get('state'),'worker_sha256':hashlib.sha256((source/'platform_worker.py').read_bytes()).hexdigest(),
        'plan_sha256':hashlib.sha256(plan.read_bytes()).hexdigest(),
        'hardware_connected':False,'original_experiment_files_modified':False}
    (plan.parent/'offline_guard_report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print('OFFLINE_GUARD_OK: no socket/VISA access attempted; no acquisition',flush=True)
