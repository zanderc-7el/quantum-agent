import tempfile
from pathlib import Path
import unittest
from backend.common import APIError
from tests.test_controller import make_fixture


class ScientificConfigurationContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.c,_=make_fixture(Path(self.temp.name));self.addCleanup(self.c.close)
        self.chips=self.c.workspace.chips

    def test_topology_rejects_unknown_endpoints_and_requires_sources(self):
        base={'operationId':'bad','name':'chip','qubits':[{'id':'Q11'}],
              'topology':{'nodes':[{'resourceId':'Q11','x':0,'y':0}],
                          'edges':[{'id':'e','from':'Q11','to':'Q18','kind':'coupling','confirmation':'confirmed','source':'p3'}]}}
        with self.assertRaises(APIError):self.chips.save_chip(base)
        base['topology']['edges']=[]
        good=self.chips.save_chip({**base,'operationId':'good'})
        self.assertEqual(good['topology']['edges'],[])

    def test_signal_path_requires_actual_component_identity_and_finite_units(self):
        path={'id':'xy','label':'XY','direction':'output','confirmation':'pending','source':'operator',
              'components':[{'id':'att','type':'attenuator','label':'attenuator','attenuationDb':1000}]}
        with self.assertRaises(APIError):self.chips.save_deployment({'operationId':'badpath','name':'test','signalPaths':[path]})
        path['components'][0]['attenuationDb']=20
        good=self.chips.save_deployment({'operationId':'path','name':'test','signalPaths':[path]})
        self.assertEqual(good['signalPaths'][0]['components'][0]['attenuationDb'],20)


if __name__=='__main__':unittest.main()
