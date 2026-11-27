"""TEST ONLY archive contract; no instrument samples or physical acceptance.

Tiny distinct bytes exercise hash/registration/lifecycle boundaries. The separate
pinned native test covers real NumPy block capture and finite ADC trains.
"""
from copy import deepcopy
import hashlib
from adapters.f8 import _digest, _dump
from backend.common import now


def archive_result(adapter, request):
    from adapters.arclight_rabi16_runtime.execute_worker import sampling_receipt
    plan=adapter.build_plan(request)
    folder=adapter._operation_dir(request['operationId']);folder.mkdir(parents=True,exist_ok=True)
    native=folder/'results'/'TEST_ONLY';native.mkdir(parents=True,exist_ok=True)
    _dump(folder/'plan.json',plan)
    _dump(native/'job.json',plan['jobs'][0])
    _dump(native/'axes.json',{'fixtureOnly':True,'rows':plan['totalRows']})
    _dump(native/'status.json',{'points_completed':plan['totalRows'],'shutdown_ok':True})
    (native/'raw_scan.npz').write_bytes(('TEST_ONLY contract bytes '+request['operationId']).encode())
    blocks=plan['parameters']['acquisitions_per_point'];shots=plan['parameters']['shots']
    for row in range(plan['totalRows']):
        entries=[]
        for block in range(blocks):
            name=f'point_{row:04d}_block_{block:03d}.npz'
            value=('TEST_ONLY '+request['operationId']+f'/{row}/{block}').encode()
            (native/name).write_bytes(value)
            sha=hashlib.sha256(value).hexdigest()
            entries.append({'point_index':row,'block_index':block,'block_file':name,
                'shots_in_block':shots,'shape':[1,shots],'exact_duplicate_of_blocks':[],
                'sha256':sha,'raw_iq_sha256':sha})
        _dump(native/f'point_{row:04d}_acquisition_blocks.json',{
            'schema':'arclight_actual_repeated_iq_v1','state':'complete','point_index':row,
            'acquisitions_per_point':blocks,'shots_per_acquisition':shots,'blocks_completed':blocks,
            'effective_shots_completed':blocks*shots,'exact_duplicate_blocks_detected':False,'blocks':entries})
    analysis={'metrics':{'contrast_snr':8,'r_squared':.95,'cycles':2,'relative_stderr':.1,'scan_coverage':1},
        'candidateParameters':{'drive_amp':.8 if request['operationId'].endswith('/measure') else .79},
        'series':[],'analysis':{'accepted':True,'fixtureOnly':True}}
    completion={'state':'completed','shutdown_ok':True,'acquiredAt':now(),'folders':[str(native)],
        'files':[str(native/'raw_scan.npz')],'analysis':analysis}
    sampling_receipt(folder,plan,completion);_dump(folder/'completion.json',completion)
    canonical={k:v for k,v in request.items() if k!='outputDir'}
    _dump(folder/'adapter_intent.json',{'operationId':request['operationId'],'requestHash':_digest(canonical),
        'request':canonical,'profileHash':_digest({**adapter.profile,'enabled':True})})
    result=adapter._result(folder,plan,request,completion)
    _dump(folder/'adapter_receipt.json',{'status':'completed','operationId':request['operationId'],
        'requestHash':_digest(canonical),'result':deepcopy(result)})
    return result
