"""Fixed no-hardware native circuit planner. JSON-only bounded input/output."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'adapters/science_runtime'))
from compile_only_worker import audit,namespace
sys.addaudithook(audit)

def main():
    from adapters.science_v3_irb.policy import verify_sources
    verify_sources(ROOT)
    from adapters.science_v3_irb.runtime_contract import check_runtime
    check_runtime()
    native=ROOT/'adapters/arclight_configurable_runtime'
    sys.path.insert(0,str(native));core=native/'work/server/core'
    namespace('core',core);namespace('core.circuit',core/'circuit')
    from adapters.science_v3_irb.sequences import build_campaign
    raw=sys.stdin.buffer.read(65537)
    if len(raw)>65536:raise ValueError('IRB parameters exceed input size limit')
    result=build_campaign(json.loads(raw))
    sys.stdout.write(json.dumps(result,separators=(',',':'),allow_nan=False))

if __name__=='__main__':main()
