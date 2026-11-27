"""Persistent drafts and immutable chip/deployment versions; no hardware effects."""
from copy import deepcopy
import json
from .common import APIError, digest, dumps, ident, now, number
from .science_configuration import validate_science_configuration,validate_deployment_xy_passbands

class ChipCatalog:
 def __init__(self,store,root,base_profile,brain=None):
  self.store,self.root,self.base_profile,self.brain=store,root,deepcopy(base_profile),brain
  with store.connect() as rt:
   rt.db.executescript('''CREATE TABLE IF NOT EXISTS config_entities(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS config_versions(id TEXT PRIMARY KEY,entity_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS config_operations(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,response TEXT NOT NULL);''')
  if self.base_profile.get('resources'):
   with store.transaction() as rt:
    if not rt.db.execute("SELECT 1 FROM config_entities WHERE id='legacy-chip-draft'").fetchone():
     qs=[{'id':id,'label':id,'identityStatus':'unresolved','parameters':{k:deepcopy(v) for k,v in r.items() if k in self.base_profile.get('parameterBounds',{})},'provenance':{k:{'kind':'default','sourceId':'legacy_profile','location':id} for k in r}} for id,r in self.base_profile['resources'].items()]
     value=self._normalize('chip',{'name':'八号机现有参数','qubits':qs,'source':'legacy_profile'})
     value.update(id='legacy-chip-draft',kind='chip',revision=1,createdAt=now(),updatedAt=now())
     rt.db.execute('INSERT INTO config_entities VALUES(?,?,?)',(value['id'],'chip',dumps(value)))
    if not rt.db.execute("SELECT 1 FROM config_entities WHERE id='legacy-deployment-draft'").fetchone():
     instrument=deepcopy(self.base_profile.get('instrument',{}));routes={id:{k:deepcopy(instrument[k]) for k in ('xyPhysical','readoutPhysical','adcPhysical') if k in instrument} for id in self.base_profile['resources']}
     value={'id':'legacy-deployment-draft','kind':'deployment','revision':1,'createdAt':now(),'updatedAt':now(),'name':'八号机接线草稿','machine':'八号机','source':'legacy_profile','instrument':instrument,'routes':routes,'wiringConfirmed':False}
     rt.db.execute('INSERT INTO config_entities VALUES(?,?,?)',(value['id'],'deployment',dumps(value)))
 def _operation(self,action,body,fn):
  if not isinstance(body,dict) or not isinstance(body.get('operationId'),str) or not body['operationId'].strip():raise APIError('OPERATION_ID_REQUIRED','缺少操作编号 operationId')
  key=body['operationId']
  try:h=digest({'action':action,'body':body})
  except (ValueError,TypeError):raise APIError('INVALID_CONFIGURATION_JSON','配置包含无法保存的数值或类型')
  with self.store.transaction() as rt:
   row=rt.db.execute('SELECT request_hash,response FROM config_operations WHERE id=?',(key,)).fetchone()
   if row:
    if row[0]!=h:raise APIError('OPERATION_CONFLICT','操作编号已用于其他请求',409)
    return json.loads(row[1])
   value=fn(rt); rt.db.execute('INSERT INTO config_operations VALUES(?,?,?)',(key,h,dumps(value)));return value
 def _get(self,kind,id,rt=None):
  if rt is None:
   with self.store.connect() as rt:return self._get(kind,id,rt)
  row=rt.db.execute('SELECT payload FROM config_entities WHERE id=? AND kind=?',(id,kind)).fetchone()
  if row:
   value=json.loads(row[0]);value['versions']=[json.loads(r[0]) for r in rt.db.execute('SELECT payload FROM config_versions WHERE entity_id=? ORDER BY rowid',(id,))];return value
  row=rt.db.execute('SELECT payload FROM config_versions WHERE id=? AND kind=?',(id,kind)).fetchone()
  if not row:raise APIError('CONFIG_NOT_FOUND','配置不存在',404)
  return json.loads(row[0])
 def _list(self,kind):
  with self.store.connect() as rt:return [self._get(kind,r[0],rt) for r in rt.db.execute('SELECT id FROM config_entities WHERE kind=? ORDER BY rowid DESC',(kind,))]
 def list_chips(self):return self._list('chip')
 def get_chip(self,id):return self._get('chip',id)
 def list_deployments(self):return self._list('deployment')
 def get_deployment(self,id):return self._get('deployment',id)
 def _normalize(self,kind,body):
  if not isinstance(body.get('name'),str) or not body['name'].strip():raise APIError('CONFIG_NAME_REQUIRED','请填写配置名称')
  value={k:deepcopy(v) for k,v in body.items() if k not in ('operationId','expectedRevision','versions','id','revision','createdAt','updatedAt','digest','entityId','publishedAt','kind')}
  if kind=='chip':
   qs=value.get('qubits')
   if not isinstance(qs,list) or len(qs)>1024:raise APIError('INVALID_QUBITS','比特列表最多包含 1024 项')
   ids=[]
   for q in qs:
    if not isinstance(q,dict) or not isinstance(q.get('id'),str) or not q['id'].strip():raise APIError('INVALID_RESOURCE_ID','每个比特都需要外部编号')
    ids.append(q['id']);q.setdefault('label',q['id']);q.setdefault('parameters',{});q.setdefault('provenance',{});q.setdefault('identityStatus','unresolved')
    if not isinstance(q['parameters'],dict) or not isinstance(q['provenance'],dict):raise APIError('INVALID_RESOURCE','参数和来源信息必须为对象')
   if len(ids)!=len(set(ids)):raise APIError('DUPLICATE_RESOURCE','外部比特编号不能重复')
   # Frequency rank is a derived display alias, never a physical identity.
   ordered=sorted((q for q in qs if number(q['parameters'].get('readout_hz'))),key=lambda q:(q['parameters']['readout_hz'],q['id']))
   ranks={q['id']:'Q'+str(i+1) for i,q in enumerate(ordered)}
   for q in qs:
    q['frequencyAlias']=ranks.get(q['id'])
    q['provenance']['frequencyAlias']={'kind':'inferred','sourceId':q['id'],'location':'按当前读出频率由低到高排序，仅用于显示，不代表物理身份'}
   physical=[q.get('physicalId') for q in qs if q.get('physicalId')]
   for q in qs:q['issues']=self._issues(q,physical);q['issueMessages']=[self._issue_message(i) for i in q['issues']];q['ready']=not q['issues']
  else:
   if not isinstance(value.get('routes',{}),dict) or not isinstance(value.get('instrument',{}),dict):raise APIError('INVALID_DEPLOYMENT','接线映射和仪器配置必须为对象')
   value['wiringConfirmed']=value.get('wiringConfirmed') is True
   from .bias_route_drafts import prepare_bias_route_drafts
   prepare_bias_route_drafts(value)
  validate_science_configuration(kind,value)
  return value
 @staticmethod
 def _issue_message(issue):
  known={'physical_identity_unresolved':'尚未确认对应的物理比特','physical_identity_conflict':'多个外部编号指向同一物理比特，请核对','bus_unresolved':'尚未填写总线编号'}
  labels={'readout_hz':'读出频率','drive_hz':'驱动频率','readout_amp':'读出幅度','drive_amp':'驱动幅度','drive_length_s':'驱动脉冲时长','half_pi_amp':'半π脉冲幅度','xy_delay_s':'XY 延时','xy_attenuation_db':'XY 衰减','xy_filter_mhz':'XY 滤波器','readout_width_s':'读出脉冲时长','pulse_compatibility':'脉冲兼容策略'}
  if issue in known:return known[issue]
  kind,_,key=issue.partition(':')
  return ('尚未填写：' if kind=='missing' else '超出设备允许范围：')+labels.get(key,key)
 def _issues(self,q,physical):
  issues=[]
  if q.get('identityStatus')!='confirmed' or not q.get('physicalId'):issues.append('physical_identity_unresolved')
  if q.get('physicalId') and physical.count(q['physicalId'])>1:issues.append('physical_identity_conflict')
  if not q.get('busId'):issues.append('bus_unresolved')
  from adapters.arclight import CALIBRATION_KEYS
  for k in CALIBRATION_KEYS:
   v=q['parameters'].get(k);b=self.base_profile.get('parameterBounds',{}).get(k)
   if v is None:issues.append('missing:'+k)
   elif isinstance(b,list) and (not number(v) or not b[0]<=v<=b[1]):issues.append('out_of_bounds:'+k)
   elif isinstance(b,dict) and v not in b.get('enum',[]):issues.append('out_of_bounds:'+k)
  return issues
 def _save(self,kind,body):
  if isinstance(body,dict) and isinstance(body.get('draft'),dict):body={**body['draft'],**{k:v for k,v in body.items() if k!='draft'}}
  def work(rt):
   current=self._get(kind,body['id'],rt) if body.get('id') else None
   if current and ('entityId' in current or body.get('expectedRevision')!=current['revision']):raise APIError('REVISION_CONFLICT','草稿已更新，或正在尝试修改已发布版本',409)
   value=self._normalize(kind,body);value.update(id=current['id'] if current else ident(kind),kind=kind,revision=current['revision']+1 if current else 1,createdAt=current['createdAt'] if current else now(),updatedAt=now())
   rt.db.execute('INSERT OR REPLACE INTO config_entities VALUES(?,?,?)',(value['id'],kind,dumps(value)));return self._get(kind,value['id'],rt)
  return self._operation('save:'+kind,body,work)
 def save_chip(self,body):return self._save('chip',body)
 def save_deployment(self,body):return self._save('deployment',body)
 def _publish(self,kind,id,body):
  def work(rt):
   current=self._get(kind,id,rt)
   if 'entityId' in current or body.get('expectedRevision')!=current['revision']:raise APIError('REVISION_CONFLICT','草稿已更新，或正在尝试修改已发布版本',409)
   if kind=='deployment':validate_deployment_xy_passbands(current)
   value={k:deepcopy(v) for k,v in current.items() if k!='versions'};value.update(id=ident(kind+'v'),entityId=id,publishedAt=now());value['digest']=digest(value)
   rt.db.execute('INSERT INTO config_versions VALUES(?,?,?,?)',(value['id'],id,kind,dumps(value)));return value
  return self._operation('publish:'+kind+':'+id,body,work)
 def publish_chip(self,id,body):return self._publish('chip',id,body)
 def publish_deployment(self,id,body):return self._publish('deployment',id,body)
 def resources(self):
  with self.store.connect() as rt:
   return [{**q,'chipVersionId':v['id'],'chipName':v['name']} for row in rt.db.execute("SELECT payload FROM config_versions WHERE kind='chip' ORDER BY rowid DESC") for v in [json.loads(row[0])] for q in v['qubits']]
 def resolve_binding(self,chipVersionId,deploymentVersionId,resourceIds):
  chip=self.get_chip(chipVersionId);dep=self.get_deployment(deploymentVersionId)
  if 'entityId' not in chip or 'entityId' not in dep:raise APIError('VERSION_REQUIRED','请选择已发布的芯片版本和部署版本')
  if not isinstance(resourceIds,list) or not resourceIds or any(not isinstance(i,str) for i in resourceIds) or len(resourceIds)!=len(set(resourceIds)):raise APIError('RESOURCE_REQUIRED','请选择不重复的比特编号')
  byid={q['id']:q for q in chip['qubits']};selected=[]
  for id in resourceIds:
   q=byid.get(id)
   if not q or not q['ready']:raise APIError('RESOURCE_NOT_READY','比特缺失或配置未完成：'+str(id),409)
   if not dep.get('wiringConfirmed') or not isinstance(dep.get('routes',{}).get(id),dict):raise APIError('WIRING_UNCONFIRMED','请先确认该比特的实际接线：'+id,409)
   selected.append(deepcopy(q))
  from adapters.arclight_dynamic import build_dynamic_profile
  cv={**chip,'resources':{q['id']:{'parameters':q['parameters'],'physical_id':q['physicalId'],'bus_id':q['busId']} for q in chip['qubits']}}
  from adapters.f8 import AdapterError
  try:profile=build_dynamic_profile(self.root,self.base_profile,cv,dep,resourceIds)
  except AdapterError as e:
   message={'INVALID_RESOURCE_BINDING':'比特初始化参数或设备接线无效，请核对物理通道、逻辑通道和采样率','INVALID_PARAMETER':'初始化参数不符合设备允许范围或脉冲策略','PARAMETER_OUT_OF_RANGE':'比特初始化参数超出当前设备允许范围'}.get(e.code,'所选配置未通过设备校验，请核对初始化参数与接线')
   raise APIError(e.code,message,409) from e
  profile['machine']=dep.get('machine',dep['name'])
  # Bias authorization never comes from a legacy base profile. Only the selected
  # published deployment can freeze an explicit route into this binding.
  profile.pop('biasRoutes',None)
  if 'biasRoutes' in dep:
   validate_science_configuration('deployment',dep)
   profile['biasRoutes']={resource:deepcopy(dep['biasRoutes'][resource]) for resource in resourceIds if resource in dep['biasRoutes']}
  value={'chipVersionId':chipVersionId,'deploymentVersionId':deploymentVersionId,'resources':selected,'profile':profile,'source':{'chipDigest':chip['digest'],'deploymentDigest':dep['digest']}}
  value['digest']=digest(value);return value


