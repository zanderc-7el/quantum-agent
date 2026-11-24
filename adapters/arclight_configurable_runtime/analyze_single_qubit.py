"""Analyze saved real Arclight IQ. No instrument/runtime imports or synthetic results."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import os
import stat
import warnings

import numpy as np
from scipy.optimize import curve_fit, OptimizeWarning

ROOT = Path(__import__('os').environ.get('QCAL_ARCLIGHT_RUN_DIR',str(Path(__file__).absolute().parent))).absolute()
UNCERTAINTY = '95% model-conditional covariance interval; excludes drift, state-preparation, model and systematic errors.'
ALIASES = {'timerabi': 'rabi_time', 'powerrabi': 'rabi_amplitude', 'rabi': 'rabi_time',
           'singleshot': 'readout', 'single_shot': 'readout', 'iq': 'readout',
           'hahn_echo': 'echo', 'hahnecho': 'echo'}


def kind_of(job):
    kind = str(job['kind']).lower().replace('-', '_')
    return ALIASES.get(kind, kind)


def validate_arrays(axes, i, q):
    i, q = np.asarray(i), np.asarray(q)
    if not isinstance(axes, list) or not axes or not all(isinstance(a, dict) for a in axes):
        raise ValueError('axes.json must be a nonempty list of native axis dictionaries.')
    if i.ndim != 3 or i.shape != q.shape or i.shape[:2] != (len(axes), 1) or i.shape[2] < 2:
        raise ValueError('Expected matching I/Q [points, 1, shots>=2].')
    if any(a.dtype.kind not in 'iuf' or not np.isfinite(a).all() for a in (i, q)):
        raise ValueError('I/Q arrays must contain finite real values.')
    return i[:, 0, :]+1j*q[:, 0, :]


def normalize_axes(records):
    """Accept both native dictionaries and randomized capture metadata from the runner."""
    if records and all(isinstance(a.get('axes'), dict) for a in records):
        capture_bases = {}
        base_axes = {}
        normalized = []
        for record in records:
            capture, base = record['capture'], record['base_index']
            if not isinstance(capture, int) or not isinstance(base, int):
                raise ValueError('capture/base_index metadata must be integers.')
            seen = capture_bases.setdefault(capture, set())
            if base in seen: raise ValueError('Repeated base_index within one capture.')
            seen.add(base)
            token = json.dumps(record['axes'], sort_keys=True)
            if base in base_axes and base_axes[base] != token:
                raise ValueError('A base_index changes native axes across captures.')
            base_axes[base] = token
            normalized.append({**record['axes'], 'repeat_index': capture})
        sets = list(capture_bases.values())
        if any(s != sets[0] for s in sets[1:]): raise ValueError('Captures cover different base indices.')
        return normalized
    if any('axes' in a for a in records): raise ValueError('Mixed wrapped/native axis records.')
    return records


def normalized_acquisition_blocks(index, shape, count):
    """Validate train provenance; equal-sized independent acquisitions, in saved order."""
    if not isinstance(count, (int, np.integer)) or isinstance(count, bool) or count < 1:
        raise ValueError('acquisitions_per_point must be a positive integer.')
    if count == 1 and index is None: return None
    if index is None:
        raise ValueError('Multi-acquisition data require explicit acquisition_block_index provenance.')
    a = np.asarray(index)
    if a.dtype.kind not in 'iu': raise ValueError('Acquisition block indices must be integers.')
    if a.ndim == 3 and a.shape[1] == 1: a = a[:, 0, :]
    if a.ndim == 1 and a.shape == (shape[1],): a = np.broadcast_to(a, shape)
    if a.shape != shape: raise ValueError('Acquisition block indices must match [points,shots] or shared [shots].')
    for row in a:
        labels, counts = np.unique(row, return_counts=True)
        if not np.array_equal(labels, np.arange(count)) or len(set(counts.tolist())) != 1 or min(counts) < 2:
            raise ValueError('Each point requires all equal-sized acquisition blocks, >=2 shots per block.')
        if np.any(np.diff(row.astype(np.int64)) < 0):
            raise ValueError('Acquisition blocks must be contiguous in declared acquisition order.')
    return a


def projected_standard_error(shots, acquisitions_per_point=1, acquisition_block_index=None):
    """Conservative mean SEM across independently re-armed acquisitions, no smoothing."""
    shots = np.asarray(shots)
    if shots.ndim != 2 or shots.shape[1] < 2 or not np.isfinite(shots).all():
        raise ValueError('SEM requires finite [points,shots>=2] data.')
    blocks = normalized_acquisition_blocks(acquisition_block_index, shots.shape, acquisitions_per_point)
    iid = shots.std(axis=1, ddof=1)/np.sqrt(shots.shape[1])
    if acquisitions_per_point == 1: return iid, None
    block_means = np.array([[shots[n, row == block].mean() for block in range(acquisitions_per_point)]
                           for n, row in enumerate(blocks)])
    block_sem = block_means.std(axis=1, ddof=1)/np.sqrt(acquisitions_per_point)
    effective = np.maximum(iid, block_sem)
    stats = {'acquisitions_per_point': int(acquisitions_per_point),
             'shots_per_acquisition': int(shots.shape[1]//acquisitions_per_point),
             'method': 'max(shot_IID_SEM, SEM_of_independent_acquisition_block_means)',
             'point_order': 'Same order as axes.json and raw_scan.npz; not sorted by scan coordinate.',
             'shot_iid_sem': iid.tolist(), 'acquisition_block_sem': block_sem.tolist(),
             'effective_sem': effective.tolist(),
             'assumptions': 'Equal-size acquisitions are separately armed and treated as independent blocks. Block SEM uses only the observed block means (4 blocks -> 3 degrees of freedom); it does not establish independence or include drift between scan captures. No synthetic shots or smoothing.'}
    return effective, stats


def projection(iq, calibration, acquisitions_per_point=1, acquisition_block_index=None):
    reference = calibration.get('projection') or {}
    if isinstance(reference, (list, tuple)):
        reference = {'direction': reference}
    supplied = reference.get('direction')
    mean = iq.mean(axis=1)
    if supplied is not None:
        direction = np.asarray(supplied, float)
        if direction.shape != (2,) or not np.isfinite(direction).all() or np.linalg.norm(direction) == 0:
            raise ValueError('Projection direction must be a finite nonzero [I,Q] vector.')
        direction /= np.linalg.norm(direction)
        source = str(reference.get('source', 'unspecified reference'))
        independent = reference.get('independent_reference') is True and bool(reference.get('source'))
        method = 'supplied reference direction'
    else:
        cloud = np.column_stack([mean.real, mean.imag])
        centered = cloud-cloud.mean(axis=0)
        _, vectors = np.linalg.eigh(centered.T@centered)
        direction = vectors[:, -1]
        if direction[np.argmax(np.abs(direction))] < 0:
            direction = -direction
        source, independent, method = 'same acquired sweep', False, 'PCA of point means'
    origin = np.asarray(reference.get('origin', [0, 0]), float)
    if origin.shape != (2,) or not np.isfinite(origin).all():
        raise ValueError('Projection origin must be finite [I,Q].')
    shots = (iq.real-origin[0])*direction[0] + (iq.imag-origin[1])*direction[1]
    metadata = {'direction': direction.tolist(), 'origin': origin.tolist(), 'method': method,
                'source': source, 'independent_reference': independent,
                'interpretation': 'projected IQ (a.u.), not probability',
                'exploratory': not independent}
    error, statistics = projected_standard_error(shots, acquisitions_per_point, acquisition_block_index)
    if statistics is not None: metadata['point_statistics'] = statistics
    return shots.mean(axis=1), error, metadata


def axis_key(axes, suffix, override=None):
    if override:
        if not all(override in a for a in axes):
            raise ValueError('Requested analysis axis is absent.')
        return override
    keys = sorted(set.intersection(*(set(a) for a in axes)))
    found = [k for k in keys if k.endswith(suffix)]
    if len(found) != 1:
        raise ValueError(f'Expected one axis ending {suffix!r}; found {found}. Set job.axis_key explicitly.')
    return found[0]


def _numeric(values):
    a = np.asarray(values, float)
    if a.ndim != 1 or not np.isfinite(a).all():
        raise ValueError('Analysis axes must contain finite numeric coordinates.')
    return a


def split_groups(axes, excluded, requested=None):
    keys = set().union(*(a.keys() for a in axes))
    keys = sorted(requested if requested is not None else keys-set(excluded)-{'repeat', 'repeat_index'})
    groups = {}
    for index, a in enumerate(axes):
        labels = {key: a.get(key) for key in keys}
        token = json.dumps(labels, sort_keys=True)
        groups.setdefault(token, {'axes': labels, 'indices': []})['indices'].append(index)
    return list(groups.values())


def repeat_blocks(x, indices, axes=None):
    coordinates = x[indices]
    unique = np.unique(coordinates)
    if len(unique) < 3 or len(indices) % len(unique):
        raise ValueError('Every scan group must contain complete repeats of at least three x values.')
    blocks = []
    if axes is not None and all('repeat_index' in axes[n] for n in indices):
        captures = {}
        for n in indices: captures.setdefault(axes[n]['repeat_index'], []).append(n)
        batches = captures.values()
    else:
        batches = [indices[start:start+len(unique)] for start in range(0, len(indices), len(unique))]
    for batch in batches:
        block = np.asarray(batch)
        if not np.array_equal(np.sort(x[block]), unique):
            raise ValueError('Repeat frequency/time coverage differs or contains duplicate points.')
        blocks.append(block[np.argsort(x[block])])
    return unique, blocks


def _diagnostics(x, y, sigma, prediction, params, covariance, parameter_count):
    reasons = []
    if not np.isfinite(params).all() or not np.isfinite(covariance).all() or np.any(np.diag(covariance) < 0):
        reasons.append('Non-finite parameters/covariance or invalid covariance diagonal.')
    residual = y-prediction
    sse = float(np.sum(residual**2))
    trend = np.polyval(np.polyfit(x, y, 1), x)
    constant_sse = float(np.sum((y-y.mean())**2))
    trend_sse = float(np.sum((y-trend)**2))
    floor = max(float(np.sum(sigma**2))*1e-6, 1e-30)
    n = len(y)
    bic = n*np.log(max(sse, floor)/n)+parameter_count*np.log(n)
    improvement_constant = float(n*np.log(max(constant_sse, floor)/n)+np.log(n)-bic)
    improvement_trend = float(n*np.log(max(trend_sse, floor)/n)+2*np.log(n)-bic)
    rms = math.sqrt(sse/n)
    if improvement_constant < 10 or improvement_trend < 10:
        reasons.append('Insufficient BIC improvement over constant/linear-trend models (<10).')
    return reasons, {'residual_rms': rms, 'bic_improvement_constant': improvement_constant,
                     'bic_improvement_trend': improvement_trend, 'bic': float(bic)}


def fit_signal(x, y, sigma, *, oscillatory=True):
    """Positive-time empirical fit with normalized x; invalid fits return None."""
    x, y, sigma = map(lambda a: np.asarray(a, float), (x, y, sigma))
    if len(x) < (9 if oscillatory else 7) or np.ptp(x) <= 0:
        return None, {'rejection_reasons': ['Insufficient distinct points or x span.']}, None
    span = float(np.ptp(x)); z = (x-x[0])/span
    signal_range = float(np.ptp(y)); noise = float(np.median(sigma))
    if signal_range <= 5*noise or signal_range <= 1e-15:
        return None, {'rejection_reasons': ['Signal excursion does not exceed the shot-noise scale.']}, None
    candidates = []
    models = []
    if oscillatory:
        if not np.allclose(np.diff(z), np.median(np.diff(z)), rtol=1e-5, atol=1e-9):
            return None, {'rejection_reasons': ['Oscillation scan must have a uniform grid.']}, None
        maximum = .45*(len(z)-1)
        frequencies = np.fft.rfftfreq(len(z), np.median(np.diff(z)))
        order = np.argsort(np.abs(np.fft.rfft(y-y.mean()))[1:])[::-1][:4]+1
        seeds = [float(frequencies[k]) for k in order if .2 < frequencies[k] < maximum]
        seeds = seeds or [min(2, maximum*.75)]
        def damped(t, b, slope, a, cycles, phase, decay):
            return b+slope*t+a*np.exp(-t/decay)*np.cos(2*np.pi*cycles*t+phase)
        def sinusoid(t, b, slope, a, cycles, phase):
            return b+slope*t+a*np.cos(2*np.pi*cycles*t+phase)
        for seed in seeds:
            models.append(('damped_sinusoid', damped, [y.mean(), 0, signal_range/2, seed, 0, 2],
                ([-np.inf, -np.inf, 0, .1, -4*np.pi, .02], [np.inf, np.inf, np.inf, maximum, 4*np.pi, 100])))
            models.append(('sinusoid_decay_unresolved', sinusoid, [y.mean(), 0, signal_range/2, seed, 0],
                ([-np.inf, -np.inf, 0, .1, -4*np.pi], [np.inf, np.inf, np.inf, maximum, 4*np.pi])))
    else:
        def exponential(t, b, a, decay):
            return b+a*np.exp(-t/decay)
        for decay in (.15, .5, 2):
            models.append(('exponential', exponential, [y[-1], y[0]-y[-1], decay],
                           ([-np.inf, -np.inf, .01], [np.inf, np.inf, 100])))
    errors = []
    for name, model, guess, bounds in models:
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always', OptimizeWarning)
                params, cov = curve_fit(model, z, y, p0=guess, bounds=bounds, maxfev=20000)
            reasons, diagnostics = _diagnostics(z, y, sigma, model(z, *params), params, cov, len(params))
            if caught:
                reasons.append('Optimizer emitted a covariance/fit warning.')
            selected = [3] if oscillatory else [2]
            if name == 'damped_sinusoid': selected.append(5)
            for p in selected:
                low, high = bounds[0][p], bounds[1][p]
                if min(params[p]-low, high-params[p]) <= max(1e-6, .001*(high-low)):
                    reasons.append('Fit reached a frequency/time optimization boundary.')
            if oscillatory and params[3] < 1.5:
                reasons.append('Fitted oscillation contains fewer than 1.5 cycles.')
            parameter = 3 if oscillatory else 2
            std = float(np.sqrt(max(0, cov[parameter, parameter]))) if np.isfinite(cov[parameter, parameter]) else math.inf
            if 1.96*std >= .5*abs(params[parameter]):
                reasons.append('95% model interval is too wide relative to the fitted parameter.')
            if oscillatory and params[2] <= 3*max(noise, diagnostics['residual_rms']):
                reasons.append('Oscillation amplitude is not resolved relative to residual/shot noise.')
            if reasons:
                errors.extend(reasons); continue
            value = float(params[parameter]/span if oscillatory else params[parameter]*span)
            uncertainty = float(std/span if oscillatory else std*span)
            fit = {'model': name, 'parameter': 'frequency_per_x_unit' if oscillatory else 'decay_time_in_x_units',
                   'value': value, 'ci95_model': [value-1.96*uncertainty, value+1.96*uncertainty],
                   'standard_error_model': uncertainty, 'uncertainty_scope': UNCERTAINTY,
                   'cycles_in_window': float(params[3]) if oscillatory else None,
                   'decay_time': float(params[5]*span) if name == 'damped_sinusoid' else (value if not oscillatory else None),
                   'x_origin': float(x[0]), 'x_span': span,
                   'fit_parameters_normalized_x': params.tolist(),
                   **diagnostics}
            if oscillatory:
                fit['period_x_units'] = 1/value
                fit['phase_rad'] = float(params[4])
            if name == 'damped_sinusoid':
                decay, decay_std = float(params[5]*span), float(np.sqrt(cov[5, 5])*span)
                fit['decay_time_ci95_model'] = [decay-1.96*decay_std, decay+1.96*decay_std]
            dense_x = np.linspace(x[0], x[-1], 500)
            candidates.append((diagnostics['bic'], fit, (dense_x, model((dense_x-x[0])/span, *params))))
        except (ValueError, RuntimeError, FloatingPointError) as exc:
            errors.append(type(exc).__name__+': '+str(exc))
    if not candidates:
        return None, {'rejection_reasons': sorted(set(errors)) or ['No accepted fit.']}, None
    _, fit, curve = min(candidates, key=lambda a: a[0])
    return fit, {'rejection_reasons': []}, curve


def _repeat_agreement(repeats):
    fits = [r['fit'] for r in repeats]
    if len(fits) < 2 or any(f is None for f in fits):
        return {'repeat_validated': False, 'reason': 'At least two separately acquired, valid repeat fits are required.'}
    values = np.array([f['value'] for f in fits])
    stderr = np.array([f['standard_error_model'] for f in fits])
    tolerance = max(.15*float(np.median(values)), 3*float(np.sqrt(np.max(stderr)**2+np.min(stderr)**2)))
    return {'repeat_validated': bool(np.ptp(values) <= tolerance),
            'parameter_values': values.tolist(), 'spread': float(np.ptp(values)), 'tolerance': tolerance,
            'reason': 'Frequency/time consistency across separate acquired sweeps; not a gate-fidelity measurement.'}


def analyze_arrays(axes, i, q, job, acquisition_block_index=None):
    iq = validate_arrays(axes, i, q)
    axes = normalize_axes(axes)
    kind = kind_of(job)
    acquisition_count = job.get('acquisitions_per_point', 1)
    projected, error, project_meta = projection(iq, job.get('calibration') or {}, acquisition_count, acquisition_block_index)
    statistics = project_meta.pop('point_statistics', None)
    mean = iq.mean(axis=1)
    result = {'kind': kind, 'target': job.get('target'), 'source_qubit': job.get('sourcequbit', job.get('source_qubit',
               job.get('config', {}).get('_agent', {}).get('source_qubit'))),
              'points': len(axes), 'shots_per_point': iq.shape[1], 'projection': project_meta,
              'probability_interpretation': False, 'raw_data_smoothed': False,
              'uncertainty_scope': UNCERTAINTY, 'groups': []}
    if statistics is not None: result['point_statistics'] = statistics
    plots = []
    if kind in ('readout', 'single_shot', 'singleshot'):
        return analyze_readout(axes, iq, job, result, acquisition_block_index)
    if kind == 'spectrum':
        key = axis_key(axes, '.para.drivFreq', job.get('axis_key'))
    elif kind == 'rabi_time':
        key = axis_key(axes, '.gate.U_gates.pi_len', job.get('axis_key'))
    elif kind == 'rabi_amplitude':
        key = axis_key(axes, '.gate.U_gates.pi_amp', job.get('axis_key'))
    elif kind in ('t1', 'ramsey', 'echo', 'cpmg'):
        suffix = '.wait_a.params' if kind in ('echo','cpmg') else '.wait.params'
        key = axis_key(axes, suffix, job.get('axis_key'))
    else:
        raise ValueError('Unsupported single-qubit analysis kind: '+kind)
    x = _numeric([a[key] for a in axes])
    if kind == 'echo': x *= 2
    if kind == 'cpmg': x *= job['total_delay_multiplier']
    excluded = {key, 'exp.cir.default'}
    if kind == 'cpmg': excluded.update(k for a in axes for k in a if '.wait_' in k)
    excluded.update(k for a in axes for k in a if k.endswith('.phase.params') or (kind == 'echo' and k.endswith('.wait_b.params')))
    groups = split_groups(axes, excluded, job.get('group_by'))
    result['x_axis'] = {'native_key': key, 'unit': 'Hz' if kind == 'spectrum' else ('waveform_scale' if kind == 'rabi_amplitude' else 's'),
                        'echo_multiplier': 2 if kind == 'echo' else job.get('total_delay_multiplier',1) if kind == 'cpmg' else 1}
    for group in groups:
        indices = group['indices']
        if kind == 'spectrum':
            complex_sem, _ = projected_standard_error(iq, acquisition_count, acquisition_block_index)
            summary, plot = analyze_spectrum_group(axes, iq, x, indices, projected, error, complex_sem)
        else:
            unique, blocks = repeat_blocks(x, indices, axes)
            # Ramsey free-phase fitting returns a positive frequency magnitude, not a signed frequency correction.
            oscillatory = kind.startswith('rabi') or kind == 'ramsey' or (kind == 'echo' and any(
                len({json.dumps(a.get(k)) for a in axes}) > 1 for k in excluded if k.endswith('.phase.params')))
            repeats, curves = [], []
            for number, block in enumerate(blocks, 1):
                fit, diagnostics, curve = fit_signal(unique, projected[block], error[block], oscillatory=oscillatory)
                repeats.append({'repeat': number, 'fit': fit, 'diagnostics': diagnostics})
                curves.append(curve)
            interpretation = ('Independent-reference projected IQ; fitted decay or oscillation parameters are model conditional, not state probabilities or gate fidelity.'
                              if project_meta['independent_reference'] else
                              'Exploratory IQ response; a single oscillation fit does not establish calibrated Rabi or state probability.')
            summary = {'repeats': repeats, **_repeat_agreement(repeats), 'fit': None,
                       'interpretation': interpretation}
            if summary['repeat_validated']:
                summary['fit'] = {'parameter': repeats[0]['fit']['parameter'],
                                  'repeat_mean_value': float(np.mean([r['fit']['value'] for r in repeats]))}
            plot = {'x': x[indices], 'y': projected[indices], 'indices': indices, 'blocks': blocks,
                    'curves': curves, 'comparison': None, 'xlabel': result['x_axis']['unit']}
        summary['group_axes'] = group['axes']
        result['groups'].append(summary); plots.append(plot)
    result['any_repeat_validated'] = any(g.get('repeat_validated', False) for g in result['groups'])
    return result, {'iq': iq, 'mean_iq': mean, 'projection': projected, 'projection_sem': error, 'plots': plots, 'axes': axes}


def analyze_spectrum_group(axes, iq, x, indices, projected, error, complex_sem=None):
    labels = [str(axes[n].get('exp.cir.default', '')).lower() for n in indices]
    off = [n for n, s in zip(indices, labels) if s in ('off', 'ground', 'r0_base')]
    on = [n for n, s in zip(indices, labels) if s in ('on', 'excited', 'r1_base')]
    if len(off) != len(on) or not off or len(off)+len(on) != len(indices):
        raise ValueError('Paired Spectrum requires matching off/on axes (exp.cir.default).')
    unique, off_blocks = repeat_blocks(x, off, axes)
    _, on_blocks = repeat_blocks(x, on, axes)
    if len(off_blocks) != len(on_blocks): raise ValueError('Spectrum repeat counts differ.')
    mean = iq.mean(axis=1)
    if complex_sem is None: complex_sem = iq.std(axis=1, ddof=1)/np.sqrt(iq.shape[1])
    repeats, comparisons = [], []
    for number, (a, b) in enumerate(zip(off_blocks, on_blocks), 1):
        delta = mean[b]-mean[a]
        sem = np.hypot(complex_sem[a], complex_sem[b])
        edge = max(1, len(unique)//8)
        background = complex(np.median(np.r_[delta[:edge].real, delta[-edge:].real]),
                             np.median(np.r_[delta[:edge].imag, delta[-edge:].imag]))
        excursion = np.abs(delta-background)
        peak = int(np.argmax(excursion))
        significance = float(excursion[peak]/max(sem[peak], 1e-30))
        detected = bool(0 < peak < len(unique)-1 and significance >= 5)
        repeats.append({'repeat': number, 'candidate_frequency_hz': float(unique[peak]) if detected else None,
                        'candidate_significance': significance, 'candidate_detected': detected,
                        'fit': None, 'background_complex': [background.real, background.imag]})
        comparisons.append((unique, projected[b]-projected[a], excursion))
    candidates = [r['candidate_frequency_hz'] for r in repeats]
    tolerance = 2*float(np.median(np.diff(unique)))
    consistent = len(candidates) >= 2 and all(v is not None for v in candidates) and np.ptp(candidates) <= tolerance
    summary = {'repeats': repeats, 'repeat_validated': bool(consistent), 'fit': None,
               'candidate_frequency_hz': float(np.mean(candidates)) if consistent else None,
               'interpretation': 'Paired drive response candidate only. Require Rabi/control validation before identifying a qubit transition.'}
    plot = {'x': x[indices], 'y': projected[indices], 'indices': indices,
            'blocks': [block for pair in zip(off_blocks, on_blocks) for block in pair],
            'curves': [], 'comparison': comparisons, 'xlabel': 'Hz'}
    return summary, plot


def _wilson(success, count):
    z = 1.96; p = success/count; scale = 1+z*z/count
    middle = (p+z*z/(2*count))/scale
    width = z*np.sqrt(p*(1-p)/count+z*z/(4*count*count))/scale
    return [float(middle-width), float(middle+width)]


def analyze_readout(axes, iq, job, result, acquisition_block_index=None):
    groups = split_groups(axes, {'exp.cir.default'}, job.get('group_by'))
    mean = iq.mean(axis=1); plots = []
    projected_all = np.full(len(iq), np.nan); error_all = np.full(len(iq), np.nan)
    count = job.get('acquisitions_per_point', 1)
    blocks = normalized_acquisition_blocks(acquisition_block_index, iq.shape, count)
    readout_statistics = {key: np.full(len(iq), np.nan) for key in ('shot_iid_sem', 'acquisition_block_sem', 'effective_sem')}
    for group in groups:
        collections = []
        for names in ({'ground', 'off', 'r0_base'}, {'excited', 'on', 'r1_base'}):
            ids = [n for n in group['indices'] if str(axes[n].get('exp.cir.default', '')).lower() in names]
            if not ids: raise ValueError('Readout classification requires both prepared-state labels.')
            cloud = np.concatenate([np.column_stack([iq[n].real, iq[n].imag]) for n in ids])
            if len(cloud) < 20: raise ValueError('At least 20 shots per prepared label are required for train/test classification.')
            # Deterministic disjoint held-out shots; labels refer to preparation, not verified pure states.
            collections.append((cloud[::2], cloud[1::2]))
        (train0, test0), (train1, test1) = collections
        pool = (np.cov(train0.T)+np.cov(train1.T))/2
        pool += np.eye(2)*max(np.trace(pool)*1e-8, 1e-30)
        direction = np.linalg.solve(pool, train1.mean(axis=0)-train0.mean(axis=0))
        if not np.isfinite(direction).all() or np.linalg.norm(direction) == 0:
            raise ValueError('Readout training clouds have no usable separation.')
        direction /= np.linalg.norm(direction)
        p0, p1 = train0@direction, train1@direction
        all_values = np.sort(np.r_[p0, p1]); thresholds = (all_values[:-1]+all_values[1:])/2
        fidelity = (np.searchsorted(np.sort(p0), thresholds, side='right')/len(p0)
                    +1-np.searchsorted(np.sort(p1), thresholds, side='right')/len(p1))/2
        threshold = float(thresholds[np.argmax(fidelity)])
        v0, v1 = test0@direction, test1@direction
        correct0, correct1 = int(np.sum(v0 <= threshold)), int(np.sum(v1 > threshold))
        d0, d1 = correct0/len(v0), correct1/len(v1)
        summary = {'group_axes': group['axes'], 'fit': None, 'repeat_validated': False,
            'classification': {'label': 'held-out prepared-label assignment accuracy (includes preparation/readout errors)',
                'split': 'even training shots / odd held-out shots; not a separately acquired validation run',
                'direction': direction.tolist(), 'phi_rad': float(np.arctan2(direction[1], direction[0])),
                'threshold': threshold, 'train_counts': [len(p0), len(p1)], 'test_counts': [len(v0), len(v1)],
                'P_assign0_given_prep0': d0, 'P_assign1_given_prep1': d1,
                'assignment_accuracy_mean': (d0+d1)/2,
                'ci95_assign0_wilson': _wilson(correct0, len(v0)), 'ci95_assign1_wilson': _wilson(correct1, len(v1)),
                'snr_dprime': float(abs(v1.mean()-v0.mean())/max(np.sqrt((v0.var(ddof=1)+v1.var(ddof=1))/2), 1e-30)),
                'snr_definition': '|mean1-mean0|/sqrt((variance0+variance1)/2) on held-out projections'}}
        result['groups'].append(summary)
        ids = group['indices']
        projected_shots = iq[ids].real*direction[0]+iq[ids].imag*direction[1]
        projected_all[ids] = projected_shots.mean(axis=1)
        error_all[ids], statistics = projected_standard_error(projected_shots, count, None if blocks is None else blocks[ids])
        if statistics is not None:
            for key in readout_statistics: readout_statistics[key][ids] = statistics[key]
        plots.append({'x': np.arange(len(ids)), 'y': mean[ids].real*direction[0]+mean[ids].imag*direction[1],
                      'indices': ids, 'blocks': [np.asarray(ids)], 'curves': [], 'comparison': None,
                      'xlabel': 'acquisition index', 'readout_hist': (v0, v1, threshold)})
    result['any_repeat_validated'] = False
    result['projection'] = {'method': 'Per-group LDA learned from training prepared labels', 'exploratory': True,
                            'independent_reference': False, 'interpretation': 'projected IQ, not pure-state probability'}
    if count > 1:
        result['point_statistics'] = {**statistics, **{key: value.tolist() for key,value in readout_statistics.items()},
            'classification_interval_note': 'Wilson assignment intervals still assume independent held-out shots; block SEM correction applies to plotted point means only.'}
    return result, {'iq': iq, 'mean_iq': mean, 'projection': projected_all, 'projection_sem': error_all, 'plots': plots, 'axes': axes}


def _safe_folder(folder):
    folder = Path(os.path.abspath(os.fspath(folder))); allowed = Path(os.path.abspath(ROOT/'results'))
    if not folder.is_relative_to(allowed): raise ValueError('Only this task results directory is accepted.')
    paths = [allowed]
    for part in folder.relative_to(allowed).parts: paths.append(paths[-1]/part)
    for path in paths:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise ValueError('Linked result paths are not accepted.')
    for path in folder.iterdir():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise ValueError('Linked result files are not accepted.')
    return folder


def analyze_directory(folder):
    folder = _safe_folder(folder)
    status = json.loads((folder/'status.json').read_text(encoding='utf8'))
    if status.get('mode') != 'real_hardware' or status.get('state') != 'acquired':
        raise ValueError('Only acquired real hardware runs may be analyzed.')
    axes = json.loads((folder/'axes.json').read_text(encoding='utf8'))
    job = json.loads((folder/'job.json').read_text(encoding='utf8'))
    with np.load(folder/'raw_scan.npz', allow_pickle=False) as raw:
        i, q = raw['i'].copy(), raw['q'].copy()
        acquisition_block_index = raw['acquisition_block_index'].copy() if 'acquisition_block_index' in raw.files else None
    validate_arrays(axes, i, q)
    normalized_blocks = normalized_acquisition_blocks(acquisition_block_index, (len(i), i.shape[2]), job.get('acquisitions_per_point', 1))
    for key in ('points_completed', 'points_expected'):
        if key in status and status[key] != len(axes): raise ValueError('Status point count does not match raw scan.')
    for n in range(len(axes)):
        with np.load(folder/f'point_{n:04d}.npz', allow_pickle=False) as point:
            if not np.array_equal(point['i'], i[n]) or not np.array_equal(point['q'], q[n]):
                raise ValueError(f'Per-point data differs from raw_scan at {n}.')
            if job.get('acquisitions_per_point', 1) > 1:
                if 'acquisition_block_index' not in point.files:
                    raise ValueError(f'Per-point acquisition-block provenance is absent at {n}.')
                point_blocks = normalized_acquisition_blocks(point['acquisition_block_index'], (1, i.shape[2]), job['acquisitions_per_point'])
                if not np.array_equal(point_blocks[0], normalized_blocks[n]):
                    raise ValueError(f'Per-point acquisition blocks differ from raw_scan at {n}.')
    result, views = analyze_arrays(axes, i, q, job, acquisition_block_index)
    axes = views['axes']
    result['raw_scan'] = str(folder/'raw_scan.npz')
    result['analysis_source_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    mean = views['mean_iq']
    columns = [np.arange(len(axes)), mean.real, mean.imag, abs(mean), views['projection'], views['projection_sem']]
    header = 'point_index,mean_i,mean_q,abs_mean_iq,projected_iq_a_u,projection_standard_error'
    if 'point_statistics' in result:
        columns += [result['point_statistics']['shot_iid_sem'], result['point_statistics']['acquisition_block_sem']]
        header += ',projection_shot_iid_sem,projection_acquisition_block_sem'
    np.savetxt(folder/'single_qubit_summary.csv', np.column_stack(columns), delimiter=',', comments='', header=header)
    os.environ.setdefault('MPLCONFIGDIR', str(ROOT/'runtime_state'/'matplotlib'))
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    images = []
    for n, view in enumerate(views['plots']):
        fig = Figure(figsize=(12, 8), constrained_layout=True); FigureCanvasAgg(fig)
        axs = fig.subplots(2, 2); ids = view['indices']
        shots = views['iq'][ids].ravel(); stride = max(1, int(np.ceil(len(shots)/10000)))
        axs[0, 0].scatter(shots.real[::stride], shots.imag[::stride], s=2, alpha=.2)
        axs[0, 0].plot(mean[ids].real, mean[ids].imag, '.', ms=4)
        axs[0, 0].set(xlabel='I (a.u.)', ylabel='Q (a.u.)', title=f'Raw IQ shots (display stride {stride}); all point means')
        for repeat, block in enumerate(view['blocks'], 1):
            if result['kind'] == 'readout':
                xx = np.arange(len(block)); yy = view['y']
            else:
                xx = np.asarray([float(axes[k][result['x_axis']['native_key']]) for k in block])
                if result['kind'] == 'echo': xx *= 2
                yy = views['projection'][block]
            axs[0, 1].plot(xx, yy, '.', label=f'Trace {repeat}')
            axs[1, 0].plot(xx, abs(mean[block]), '.', label=f'Trace {repeat}')
        for curve in view['curves']:
            if curve is not None: axs[0, 1].plot(curve[0], curve[1], '--', lw=1)
        axs[0, 1].set(xlabel=view['xlabel'], ylabel='Projected IQ (a.u.; not probability)', title='Raw projections; accepted model fits dashed')
        axs[0, 1].legend(fontsize=7)
        axs[1, 0].set(xlabel=view['xlabel'], ylabel='|mean IQ| (a.u.)', title='Raw point magnitudes')
        if view.get('readout_hist'):
            a, b, threshold = view['readout_hist']
            axs[1, 1].hist(a, bins=50, alpha=.5, label='prepared ground / held out')
            axs[1, 1].hist(b, bins=50, alpha=.5, label='prepared excited / held out')
            axs[1, 1].axvline(threshold, color='black', ls='--'); axs[1, 1].legend(fontsize=7)
        elif view['comparison'] is not None:
            for repeat, (x, delta, excursion) in enumerate(view['comparison'], 1):
                axs[1, 1].plot(x, delta, '.', label=f'ON-OFF projection {repeat}')
            axs[1, 1].legend(fontsize=7)
        else:
            axs[1, 1].plot(view['x'], views['projection_sem'][ids], '.')
        axs[1, 1].set(title='Paired response / held-out labels / shot uncertainty')
        for ax in axs.flat: ax.grid(alpha=.2)
        fig.suptitle(f'Arclight real IQ / {job.get("target", "")} / {result["kind"]} / group {n}\n'
                     + (result['projection']['method']+' (exploratory)' if result['projection']['exploratory'] else 'Independent reference projection'))
        name = f'single_qubit_group_{n:03d}.png'; fig.savefig(folder/name, dpi=160); images.append(name)
    result['images'] = images
    (folder/'single_qubit_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('folder', type=Path)
    print(json.dumps(analyze_directory(parser.parse_args().folder), ensure_ascii=False, indent=2, allow_nan=False))
