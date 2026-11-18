import unittest
from quantum_agents.advisor import catalog
from quantum_agents.planning import affected_agents, plan, topological

class PlanningTests(unittest.TestCase):
    def test_runtime_frequency_maps_procedures_then_owners_and_descendants(self):
        result=affected_agents(['q0.frequency_hz']);changed={x['agent_id'] for x in result}
        self.assertTrue({'s03_rabi_amplitude','s06_t1','d05_cz_detuning'}<=changed)
        self.assertTrue(changed<=set(catalog())-{'supervisor'})
        self.assertTrue(any('sq_rabi_time' in x['affected_procedures'] for x in result))

    def test_readout_alias_and_unknown_parameter_are_distinguished(self):
        changed={x['agent_id'] for x in affected_agents(['readout.frequency_hz'])}
        self.assertTrue({'s01_readout_frequency','s02_qubit_frequency','s07_t2'}<=changed)
        self.assertEqual(affected_agents(['unregistered.device.parameter']),[])

    def test_cycle_and_unknown_domain_are_rejected(self):
        with self.assertRaises(ValueError):topological({'a':{'depends_on':['b']},'b':{'depends_on':['a']}})
        with self.assertRaises(ValueError):plan('not-a-domain')

    def test_public_plan_has_fifteen_owners_and_prerequisite_order(self):
        result=plan('two_qubit');positions={x['agent_id']:i for i,x in enumerate(result)}
        self.assertEqual(len(plan()),15)
        self.assertIn('s03_rabi_amplitude',positions);self.assertIn('d06_cz_xeb',positions)
        self.assertNotIn('sq_rabi_time',positions)
        for x in result:
            for dep in x['depends_on']:self.assertLess(positions[dep],positions[x['agent_id']])
        self.assertTrue(any(x['execution_status']=='contract_only' for x in result))
