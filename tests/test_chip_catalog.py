import json, tempfile, unittest
from pathlib import Path
from backend.store import Store
from backend.common import APIError
try:
 from backend.chip_catalog import ChipCatalog
except ImportError:
 ChipCatalog = None

class ChipCatalogTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
  self.store=Store(self.root/'data/test.db', root=self.root)
  self.base=json.loads((Path(__file__).resolve().parents[1]/'adapters/arclight_profile_v2.json').read_text(encoding='utf-8'))
 def tearDown(self): self.tmp.cleanup()
 def catalog(self):
  self.assertIsNotNone(ChipCatalog, 'persistent catalog is missing')
  return ChipCatalog(self.store,self.root,self.base)
 def qubit(self,id='A',confirmed=True):
  return {'id':id,'label':'external '+id,'physicalId':'physical-'+id,'busId':'bus0','identityStatus':'confirmed' if confirmed else 'unresolved','parameters':{k:v for k,v in self.base['resources']['Q11'].items() if k in self.base['parameterBounds']}}
 def test_published_versions_survive_edits_and_restarts(self):
  c=self.catalog(); body={'operationId':'save1','name':'first','qubits':[self.qubit()]}; d=c.save_chip(body)
  self.assertEqual(c.save_chip(body),d)
  v=c.publish_chip(d['id'],{'operationId':'pub1','expectedRevision':1})
  c.save_chip({**d,'operationId':'save2','expectedRevision':1,'name':'edited'})
  restarted=self.catalog(); self.assertEqual(restarted.get_chip(v['id'])['name'],'first')
  self.assertEqual(restarted.get_chip(d['id'])['name'],'edited')
  with self.assertRaises(APIError): restarted.save_chip({**d,'operationId':'stale','expectedRevision':1})
  with self.assertRaises(APIError): restarted.save_chip({**body,'name':'different'})
 def test_readiness_is_per_resource_and_physical_identity_is_not_alias(self):
  c=self.catalog(); d=c.save_chip({'operationId':'s','name':'multi','qubits':[self.qubit(),self.qubit('B',False)]})
  v=c.publish_chip(d['id'],{'operationId':'p','expectedRevision':1})
  self.assertTrue(v['qubits'][0]['ready']); self.assertFalse(v['qubits'][1]['ready'])
  self.assertTrue(any(r['id']=='B' and r['issues'] for r in c.resources()))
 def test_unconfirmed_wiring_and_out_of_bounds_are_blocked(self):
  c=self.catalog(); q=self.qubit(); q['parameters']['drive_hz']=99e9
  d=c.save_chip({'operationId':'s','name':'bad','qubits':[q]})
  v=c.publish_chip(d['id'],{'operationId':'p','expectedRevision':1})
  self.assertFalse(v['qubits'][0]['ready'])
  dep=c.save_deployment({'operationId':'d','name':'machine','machine':'m','instrument':{},'routes':{'A':{'xyPhysical':[0,0],'readoutPhysical':[8,0],'adcPhysical':[0,0]}},'wiringConfirmed':False})
  dv=c.publish_deployment(dep['id'],{'operationId':'dp','expectedRevision':1})
  with self.assertRaises(APIError): c.resolve_binding(v['id'],dv['id'],['A'])

if __name__=='__main__':unittest.main()

class LegacyDraftTests(ChipCatalogTests):
 def test_legacy_is_visible_but_never_confirmed(self):
  c=self.catalog();legacy=[d for d in c.list_chips() if d.get('source')=='legacy_profile']
  self.assertEqual(len(legacy),1);self.assertEqual(legacy[0]['qubits'][0]['identityStatus'],'unresolved')
  self.assertFalse(legacy[0]['qubits'][0]['ready']);self.assertEqual(len(self.catalog().list_chips()),1)

class WrappedDraftTests(ChipCatalogTests):
 def test_frontend_wrapper_is_accepted(self):
  c=self.catalog();d=c.save_chip({'operationId':'wrap','draft':{'id':'','name':'wrapped','qubits':[self.qubit()]}})
  self.assertEqual(d['name'],'wrapped')

class BindingTests(ChipCatalogTests):
 def test_one_ready_resource_binds_despite_other_unresolved_and_stays_frozen(self):
  c=self.catalog();c.root=Path(__file__).resolve().parents[1]
  d=c.save_chip({'operationId':'bind-s','name':'chip','qubits':[self.qubit(),self.qubit('B',False)]})
  v=c.publish_chip(d['id'],{'operationId':'bind-p','expectedRevision':1})
  dep=c.save_deployment({'operationId':'bind-d','name':'deployment','machine':'M','instrument':self.base['instrument'],'routes':{'A':{'xyPhysical':[1,1],'readoutPhysical':[8,1],'adcPhysical':[0,1]}},'wiringConfirmed':True})
  dv=c.publish_deployment(dep['id'],{'operationId':'bind-dp','expectedRevision':1})
  binding=c.resolve_binding(v['id'],dv['id'],['A'])
  self.assertEqual(set(binding['profile']['resources']),{'A'});self.assertEqual(binding['profile']['routingByResource']['A']['xyPhysical'],[1,1]);self.assertEqual(binding['profile']['parameterBounds'],self.base['parameterBounds'])
  c.save_chip({**d,'operationId':'bind-edit','expectedRevision':1,'name':'new draft'})
  self.assertEqual(c.resolve_binding(v['id'],dv['id'],['A'])['digest'],binding['digest'])

class FrequencyAliasTests(ChipCatalogTests):
 def test_frequency_edit_reorders_display_alias_but_not_stable_or_physical_identity(self):
  c=self.catalog();a=self.qubit('A');b=self.qubit('B');a['parameters']['readout_hz']=7.1e9;b['parameters']['readout_hz']=6.9e9
  d=c.save_chip({'operationId':'alias-save','name':'aliases','qubits':[a,b]});byid={q['id']:q for q in d['qubits']}
  self.assertEqual((byid['A'].get('frequencyAlias'),byid['B'].get('frequencyAlias')),('Q2','Q1'))
  v=c.publish_chip(d['id'],{'operationId':'alias-publish','expectedRevision':1})
  a['parameters']['readout_hz']=6.8e9;new=c.save_chip({'operationId':'alias-edit','id':d['id'],'expectedRevision':1,'name':'aliases','qubits':[a,b]});newby={q['id']:q for q in new['qubits']}
  self.assertEqual((newby['A']['frequencyAlias'],newby['B']['frequencyAlias']),('Q1','Q2'))
  self.assertEqual((newby['A']['id'],newby['A']['physicalId'],newby['A']['label']),('A','physical-A','external A'))
  self.assertEqual(c.get_chip(v['id'])['qubits'][0]['frequencyAlias'],'Q2')
  b['parameters']['readout_hz']=None;partial=c.save_chip({'operationId':'alias-partial','name':'partial','qubits':[a,b]})
  self.assertIsNone(partial['qubits'][1]['frequencyAlias'])
