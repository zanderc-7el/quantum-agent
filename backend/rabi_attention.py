"""Small display projection of a failed native Rabi acquisition. No fitting or file access."""
import math
import re


def rabi_fit_evidence(node):
    if not isinstance(node, dict):
        return None
    procedure = node.get('procedureId')
    if procedure not in ('sq_rabi_amplitude', 'sq_rabi_time'):
        return None
    result = node.get('result') or {}
    attempt = node.get('latestAttemptId')
    if (not isinstance(result, dict) or not isinstance(attempt, str) or not attempt
            or result.get('sourceRunId') not in (attempt+'/measure', attempt+'/verify')
            or result.get('dataKind') != 'real' or result.get('partial') or result.get('stale')
            or not isinstance(result.get('quality'), dict) or result['quality'].get('status') != 'failed'):
        return None
    native = result.get('analysisQuality')
    if not isinstance(native, dict) or native.get('accepted') is not False or native.get('procedureId') != procedure:
        return None
    digest = native.get('raw_sha256')
    if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
        return None
    artifacts = result.get('artifacts')
    if not isinstance(artifacts, list) or not any(isinstance(a, dict) and a.get('kind')=='raw' and a.get('sha256')==digest for a in artifacts):
        return None
    groups = native.get('groups')
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(groups[0], dict):
        return None
    repeats = groups[0].get('repeats')
    parameters = result.get('parameters')
    expected = parameters.get('capture_repeats') if isinstance(parameters, dict) else None
    if type(expected) is not int or not 1 <= expected <= 32 or not isinstance(repeats, list) or len(repeats)!=expected:
        return None
    captures = []
    for index, repeat in enumerate(repeats, 1):
        if not isinstance(repeat, dict) or type(repeat.get('repeat')) is not int or repeat['repeat'] != index or 'fit' not in repeat:
            return None
        fit = repeat['fit']
        diagnostics = repeat.get('diagnostics')
        reasons = diagnostics.get('rejection_reasons') if isinstance(diagnostics, dict) else None
        if not isinstance(reasons, list):
            return None
        if fit is None and reasons:
            captures.append({'index':index,'valid':False})
        elif isinstance(fit, dict) and not reasons and fit.get('model') in ('sinusoid_decay_unresolved','damped_sinusoid'):
            value = fit.get('value')
            cycles = fit.get('cycles_in_window')
            if not all(type(v) in (float,int) and math.isfinite(v) and v > 0 for v in (value, cycles)):
                return None
            captures.append({'index':index,'valid':True,'cycles':cycles})
        else:
            return None
    if all(c['valid'] for c in captures):
        return None
    # Native _repeat_agreement needs every acquired repeat and at least two valid fits.
    return {'schema':'qcal-rabi-fit-display/1','stage':result['sourceRunId'].rsplit('/',1)[1],
            'requiredValid':max(2, expected),'captures':captures}
