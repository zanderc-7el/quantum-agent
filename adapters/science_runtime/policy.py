"""Strict scalar-only compile policy. No arbitrary circuits, code or plugins."""
import hashlib
import json
import math
from pathlib import Path

POLICY = 'native_offline_segmented_gaussian_cos_drag_v1'
NATIVE = 'adapters/arclight_configurable_runtime'
FROZEN = {'adapters/arclight_runtime/source_manifest.json': '1531ee2cd78774e6c6a5e089be54c3f35a98d8c0fdf03aa0547169106527a322',
          NATIVE+'/source_manifest.json': '47140c511810a155add2c6bb93615fdc122efdc3e4226d228ab1d5a6a47b4f7f'}
MAX_SAMPLES = 650000
TIMEOUT = 25

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()

def verify_sources(root):
    root=Path(root).resolve()
    hashes={}
    names=list(FROZEN)+['adapters/science_runtime/source_manifest.json']
    for name in names:
        path=root/name
        actual=sha(path)
        if name in FROZEN and actual!=FROZEN[name]:raise ValueError('Frozen native manifest changed: '+name)
        rows=json.loads(path.read_text(encoding='utf-8'))
        for row in rows:
            item=(root/row['path']).resolve()
            if not item.is_relative_to(root) or sha(item)!=row['sha256']:
                raise ValueError('Source integrity failed: '+row['path'])
        hashes[name]={'sha256':actual,'fileCount':len(rows)}
    return hashes

def value(inputs,key,default,low,high,rate=None):
    x=inputs.get(key,default)
    if isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) or not low<=x<=high:
        raise ValueError(f'{key} must be finite in [{low}, {high}]')
    if rate and abs(x*rate-round(x*rate))>1e-7:raise ValueError(key+' is outside the native sample grid')
    return float(x)

def normalize(request):
    if not isinstance(request,dict) or set(request)-{'kind','input','scope','source','policy'}:raise ValueError('Unexpected compile request fields')
    if request.get('policy',POLICY)!=POLICY:raise ValueError('Unsupported native compile policy')
    kind=request.get('kind','pulse')
    if kind not in ('pulse','drag','shelve'):raise ValueError('Only pulse, drag and shelve compile templates are supported')
    p=request.get('input',{})
    allowed={'shape','durationNs','amplitude','phaseRad','sigmaNs','lambda1','anharmonicityHz','anharmonicitySource',
             'frequencyHz','gapNs','readoutDelayNs','readoutWidthNs','readoutAmplitude','readoutFrequencyHz','adcDelayNs',
             'f12Hz','probeDurationNs','probeAmplitude','rotationRad','virtualZRad'}
    if not isinstance(p,dict) or set(p)-allowed:raise ValueError('Unexpected pulse configuration fields')
    shape=p.get('shape','CosPulse' if kind=='drag' else 'DC')
    if shape not in ('DC','Gaussian','CosPulse'):raise ValueError('Only native DC, Gaussian and CosPulse are supported')
    n={'shape':shape}
    rules={'durationNs':(20,4,2000,4),'amplitude':(.3,0,.99,None),'phaseRad':(0,-2*math.pi,2*math.pi,None),
           'frequencyHz':(4.86269e9,1e6,10e9,None),'lambda1':(0,-10,10,None),
           'gapNs':(300,0,10000,4),'readoutDelayNs':(0,0,10000,2),'readoutWidthNs':(2200,200,10000,2),
           'readoutAmplitude':(.8,0,.99,None),'readoutFrequencyHz':(7.00951e9,6.5e9,7.6e9,None),
           'adcDelayNs':(0,-1000,10000,2.8),'rotationRad':(math.pi,-2*math.pi,2*math.pi,None),
           'virtualZRad':(0,-2*math.pi,2*math.pi,None)}
    for k,args in rules.items():n[k]=value(p,k,*args)
    if abs(n['rotationRad'])<1e-12:raise ValueError('Zero rotation is not an XY pulse template')
    if shape=='Gaussian':n['sigmaNs']=value(p,'sigmaNs',None,.25,n['durationNs']/2)
    if n['lambda1']!=0:
        n['anharmonicityHz']=value(p,'anharmonicityHz',None,-2e9,2e9)
        if abs(n['anharmonicityHz'])<1e6:raise ValueError('DRAG requires explicit nonzero signed anharmonicity in Hz')
        source=p.get('anharmonicitySource')
        if not isinstance(source,str) or not 1<=len(source)<=200:raise ValueError('DRAG requires an explicit anharmonicity source label')
        n['anharmonicitySource']=source
    else:n.update(anharmonicityHz=None,anharmonicitySource=None)
    if kind=='shelve':
        n['f12Hz']=value(p,'f12Hz',None,1e6,10e9)
        n['probeDurationNs']=value(p,'probeDurationNs',n['durationNs'],4,2000,4)
        n['probeAmplitude']=value(p,'probeAmplitude',n['amplitude'],0,.99)
        if shape=='Gaussian' and n['sigmaNs']>n['probeDurationNs']/2:raise ValueError('sigmaNs exceeds probe half-width')
    if n['durationNs']*(2 if kind=='shelve' else 1)+n['gapNs']>79000:raise ValueError('XY exceeds native timing window')
    duration_ns=80000+n['readoutDelayNs']+n['readoutWidthNs']+1000
    if round(duration_ns*6)>MAX_SAMPLES:raise ValueError('Total native sample budget exceeded')
    return {'kind':kind,'input':n,'scope':request.get('scope',{}),'source':request.get('source',{}),'policy':POLICY}
