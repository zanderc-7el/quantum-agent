"""Frozen real native compiler plus actual five-pulse area and sample evidence."""
from adapters.science_v3_irb.compiler import compile_row as original_compile,archive_row as original_archive
from adapters.science_runtime.policy import digest
from .plans import N
import numpy as np

def compile_row(row,base,parameters):
    if row.get('gateRepeats')!=N or len(row.get('gates',[]))!=N or any(g!=row['gates'][0] for g in row['gates']):
        raise ValueError('Five identical X gate row required')
    seq,waves=original_compile(row,base,parameters)
    segments=[s for s in seq.segments if s['channel']=='xy']
    if len(segments)!=N:raise ValueError('Native compiler did not produce five XY pulses')
    for s in segments:
        i=seq.baseband_arrays[s['basebandI']['array']];q=seq.baseband_arrays[s['basebandQ']['array']]
        s['areaISeconds']=float(np.sum(i)/s['sampleRateHz']);s['areaQSeconds']=float(np.sum(q)/s['sampleRateHz'])
        s['areaUnit']='waveform_scale seconds'
    signature=lambda s: {k:s[k] for k in ('sampleCount','sampleRateHz','envelopeDurationNs','bufferNs','shape',
        'shapeParameters','phaseRad','frequencyHz','betaSeconds','areaISeconds','areaQSeconds')}|{
        'basebandISha256':s['basebandI']['sha256'],'basebandQSha256':s['basebandQ']['sha256']}
    single=signature(segments[0])
    if any(signature(s)!=single for s in segments):raise ValueError('Compiled five gates do not have identical native I/Q and area')
    seq.repeat5_evidence={'gateRepeats':N,'singlePulse':single,'singlePulseSha256':digest(single),
        'segmentIds':[s['id'] for s in segments],'startSample':segments[0]['startSample'],
        'endSample':segments[-1]['endSample'],'sampleRateHz':segments[0]['sampleRateHz'],
        'totalControlDurationS':sum(s['sampleCount']/s['sampleRateHz'] for s in segments),
        'readoutStartSample':next(s['startSample'] for s in seq.segments if s['channel']=='readout')}
    seq.repeat5_evidence['sha256']=digest(seq.repeat5_evidence)
    return seq,waves

def archive_row(path,seq,expected):
    result=original_archive(path,seq,expected);result['repeat5']=seq.repeat5_evidence
    return result
