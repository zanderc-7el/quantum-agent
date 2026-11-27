"""Attach a published bias route to an observed resource, without design claims."""
from copy import deepcopy
from .common import APIError,digest
from .workspace import device_key


def scope_flux_binding(binding,resource):
    out=deepcopy(binding);profile=out['profile']
    if resource not in profile.get('resources',{}):raise APIError('BIAS_DEPLOYMENT_SCOPE','所选资源不属于冻结来源',409)
    profile['resources']={resource:profile['resources'][resource]}
    if 'routingByResource' in profile:profile['routingByResource']={k:v for k,v in profile['routingByResource'].items() if k==resource}
    if 'biasRoutes' in profile:profile['biasRoutes']={k:v for k,v in profile['biasRoutes'].items() if k==resource}
    out['digest']=digest({k:v for k,v in out.items() if k!='digest'})
    return out


def published_bias_binding(catalog,binding,version_id,resource):
    from adapters.arclight_configurable_runtime.routing import validate_instrument,validate_route
    from adapters.science_flux.contracts import validate_route as validate_bias
    if not isinstance(version_id,str) or not version_id:
        raise APIError('BIAS_DEPLOYMENT_REQUIRED','请选择已发布且已核实的偏置接线版本',409)
    dep=catalog.get_deployment(version_id)
    if not dep.get('entityId') or dep.get('wiringConfirmed') is not True:
        raise APIError('WIRING_UNCONFIRMED','偏置接线必须已发布并完成现场核实',409)
    profile=binding['profile']
    try:
        old=validate_instrument(profile['instrument']);new=validate_instrument(dep['instrument'])
        if any(old[k]!=new[k] for k in ('id','host','port','address')):raise ValueError('设备端点不同')
        a=validate_route(profile.get('routingByResource',{}).get(resource,profile['instrument']))
        b=validate_route(dep.get('routes',{}).get(resource))
        # Optional passband facts may be added only by this published version;
        # all actual RF/ADC routing and clocks must match the observed source.
        physical=('xyPhysical','readoutPhysical','adcPhysical','xyLogical','readoutLogical','adcLogical','adcSampleRateHz')
        if any(a[k]!=b[k] for k in physical):raise ValueError('RF/ADC 线路或采样率不同')
        from .science_configuration import validate_deployment_xy_passbands
        validate_deployment_xy_passbands(dep)
        route=validate_bias(dep.get('biasRoutes',{}).get(resource))
        if route['resourceId']!=resource or route['deviceKey']!=device_key(profile):raise ValueError('偏置资源或设备身份不同')
    except (ValueError,TypeError,KeyError) as exc:
        raise APIError('BIAS_DEPLOYMENT_MISMATCH','偏置接线与实测来源不匹配：'+str(exc),409) from None
    out=deepcopy(binding);out['profile']['biasRoutes']={resource:route}
    # RF remains the original frozen route. Optional capabilities are not
    # silently migrated to an older observation's route hash.
    evidence={'schema':'qcal-bias-deployment-binding/1','deploymentVersionId':version_id,
        'deploymentDigest':dep['digest'],'deploymentName':dep['name'],'resourceId':resource,
        'deviceKey':device_key(profile),'routeSha256':digest(route),'rfRouteSha256':digest(a)}
    evidence['sha256']=digest(evidence)
    out['biasDeploymentBinding']=deepcopy(evidence);out['biasDeploymentVersionId']=version_id
    out['profile']['biasDeploymentBinding']=deepcopy(evidence)
    out['profile']['enabled']=False
    out['digest']=digest({k:v for k,v in out.items() if k!='digest'})
    return out


def validate_bias_binding(catalog,workflow,profile):
    frozen=workflow.get('configurationBinding',{}).get('biasDeploymentBinding')
    if not frozen:return
    rebuilt=published_bias_binding(catalog,workflow['configurationBinding'],frozen.get('deploymentVersionId'),frozen.get('resourceId'))
    if rebuilt['biasDeploymentBinding']!=frozen or profile.get('biasDeploymentBinding')!=frozen or profile.get('biasRoutes')!=rebuilt['profile']['biasRoutes']:
        raise APIError('BIAS_DEPLOYMENT_CHANGED','冻结偏置线路或已发布版本证据改变',409)
