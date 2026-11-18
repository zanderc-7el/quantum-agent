import copy,json,unittest
from unittest.mock import patch
from quantum_agents.advisor import catalog,validate_proposal,rule_proposal,openai_proposal,build_model_request
from quantum_agents.planning import affected_agents,topological

AGENTS={'a':{'id':'a','depends_on':[],'parameter_reads':['q0.f'],'parameter_writes':['q0.amp'],'budgets':{'max_attempts':2},'prompt_path':'docs/model_interface.md'},
        'b':{'id':'b','depends_on':['a'],'parameter_reads':[],'parameter_writes':[]}}

class AdvisorTests(unittest.TestCase):
    def test_budget_escalation(self):
        self.assertEqual(rule_proposal('a',{'attempt':2},AGENTS)['decision'],'escalate')
    def test_declared_diagnostic_route(self):
        self.assertEqual(rule_proposal('a',{'issue':'aliasing'},AGENTS)['decision'],'change_scan')
    def test_no_model_commit_or_fabricated_evidence(self):
        p=rule_proposal('a',{},AGENTS);p['decision']='commit'
        with self.assertRaises(ValueError):validate_proposal(p,'a',{},AGENTS)
        p=rule_proposal('a',{},AGENTS);p['evidence_ids']=['invented']
        with self.assertRaises(ValueError):validate_proposal(p,'a',{},AGENTS)
    def test_unit_boundary_and_nonfinite(self):
        ctx={'allowed_parameters':{'q0.amp':{'unit':'dimensionless','min':0.,'max':.5}}}
        for value,unit in [(1,'dimensionless'),(.2,'Hz'),(float('nan'),'dimensionless'),(True,'dimensionless')]:
            p=rule_proposal('a',{},AGENTS);p['parameter_proposals']=[{'name':'q0.amp','value':value,'unit':unit}]
            with self.assertRaises(ValueError):validate_proposal(p,'a',ctx,AGENTS)
    def test_targets_and_parameter_writes_cannot_escape_public_owner(self):
        agents=catalog();context={'procedure_id':'sq_rabi_time','allowed_parameters':{'readout.frequency_hz':{'unit':'Hz','min':1e9,'max':10e9}}}
        p=rule_proposal('s03_rabi_amplitude',context,agents);p['target_agents']=['sq_rabi_time']
        with self.assertRaises(ValueError):validate_proposal(p,'s03_rabi_amplitude',context,agents)
        p['target_agents']=['s03_rabi_amplitude'];p['parameter_proposals']=[{'name':'readout.frequency_hz','value':6.5e9,'unit':'Hz'}]
        with self.assertRaises(ValueError):validate_proposal(p,'s03_rabi_amplitude',context,agents)
    def test_cycle_rejected(self):
        a=copy.deepcopy(AGENTS);a['a']['depends_on']=['b']
        with self.assertRaises(ValueError):topological(a)
    @patch('pathlib.Path.read_text',return_value='仅输出结构化建议')
    def test_api_contract_without_network(self,_):
        p=rule_proposal('a',{},AGENTS)
        seen=[]
        def transport(body):
            seen.append(body)
            return {'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(p)}]}]}
        self.assertEqual(openai_proposal('a',{},model='explicit-test-model',transport=transport,agents=AGENTS),p)
        self.assertFalse(seen[0]['store']);self.assertTrue(seen[0]['text']['format']['strict'])
        self.assertNotIn('tools',seen[0])
    @patch('pathlib.Path.read_text',return_value='prompt')
    def test_incomplete_model_output_rejected(self,_):
        with self.assertRaises(ValueError):openai_proposal('a',{},model='test',transport=lambda _: {'status':'incomplete'},agents=AGENTS)

if __name__=='__main__':unittest.main()
