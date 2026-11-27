"""Bounded, read-only projection of Controller-owned SHELVE receipts.

This is a display whitelist, not a new acceptance or acquisition path. Missing
historical fields stay missing; neither counts nor capture groups are inferred.
"""
from copy import deepcopy
import math

METHOD = 'sq_shelve_readout_compare'
METRICS = ('assignment_fidelity','assignment_gain_pp','paired_capture_gain_stderr_pp',
           'heldout_captures','conditional_shift_snr','ground_disturbance_ratio')


def _finite(value, low=-1e100, high=1e100):
    return type(value) in (float,int) and math.isfinite(value) and low <= value <= high


def _text(value, limit=256):
    return isinstance(value,str) and 0 < len(value) <= limit


def _condition(value):
    if not isinstance(value,dict):return None
    counts=value.get('counts')
    if not (isinstance(counts,list) and len(counts)==2 and all(isinstance(row,list) and len(row)==2
            and all(type(n) is int and 0<=n<=1000000000 for n in row) for row in counts)):return None
    n0,n1=map(sum,counts)
    if n0<=0 or n1<=0 or value.get('n0')!=n0 or value.get('n1')!=n1:return None
    out={'counts':deepcopy(counts),'n0':n0,'n1':n1}
    for key,expected in (('wrongAs1',counts[0][1]),('wrongAs0',counts[1][0])):
        if key in value:
            if value[key]!=expected:return None
            out[key]=expected
    for key in ('fidelity','standardErrorBinomial'):
        if key in value:
            if not _finite(value[key],0,1):return None
            out[key]=value[key]
    if 'perCaptureFidelity' in value:
        per=value['perCaptureFidelity']
        if not (isinstance(per,dict) and len(per)<=12 and all(isinstance(k,str) and k.isascii()
                and k.isdigit() and str(int(k))==k and int(k)<12 and _finite(v,0,1) for k,v in per.items())):return None
        out['perCaptureFidelity']=dict(per)
    classifier=value.get('classifier')
    if isinstance(classifier,dict):
        direction=classifier.get('direction');threshold=classifier.get('threshold')
        if isinstance(direction,list) and len(direction)==2 and all(_finite(v) for v in direction) and _finite(threshold):
            out['classifier']={'direction':list(direction),'threshold':threshold}
    return out


def _protocol(value):
    if not isinstance(value,dict):return None
    groups=[value.get(k) for k in ('trainingCaptures','validationCaptures')]
    if not all(isinstance(g,list) and 1<=len(g)<=12 and all(type(n) is int and 0<=n<12 for n in g)
               and len(set(g))==len(g) for g in groups):return None
    if set(groups[0]) & set(groups[1]):return None
    if not _text(value.get('type'),256) or type(value.get('logicalLabelsRetained')) is not bool:return None
    return {k:deepcopy(value[k]) for k in ('type','trainingCaptures','validationCaptures','logicalLabelsRetained')}


def _pulses(value):
    if not isinstance(value,dict):return None
    out={}
    for role in ('prep01','probe12'):
        envelope=value.get(role)
        if not isinstance(envelope,dict) or not isinstance(envelope.get('identity'),dict):continue
        original=envelope['identity'];identity={}
        for k in ('policy','transition','shape','readoutContextHash'):
            if _text(original.get(k)):identity[k]=original[k]
        for k in ('durationS','sigmaS','amplitude','frequencyHz','phaseRad','rotationRad','lambda1',
                  'anharmonicityHz','bufferS','xySampleRateHz'):
            if k in original and (original[k] is None or _finite(original[k])):identity[k]=original[k]
        if identity:
            out[role]={'identity':identity}
            if _text(envelope.get('sha256'),64):out[role]['sha256']=envelope['sha256']
    return out or None


def _snapshot(raw):
    out={}
    comparison=raw.get('comparison')
    if isinstance(comparison,dict):
        conditions={key:_condition(comparison.get(key)) for key in ('off','on')}
        if all(conditions.values()):out['comparison']=conditions
    for key,project in (('classifierProtocol',_protocol),('pulseIdentities',_pulses)):
        value=project(raw.get(key))
        if value is not None:out[key]=value
    return out


def measurement_fields(raw,attempt):
    if attempt.get('procedureId')!=METHOD:return {}
    out=_snapshot(raw)
    verification=bool(attempt.get('verificationOperationId'))
    out['shelveStage']='verification' if verification else 'selection'
    selection=raw.get('selectionResult')
    if verification and isinstance(selection,dict):
        first=_snapshot(selection)
        if first:
            metrics=selection.get('metrics',{})
            first['metrics']={k:metrics[k] for k in METRICS if _finite(metrics.get(k))}
            if _text(selection.get('acquiredAt'),128):first['acquiredAt']=selection['acquiredAt']
            out['shelveSelection']=first
    return out
