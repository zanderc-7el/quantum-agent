"""Strict published-route and measured-provider contracts; no I/O at import."""
from copy import deepcopy
import datetime as dt
import math
import re
from types import MappingProxyType

POLICY='f8_offset_only_measured_restore_v1'
COMMAND_POLICY='f8_offset_only_commanded_return_v2'
SUPPORTED_POLICIES=(POLICY,COMMAND_POLICY)
SCHEMA='qcal-bias-route/1'
# Deliberately empty: no documented site measurement/get-setting protocol has
# been supplied. Adding a provider is a reviewed source change, never a JSON
# import path or arbitrary SCPI expression. Injections are in-process tests only.
PROVIDERS=MappingProxyType({})
TEXT=('resourceId','deviceKey','lineId','measurementPlane','providerId','stationIdentity','measurementInstrumentIdentity')
HASHES=('preparedStaticDcEvidenceSha256','confirmationEvidenceSha256','providerProtocolSha256')
NUMBERS={'minimumV':(-10,10),'maximumV':(-10,10),'minimumMeasuredV':(-100,100),'maximumMeasuredV':(-100,100),'maxStepV':(1e-9,1),
    'maxSlewVPerSecond':(1e-9,10),'maxRampSeconds':(.01,120),'settleSeconds':(0,30),
    'readbackIntervalSeconds':(.001,10),'readTimeoutSeconds':(.01,10),
    'toleranceV':(1e-9,1),'restoreTimeoutSeconds':(.1,180),'maximumUncertaintyV':(0,1)}
REQUIRED=set(TEXT+HASHES+tuple(NUMBERS))|{'schema','board','channel','unit','polarity',
    'stabilitySamples','restorePolicy','offsetToOutput'}

class ContractError(ValueError):pass

def number(value,name,lo=-math.inf,hi=math.inf):
    if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
        raise ContractError('Invalid '+name)
    return float(value)

def hash_value(value,name):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise ContractError('Invalid '+name)

def validate_route(source):
    if isinstance(source,dict) and source.get('schema')=='qcal-bias-command-route/1':
        from .command_contracts import validate_command_route
        return validate_command_route(source)
    if not isinstance(source,dict):raise ContractError('Missing biasRoutes resource route')
    missing=sorted(REQUIRED-set(source))
    if missing:raise ContractError('Missing bias route fields: '+', '.join(missing))
    unknown=set(source)-REQUIRED
    if unknown:raise ContractError('Unknown bias route fields: '+', '.join(sorted(unknown)))
    r=deepcopy(source)
    if r['schema']!=SCHEMA or r['unit']!='V' or r['restorePolicy']!='restore_initial_verified':raise ContractError('Unsupported schema/unit/restorePolicy')
    for key in TEXT:
        if not isinstance(r[key],str) or not 1<=len(r[key])<=128 or any(ord(c)<32 for c in r[key]):raise ContractError('Invalid '+key)
    for key in HASHES:hash_value(r[key],key)
    for key,bounds in NUMBERS.items():number(r[key],key,*bounds)
    for key,lo,hi in [('board',0,5),('channel',0,3),('stabilitySamples',2,20)]:
        if type(r[key]) is not int or not lo<=r[key]<=hi:raise ContractError('Invalid '+key)
    if type(r['polarity']) is not int or r['polarity'] not in (-1,1):raise ContractError('Invalid polarity')
    if r['minimumV']>=r['maximumV']:raise ContractError('Invalid operating voltage interval')
    if r['minimumMeasuredV']>=r['maximumMeasuredV']:raise ContractError('Invalid measured voltage interval')
    if r['maximumUncertaintyV']>=r['toleranceV']:raise ContractError('Readback uncertainty must be below toleranceV')
    m=r['offsetToOutput']
    if not isinstance(m,dict) or set(m)!={'gain','offsetV','uncertaintyV','evidenceSha256'}:raise ContractError('Missing verified offsetToOutput mapping')
    gain=number(m['gain'],'offsetToOutput.gain',-100,100);number(m['offsetV'],'offsetToOutput.offsetV',-10,10);hash_value(m['evidenceSha256'],'offsetToOutput.evidenceSha256')
    if not gain or math.copysign(1,gain)!=r['polarity']:raise ContractError('Mapping gain contradicts polarity')
    number(m['uncertaintyV'],'mapping uncertainty',0,r['toleranceV']-r['maximumUncertaintyV'])
    if 2*r['maximumUncertaintyV']+m['uncertaintyV']>r['toleranceV']:raise ContractError('Declared uncertainty cannot verify restoration within tolerance')
    for value in (r['minimumV'],r['maximumV']):
        predicted=gain*value+m['offsetV']
        if not r['minimumMeasuredV']<=predicted-m['uncertaintyV']<=predicted+m['uncertaintyV']<=r['maximumMeasuredV']:raise ContractError('Command interval exceeds measured-plane operating bounds')
    steps=math.ceil((r['maximumV']-r['minimumV'])/r['maxStepV'])
    if steps>2048:raise ContractError('Route worst-case ramp exceeds 2048 writes')
    worst=(r['maximumV']-r['minimumV'])/r['maxSlewVPerSecond']+steps*2*r['readTimeoutSeconds']
    stable=r['settleSeconds']+r['stabilitySamples']*(r['readbackIntervalSeconds']+r['readTimeoutSeconds'])
    if worst+stable>min(r['maxRampSeconds'],r['restoreTimeoutSeconds']):raise ContractError('Ramp/restore deadline cannot cover declared worst-case commands and readback')
    return r

def provider_for(route,*,registry=None,allow_fixture=False):
    if is_commanded(route):validate_route(route);return None
    entry=(PROVIDERS if registry is None else registry).get(route['providerId'])
    if entry is None:raise ContractError('Missing reviewed measured-output provider: '+route['providerId'])
    if getattr(entry,'fixture_only',False) and not allow_fixture:raise ContractError('Fixture provider is not physical execution eligible')
    for attr,expected in [('provider_id',route['providerId']),('protocol_sha256',route['providerProtocolSha256']),
            ('measurement_kind','measured_output'),('setting_kind','hardware_setting_register')]:
        if getattr(entry,attr,None)!=expected:raise ContractError('Invalid provider '+attr)
    if not isinstance(getattr(entry,'lock_names',None),tuple) or not entry.lock_names:raise ContractError('Provider requires fixed physical lock identities')
    return entry

def is_commanded(route):return isinstance(route,dict) and route.get('schema')=='qcal-bias-command-route/1'

def policy_for(route):
    if is_commanded(route):return COMMAND_POLICY
    return POLICY

def command_authorization(route,operation_id,*,stage='selection',entry_evidence=None,start_confirmation=None):
    from .command_contracts import authorization
    return authorization(route,{'operationId':operation_id,'resourceId':route['resourceId']},stage=stage,entry_evidence=entry_evidence,start_confirmation=start_confirmation)

def timeout_for(route):return route['commandTimeoutSeconds'] if is_commanded(route) else route['readTimeoutSeconds']

def end_timeout_for(route):return route['endTimeoutSeconds'] if is_commanded(route) else route['restoreTimeoutSeconds']

def required_fields(route):
    if is_commanded(route):
        from .command_contracts import REQUIRED as command_required
        return command_required
    return REQUIRED

def validate_snapshot(value,route,nonce,*,fresh=True):
    if not isinstance(value,dict) or value.get('nonce')!=nonce or value.get('providerId')!=route['providerId'] or value.get('protocolSha256')!=route['providerProtocolSha256']:
        raise ContractError('Readback provider/request identity mismatch')
    try:
        stamp=dt.datetime.fromisoformat(value['observedAt'])
        age=(dt.datetime.now(dt.timezone.utc)-stamp).total_seconds()
        if stamp.tzinfo is None or (fresh and not -.5<=age<=route['readTimeoutSeconds']+1):raise ValueError()
    except (KeyError,TypeError,ValueError):raise ContractError('Readback is missing a fresh timezone-aware timestamp') from None
    setting=value.get('setting',{});measured=value.get('measurement',{})
    output=value.get('outputState',{})
    if output.get('kind')!='hardware_setting_register' or output.get('mode')!='static_dc' or output.get('outputEnabled') is not True or not isinstance(output.get('raw'),str) or not 1<=len(output['raw'])<=2048:raise ContractError('Fresh static DC mode/output state is required')
    hash_value(output.get('sharedSettingsSha256'),'shared settings identity')
    if setting.get('kind')!='hardware_setting_register' or measured.get('kind')!='measured_output':raise ContractError('Cache/ACK is not hardware setting and measured output')
    for item in (setting,measured):
        if not isinstance(item.get('raw'),str) or not 1<=len(item['raw'])<=2048:raise ContractError('Missing bounded raw readback')
        number(item.get('valueV'),'readback voltage',-100,100)
    if measured.get('plane')!=route['measurementPlane']:raise ContractError('Wrong measurement plane')
    if measured.get('instrumentIdentity')!=route['measurementInstrumentIdentity']:raise ContractError('Wrong measurement instrument identity')
    number(measured.get('uncertaintyV'),'measurement uncertainty',0,route['maximumUncertaintyV'])
    number(setting['valueV'],'hardware setting',route['minimumV'],route['maximumV'])
    if not route['minimumMeasuredV']<=measured['valueV']-measured['uncertaintyV']<=measured['valueV']+measured['uncertaintyV']<=route['maximumMeasuredV']:raise ContractError('Measured voltage outside operating bounds')
    return deepcopy(value)

def target_output(route,setting):return route['offsetToOutput']['gain']*setting+route['offsetToOutput']['offsetV']

def validate_held_receipt(receipt,route,*,setting=None,measured=None):
    """Archived readbacks keep their original timestamps; never refreshed here."""
    if is_commanded(route):
        from .command_contracts import validate_command_receipt
        return validate_command_receipt(receipt,route,setting=setting)
    validate_snapshot(receipt,route,receipt.get('nonce'),fresh=False)
    samples=receipt.get('stabilitySamples')
    if not isinstance(samples,list) or len(samples)!=route['stabilitySamples']:raise ContractError('Missing actual stable measured samples')
    target=receipt['requestedSettingV'] if setting is None else setting
    expected=target_output(route,target) if measured is None else measured
    seen=set();values=[]
    for sample in samples:
        validate_snapshot(sample,route,sample.get('nonce'),fresh=False)
        nonce=sample.get('nonce')
        if not isinstance(nonce,str) or not nonce or nonce in seen:raise ContractError('Reused/missing readback request nonce')
        seen.add(nonce);actual=sample['measurement'];values.append(actual['valueV'])
        if abs(sample['setting']['valueV']-target)>route['toleranceV']/max(1,abs(route['offsetToOutput']['gain'])):raise ContractError('Archived setting differs from held target')
        if abs(actual['valueV']-expected)+actual['uncertaintyV']+route['offsetToOutput']['uncertaintyV']>route['toleranceV']:raise ContractError('Archived measured output differs from held target')
    if max(values)-min(values)>route['toleranceV']:raise ContractError('Archived bias samples are unstable')
    if any(receipt.get(k)!=samples[-1].get(k) for k in ('nonce','setting','measurement','outputState','observedAt')):raise ContractError('Held receipt differs from final physical sample')
    return receipt
