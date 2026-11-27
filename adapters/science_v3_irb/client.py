"""Bounded isolated native sequence planning; never imports instrument code."""
import json
from pathlib import Path
import subprocess
from .policy import verify_sources
from .plans import normalize_parameters

def plan_native(root,python,parameters):
    root=Path(root);verify_sources(root);p,_=normalize_parameters(parameters)
    result=subprocess.run([str(python),'-I','-B',str(root/'adapters/science_v3_irb/plan_worker.py')],
        input=json.dumps(p,allow_nan=False),capture_output=True,text=True,timeout=45,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('Native IRB planning failed: '+result.stderr[-4000:])
    if len(result.stdout)>64_000_000:raise ValueError('Native IRB plan exceeds output budget')
    return json.loads(result.stdout)
