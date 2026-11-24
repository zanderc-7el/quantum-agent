"""Paired native Arclight RB/IRB and conservative, offline-only analysis.

Call bootstrap.initialize() before importing this module in the task runtime.
No device creation, connection, plotting, or file write occurs on import.
The caller saves sequence_manifest(...) alongside axes.json and raw_scan.npz.
"""
from dataclasses import fields, replace
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from core.circuit.clifford import SingleQubitRB


SOURCES = [
    {"title": "Magesan et al., PRL 109, 080505 (2012), Eq. (2), (4), (5)",
     "url": "https://arxiv.org/pdf/1203.4550", "doi": "10.1103/PhysRevLett.109.080505"},
    {"title": "Qiskit Experiments InterleavedRBAnalysis: model and parameter definitions",
     "url": "https://qiskit-community.github.io/qiskit-experiments/stubs/qiskit_experiments.library.randomized_benchmarking.InterleavedRBAnalysis.html"},
]
ASSUMPTIONS = [
    "A single exponential adequately describes both sequence ensembles.",
    "State preparation, measurement, and recovery error are stable and absorbed by A and B.",
    "Clifford noise has small average gate dependence; the ratio is a conditional gate-fidelity estimate, not full process tomography.",
    "Leakage, drift, temporal correlations, and coherent gate-dependent error can invalidate the simple ratio model.",
    "Bootstrap intervals condition on the chosen calibration, pulse implementation, lengths, and acquired captures; they exclude model systematic error and calibration uncertainty.",
]


def _integer(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return int(value)


class PairedSingleQubitRB(SingleQubitRB):
    """Keep native sequence synthesis; only remove mode from the seed distinction."""
    def derive_seed(self, run_seed, m, k):
        m, k = _integer(m, "clifford_m"), _integer(k, "repeat")
        return SingleQubitRB.derive_seed(replace(self, mode="rb"), run_seed, m, k)


def install_paired_rb(exp, *, mode="rb", interleaved="X/2"):
    """Replace exp.rb immediately after Experiment.load, before sequence compilation."""
    if mode not in ("rb", "irb"):
        raise ValueError("mode must be 'rb' or 'irb'")
    if not isinstance(getattr(exp, "rb", None), SingleQubitRB):
        raise TypeError("Expected a native RBExperiment with SingleQubitRB")
    values = {f.name: getattr(exp.rb, f.name) for f in fields(SingleQubitRB)}
    values.update(targets=tuple(exp.rb.targets), mode=mode,
                  interleaved=interleaved if mode == "irb" else None)
    if len(values["targets"]) != 1 or not values["include_inverse"] or not values["add_measure"]:
        raise ValueError("This task requires exactly one qubit, inverse recovery, and final measurement")
    rb = PairedSingleQubitRB(**values)
    if mode == "irb":
        rb._normalize_interleaved()
    exp.rb = rb
    return rb


def _native_gates_json(gates):
    return [[g[0], list(g[1]), list(g[2]) if len(g) > 2 else []] for g in gates]


def ideal_gate_closure(gates):
    """Independent SO(3) product of emitted primitive gates, up to global phase."""
    total = np.eye(3)
    measures = 0
    for n, gate in enumerate(gates):
        name = gate[0]
        if name == "I":
            continue  # Physical idle duration is recorded by the opt-in compiler.
        if name == "MEASURE":
            measures += 1
            if n != len(gates)-1:
                raise ValueError("Measurement must be final")
            continue
        if name in ("X", "Y"):
            angle, axis = np.pi, name
        elif name in ("RX", "RY") and len(gate) > 2 and len(gate[2]) == 1:
            angle, axis = float(gate[2][0]), name[-1]
        else:
            raise ValueError(f"Unsupported primitive gate {name!r}")
        c, s = np.cos(angle), np.sin(angle)
        rot = (np.array([[1,0,0],[0,c,-s],[0,s,c]]) if axis == "X"
               else np.array([[c,0,s],[0,1,0],[-s,0,c]]))
        total = rot @ total
    return {"closed": bool(np.allclose(total, np.eye(3), atol=1e-9, rtol=0)),
            "identity_error_max": float(np.max(np.abs(total-np.eye(3)))),
            "measurement_count": measures, "bloch_product": total.tolist()}


def _sequence_record(rb, m, k, run_seed):
    seed = rb.derive_seed(run_seed, m, k)
    random = np.random.Generator(np.random.PCG64(seed)).integers(0, 24, size=m).tolist()
    indices = []
    target = rb._normalize_interleaved() if rb.mode == "irb" else None
    for c in random:
        indices.append(c)
        if target is not None:
            indices.append(target)
    now = 0
    for c in indices:
        now = rb.compose(c, now)
    recovery = rb.inverse(now) if rb.include_inverse else None
    if recovery is not None:
        indices.append(recovery)
    gates = _native_gates_json(rb.rb_sequence(m=m, k=k, run_seed=run_seed))
    closure = ideal_gate_closure(gates)
    if not closure["closed"] or closure["measurement_count"] != 1:
        raise ValueError("Native RB sequence does not ideally close with one final measurement")
    return {"clifford_m": m, "repeat": k, "seed": seed,
            "random_clifford_indices": random, "effective_clifford_indices": indices,
            "interleaved_index": target, "recovery_clifford_index": recovery,
            "native_gates": gates, "ideal_closure": closure}


def sequence_manifest(rb, axes, run_seed):
    """JSON-serializable native gates + seeds; accepts base axes or saved wrapped axes."""
    if not isinstance(rb, PairedSingleQubitRB):
        raise TypeError("Install PairedSingleQubitRB first")
    keys = set()
    for record in axes:
        a = record.get("axes", record)
        keys.add((_integer(a["clifford_m"], "clifford_m"), _integer(a["repeat"], "repeat")))
    if not keys:
        raise ValueError("No RB sequence axes")
    native_path = Path(__import__(SingleQubitRB.__module__, fromlist=["__file__"]).__file__)
    result = {"schema": "arclight_paired_rb_v1", "mode": rb.mode,
            "run_seed": run_seed, "tag": rb.tag, "targets": list(rb.targets),
            "interleaved": rb.interleaved, "include_inverse": rb.include_inverse,
            "add_measure": rb.add_measure, "seed_policy": "native derive_seed with mode fixed to rb",
            "native_source": str(native_path), "native_source_sha256": hashlib.sha256(native_path.read_bytes()).hexdigest(),
            "sequences": [_sequence_record(rb, m, k, run_seed) for m, k in sorted(keys)]}
    if getattr(rb, 'pulse_policy', None):
        result['pulse_policy'] = rb.pulse_policy
    return result


def _manifest_index(manifest, mode, expected_keys):
    if manifest.get("schema") != "arclight_paired_rb_v1" or manifest.get("mode") != mode:
        raise ValueError(f"Expected {mode} paired sequence manifest")
    rb_class = PairedSingleQubitRB
    if manifest.get('pulse_policy'):
        from validated_short_half_compat import POLICY, ValidatedShortHalfPairedRB
        if manifest['pulse_policy'] != POLICY:
            raise ValueError('Unknown RB pulse policy')
        rb_class = ValidatedShortHalfPairedRB
    rb = rb_class(targets=tuple(manifest["targets"]), tag=manifest["tag"],
         mode=mode, interleaved=manifest["interleaved"],
         include_inverse=manifest["include_inverse"], add_measure=manifest["add_measure"])
    if not rb.include_inverse or not rb.add_measure or len(rb.targets) != 1:
        raise ValueError("Invalid recovery/measurement/target manifest")
    out = {}
    for row in manifest["sequences"]:
        key = (_integer(row["clifford_m"], "clifford_m"), _integer(row["repeat"], "repeat"))
        if key in out:
            raise ValueError("Duplicate sequence in manifest")
        actual = _sequence_record(rb, *key, manifest["run_seed"])
        # Reconstruct instead of trusting a saved success flag.
        for field in ("seed", "random_clifford_indices", "effective_clifford_indices", "native_gates"):
            if row[field] != actual[field]:
                raise ValueError(f"Sequence manifest mismatch: {field}")
        out[key] = row
    if set(out) != expected_keys:
        raise ValueError("Manifest coverage differs from measured axes")
    return out


def fixed_projection(mean_iq, calibration):
    """Independent centroids define ground=1/excited=0. Never clip raw projections."""
    if calibration.get("independent_reference") is not True or not calibration.get("source"):
        raise ValueError("An identified independent IQ calibration is required")
    g, e = np.asarray(calibration["ground_iq"], float), np.asarray(calibration["excited_iq"], float)
    if g.shape != (2,) or e.shape != (2,) or not np.isfinite([g,e]).all():
        raise ValueError("ground_iq and excited_iq must be finite [I,Q] centroids")
    if 'direction' in calibration:
        # Preserve an independently frozen LDA axis; do not silently replace it
        # by the centroid line. Ground=1 / excited=0 convention stays unchanged.
        direction = np.asarray(calibration['direction'], float)
        if direction.shape != (2,) or not np.isfinite(direction).all() or np.linalg.norm(direction) == 0:
            raise ValueError('Fixed independent direction must be finite nonzero [I,Q]')
        direction = direction/np.linalg.norm(direction)
        contrast = float((e-g) @ direction)
        if contrast <= 1e-15:
            raise ValueError('Fixed direction requires positive excited-minus-ground reference contrast')
        z = np.asarray(mean_iq)
        return ((e[0]-z.real)*direction[0] + (e[1]-z.imag)*direction[1])/contrast
    delta = complex(*(g-e))
    if abs(delta) < 1e-15:
        raise ValueError("Independent calibration has zero IQ contrast")
    excited = complex(*e)
    return np.real((np.asarray(mean_iq)-excited)*np.conj(delta))/abs(delta)**2


def _cells(records, iq, projection):
    iq = np.asarray(iq)
    if not np.iscomplexobj(iq) or not np.isfinite(iq).all() or iq.shape[0] != len(records):
        raise ValueError("IQ must be finite complex data with one first-axis row per axes record")
    if iq.ndim == 3 and iq.shape[1] == 1:
        mean = iq[:,0,:].mean(axis=1)
    elif iq.ndim == 2:
        mean = iq.mean(axis=1)
    elif iq.ndim == 1:
        mean = iq
    else:
        raise ValueError("IQ shape must be [points], [points,shots], or [points,1,shots]")
    if not np.isfinite(mean).all():
        raise ValueError("IQ point means must be finite; empty shot arrays are invalid")
    values = fixed_projection(mean, projection)
    if not np.isfinite(values).all():
        raise ValueError("Independent IQ projection produced non-finite values")
    cells, base_map, capture_keys = {}, {}, {}
    for r, value in zip(records, values):
        capture = _integer(r["capture"], "capture")
        base = _integer(r["base_index"], "base_index")
        a = r["axes"]
        key = (_integer(a["clifford_m"], "clifford_m"), _integer(a["repeat"], "repeat"))
        if base in base_map and base_map[base] != key:
            raise ValueError("base_index changes sequence across captures")
        base_map[base] = key
        per = capture_keys.setdefault(capture, set())
        if key in per:
            raise ValueError("Duplicate sequence within one capture")
        per.add(key)
        cells.setdefault(key, {})[capture] = float(value)
    if not cells or any(s != set(cells) for s in capture_keys.values()):
        raise ValueError("Captures must cover identical complete sequence sets")
    return cells, values


def _model(x, params):
    a, p, b = params
    return a*np.power(p, x)+b


def _fit(x, y, sem, start=None):
    scale = np.maximum(sem, 1e-4)
    lower, upper = np.array([1e-9, 1e-6, 0.0]), np.array([1.0, 1-1e-9, 1.0])
    def residual(params):
        return (_model(x, params)-y)/scale
    if start is None:
        b0 = max(0.02, min(0.9, float(np.min(y))))
        a0 = max(0.02, min(0.9, float(np.max(y)-b0)))
        starts = [[a0, p, b0] for p in (.6, .85, .96, .995)]
    else:
        starts = [start]
    fits = [least_squares(residual, s, bounds=(lower, upper), max_nfev=1500,
                          ftol=1e-10, xtol=1e-10, gtol=1e-10) for s in starts]
    fit = min(fits, key=lambda f: float(np.sum(f.fun**2)))
    if not fit.success or not np.isfinite(fit.x).all():
        raise ValueError("Decay fit failed")
    params = fit.x
    pred = _model(x, params)
    dof = len(x)-3
    condition = float(np.linalg.cond(fit.jac))
    reduced = float(np.sum(fit.fun**2)/max(1,dof))
    covariance = np.linalg.pinv(fit.jac.T@fit.jac)*max(1.0,reduced)
    se = np.sqrt(np.maximum(np.diag(covariance),0))
    delta = float(pred[0]-pred[-1])
    reasons = []
    if params[0] < 3*se[0] or delta <= max(0.015, 3*np.hypot(scale[0], scale[-1])):
        reasons.append("decay_contrast_not_identifiable")
    if condition > 1e7 or not np.isfinite(condition) or se[1] > .1:
        reasons.append("decay_parameters_not_identifiable")
    if params[1] <= 1e-5 or params[1] >= 1-1e-5:
        reasons.append("decay_parameter_at_boundary")
    if params[0] >= 1-1e-5 or params[2] >= 1-1e-5:
        reasons.append("SPAM_parameter_at_upper_boundary")
    if reduced > 9:
        reasons.append("single_exponential_lack_of_fit")
    return {"A": float(params[0]), "p": float(params[1]), "B": float(params[2]),
            "parameter_standard_errors": dict(zip(("A","p","B"),map(float,se))),
            "reduced_chi_square": reduced, "jacobian_condition": condition if np.isfinite(condition) else None,
            "fitted_contrast": delta, "identifiable": not reasons, "reasons": reasons,
            "fitted_values": pred.tolist()}, params


def analyze_paired_rb(ref_axes, ref_iq, irb_axes, irb_iq, projection, *,
                      ref_manifest, irb_manifest, bootstrap_samples=1000,
                      bootstrap_seed=20261117, confidence=.95, min_seeds=8):
    """Fit independent A,p,B per mode and pair seed draws at each length.

    Repeated captures of a given (m,repeat) are averaged, not counted as new seeds.
    Native seed includes m, so resampling is stratified by m and shared across modes.
    Result is JSON serializable. Invalid *data* give reportable=False; malformed
    metadata/calibration/manifests raise ValueError to avoid silent alignment errors.
    """
    if not isinstance(bootstrap_samples, int) or bootstrap_samples < 100:
        raise ValueError("At least 100 bootstrap samples required")
    if not 0 < confidence < 1 or min_seeds < 3:
        raise ValueError("Invalid confidence or minimum seed count")
    ref, ref_values = _cells(ref_axes, ref_iq, projection)
    irb, irb_values = _cells(irb_axes, irb_iq, projection)
    keys = set(ref)
    if set(irb) != keys or any(set(ref[k]) != set(irb[k]) for k in keys):
        raise ValueError("Reference/IRB must have identical (m,repeat,capture) coverage")
    rman = _manifest_index(ref_manifest, "rb", keys)
    iman = _manifest_index(irb_manifest, "irb", keys)
    if ref_manifest.get('pulse_policy') != irb_manifest.get('pulse_policy'):
        raise ValueError('Unpaired manifest pulse_policy')
    for field in ("run_seed", "targets", "tag", "native_source_sha256"):
        if ref_manifest[field] != irb_manifest[field]:
            raise ValueError(f"Unpaired manifest {field}")
    if irb_manifest["interleaved"] not in ("X/2",12):
        raise ValueError("This analysis reports target X/2 only")
    if any(rman[k]["seed"] != iman[k]["seed"] or rman[k]["random_clifford_indices"] != iman[k]["random_clifford_indices"] for k in keys):
        raise ValueError("Reference/IRB random Clifford sequences are not paired")
    lengths = sorted({k[0] for k in keys})
    reps = {m: sorted(k[1] for k in keys if k[0] == m) for m in lengths}
    result = {"schema": "arclight_paired_rb_analysis_v1", "reportable": False,
        "status": "not_reportable", "reasons": [], "target_gate": "X/2", "sources": SOURCES,
        "assumptions": ASSUMPTIONS, "projection": dict(projection), "lengths": lengths,
        "random_sequences_per_length": {str(m):len(reps[m]) for m in lengths},
        "captures_per_sequence": len(next(iter(ref.values()))),
        "reference_projected_points": ref_values.tolist(), "interleaved_projected_points": irb_values.tolist(),
        "F_clifford": None, "F_X_half_conditional": None,
        "formula": {"F_clifford":"(1+p_ref)/2", "F_X_half_conditional":"(1+p_irb/p_ref)/2"}}
    if len(lengths) < 5 or min(len(v) for v in reps.values()) < min_seeds:
        result["reasons"] = ["insufficient_lengths_or_independent_random_sequences"]
        return result
    ref_rows, irb_rows = [], []
    for m in lengths:
        ref_rows.append(np.array([np.mean(list(ref[m,k].values())) for k in reps[m]]))
        irb_rows.append(np.array([np.mean(list(irb[m,k].values())) for k in reps[m]]))
    x = np.asarray(lengths,float)
    yr, yi = np.array([a.mean() for a in ref_rows]), np.array([a.mean() for a in irb_rows])
    sr = np.array([a.std(ddof=1)/np.sqrt(len(a)) for a in ref_rows])
    si = np.array([a.std(ddof=1)/np.sqrt(len(a)) for a in irb_rows])
    result["curves"] = {"reference_mean":yr.tolist(), "interleaved_mean":yi.tolist(),
                         "reference_seed_sem":sr.tolist(), "interleaved_seed_sem":si.tolist()}
    try:
        rf, rp = _fit(x,yr,sr)
        inf, ip = _fit(x,yi,si)
    except (ValueError, FloatingPointError, np.linalg.LinAlgError) as exc:
        result["reasons"].append("decay_fit_failed_or_unidentifiable")
        result["fit_error"] = str(exc)
        return result
    ratio = float(ip[1]/rp[1])
    result.update(reference_fit=rf, interleaved_fit=inf, p_ratio_unclipped=ratio,
        F_clifford_unvalidated=float((1+rp[1])/2), F_X_half_unvalidated=float((1+ratio)/2))
    result["reasons"].extend("reference:"+r for r in rf["reasons"])
    result["reasons"].extend("interleaved:"+r for r in inf["reasons"])
    if not 0 <= ratio <= 1:
        result["reasons"].append("interleaved_to_reference_ratio_outside_0_1")
    # Do not manufacture a precise bootstrap CI when the original fits are unidentified.
    if not rf["identifiable"] or not inf["identifiable"]:
        return result
    rng = np.random.default_rng(bootstrap_seed)
    samples, failures = [], 0
    for _ in range(bootstrap_samples):
        br, bi = [], []
        for rr, ii in zip(ref_rows,irb_rows):
            draw = rng.integers(0,len(rr),len(rr))
            br.append(rr[draw].mean()); bi.append(ii[draw].mean())
        try:
            _, pr = _fit(x,np.asarray(br),sr,start=rp)
            _, pi = _fit(x,np.asarray(bi),si,start=ip)
            samples.append([pr[1],pi[1],pi[1]/pr[1],(1+pr[1])/2,(1+pi[1]/pr[1])/2])
        except (ValueError, FloatingPointError, np.linalg.LinAlgError):
            failures += 1
    arr = np.asarray(samples)
    boot = {"requested":bootstrap_samples, "successful":len(samples), "failures":failures,
            "rng_seed":bootstrap_seed, "confidence":confidence,
            "method":"paired seed bootstrap stratified by length; captures averaged within seed",
            "conditional_on":"fixed independent IQ calibration, acquired captures, pulse implementation and single-exponential model",
            "systematic_model_error_included":False, "intervals_unclipped":{}}
    result["bootstrap"] = boot
    if len(samples) < .9*bootstrap_samples:
        result["reasons"].append("too_many_bootstrap_fit_failures")
        return result
    qs = [(1-confidence)/2,1-(1-confidence)/2]
    for n,name in enumerate(("p_ref","p_irb","p_ratio","F_clifford","F_X_half_conditional")):
        boot["intervals_unclipped"][name] = np.quantile(arr[:,n],qs).tolist()
    boot["ratio_outside_physical_range_fraction"] = float(np.mean((arr[:,2]<0)|(arr[:,2]>1)))
    if boot["intervals_unclipped"]["p_ratio"][1] > 1 or boot["intervals_unclipped"]["p_ratio"][0] < 0:
        result["reasons"].append("conditional_ratio_interval_crosses_physical_range")
    if not result["reasons"]:
        result.update(reportable=True, status="conditional_estimate",
                      F_clifford=float((1+rp[1])/2), F_X_half_conditional=float((1+ratio)/2))
    return result
