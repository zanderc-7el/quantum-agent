"""Capacity fixture writer: real native waveform archives, explicitly fixture ADC.

Call write_fixture only in the guarded native test interpreter. Nothing in this
module creates a measurement record, accepted source, hardware lease or device.
"""
from pathlib import Path
import json
import shutil
from copy import deepcopy

def write_fixture(root,output):
    import numpy as np
    from adapters.science_v3.compiler import compile_row,archive_row
    from adapters.science_v3.plans import build_rows
    from adapters.science_v3_adapter import ScienceV3Adapter,build_science_profile
    from adapters.science_runtime.policy import digest,sha
    root=Path(root);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    base=json.loads((root/'adapters/arclight_configurable_runtime/base_config.json').read_text(encoding='utf-8'))
    profile=build_science_profile(root,json.loads((root/'adapters/arclight_profile.json').read_text(encoding='utf-8')))
    adapter=ScienceV3Adapter(root,profile);rng=np.random.default_rng(261126);summary=[]
    pulse={'drive_hz':4.86e9,'drive_amp':.3,'drive_length_s':20e-9,'readout_hz':7.01e9,'readout_amp':.4,
        'readout_width_s':2e-6,'xy_delay_s':100e-9,'xy_filter_mhz':4700,'xy_attenuation_db':3.75,
        'pulse_shape':'Gaussian','pulse_length_s':20e-9,'pulse_sigma_s':4e-9,'shape_pi_amp':.3,'anharmonicity_hz':-200e6}
    for method in ('sq_shape_rabi_amplitude','sq_shape_half_verify','sq_drag_scan'):
        p,rows=build_rows(method,pulse);cache={}
        for stage in ('measure','verify'):
            operation=method+'/'+stage;folder=output/method/stage;results=folder/'results';results.mkdir(parents=True)
            params=deepcopy(p)
            if stage=='verify':
                key={'sq_shape_rabi_amplitude':'shape_pi_amp','sq_drag_scan':'drag_lambda1'}.get(method)
                if key:params['verify_'+key]=.3 if key=='shape_pi_amp' else .2
            params,actual_rows=build_rows(method,params);compiled=[];raw_i=[];raw_q=[]
            for row in actual_rows:
                path=results/f'preflight_{row["id"]:04d}.npz';key=digest(row['gates'])
                if key in cache:
                    previous=cache[key];shutil.copyfile(previous['file'],path);item={**deepcopy(previous),'file':str(path)}
                else:
                    seq,expected=compile_row(row,base,params);item=archive_row(path,seq,expected);cache[key]=deepcopy(item);del seq
                item['rowId']=row['id'];compiled.append(item)
                # Explicit test-only samples, never promoted to physical evidence.
                i=rng.normal(0,.05,(1,params['shots']));q=rng.normal(0,.05,i.shape);raw_i.append(i);raw_q.append(q)
                block=results/f'point_{row["id"]:04d}_block_000.npz'
                np.savez_compressed(block,i=i,q=q,measured=np.array([1]),fixture_only=np.array([True]))
                np.savez_compressed(results/f'point_{row["id"]:04d}.npz',i=i,q=q,acquisition_block_index=np.zeros(params['shots'],dtype=int))
                (results/f'point_{row["id"]:04d}_acquisition_blocks.json').write_text(json.dumps({'fixtureOnly':True,'blocks_completed':1,'blocks':[{'path':str(block),'sha256':sha(block)}]}),encoding='utf-8')
            np.savez_compressed(results/'raw_scan.npz',i=np.stack(raw_i),q=np.stack(raw_q),fixture_only=np.array([True]))
            docs={'axes.json':actual_rows,'parameters.json':params,'compiled_plan.json':compiled,
                  'instrument.json':{'fixtureOnly':True,'hardwareConnected':False},'status.json':{'fixtureOnly':True,'state':'fixture'},
                  'analysis.json':{'fixtureOnly':True,'accepted':False}}
            for name,value in docs.items():(results/name).write_text(json.dumps(value),encoding='utf-8')
            (results/'uploads.jsonl').write_text(json.dumps({'fixtureOnly':True,'uploadCommandsCompleted':False})+'\n',encoding='utf-8')
            (folder/'plan.json').write_text(json.dumps({'fixtureOnly':True,'operationId':operation,'parameters':params,'rows':actual_rows}),encoding='utf-8')
            artifacts=adapter._artifacts(folder,[str(path) for path in results.iterdir()]+[str(folder/'plan.json')])
            (folder/'artifact_fixture.json').write_text(json.dumps({'schema':'science-artifact-capacity-fixture/1','dataKind':'fixture','physicalAccepted':False,'artifacts':artifacts}),encoding='utf-8')
            summary.append({'method':method,'stage':stage,'rows':len(actual_rows),'artifactCount':len(artifacts),
                'uniqueIds':len({a['id'] for a in artifacts}),'metadataBytes':(folder/'artifact_fixture.json').stat().st_size,
                'folder':str(folder),'nativeCompiledUniqueGateLists':len(cache)})
    (output/'summary.json').write_text(json.dumps({'fixtureOnly':True,'hardwareConnected':False,'operations':summary},indent=2),encoding='utf-8')
    return summary
