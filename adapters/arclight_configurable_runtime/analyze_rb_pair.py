"""Offline paired native RB/IRB analysis with immutable source evidence.

No hardware calls. Refuse existing analysis outputs, incomplete acquisition,
changed fresh PRE calibration, mismatched pulse/route/timing, or unverified shots.
The scientific model is unchanged from paired_rb_support.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).absolute().parent))
from bootstrap import initialize
ROOT = initialize()
import argparse
from datetime import datetime
import hashlib
import json
import numpy as np
from paired_rb_support import analyze_paired_rb
from readout_reference_support import _safe, _load_run
from analyze_historical_allxy import verify_real_blocks
from prepare_rb_from_fresh_reference import assert_conditions, load_reference
from export_paired_lda_reference import build_reference

SOURCE_NAMES = ('analyze_rb_pair.py', 'paired_rb_support.py', 'validated_short_half_compat.py',
                'readout_reference_support.py', 'analyze_historical_allxy.py',
                'analyze_single_qubit.py', 'analyze_allxy.py',
                'prepare_rb_from_fresh_reference.py', 'export_paired_lda_reference.py',
                'bootstrap.py', 'work/server/core/circuit/clifford.py')
OUTPUT_NAMES = ('paired_rb_result.json', 'report_paired_rb.png', 'paired_rb_analysis_sources')

def read(path): return json.loads(_safe(path).read_text(encoding='utf-8-sig'))
def sha(path): return hashlib.sha256(_safe(path).read_bytes()).hexdigest()

def require_new(folder):
    for name in OUTPUT_NAMES:
        if _safe(folder/name, exists=False).exists():
            raise FileExistsError('Existing analysis is immutable; refusing overwrite: '+str(folder/name))

def load(folder):
    folder = _safe(folder)
    if not folder.is_relative_to(ROOT/'results'): raise ValueError('Task results only')
    axes, iq, job, hashes = _load_run(folder)
    status = read(folder/'status.json')
    if status.get('shutdown_ok') is not True or status.get('hardware_connected') is not False:
        raise ValueError('Require successful acquisition shutdown')
    if iq.ndim != 3 or iq.shape[1] != 1 or len(iq) != len(axes) or not np.isfinite(iq).all():
        raise ValueError('Finite raw IQ [points,1,shots] required')
    blocks, proof = verify_real_blocks(folder, iq, job)
    if proof['exact_duplicate_blocks']: raise ValueError('Exactly duplicated acquisition blocks require investigation')
    manifest = read(folder/'sequence_manifest.json')
    hashes['sequence_manifest.json'] = sha(folder/'sequence_manifest.json')
    for index in range(len(axes)):
        hashes[f'point_{index:04d}.npz'] = sha(folder/f'point_{index:04d}.npz')
    hashes.update(proof['block_files_and_manifest_sha256'])
    return dict(folder=folder, axes=axes, iq=iq, job=job, status=status,
                sequence_manifest=manifest, block_proof=proof, hashes=hashes,
                source={'folder':str(folder), 'raw_sha256':hashes['raw_scan.npz'], 'input_sha256':hashes})

def validate_pair(ref, irb):
    if ref['folder'] == irb['folder']: raise ValueError('Two separately acquired runs required')
    target = ref['job']['target']
    if target not in ('Q11','Q18'): raise ValueError('Unsupported campaign target')
    assert_conditions(ref['job'], irb['job'])
    for key in ('spec', 'calibration', 'paired_rb_projection', 'captures',
                'acquisitions_per_point', 'randomize', 'order_seed'):
        if ref['job'].get(key) != irb['job'].get(key): raise ValueError('Unpaired '+key)
    for data, kind, mode in ((ref,'reference','rb'), (irb,'interleaved','irb')):
        job = data['job']; meta = job['spec']['meta']; manifest = data['sequence_manifest']
        if (job['name'] != f'{target}_historical_rb_{kind}' or job['kind'] != 'rb_'+kind
            or not data['folder'].name.endswith('_live_'+job['name'])):
            raise ValueError('Require designated historical paired RB campaign runs')
        if str(meta['type']) != '3' or manifest['mode'] != mode:
            raise ValueError('Native RB experiment/mode mismatch')
        if ((kind == 'reference' and job.get('interleaved_gate')) or
            (kind == 'interleaved' and job.get('interleaved_gate') != 'X/2')):
            raise ValueError('Require reference RB and X/2 IRB')
        if manifest['run_seed'] != meta['run_seed'] or manifest['targets'] != [meta['qubit']]:
            raise ValueError('Sequence seed/target differs from acquired spec')
        if manifest.get('pulse_policy') != meta.get('pulse_compatibility'):
            raise ValueError('Sequence pulse policy differs from acquired spec')
        native = _safe(manifest['native_source'])
        if sha(native) != manifest['native_source_sha256']:
            raise ValueError('Native sequence source changed since acquisition')
        sweeps = [s for s in job['spec']['sweeps'] if s.get('bindings') == 'clifford_m']
        if len(sweeps) != 1: raise ValueError('Expected one Clifford length sweep')
        expected = {(c,m,k) for c in range(job['captures']) for m in sweeps[0]['values']
                    for k in range(meta['repeat'])}
        actual = [(a['capture'],a['axes']['clifford_m'],a['axes']['repeat']) for a in data['axes']]
        if len(actual) != len(expected) or set(actual) != expected:
            raise ValueError('Acquired RB coverage differs from plan')
    if datetime.fromisoformat(ref['status']['ended']) > datetime.fromisoformat(irb['status']['started']):
        raise ValueError('Expected nonoverlapping reference RB followed by IRB')

def validate_fresh_reference(ref, irb):
    projection = ref['job']['paired_rb_projection']; fresh = projection.get('fresh_source', {})
    if fresh.get('role') != 'fresh_pre_before_RB': raise ValueError('Fresh PRE reference provenance required')
    folder = _safe(fresh['folder'])
    axes, iq, job, hashes, blocks, proof = load_reference(folder, role='pre')
    if folder in (ref['folder'],irb['folder']): raise ValueError('Reference must be independent of RB data')
    for data in (ref,irb):
        assert_conditions(job, data['job'])
        if datetime.fromisoformat(read(folder/'status.json')['ended']) >= datetime.fromisoformat(data['status']['started']):
            raise ValueError('Independent PRE must be completed before RB acquisition')
    required = {'reference_job_sha256':'job.json', 'raw_scan_sha256':'raw_scan.npz',
                'axes_sha256':'axes.json', 'frozen_reference_sha256':'independent_projection.json'}
    for field, name in required.items():
        if fresh[field] != sha(folder/name): raise ValueError('Fresh reference source hash differs: '+name)
    export_path = _safe(fresh['paired_reference_export'])
    if export_path != folder/'paired_rb_projection_export.json' or sha(export_path) != fresh['paired_reference_export_sha256']:
        raise ValueError('Fresh paired projection export hash/path mismatch')
    exported = read(export_path)
    rebuilt = build_reference(folder)
    if exported['paired_rb_projection'] != rebuilt['paired_rb_projection']:
        raise ValueError('Export does not match independently frozen training centroids/direction')
    if projection != {**exported['paired_rb_projection'], 'fresh_source':fresh}:
        raise ValueError('Acquired paired projection differs from PRE export')
    calibration = ref['job']['calibration']
    if (calibration.get('projection') != read(folder/'independent_projection.json')['projection']
        or calibration.get('fresh_source') != fresh):
        raise ValueError('Acquired LDA calibration differs from fresh PRE')
    for name in ('independent_projection.json', 'paired_rb_projection_export.json'):
        hashes[name] = sha(folder/name)
    hashes.update(proof['block_files_and_manifest_sha256'])
    return {'folder':str(folder), 'input_sha256':hashes, 'real_block_verification':proof,
            'fixed_pre_direction_and_training_centroids_preserved':True,
            'frozen_before_RB':True, 'RB_data_used_to_train_reference':False}

def plot_diagnostic(result, output):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    import textwrap
    fig = Figure(figsize=(12,8),dpi=160); FigureCanvasAgg(fig); ax=fig.add_subplot(111)
    x=np.asarray(result['lengths'])
    if 'curves' in result:
        for label,key,color in [('Reference RB','reference','#1765ad'),('X/2 IRB','interleaved','#d24a36')]:
            ax.errorbar(x,result['curves'][key+'_mean'],yerr=result['curves'][key+'_seed_sem'],
                        fmt='o',capsize=3,color=color,label=label+' (seed mean ± SEM)')
            fit=result.get(key+'_fit')
            if fit and all(k in fit for k in ('A','p','B')):
                xx=np.linspace(x.min(),x.max(),500)
                ax.plot(xx,fit['A']*fit['p']**xx+fit['B'],color=color,
                        linestyle='-' if fit['identifiable'] else '--')
    ax.set(xlabel='Number of random Cliffords',ylabel='Prepared-reference IQ scale (ground = 1)',
           title=result['target']+' | Reference RB and X/2 IRB')
    ax.grid(alpha=.16); ax.legend()
    note='Conditional single-exponential estimate; paired seed bootstrap excludes calibration uncertainty, drift and model systematic error. POST drift audit is separate.'
    if result['reportable']:
        note='F(X/2) = %.5f; F(Clifford) = %.5f. '%(result['F_X_half_conditional'],result['F_clifford'])+note
    else: note='Estimate not validated: '+', '.join(result['reasons'])+'. '+note
    fig.text(.08,.035,'\n'.join(textwrap.wrap(note,115)),fontsize=9)
    fig.tight_layout(rect=(0,.13,1,1)); fig.savefig(output)

def analyze(reference, interleaved, *, bootstrap_samples=1000):
    reference=_safe(reference); interleaved=_safe(interleaved); require_new(reference)
    sources={name:_safe(ROOT/name).read_bytes() for name in SOURCE_NAMES}
    ref,irb=load(reference),load(interleaved); validate_pair(ref,irb)
    pre=validate_fresh_reference(ref,irb)
    result=analyze_paired_rb(ref['axes'],ref['iq'],irb['axes'],irb['iq'],
        ref_manifest=ref['sequence_manifest'],irb_manifest=irb['sequence_manifest'],
        projection=ref['job']['paired_rb_projection'],bootstrap_samples=bootstrap_samples)
    result.update(target=ref['job']['target'],source_runs={'reference':ref['source'],'interleaved':irb['source']},
        real_acquisition_block_verification={'reference':ref['block_proof'],'interleaved':irb['block_proof']},
        fresh_pre_reference_verification=pre,analysis_source_sha256={k:hashlib.sha256(v).hexdigest() for k,v in sources.items()},
        analysis_entry_sha256=hashlib.sha256(sources['analyze_rb_pair.py']).hexdigest(),
        temporal_drift_audit={'status':'separate_POST_audit_required','included_in_bootstrap':False,
            'reportable_scope':'Conditional statistical model verdict only; not stationarity verification'},
        raw_points_clipped=False,calibration_retrained_from_RB=False,
        uncertainty_scope='Seed bootstrap conditional on fixed PRE calibration and acquired captures. Acquisition blocks are genuine shots, not independent outer captures or Clifford seeds.')
    # Freeze exact source bytes used by this invocation and reject source mutation.
    for data in (ref,irb):
        if any(sha(data['folder']/name)!=digest for name,digest in data['hashes'].items()):
            raise RuntimeError('Acquisition source changed during analysis')
    if any(sha(Path(pre['folder'])/name)!=digest for name,digest in pre['input_sha256'].items()):
        raise RuntimeError('Fresh reference source changed during analysis')
    if any((ROOT/name).read_bytes()!=value for name,value in sources.items()):
        raise RuntimeError('Analysis source changed during computation')
    require_new(reference)
    snapshots=reference/'paired_rb_analysis_sources'; snapshots.mkdir()
    for name,value in sources.items():
        output=snapshots/name;output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(value)
    out=reference/'paired_rb_result.json'
    with out.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    plot_diagnostic(result,reference/'report_paired_rb.png')
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('reference',type=Path);p.add_argument('interleaved',type=Path)
    p.add_argument('--bootstrap-samples',type=int,default=1000);a=p.parse_args()
    result=analyze(a.reference,a.interleaved,bootstrap_samples=a.bootstrap_samples)
    print(json.dumps({'output':str(a.reference/'paired_rb_result.json'),'reportable':result['reportable'],
        'reasons':result['reasons'],'F_clifford':result['F_clifford'],
        'F_X_half_conditional':result['F_X_half_conditional']},ensure_ascii=False))

if __name__=='__main__': main()
