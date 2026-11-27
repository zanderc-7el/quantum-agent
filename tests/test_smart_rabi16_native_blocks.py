"""New smart plan through the frozen real ADC-block helper with injected IO only."""
from pathlib import Path
import json
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]

class SmartNativeBlocks(unittest.TestCase):
    def test_ordinal11_plan_actual_82_by_16_by_2000_and_cancel(self):
        profile=json.loads((ROOT/'adapters/arclight_profile_smart_rabi16_v1.json').read_text(encoding='utf-8'))
        result=subprocess.run([profile['python'],'-I','-B',str(Path(__file__).resolve()),'--native'],
            capture_output=True,text=True,encoding='utf-8',timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('"totalShots": 2624000',result.stdout)
        self.assertIn('"hardwareConnections": 0',result.stdout)
        self.assertIn('SMART_POLICY_ACTUAL_ORDINAL11',result.stdout)

def run_native():
    sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
    import test_arclight_rabi16 as inherited_fixture
    from test_smart_rabi16_native_contract import SmartSamplingContracts,ArcLightSmartRabi16Adapter
    from test_arclight_rabi16_native import native_cases
    # Reuse the unchanged injected ADC, cancellation and full raw/hash checks,
    # but feed the NEW audited plan/profile with real attempt ordinal 11.
    inherited_fixture.SamplingContracts=SmartSamplingContracts
    inherited_fixture.ArcLightRabi16Adapter=ArcLightSmartRabi16Adapter
    native_cases(ROOT,ROOT)
    print('SMART_POLICY_ACTUAL_ORDINAL11')

if __name__=='__main__':
    if '--native' in sys.argv:run_native()
    else:unittest.main()
