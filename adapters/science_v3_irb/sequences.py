"""Native Clifford generator and independently checked primitive pulse binding.

Imported only after the fixed native sources have been verified and namespaced.
"""
from copy import deepcopy
import math
from functools import cached_property
import numpy as np
from paired_rb_support import PairedSingleQubitRB,sequence_manifest,ideal_gate_closure
from adapters.science_runtime.policy import digest
from .plans import POLICY,normalize_parameters,target_pulse,identity
from . import beta

class CachedNativeRB(PairedSingleQubitRB):
    """Cache immutable native lookup tables; do not replace native synthesis."""
    @cached_property
    def rots(self):return super().rots
    @cached_property
    def rot2idx(self):return super().rot2idx
    @cached_property
    def cgates(self):return super().cgates

def build_campaign(parameters):
    p,budget=normalize_parameters(parameters);pulse=target_pulse(p)
    axes=[{'clifford_m':m,'repeat':k} for m in p['rb_lengths'] for k in range(p['rb_sequences'])]
    manifests={}
    for mode in ('rb','irb'):
        native=CachedNativeRB(targets=(1,),tag='science_v3_drag_irb_v1',mode=mode,
                                   interleaved='X/2' if mode=='irb' else None,include_inverse=True,add_measure=True)
        manifests[mode]=sequence_manifest(native,axes,p['rb_master_seed'])
    indices={mode:{(s['clifford_m'],s['repeat']):s for s in manifest['sequences']} for mode,manifest in manifests.items()}
    rows=[];primitive_count=0
    def add(stage,capture,condition,axis,gates,state=None,sequence=None):
        nonlocal primitive_count
        primitive_count+=len(gates)
        if primitive_count>100000:raise ValueError('IRB native primitive/serialized plan budget exceeded')
        duration=sum(g['pulse']['durationNs']+2 for g in gates)
        if duration>79700:raise ValueError('Native IRB sequence exceeds fixed control window')
        row={'id':len(rows),'stage':stage,'capture':capture,'condition':condition,'axisValue':axis,'logicalState':state,'gates':gates}
        if sequence:row.update(sequenceKey={'clifford_m':sequence['clifford_m'],'repeat':sequence['repeat']},nativeSequenceSha256=digest(sequence))
        rows.append(row)
    def references(stage):
        for capture in range(p['reference_captures']):
            for state in (0,1):
                prepared=deepcopy(pulse);prepared['amplitude']*=state
                add(stage,capture,'reference',state,[{'axis':'x','theta':math.pi,'pulse':prepared}],state)
    references('pre')
    for capture in range(p['rb_captures']):
        rng=np.random.default_rng((p['rb_master_seed']+capture)%(2**32))
        for index in rng.permutation(len(axes)):
            key=(axes[index]['clifford_m'],axes[index]['repeat'])
            for mode in (('rb','irb') if rng.integers(2)==0 else ('irb','rb')):
                seq=indices[mode][key];gates=[]
                if not ideal_gate_closure(seq['native_gates'])['closed']:raise ValueError('Native sequence does not close')
                for name,targets,args in seq['native_gates']:
                    if name=='MEASURE':continue
                    if targets!=[1] or name not in ('X','Y','RX','RY'):raise ValueError('Unsupported native Clifford primitive')
                    theta=math.pi if name in ('X','Y') else args[0]
                    if theta not in (math.pi,math.pi/2,-math.pi/2):raise ValueError('Unexpected Clifford rotation')
                    gates.append({'axis':name[-1].lower(),'theta':theta,'pulse':deepcopy(pulse)})
                add(mode,capture,mode,key[0],gates,sequence=seq)
    references('post')
    if len(rows)!=budget['rows']:raise ValueError('Campaign row budget differs from generated native sequences')
    bundle={'schema':'science-v3-irb-sequences/1','campaignPolicy':beta.IRB_POLICY if beta.empirical(p) else POLICY,'pulseIdentity':identity(p,half=False),
            'targetPulseIdentity':identity(p),'identityCliffordDurationS':0.,'identityPolicy':'native generator emits no primitive for I',
            'acquisitionOrder':'PRE then adjacent paired RB/IRB rows with randomized pair order then POST',
            'nativeManifests':manifests,'rows':rows,'budget':budget,'totalPrimitiveCount':primitive_count}
    bundle['sha256']=digest(bundle)
    return bundle

def verify_campaign(bundle,parameters):
    if bundle!=build_campaign(parameters):raise ValueError('Native sequence manifest or pulse mapping differs')
    return True
