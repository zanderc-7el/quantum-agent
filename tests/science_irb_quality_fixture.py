"""Combine native numeric analysis and authenticated temporary timing fixtures.

No device calls, no production database, and no reusable accepted source.
"""
from copy import deepcopy
import json
from pathlib import Path
from backend.common import digest


def result_fixture(test):
    from tests.test_science_v3_irb_timing import TimingContracts
    envelope=json.loads((Path(__file__).resolve().parent/'fixtures/science_irb_quality_1126.json').read_text(encoding='utf8'))
    test.assertTrue(envelope['fixtureOnly']);test.assertFalse(envelope['physicalAcceptanceEligible'])
    result=deepcopy(envelope['result']);timing=TimingContracts();timing.setUp();test.addCleanup(timing.doCleanups)
    ancestry,_,lineage=timing.lineage_fixture()
    result['provenance']['scienceIRB']['rabiLineage']=lineage
    proof=result['provenance']['scienceDependencies'];drag=proof['nodes'][0]['recordId']
    proof['nodes'].extend(deepcopy(ancestry['nodes']))
    next(n for n in proof['nodes'] if n['recordId']=='half')['procedureId']='sq_shape_half_verify'
    proof['rootReferences'].extend([deepcopy(r) for r in ancestry['rootReferences'] if r['role'] in ('pi01','half01')])
    proof['edges']=[{'fromRecordId':drag,'toRecordId':r['recordId'],'role':r['role']} for r in ancestry['rootReferences']]
    proof['closureSha256']=digest({k:v for k,v in proof.items() if k!='closureSha256'})
    return result
