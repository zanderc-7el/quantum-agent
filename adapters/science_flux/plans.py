"""Finite explicit bias/cavity campaigns. No site voltage defaults."""
from copy import deepcopy
import math
from .contracts import POLICY,number,validate_route,is_commanded,end_timeout_for
from adapters.science_runtime.policy import digest

METHODS={'ro_flux_scan':('Measured bias / cavity scan','s01_readout_frequency',('cavity_seed',)),
         'sq_flux_point_recheck':('Same-bias local cavity / optional f01','s02_qubit_frequency',('cavity_seed','flux_scan'))}
BOUNDS={'readout_hz':(6.5e9,7.6e9),'readout_amp':(.001,.99),'readout_width_s':(200e-9,10e-6),
    'xy_delay_s':(0,10e-6),'xy_attenuation_db':(0,40),'xy_filter_mhz':(100,10000),
    'shots':(16,1024),'capture_repeats':(2,4),'acquisitions_per_point':(1,4),
    'drive_amp':(.001,.99),'spectroscopy_width_s':(4e-9,20e-6)}
INTEGERS={'shots','capture_repeats','acquisitions_per_point','xy_filter_mhz'}
DEFAULTS={'shots':128,'capture_repeats':2,'acquisitions_per_point':1}
COMMON=set(BOUNDS)-{'drive_amp','spectroscopy_width_s'}
FIELDS={'ro_flux_scan':COMMON|{'bias_values_v','readout_frequencies_hz'},
    'sq_flux_point_recheck':COMMON|{'local_readout_frequencies_hz','drive_frequencies_hz','drive_amp','spectroscopy_width_s'}}
AXIS_RULES={'bias_values_v':(1,21,-10,10),'readout_frequencies_hz':(5,101,6.5e9,7.6e9),
            'local_readout_frequencies_hz':(5,101,6.5e9,7.6e9),'drive_frequencies_hz':(5,101,1e6,10e9)}
MAX_ROWS=512

def axis(value,key):
    lo,hi,minimum,maximum=AXIS_RULES[key]
    if not isinstance(value,list) or not lo<=len(value)<=hi:raise ValueError('Explicit bounded axis required: '+key)
    result=[number(v,key,minimum,maximum) for v in value]
    if len(set(result))!=len(result):raise ValueError('Duplicate axis values: '+key)
    if key!='bias_values_v' and any(a>=b for a,b in zip(result,result[1:])):raise ValueError('Frequency axis must increase strictly: '+key)
    return result

def build_campaign(method,inputs,route,*,point=None):
    if method not in METHODS:raise ValueError('Unsupported flux procedure')
    route=validate_route(route);p={**DEFAULTS,**{k:v for k,v in inputs.items() if k in FIELDS[method]}}
    for key in COMMON:
        p[key]=number(p.get(key),key,*BOUNDS[key])
        if key in INTEGERS:
            if isinstance(inputs.get(key,DEFAULTS.get(key)),bool) or p[key]!=int(p[key]):raise ValueError('Integer required: '+key)
            p[key]=int(p[key])
    if abs(p['readout_width_s']*2e9-round(p['readout_width_s']*2e9))>1e-6:raise ValueError('Readout duration is off native 0.5 ns grid')
    cuts=[]
    if method=='ro_flux_scan':
        p['bias_values_v']=axis(p.get('bias_values_v'),'bias_values_v')
        frequencies=p['readout_frequencies_hz']=axis(p.get('readout_frequencies_hz'),'readout_frequencies_hz')
        cuts=[{'phase':'baseline','pointId':'baseline','requestedSettingV':None,'frequenciesHz':frequencies}]
        cuts += [{'phase':'cavity','pointId':'bias-'+str(i),'requestedSettingV':v,'frequenciesHz':frequencies} for i,v in enumerate(p['bias_values_v'])]
    else:
        if not isinstance(point,dict) or not point.get('recordId') or not point.get('pointId'):raise ValueError('Server-resolved source point is required')
        if point.get('resourceId')!=route['resourceId'] or point.get('routeSha256')!=digest(route):raise ValueError('Source point belongs to different resource/bias route')
        band=point.get('usableReadoutBandHz')
        if not isinstance(band,list) or len(band)!=2 or not all(type(v) in (int,float) and math.isfinite(v) for v in band) or band[0]>=band[1]:raise ValueError('Source usable readout band is missing')
        frequencies=p['local_readout_frequencies_hz']=axis(p.get('local_readout_frequencies_hz'),'local_readout_frequencies_hz')
        if frequencies[0]<band[0] or frequencies[-1]>band[1]:raise ValueError('Local cavity axis exceeds original usable band')
        cuts=[{'phase':'local_cavity','pointId':point['pointId'],'requestedSettingV':point['requestedSettingV'],'frequenciesHz':frequencies}]
        if p.get('drive_frequencies_hz'):
            p['drive_frequencies_hz']=axis(p['drive_frequencies_hz'],'drive_frequencies_hz')
            for key in ('drive_amp','spectroscopy_width_s'):p[key]=number(p.get(key),key,*BOUNDS[key])
            if abs(p['spectroscopy_width_s']*4e9-round(p['spectroscopy_width_s']*4e9))>1e-6:raise ValueError('Spectroscopy pulse duration is off native 0.25 ns grid')
            cuts.append({'phase':'f01','pointId':point['pointId'],'requestedSettingV':point['requestedSettingV'],
                'frequenciesHz':p['drive_frequencies_hz'],'readoutPolicy':'fresh_accepted_local_cavity'})
    rows=[]
    for index,cut in enumerate(cuts):
        if cut['requestedSettingV'] is not None:number(cut['requestedSettingV'],'requested offset',route['minimumV'],route['maximumV'])
        cut['cutId']=index;cut['rows']=[]
        for capture in range(p['capture_repeats']):
            # Reverse the next capture to expose order-dependent drift.
            for frequency_index in (range(len(cut['frequenciesHz'])) if capture%2==0 else reversed(range(len(cut['frequenciesHz'])))):
                f=cut['frequenciesHz'][frequency_index]
                for condition in (('off','on') if cut['phase']=='f01' else ('off',)):
                    row={'id':len(rows),'cutId':index,'pointId':cut['pointId'],'phase':cut['phase'],'capture':capture,
                         'frequencyHz':f,'axisValue':f,'frequencyIndex':frequency_index,'condition':condition,
                         'requestedSettingV':cut['requestedSettingV']}
                    rows.append(row);cut['rows'].append(row['id'])
    if len(rows)>MAX_ROWS:raise ValueError('Campaign exceeds 512 acquired rows; split into separately authorized bounded operations')
    native_samples=len(rows)*round((81e-6+p['readout_width_s'])*6e9)
    if native_samples>400_000_000:raise ValueError('Native compiled-sample budget exceeded')
    # Includes initial unknown-in-range entry, each cut transition, restoration,
    # and one extra read per ramp command. Site timing limits are not defaults.
    command_mode=is_commanded(route)
    # Verification may enter at the preceding end coordinate and must ramp
    # through the complete declared range before its first acquisition.
    ramp_count=len(cuts)+1+(1 if command_mode else 0)
    commands=ramp_count*math.ceil((route['maximumV']-route['minimumV'])/route['maxStepV'])
    if command_mode:commands+=1+len(cuts)  # explicit start and repeated identical point/end commands
    snapshots=0 if command_mode else (2*len(rows)+len(cuts)+2)*route['stabilitySamples']+commands+len(cuts)+4
    if commands>4096 or snapshots>4096:raise ValueError('Campaign exceeds bounded 4096 ramp-command/readback journal budget')
    stable=route['settleSeconds'] if command_mode else route['settleSeconds']+route['stabilitySamples']*(route['readbackIntervalSeconds']+route['readTimeoutSeconds'])
    seconds=(len(rows)*(p['acquisitions_per_point']*22+2*stable)+len(cuts)*route['maxRampSeconds']
        +end_timeout_for(route)+120+(route['maxRampSeconds'] if command_mode else 0))
    if seconds>14_400:raise ValueError('Conservative acquisition/ramp/cleanup wall-time budget exceeds four hours')
    return {'parameters':p,'cuts':cuts,'rows':rows,'totalRows':len(rows),
        'estimated_total_shots':len(rows)*p['shots']*p['acquisitions_per_point'],
        'maximumRampCommands':commands,'maximumReadbackSnapshots':snapshots,'maximumNativeSamples':native_samples,'requiredSeconds':seconds,
        'cleanupReserveSeconds':end_timeout_for(route)+90,'sourcePoint':deepcopy(point)}
