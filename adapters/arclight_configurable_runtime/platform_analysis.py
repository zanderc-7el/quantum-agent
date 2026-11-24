"""Translate measured ArcLight IQ into platform evidence; never imports hardware.

All plots retain raw point means. Fits are the audited native analysis, not
replacement data. Neither a fitted oscillation nor AllXY is gate fidelity.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent


def native_module(name):
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    key = '_qcal_arclight_analysis_' + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, HERE / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def clean(value):
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _load(folder):
    folder = Path(folder).resolve()
    state = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
    if state.get('mode') != 'real_hardware' or state.get('state') != 'acquired':
        raise ValueError('Analysis requires a complete real hardware acquisition receipt')
    records = json.loads((folder / 'axes.json').read_text(encoding='utf-8'))
    with np.load(folder / 'raw_scan.npz', allow_pickle=False) as raw:
        i, q = raw['i'].copy(), raw['q'].copy()
        blocks = raw['acquisition_block_index'].copy() if 'acquisition_block_index' in raw else None
    analysis = native_module('analyze_single_qubit')
    iq = analysis.validate_arrays(records, i, q)
    job = json.loads((folder/'job.json').read_text(encoding='utf-8'))
    acquisition_count = job.get('acquisitions_per_point', 1)
    normalized_blocks = analysis.normalized_acquisition_blocks(blocks, iq.shape, acquisition_count)
    if any(state.get(k, len(records)) != len(records) for k in ('points_completed', 'points_expected')):
        raise ValueError('Acquisition point count and raw data disagree')
    # Point receipts provide an independent check against accidental aggregate reuse.
    for index in range(len(records)):
        with np.load(folder / f'point_{index:04d}.npz', allow_pickle=False) as point:
            if not np.array_equal(point['i'], i[index]) or not np.array_equal(point['q'], q[index]):
                raise ValueError(f'Raw aggregate differs from point {index}')
            if acquisition_count > 1:
                if 'acquisition_block_index' not in point:
                    raise ValueError(f'Acquisition block provenance missing from point {index}')
                per_point = analysis.normalized_acquisition_blocks(
                    point['acquisition_block_index'], (1, iq.shape[1]), acquisition_count)
                if not np.array_equal(per_point[0], normalized_blocks[index]):
                    raise ValueError(f'Acquisition blocks differ from point {index}')
    return records, i, q, iq, blocks


def _prediction(fit, x):
    z = (x-fit['x_origin'])/fit['x_span']
    p = fit['fit_parameters_normalized_x']
    if fit['model'] == 'exponential':
        return p[0] + p[1]*np.exp(-z/p[2])
    envelope = np.exp(-z/p[5]) if fit['model'] == 'damped_sinusoid' else 1
    return p[0]+p[1]*z+p[2]*envelope*np.cos(2*np.pi*p[3]*z+p[4])


def _fit_metrics(native, views, job, procedure):
    metrics, candidates, reasons, summaries = {}, {}, [], []
    for group, view in zip(native['groups'], views['plots']):
        if not group.get('repeat_validated'):
            reasons.append(group.get('reason', 'Native fits did not agree across acquired captures'))
        for repeat, block in zip(group.get('repeats', []), view['blocks']):
            fit = repeat.get('fit')
            if fit is None:
                reasons.extend(repeat.get('diagnostics', {}).get('rejection_reasons', ['No accepted native fit']))
                continue
            x = np.asarray([float(views['axes'][int(k)][native['x_axis']['native_key']]) for k in block])
            x *= native['x_axis'].get('echo_multiplier', 1)
            y = views['projection'][block]
            predicted = _prediction(fit, x)
            sst = float(np.sum((y-y.mean())**2))
            r2 = 1-float(np.sum((y-predicted)**2))/max(sst, 1e-30)
            sigma = views['projection_sem'][block]
            noise = max(float(np.median(sigma)), fit['residual_rms'], 1e-30)
            summaries.append({'fit': fit, 'r_squared': r2,
                'contrast_snr': float(np.ptp(predicted))/noise,
                'relative_stderr': fit['standard_error_model']/max(abs(fit['value']), 1e-30)})
    if not summaries:
        return metrics, candidates, reasons or ['No accepted fits']
    metrics.update(r_squared=min(s['r_squared'] for s in summaries),
                   contrast_snr=min(s['contrast_snr'] for s in summaries),
                   relative_stderr=max(s['relative_stderr'] for s in summaries))
    fits = [s['fit'] for s in summaries]
    if job['kind'].startswith('rabi'):
        metrics['cycles'] = min(f['cycles_in_window'] for f in fits)
        frequency = float(np.mean([f['value'] for f in fits]))
        if job['kind'] == 'rabi_time':
            metrics['rabi_frequency_hz'] = frequency
            candidates['drive_length_s'] = 1/(2*frequency)
        else:
            # n repeated X gates give n times the single-gate amplitude rotation.
            n = job.get('gate_repeats', 1)
            candidates['drive_amp'] = n/(2*frequency)
            metrics['amplitude_oscillation_frequency'] = frequency
        if reasons:
            candidates.clear()
    elif procedure == 'sq_t1':
        metrics['t1_s'] = float(np.mean([f['value'] for f in fits]))
    else:
        decay = [f.get('decay_time') for f in fits]
        if all(v is not None and v > 0 for v in decay):
            metrics['t2_s'] = float(np.mean(decay))
            uncertainty = []
            for f in fits:
                if f.get('decay_time_ci95_model'):
                    lo, hi = f['decay_time_ci95_model']
                    uncertainty.append((hi-lo)/(3.92*f['decay_time']))
                elif f['model'] == 'exponential':
                    uncertainty.append(f['standard_error_model']/f['value'])
                else:
                    reasons.append('Decay uncertainty not resolved')
            if uncertainty:
                metrics['relative_stderr'] = max(uncertainty)
        else:
            reasons.append('Coherence decay not resolved by the native fit')
    return metrics, candidates, reasons


def _readout(native, views, job, folder):
    candidates = {}
    groups = native.get('groups', [])
    if not groups:
        return {}, candidates, ['No prepared-label groups'], None
    best = max(groups, key=lambda g: g['classification']['assignment_accuracy_mean'])
    c = best['classification']
    # This metric explicitly includes preparation errors; it is not pure-state fidelity.
    metrics = {'assignment_fidelity': c['assignment_accuracy_mean'],
               'prepared_label_accuracy_0': c['P_assign0_given_prep0'],
               'prepared_label_accuracy_1': c['P_assign1_given_prep1'],
               'readout_snr_dprime': c['snr_dprime']}
    for key, value in best['group_axes'].items():
        if key.endswith('.para.probFreq'):
            candidates['readout_hz'] = value
        elif key.endswith('.para.probCarryScale'):
            candidates['readout_amp'] = value
    indices = [n for n, axis in enumerate(views['axes']) if all(axis.get(k) == v for k,v in best['group_axes'].items())]
    means = []
    for labels in ({'ground','off','r0_base'}, {'excited','on','r1_base'}):
        values = np.concatenate([views['iq'][n] for n in indices if views['axes'][n].get('exp.cir.default') in labels])
        # Exactly the native training half; held-out samples never train the reference.
        values = values[::2]
        means.append([float(values.real.mean()), float(values.imag.mean())])
    p = job.get('parameters', {})
    reference = {'direction': c['direction'], 'origin': means[0], 'ground_iq': means[0],
                 'excited_iq': means[1], 'threshold': c['threshold'],
                 'frequency_hz': candidates.get('readout_hz', p.get('readout_hz')),
                 'amplitude': candidates.get('readout_amp', p.get('readout_amp')),
                 'source': str(folder/'platform_analysis.json'), 'independent_reference': True,
                 'meaning': 'Prepared-label training reference; independent only of subsequent acquisitions'}
    return metrics, candidates, [], reference


def _cavity(records, iq, blocks, job):
    module = native_module('analyze_single_qubit')
    axes = module.normalize_axes(records)
    freq_key = module.axis_key(axes, '.para.probFreq')
    x = np.asarray([a[freq_key] for a in axes], float)
    mean = iq.mean(axis=1)
    sem, _ = module.projected_standard_error(iq, job.get('acquisitions_per_point',1), blocks)
    groups = module.split_groups(axes, {freq_key,'exp.cir.default'})
    traces, reasons, snrs, centers, powers = [], [], [], [], []
    for g in groups:
        unique, captures = module.repeat_blocks(x, g['indices'], axes)
        candidates = []
        for capture, idx in enumerate(captures):
            y = np.abs(mean[idx]); edge = max(2,len(y)//8)
            baseline = np.linspace(np.median(y[:edge]),np.median(y[-edge:]),len(y))
            depth = baseline-y
            peak = int(np.argmax(depth))
            rough = np.median(np.abs(np.diff(y, n=2)))/1.65 if len(y)>3 else 0
            noise = max(float(np.median(sem[idx])), float(rough),1e-30)
            snr = max(0,float(depth[peak]))/noise
            snrs.append(snr)
            if not 0<peak<len(y)-1 or snr<5:
                reasons.append('Cavity dip absent, on scan boundary, or below noise criterion')
            candidates.append(float(unique[peak]))
            traces.append({'label':f'capture {capture+1} {g["axes"]}', 'x':unique,'y':y,'sem':sem[idx]})
        if len(candidates)<2 or np.ptp(candidates)>max(5e4,2*float(np.median(np.diff(unique)))):
            reasons.append('Cavity position does not repeat within two frequency steps')
        centers.append(float(np.mean(candidates)))
        powers.append(next((v for k,v in g['axes'].items() if k.endswith('.para.probCarryScale')),None))
    metrics = {'contrast_snr':min(snrs) if snrs else 0, 'scan_coverage':1.0,
               'resolved_power_points':sum(s>=5 for s in snrs)}
    candidates = {'readout_hz':centers[0]} if job['kind']=='s21' and centers and not reasons else {}
    analysis = {'kind':job['kind'],'repeat_centers_hz':centers,'powers_waveform_scale':powers,
                'method':'Raw |mean IQ| dip against edge background; no smoothing',
                'power_axis_unit':'waveform scale (a.u.), not dBm',
                'accepted':not reasons,'reasons':sorted(set(reasons))}
    return metrics,candidates,analysis,traces


def analyze_job(folder: Path, job: dict, procedure_id: str):
    folder = Path(folder).resolve()
    records,i,q,iq,blocks = _load(folder)
    module = native_module('analyze_single_qubit')
    reasons, candidates, metrics, traces, files = [], {}, {}, [], []
    if job['kind'] in ('s21','s21_power'):
        metrics,candidates,analysis,traces = _cavity(records,iq,blocks,job)
    elif job['kind']=='allxy':
        allxy = native_module('analyze_allxy')
        analysis, views = allxy.analyze_arrays(records,iq,job,acquisition_block_index=blocks)
        metrics = {'allxy_rms':analysis['rms_deviation_from_ideal']}
        traces = [{'label':'measured prepared-reference IQ', 'x':np.arange(21), 'y':views['aggregate_mean'], 'sem':views['aggregate_sem']},
                  {'label':'ideal reference','x':np.arange(21),'y':views['ideal']}]
        analysis.update(accepted=True,reasons=[])
    else:
        analysis, views = module.analyze_arrays(records,i,q,job,blocks)
        if job['kind']=='spectrum':
            groups = analysis['groups']
            repeats = [r for g in groups for r in g.get('repeats',[])]
            metrics['peak_snr'] = min((r['candidate_significance'] for r in repeats),default=0)
            detected = [g.get('candidate_frequency_hz') for g in groups]
            if groups and all(g.get('repeat_validated') for g in groups) and all(v is not None for v in detected):
                candidates['drive_hz'] = float(np.mean(detected))
            else:
                reasons.append('Paired drive response candidate does not repeat away from scan boundaries')
        elif job['kind'] in ('readout','single_shot','singleshot'):
            metrics,candidates,reasons,reference = _readout(analysis,views,job,folder)
            analysis['selected_reference'] = reference
        else:
            metrics,candidates,reasons = _fit_metrics(analysis,views,job,procedure_id)
        for view in views['plots']:
            for j,idx in enumerate(view['blocks']):
                xkey=analysis.get('x_axis',{}).get('native_key')
                xx=np.asarray([views['axes'][int(n)][xkey] for n in idx],float) if xkey else np.arange(len(idx))
                xx*=analysis.get('x_axis',{}).get('echo_multiplier',1)
                traces.append({'label':f'raw trace {j+1}','x':xx,'y':views['projection'][idx],'sem':views['projection_sem'][idx]})
            for j,curve in enumerate(view['curves']):
                if curve is not None:
                    traces.append({'label':f'native fit {j+1}','x':curve[0],'y':curve[1],'fit':True})
        analysis.update(accepted=not reasons,reasons=sorted(set(reasons)))
    analysis.update(procedureId=procedure_id,raw_sha256=hashlib.sha256((folder/'raw_scan.npz').read_bytes()).hexdigest(),
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    interpretation='Actual acquired IQ; preparation-label accuracy, AllXY, and gate fidelity are separate metrics')
    unit = ('Hz' if job['kind'] in ('s21','s21_power','spectrum') else
            'a.u.' if job['kind'] == 'rabi_amplitude' else
            'index' if job['kind'] in ('readout','allxy') else 's')
    series = [{'name':t['label'],'x':list(map(float,t['x'])),'y':list(map(float,t['y'])),
               'xUnit':unit,'yUnit':'IQ a.u.','fit':bool(t.get('fit'))} for t in traces]
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig=Figure(figsize=(10,6),layout='constrained'); FigureCanvasAgg(fig); ax=fig.subplots()
    for trace in traces:
        if trace.get('fit'):
            ax.plot(trace['x'],trace['y'],'--',lw=1,label=trace['label'])
        else:
            ax.errorbar(trace['x'],trace['y'],yerr=trace.get('sem'),fmt='.',ms=3,lw=.5,label=trace['label'])
    ax.set(title=f'{job.get("target","Q11")} | ArcLight {job["kind"]}',
           xlabel={'Hz':'Frequency (Hz)','s':'Time (s)','a.u.':'Drive waveform scale (a.u.)',
                   'index':'Acquisition / native sequence index'}[unit],
           ylabel='Raw mean IQ magnitude' if job['kind'].startswith('s21') else 'Projected IQ (a.u.)')
    ax.grid(alpha=.2)
    if len(traces)<=12: ax.legend(fontsize=7)
    fig.text(.01,.002,'Raw acquired means; native fits dashed. No synthetic data or smoothing.',fontsize=8)
    plot=folder/'platform_measurement.png'; fig.savefig(plot,dpi=160); files.append(str(plot))
    if job['kind'] in ('readout','single_shot','singleshot'):
        # Native training/held-out partition is preserved in these diagnostics.
        groups=analysis['groups']; best_index=max(range(len(groups)),key=lambda j:groups[j]['classification']['assignment_accuracy_mean'])
        best=groups[best_index]; view=views['plots'][best_index]
        diagram=Figure(figsize=(12,8),layout='constrained');FigureCanvasAgg(diagram); panels=diagram.subplots(2,2)
        for labels,color,label in [({'ground','off','r0_base'},'#2676ba','prepared 0'),({'excited','on','r1_base'},'#dd7b29','prepared 1')]:
            values=np.concatenate([views['iq'][n] for n in view['indices'] if str(views['axes'][n].get('exp.cir.default','')).lower() in labels])
            stride=max(1,int(np.ceil(len(values)/10000)))
            panels[0,0].scatter(values[::stride].real,values[::stride].imag,s=2,alpha=.15,color=color,label=f'{label} (display stride {stride})')
        panels[0,0].set(xlabel='I (a.u.)',ylabel='Q (a.u.)',title='Acquired IQ shots at selected setting');panels[0,0].legend(fontsize=7)
        v0,v1,threshold=view['readout_hist']
        panels[0,1].hist(v0,bins=80,alpha=.5,label='prepared 0 held-out');panels[0,1].hist(v1,bins=80,alpha=.5,label='prepared 1 held-out')
        panels[0,1].axvline(threshold,color='black',ls='--',lw=1,label='training threshold');panels[0,1].legend(fontsize=7)
        panels[0,1].set(xlabel='Fixed LDA projection (a.u.)',ylabel='Shot count',title='Held-out assignment distribution')
        key=next((k for g in groups for k in g['group_axes'] if k.endswith(('.para.probCarryScale','.para.probFreq'))),None)
        xx=np.array([g['group_axes'].get(key,j) for j,g in enumerate(groups)]); order=np.argsort(xx)
        xlabel='Readout frequency (Hz)' if key and key.endswith('.probFreq') else 'Readout waveform scale (a.u.)' if key else 'Setting index'
        for field,label in [('P_assign0_given_prep0','prepared 0'),('P_assign1_given_prep1','prepared 1'),('assignment_accuracy_mean','mean')]:
            yy=np.array([g['classification'][field] for g in groups]);panels[1,0].plot(xx[order],yy[order],'.-',label=label)
        panels[1,0].set(xlabel=xlabel,ylabel='Prepared-label assignment accuracy',ylim=(0,1),title='Held-out label accuracy (includes preparation error)');panels[1,0].legend(fontsize=7)
        yy=np.array([g['classification']['snr_dprime'] for g in groups]);panels[1,1].plot(xx[order],yy[order],'.-')
        panels[1,1].set(xlabel=xlabel,ylabel="d-prime",title='Held-out projection separation')
        for panel in panels.flat:panel.grid(alpha=.2)
        diagram.suptitle(f'{job.get("target","Q11")} | ArcLight readout | mean assignment {best["classification"]["assignment_accuracy_mean"]:.4f}')
        rp=folder/'platform_readout_diagnostics.png';diagram.savefig(rp,dpi=160);files.append(str(rp))
    if job['kind'] == 's21_power':
        normalized=module.normalize_axes(records)
        fk=module.axis_key(normalized,'.para.probFreq')
        pk=module.axis_key(normalized,'.para.probCarryScale')
        frequencies=sorted({a[fk] for a in normalized}); powers=sorted({a[pk] for a in normalized})
        captures=sorted({a.get('repeat_index',0) for a in normalized})
        mean=iq.mean(axis=1)
        for capture in captures:
            matrix=np.full((len(frequencies),len(powers)),np.nan)
            for n,a in enumerate(normalized):
                if a.get('repeat_index',0)==capture:
                    matrix[frequencies.index(a[fk]),powers.index(a[pk])]=abs(mean[n])
            if not np.isfinite(matrix).all():
                raise ValueError('Power map contains missing frequency/power coordinates')
            heat=Figure(figsize=(9,6),layout='constrained');FigureCanvasAgg(heat);hx=heat.subplots()
            mesh=hx.pcolormesh(powers,np.asarray(frequencies)/1e9,matrix,shading='nearest',cmap='viridis')
            heat.colorbar(mesh,ax=hx,label='|mean IQ| (a.u.)')
            hx.set(title=f'{job.get("target","Q11")} | ArcLight S21 Power | capture {capture+1}',
                   xlabel='Readout waveform scale (a.u.)',ylabel='Readout frequency (GHz)')
            hp=folder/f'platform_s21_power_capture_{capture+1}.png';heat.savefig(hp,dpi=160);files.append(str(hp))
    report=folder/'platform_analysis.json'
    payload=clean({'metrics':metrics,'candidateParameters':candidates,'series':series,'analysis':analysis})
    report.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    files.append(str(report)); payload['files']=files
    return payload
