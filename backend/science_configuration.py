"""Strict optional topology / signal-path fields for existing configuration versions."""
from .common import APIError, number
from .diagram_assets import validate_diagram_image, MAX_IMAGE_URL_CHARS


def _require(ok, text):
    if not ok: raise APIError('SCIENCE_CONFIGURATION_INVALID', text)


def _text(value, label, maximum=500):
    _require(isinstance(value,str) and 0<len(value.strip())<=maximum, label+'不能为空或过长')


def validate_deployment_xy_passbands(value):
    from adapters.xy_passband import validate_xy_passband
    routes=value.get('routes',{})
    _require(isinstance(routes,dict),'接线路由须为对象')
    for resource,route in routes.items():
        if not isinstance(route,dict):continue  # Existing physical-route validation owns this error.
        try:validate_xy_passband(route)
        except ValueError as exc:raise APIError('XY_PASSBAND_INVALID',str(resource)+'：'+str(exc)) from exc


def validate_science_configuration(kind, value):
    if kind=='deployment':validate_deployment_xy_passbands(value)
    if kind=='deployment' and 'biasRoutes' in value:
        from adapters.science_flux.contracts import validate_route,ContractError
        routes=value['biasRoutes']
        _require(isinstance(routes,dict) and len(routes)<=256,'偏置线路须为最多256个资源的明确映射')
        for resource,route in routes.items():
            _text(resource,'偏置资源编号',128)
            try:validated=validate_route(route)
            except ContractError as exc:raise APIError('BIAS_ROUTE_INVALID','偏置线路配置无效：'+str(exc)) from exc
            _require(validated['resourceId']==resource,'偏置线路资源编号与映射键不一致')
    if kind=='chip' and 'topology' in value:
        topology=value['topology'];_require(isinstance(topology,dict),'拓扑须为对象')
        for key in ('source','date'):
            if key in topology:_text(topology[key],key,2000)
        if 'image' in topology:validate_diagram_image(topology['image'])
        nodes=topology.get('nodes',[]);edges=topology.get('edges',[])
        _require(isinstance(nodes,list) and len(nodes)<=1024 and isinstance(edges,list) and len(edges)<=4096,'拓扑点或边数过多')
        resources={q['id'] for q in value['qubits']};known=set();edge_ids=set()
        for n in nodes:
            _require(isinstance(n,dict),'拓扑节点须为对象')
            r=n.get('resourceId');_require(isinstance(r,str) and r in resources and r not in known,'拓扑节点须唯一并指向本芯片的比特')
            if 'designId' in n:_text(n['designId'],'设计编号')
            _require(all(number(n.get(k)) and abs(n[k])<=1e6 for k in ('x','y')),'拓扑坐标须为有限数值');known.add(r)
        for e in edges:
            _require(isinstance(e,dict),'拓扑边须为对象');_text(e.get('id'),'边编号')
            _require(e['id'] not in edge_ids,'边编号重复');edge_ids.add(e['id'])
            _require(isinstance(e.get('from'),str) and isinstance(e.get('to'),str) and e['from'] in known and e['to'] in known and e['from']!=e['to'],'连线端点必须是不同的已声明节点')
            _require(e.get('kind') in ('coupling','readout','flux','unknown'),'未知连线类型')
            _require(e.get('confirmation') in ('confirmed','pending'),'连线需注明已确认或待核实');_text(e.get('source'),'连线来源',2000)
    if kind=='deployment' and 'signalPaths' in value:
        paths=value['signalPaths'];_require(isinstance(paths,list) and len(paths)<=256,'信号路径最多256条');ids=set();image_chars=0
        for p in paths:
            _require(isinstance(p,dict),'路径须为对象')
            for key in ('id','label','source'):_text(p.get(key),key,2000 if key=='source' else 500)
            for key in ('station','sourceDate'):
                if key in p:_text(p[key],key)
            if 'image' in p:image_chars+=validate_diagram_image(p['image'])
            _require(image_chars<=MAX_IMAGE_URL_CHARS,'同一配置的原图编码合计不得超过700 KiB')
            _require(p['id'] not in ids,'路径编号重复');ids.add(p['id'])
            _require(p.get('direction') in ('input','output','bidirectional'),'路径方向无效')
            _require(p.get('confirmation') in ('confirmed','pending'),'路径需注明确认状态')
            components=p.get('components');_require(isinstance(components,list) and 1<=len(components)<=100,'路径应包含1～100个部件');cids=set()
            for c in components:
                _require(isinstance(c,dict),'部件须为对象')
                for key in ('id','label'):_text(c.get(key),key)
                _require(c['id'] not in cids,'同路径部件编号重复');cids.add(c['id'])
                _require(c.get('type') in ('instrument','attenuator','filter','amplifier','mixer','cable','chip','adc','other'),'部件类型无效')
                for key in ('channel','port','fromPort','toPort','model','source'):
                    if key in c:_text(c[key],key,2000)
                if 'confirmation' in c:_require(c['confirmation'] in ('confirmed','pending'),'部件确认状态无效')
                for key,low,high in [('attenuationDb',0,200),('delayNs',0,1e9),('temperatureK',0,1e5)]:
                    if key in c:_require(number(c[key]) and low<=c[key]<=high,key+'范围或单位不正确')
