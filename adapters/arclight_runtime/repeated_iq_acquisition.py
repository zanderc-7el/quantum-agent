"""Repeat actual ADC acquisitions at one native sequence point without reloading AWGs.

Import after the local bootstrap. The first block uses SequenceRunner unchanged.
Every extra block is a new uncached arm -> finite trigger -> read transaction.
No retries, no reuse of old samples, no averaging disguised as additional shots.
The caller must retain its existing exclusive instrument lock and finally cleanup.
"""
from pathlib import Path
import datetime as dt
import hashlib
import json
import os
import stat
import time
import numpy as np
from importlib import import_module
native=import_module('core.experiment.runner')

ROOT=Path(__import__('os').environ.get('QCAL_ARCLIGHT_RUN_DIR',str(Path(__file__).absolute().parent))).absolute()


def _count(value,name,maximum):
    if isinstance(value,(bool,np.bool_)) or not isinstance(value,(int,np.integer)) or not 1<=value<=maximum:
        raise ValueError(f'{name} must be an integer in 1..{maximum}')
    return int(value)


def _folder(path):
    path=Path(os.path.abspath(path))
    if not path.is_relative_to(ROOT): raise ValueError('Block data must remain in this isolated task root')
    chain=[ROOT]
    for part in path.relative_to(ROOT).parts: chain.append(chain[-1]/part)
    for p in chain:
        info=p.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):
            raise ValueError('Linked acquisition paths are forbidden')
    if not path.is_dir(): raise ValueError('Acquisition block directory must already exist')
    return path


def _json(path,value):
    temporary=path.with_name(path.name+'.partial')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    os.replace(temporary,path)


def _validate(raw,shots,measured):
    if not isinstance(raw,(list,tuple)) or len(raw)!=3:
        raise ValueError('Native IQ acquisition must return [I,Q,measure_list]')
    i,q=np.asarray(raw[0]),np.asarray(raw[1])
    if i.shape!=(len(measured),shots) or q.shape!=i.shape:
        raise ValueError(f'Invalid acquisition block shape {i.shape}/{q.shape}; expected {(len(measured),shots)}')
    if any(a.dtype.kind not in 'fiu' or not np.isfinite(a).all() for a in (i,q)):
        raise ValueError('Acquisition block I/Q must be finite real numeric arrays')
    if list(raw[2])!=measured:
        raise ValueError('Measured-qubit order changed across acquisition blocks')
    # Own each block even if a future driver implementation returns a reused buffer.
    return i.copy(),q.copy()


def _uncached_arm(device):
    specs=getattr(device,'_param_specs_inst',None) or getattr(device,'__param_specs__',{})
    spec=specs.get('start_cap')
    if spec is None or getattr(spec,'use_cache',True):
        raise RuntimeError('Repeated IQ requires verified use_cache=False for start_cap')


def get_repeated_raw_data(runner,seq,shots,meta,*,acquisitions_per_point=4,
                          output_folder,point_index,on_block=None,return_block_index=False):
    """Return native [I,Q,measure_list], with shot axis concatenated across new reads.

    on_block(metadata) runs after that block's NPZ and point manifest are saved.
    Metadata includes block_index, blocks_completed, effective_shots_completed,
    shots_in_block, and block_file. Callback exceptions propagate for entry cleanup.
    Current task deliberately supports one measured qubit / one RO->AD path only.
    With return_block_index=True, return (native_raw, acquisition_block_index),
    where the latter is a shot-axis vector that the entry saves in point/raw NPZ.
    """
    shots=_count(shots,'shots per finite train',2047)
    count=_count(acquisitions_per_point,'acquisitions_per_point',16)
    if isinstance(point_index,(bool,np.bool_)) or not isinstance(point_index,(int,np.integer)) or point_index<0:
        raise ValueError('point_index must be a nonnegative integer')
    point_index=int(point_index)
    if meta.get(native.MetaKeySchema.readout_mode,'IQ')!='IQ':
        raise ValueError('Repeated acquisition supports native IQ mode only')
    measured=list(seq.measure_list)
    if len(measured)!=1: raise ValueError('This repeated-acquisition path supports exactly one measured qubit')
    folder=_folder(output_folder)
    manifest_path=folder/f'point_{point_index:04d}_acquisition_blocks.json'
    block_paths=[folder/f'point_{point_index:04d}_block_{b:03d}.npz' for b in range(count)]
    if manifest_path.exists() or any(p.exists() for p in block_paths):
        raise FileExistsError('Point block output already exists; refusing to mix or overwrite acquisitions')
    cfg=seq.config; group=meta.get(native.MetaKeySchema.group,'default')
    ad,ch=native.get_dev_and_ch(measured[0],cfg,type=native.ChannelTypeSchema.ad,group=group)
    _uncached_arm(ad)
    sync=native.get_hardware(meta.get(native.MetaKeySchema.sync_inst,'SYNC'),group=group)
    period=cfg.global_period()
    if not np.isfinite(period) or period<100e-9: raise ValueError('Invalid finite-train trigger period')
    delay=meta.get(native.MetaKeySchema.time_to_read,0)
    if not isinstance(delay,(int,float)) or not np.isfinite(delay) or delay<0: raise ValueError('Invalid read wait')
    manifest={'schema':'arclight_actual_repeated_iq_v1','point_index':point_index,
        'acquisitions_per_point':count,'shots_per_acquisition':shots,
        'effective_shots_expected':count*shots,'effective_shots_completed':0,
        'blocks_completed':0,'state':'collecting','measure_list':measured,
        'period_s':float(period),'blocks':[],
        'method':'Native full setup/acquisition first, then uncached arm-trigger-read; no software averaging or sample reuse',
        'cleanup_owner':'Caller entry finally block',
        'helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    _json(manifest_path,manifest)
    blocks=[]
    try:
        for block in range(count):
            started=dt.datetime.now(dt.timezone.utc).isoformat(); start=time.monotonic()
            if block==0:
                # Explicit base method avoids recursion through the entry Recorder override.
                raw=native.SequenceRunner.get_raw_data(runner,seq,shots,meta)
                active=[w for w in seq.waves._waves.values()
                        if w.name.startswith(native.ChannelTypeSchema.ro) and w.use]
                if len(active)!=1 or list(active[0].qids)!=measured:
                    raise ValueError('Expected exactly one active native readout wave matching measure_list')
            else:
                _uncached_arm(ad)
                ad.set_start_cap(0,ch=ch)  # StartCapture is an uncached command, not a Boolean OFF.
                sync.set_trigger(shots,period=period)
                if delay>0: time.sleep(delay)
                channel_data=ad.read('IQ',ch=ch)
                if channel_data is None:
                    raise RuntimeError('Additional ADC block returned no IQ data')
                i,q=native.get_IQ_data([(channel_data,measured)],shots,measured)
                raw=[i,q,measured]
            i,q=_validate(raw,shots,measured)
            path=block_paths[block]; temporary=path.with_name(path.name+'.partial')
            with temporary.open('wb') as stream:
                np.savez_compressed(stream,i=i,q=q,measured=np.asarray(measured),
                    point_index=point_index,block_index=block,shots=shots,
                    acquisition_block_index=np.full(shots,block,dtype=np.int16))
            os.replace(temporary,path)
            digest=hashlib.sha256()
            for array in (i,q):
                digest.update(array.dtype.str.encode('ascii'))
                digest.update(json.dumps(list(array.shape)).encode('ascii'))
                digest.update(array.tobytes(order='C'))
            duplicates=[b for b,(previous_i,previous_q) in enumerate(blocks)
                        if np.array_equal(i,previous_i) and np.array_equal(q,previous_q)]
            record={'point_index':point_index,'block_index':block,'block_file':path.name,
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'raw_iq_sha256':digest.hexdigest(),'exact_duplicate_of_blocks':duplicates,
                'started_utc':started,'duration_s':time.monotonic()-start,
                'acquisition_path':'native_full_setup' if block==0 else 'uncached_rearm_retrigger_read',
                'shots_in_block':shots,'blocks_completed':block+1,
                'effective_shots_completed':(block+1)*shots,
                'mean_i':float(i.mean()),'mean_q':float(q.mean()),'shape':list(i.shape)}
            blocks.append((i,q)); manifest['blocks'].append(record)
            manifest.update(blocks_completed=block+1,effective_shots_completed=(block+1)*shots)
            _json(manifest_path,manifest)
            if on_block is not None: on_block(dict(record))
        all_i=np.concatenate([b[0] for b in blocks],axis=1)
        all_q=np.concatenate([b[1] for b in blocks],axis=1)
        manifest.update(state='complete',concatenated_shape=list(all_i.shape))
        manifest['exact_duplicate_blocks_detected']=any(r['exact_duplicate_of_blocks'] for r in manifest['blocks'])
        manifest['block_identity_note']='File SHA alone does not prove distinct IQ; raw_iq_sha256 and exact array comparisons exclude block metadata. Identical blocks need physical review, not an automatic claim of fresh noise.'
        _json(manifest_path,manifest)
        raw=[all_i,all_q,measured]
        block_index=np.repeat(np.arange(count,dtype=np.int16),shots)
        return (raw,block_index) if return_block_index else raw
    except BaseException as exc:
        manifest.update(state='failed',error=f'{type(exc).__name__}: {exc}')
        try:
            _json(manifest_path,manifest)
        except Exception as write_error:
            exc.add_note('Failed to persist acquisition failure manifest: '+str(write_error))
        raise
