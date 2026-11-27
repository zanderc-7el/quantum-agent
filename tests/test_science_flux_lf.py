"""Execute the three actual frozen native setter function bodies on a recorder."""
import ast
from pathlib import Path
import types
import unittest
from tests.test_science_flux import route

class NativeSetter(unittest.TestCase):
    def test_native_exact_scpi_and_atomic_timeout_restore(self):
        from adapters.science_flux.lf_transport import NativeLFSetter
        root=Path(__file__).resolve().parents[1]
        source=root/'adapters/arclight_configurable_runtime/work/server/core/inst/driver_old/AWG1652AM.py'
        tree=ast.parse(source.read_text(encoding='utf-8-sig'));names={'boardSelect','channSelect','setOffset'}
        tree.body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
        namespace={};exec(compile(tree,str(source),'exec'),namespace)
        class Handle:
            timeout=10000
            def __init__(self):self.calls=[]
            def query(self,command):self.calls.append(command);return 'ACK '+command
        handle=Handle();setter=NativeLFSetter(handle,route(),native=types.SimpleNamespace(**{k:namespace[k] for k in names}))
        reply=setter(-.125,timeout_seconds=.3)
        self.assertEqual(handle.calls,['AWG:SELMODU 2','AWG:SELCHL 1','AWG:DCOFFSET -0.125V'])
        self.assertFalse(reply['measuredOutput']);self.assertEqual(handle.timeout,10000)
        with self.assertRaises(ValueError):setter(.9,timeout_seconds=.3)
        self.assertEqual(len(handle.calls),3)

if __name__=='__main__':unittest.main()
