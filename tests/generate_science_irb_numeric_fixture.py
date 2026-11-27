"""Reproduce native numeric/controller interface; NEVER physical evidence.

Run only with the existing pinned native Python. No driver imports, sockets,
instrument access, controller database, or acquisition records are created.
The output deliberately lacks authenticated operations/raw artifacts and cannot
qualify as a physical dependency. Its `real` label is a quality-parser fixture.
"""
from pathlib import Path
from copy import deepcopy
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'adapters/science_runtime'))
sys.path.insert(0, str(ROOT / 'adapters/arclight_configurable_runtime'))
import compile_only_worker as guard
sys.addaudithook(guard.audit)
core = ROOT / 'adapters/arclight_configurable_runtime/work/server/core'
for name, path in [('core', core), ('core.circuit', core / 'circuit')]:
    guard.namespace(name, path)


def generate():
    import numpy as np
    from tests.test_science_v3_irb import CampaignContracts
    from adapters.science_v3_irb.plans import normalize_parameters, POLICY
    from adapters.science_v3_irb.sequences import build_campaign
    from adapters.science_v3_irb.analysis import freeze_pre, analyze
    from adapters.science_v3_irb.runtime_contract import check_runtime
    from adapters.science_v3_irb.policy import verify_sources
    from backend.quality import evaluate, SCIENCE_IRB_GATES, SCIENCE_IRB_GATES_VERSION
    from backend.common import digest

    runtime = check_runtime()
    sources = verify_sources(ROOT)
    parameters, _ = normalize_parameters({**CampaignContracts().parameters(), 'rb_bootstrap_replicates': 100})
    campaign = build_campaign(parameters)
    rows = campaign['rows']
    rng = np.random.default_rng(72)
    samples = []
    for row in rows:
        if row['stage'] in ('pre', 'post'):
            mean = float(row['logicalState'])
        else:
            key = row['sequenceKey']
            decay = .97 if row['stage'] == 'rb' else .94
            mean = 1 - (.45 * decay ** key['clifford_m'] + .5) + .003 * np.sin(key['repeat'] + key['clifford_m'])
        samples.append(mean + rng.normal(0, .01, 200) + 1j * rng.normal(0, .01, 200))
    z = np.array(samples)
    pre = [j for j, row in enumerate(rows) if row['stage'] == 'pre']
    frozen = freeze_pre([rows[j] for j in pre], z[pre], shots=100, source='explicit synthetic review fixture')
    analysis = analyze({'parameters': parameters, 'rows': rows, 'campaign': campaign}, z.real, z.imag, frozen)
    proof = {'schema': 'qcal-science-dependencies/1', 'policyVersion': 'fixture-only', 'kind': 'measurement',
             'scope': {}, 'inputs': {}, 'rootReferences': [{'role': 'drag', 'recordId': 'fixture-drag'}],
             'nodes': [{'recordId': 'fixture-drag', 'procedureId': 'sq_drag_half_scan',
                        'targetPulseIdentity': campaign['targetPulseIdentity']}], 'edges': []}
    # Temporary receipt authentication is test-only; these paths are deleted
    # before export and the aggregate still has no reusable acquisition proof.
    from tests.test_science_v3_irb_timing import TimingContracts
    timing = TimingContracts()
    try:
        timing.setUp()
        ancestry, _, lineage = timing.lineage_fixture()
        proof['nodes'].extend(deepcopy(ancestry['nodes']))
        next(node for node in proof['nodes'] if node['recordId'] == 'half')['procedureId'] = 'sq_shape_half_verify'
        proof['rootReferences'].extend(deepcopy(ref) for ref in ancestry['rootReferences'] if ref['role'] in ('pi01', 'half01'))
        proof['edges'] = [{'fromRecordId': 'fixture-drag', 'toRecordId': ref['recordId'], 'role': ref['role']}
                          for ref in ancestry['rootReferences']]
    finally:
        timing.doCleanups()
    proof['closureSha256'] = digest(proof)
    result = {'dataKind': 'real', 'acquiredAt': 'explicit synthetic fixture only', 'parameters': parameters,
              'metrics': analysis['metrics'], 'candidateParameters': {}, 'series': analysis['series'],
              'targetPulseIdentity': campaign['targetPulseIdentity'], 'analysisQuality': analysis['analysis'],
              'artifacts': [{'name': 'raw_' + stage + '.npz'} for stage in ('pre', 'rb', 'irb', 'post', 'scan')],
              'provenance': {'freshAcquisition': True, 'campaignPolicy': POLICY, 'scienceDependencies': proof,
                             'scienceIRB': {'irb': analysis['irb'], 'reference': analysis['reference'],
                                            'rabiLineage': lineage}}}
    outcomes = []
    for independent in (False, True):
        checked = evaluate(result, 'sq_drag_irb', 'real', SCIENCE_IRB_GATES, independent,
                           rule_version=SCIENCE_IRB_GATES_VERSION)
        outcomes.append({'independent': independent, 'qualityStatus': checked['quality']['status'],
                         'reasons': checked['quality']['reasons'], 'fidelityPublished': 'gate_fidelity' in checked['metrics']})
    if outcomes[0]['qualityStatus'] != 'unverified' or outcomes[0]['fidelityPublished']:
        raise AssertionError(outcomes)
    if outcomes[1]['qualityStatus'] != 'passed' or not outcomes[1]['fidelityPublished']:
        raise AssertionError(outcomes)
    return {'fixtureOnly': True, 'physicalAccepted': False, 'hardwareConnected': False,
            'description': 'Synthetic ADC, native sequence/analysis, parser contract only; missing authentic operations and artifact bytes',
            'runtime': runtime, 'sources': sources, 'campaignSha256': campaign['sha256'],
            'rows': len(rows), 'adcRandomSeed': 72, 'expectedDecay': {'reference': .97, 'interleaved': .94},
            'qualityOutcomes': outcomes, 'result': result}


if __name__ == '__main__':
    output = ROOT / 'tests/fixtures/science_irb_numeric_result.json'
    value = generate()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'path': str(output), 'fixtureOnly': True, 'physicalAccepted': False,
                      'qualityOutcomes': value['qualityOutcomes']}, ensure_ascii=False))
