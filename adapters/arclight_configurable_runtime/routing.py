"""Pure, fail-closed routing contract for the configurable F8 receiver."""
from copy import deepcopy
import math


def validate_route(route):
    if not isinstance(route, dict):
        raise ValueError('A deployment route is required')
    route = deepcopy(route)
    def pair(key, boards):
        value = route.get(key)
        if not isinstance(value, (list, tuple)) or len(value) != 2 or any(type(x) is not int for x in value):
            raise ValueError(key+' must be a physical module/channel pair')
        if value[0] not in boards or value[1] not in (0, 1):
            raise ValueError(key+' is unavailable on the bounded F8 runtime')
        route[key] = list(value)
        return value
    xy = pair('xyPhysical', range(8))
    ro = pair('readoutPhysical', (8,))
    ad = pair('adcPhysical', (0,))
    for key, expected in [('xyLogical', [xy[0]+6,xy[1]]), ('readoutLogical', [ro[0]+6,ro[1]]), ('adcLogical',[ad[0]+15,ad[1]])]:
        if key in route and route[key] != expected:
            raise ValueError(key+' does not match the F8 physical-to-logical map')
        route[key] = expected
    rate = route.get('adcSampleRateHz', 2.8e9)
    if type(rate) not in (int,float) or not math.isfinite(rate) or rate != 2.8e9:
        raise ValueError('Unsupported ADC clock; bounded runtime requires 2.8GHz')
    route['adcSampleRateHz'] = rate
    return route


def validate_instrument(instrument):
    if not isinstance(instrument,dict) or instrument.get('id') != 'CeyearStationF8':
        raise ValueError('Only the bounded CeyearStationF8 driver is available')
    host, port = instrument.get('host'), instrument.get('port')
    if not isinstance(host,str) or not host or any(c in host for c in '\r\n:') or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid instrument endpoint')
    if instrument.get('address') != f'TCPIP::{host}::{port}::SOCKET':
        raise ValueError('Instrument address does not match ownership-check endpoint')
    return deepcopy(instrument)


def apply_job_routing(job, resource_id, route, instrument, visa_library):
    route = validate_route(route)
    instrument = validate_instrument(instrument)
    job['target'] = resource_id
    job['resourceId'] = resource_id
    job['routing'] = route
    job['instrument'] = instrument
    job['visaLibrary'] = visa_library
    job['xy_physical_channel'] = route['xyPhysical']
    cfg = job['config']
    cfg['_agent'] = {'source_qubit':resource_id,'arclight_qubit':'Q1','hardware_model':instrument['id'],
        'hardware_name':instrument['id'],'hardware_group':'agent_f8','routing':deepcopy(route)}
    cfg['Chip']['name'] = 'dynamic_'+resource_id+'_as_Q1'
    cfg['ADC_Meas']['B1']['para']['ADC_sRate'] = route['adcSampleRateHz']
    return job


def receiver_route(job):
    route = validate_route(job.get('routing'))
    instrument = validate_instrument(job.get('instrument'))
    if not job.get('target') or job.get('resourceId') != job['target']:
        raise ValueError('External resource identity is missing or inconsistent')
    if job.get('xy_physical_channel') != route['xyPhysical']:
        raise ValueError('Job XY channel differs from deployment route')
    if job['config']['ADC_Meas']['B1']['para']['ADC_sRate'] != route['adcSampleRateHz']:
        raise ValueError('Compiler ADC rate differs from receiver route')
    return route, instrument
