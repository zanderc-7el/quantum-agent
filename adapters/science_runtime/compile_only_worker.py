"""Isolated compile-only entry point. No experiment runner or driver import."""
import hashlib
import importlib.abc
import json
import os
from pathlib import Path
import sys
import types

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
sys.dont_write_bytecode=True

def deny(*args,**kwargs):raise PermissionError('Compile-only sandbox forbids hardware, network and child execution')

def audit(event,args):
    if event.startswith(('socket.connect','socket.bind','socket.getaddrinfo','subprocess.Popen','os.system','os.exec','os.spawn')):deny()
    if event=='ctypes.dlopen' and args and any(word in str(args[0]).lower() for word in ('visa','ceyear','awg1652','osc')):deny()
    if event in ('open','os.listdir','os.scandir','os.chdir') and args and isinstance(args[0],(str,bytes,os.PathLike)):
        raw=os.fsdecode(args[0]).replace('\\','/').lower()
        if raw=='d:/qoskit-workspace' or raw.startswith('d:/qoskit-workspace/'):deny()

class NoDrivers(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.startswith(('core.inst','pyvisa','visa','qcodes','pymeasure','serial')):deny()

def namespace(name,path):
    module=types.ModuleType(name);module.__path__=[str(path)];sys.modules[name]=module

def run(request_path):
    sys.addaudithook(audit);sys.meta_path.insert(0,NoDrivers())
    sys.path.insert(0,str(HERE))
    from policy import NATIVE,POLICY,canonical,digest,normalize,sha,verify_sources
    frozen=json.loads(request_path.read_text(encoding='utf-8'))
    if frozen.get('compileOnly') is not True:deny()
    sources=verify_sources(ROOT)  # all 99 + 107 files checked before native import
    if digest(sources)!=frozen['sourceHash']:raise ValueError('Native/extension sources changed before child import')
    request=normalize(frozen['request'])
    native=ROOT/NATIVE/'work/server/core'
    # Bypass broad native __init__ files that import instrumentation; only load
    # actual frozen compiler/pulse/sequence numeric modules. The runner import
    # dependency is a fail-closed stub and cannot resolve a device.
    namespace('core',native);namespace('core.compiler',native/'compiler');namespace('core.experiment',native/'experiment')
    runner=types.ModuleType('core.experiment.runner');runner.get_dev_and_ch=deny
    sys.modules[runner.__name__]=runner
    import numpy as np
    from extension import compile_request
    base=json.loads((ROOT/NATIVE/'base_config.json').read_text(encoding='utf-8'))
    sequence,adc,cfg=compile_request(request,base)
    if any(k.startswith(('core.inst','pyvisa','visa')) for k in sys.modules):deny()
    out=request_path.parent
    np.savez_compressed(out/'waveforms.npz',**sequence.arrays)
    all_xy=[s for s in sequence.segments if s['channel']=='xy']
    first=all_xy[0]['startSample'];last=all_xy[-1]['endSample']
    stride=max(1,int(np.ceil((last-first)/2048)))
    indices=np.arange(first,last,stride)
    x=(indices/4e9*1e9).tolist()
    i=np.zeros(last-first);q=np.zeros(last-first)
    for s in all_xy:
        start=s['startSample']-first;end=s['endSample']-first;k=s['arrayPrefix']
        i[start:end]+=sequence.arrays[k+'_i'];q[start:end]+=sequence.arrays[k+'_q']
    def trace(id_,name,values):return {'id':id_,'name':name,'label':name,'values':values,'quantity':'compiled_dac_samples','unit':'normalized DAC','source':'native_compiler','dataKind':'compiled'}
    series=[trace('native-i','原生编译 I（载频前）',i[::stride].tolist()),trace('native-q','原生编译 Q（载频前）',q[::stride].tolist()),
            trace('native-rf','原生编译 XY（数值载频）',sequence.arrays['xy_rf'][indices].tolist())]
    result={'kind':request['kind'],'dataKind':'compiled','source':'native_compiler','dispatched':False,
        'hardwareConnected':False,'uploaded':False,'measured':False,'acceptedCalibration':False,
        'modelVersion':POLICY,'policy':POLICY,'sourcePolicy':{'id':POLICY,'compileOnly':True,
        'halfRotation':'fixed envelope duration, amplitude scales by theta/pi','phaseReference':'absolute sequence t=0; native virtual-Z sign',
        'bufferNs':2,'preserveExistingDC':True,'gaussian':'native Gaussian; explicit sigma seconds; endpoints are not subtracted'},
        'scope':request['scope'],'input':request['input'],'sourceEvidence':request['source'],
        'sourceHash':digest(sources),'sourceManifests':sources,'requestHash':digest(request),
        'axes':{'x':{'name':'Absolute sequence time','unit':'ns','values':x}},'series':series,
        'segments':sequence.segments,'adc':adc,'display':{'decimated':stride>1,'stride':stride,'view':'XY control window; readout timing in segments',
        'fullResolutionArtifact':'waveforms.npz'},'artifacts':[{'name':'waveforms.npz','kind':'compiled_arrays','sha256':sha(out/'waveforms.npz')}],
        'metrics':{'xySampleRateHz':4e9,'readoutSampleRateHz':2e9,'adcSampleRateHz':adc['sampleRateHz'],
        'xyFullSampleCount':len(sequence.arrays['xy_rf']),'readoutFullSampleCount':len(sequence.arrays['readout_i']),
        'readoutWidthNs':request['input']['readoutWidthNs']},
        'sandbox':{'compileOnly':True,'driverImports':[],'networkAllowed':False,'processExecutionAllowed':False},
        'limitations':['Compiled DAC samples are not uploaded or ADC measurements.',
        'Input shape/DRAG/f12 parameters are unvalidated compile inputs; no gate calibration or fidelity is claimed.',
        'RF analog passband and f12 selectivity are not verified by digital compilation.']}
    verify_sources(ROOT)
    (out/'native_config.json').write_text(canonical(cfg),encoding='utf-8')
    result['artifacts'].append({'name':'native_config.json','kind':'compiled_config','sha256':sha(out/'native_config.json')})
    result['artifacts'].append({'name':'request.json','kind':'compiled_request','sha256':sha(request_path)})
    (out/'result.json').write_text(canonical(result),encoding='utf-8')

if __name__=='__main__':
    path=Path(sys.argv[1]).resolve()
    try:run(path)
    except BaseException as exc:
        (path.parent/'error.json').write_text(json.dumps({'message':str(exc),'type':type(exc).__name__},ensure_ascii=False),encoding='utf-8')
        raise
