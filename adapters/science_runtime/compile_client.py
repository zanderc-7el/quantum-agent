"""Bounded, cancellable Python312 subprocess; never calls a physical worker."""
import json
import os
from pathlib import Path
import subprocess
import time

from .policy import canonical, digest, normalize, sha, verify_sources, TIMEOUT

def compile_offline(root,request,output,*,cancel=lambda:False,timeout=TIMEOUT):
    root=Path(root).resolve();output=Path(output).resolve()
    normalized=normalize(request)  # reject code/axes/budgets before any process
    if cancel():raise InterruptedError('Native compilation cancelled before spawn')
    sources=verify_sources(root)
    profile_path=root/'adapters/arclight_profile.json'
    profile=json.loads(profile_path.read_text(encoding='utf-8'))
    python=Path(profile['python'])
    if not python.is_file():raise ValueError('Configured native Python312 is unavailable')
    output.mkdir(parents=True,exist_ok=False)
    frozen={'request':normalized,'sources':sources,'sourceHash':digest(sources),
            'configuredPython':str(python),'profileHash':sha(profile_path),'compileOnly':True}
    request_path=output/'request.json'
    request_path.write_text(canonical(frozen),encoding='utf-8')
    env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','QCAL_'))}
    env.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    process=None
    try:
        with (output/'compile_only.log').open('wb') as log:
            process=subprocess.Popen([str(python),'-I','-B','-u',str(root/'adapters/science_runtime/compile_only_worker.py'),str(request_path)],
                cwd=output,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            start=time.monotonic()
            while process.poll() is None:
                if cancel():raise InterruptedError('Native compilation cancelled')
                if time.monotonic()-start>min(TIMEOUT,timeout):raise TimeoutError('Native compilation timed out')
                time.sleep(.04)
            if process.returncode:
                error=output/'error.json'
                detail=json.loads(error.read_text(encoding='utf-8'))['message'] if error.exists() else 'Native compiler failed; see compile_only.log'
                raise ValueError(detail)
        result=json.loads((output/'result.json').read_text(encoding='utf-8'))
        if result['sourceHash']!=frozen['sourceHash'] or result['requestHash']!=digest(normalized):raise ValueError('Compiler receipt does not match frozen request')
        for artifact in result['artifacts']:
            path=output/artifact['name']
            if path.parent!=output or sha(path)!=artifact['sha256']:raise ValueError('Compiled array integrity failed')
        verify_sources(root)
        return result
    except BaseException as exc:
        if process and process.poll() is None:
            process.kill();process.wait(timeout=5)
        (output/'failure.json').write_text(canonical({'compileOnly':True,'dispatched':False,'reason':str(exc),'childStopped':process is None or process.poll() is not None}),encoding='utf-8')
        raise
