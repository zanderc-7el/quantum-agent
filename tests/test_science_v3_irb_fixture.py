"""Numerical quality-check fixtures only; never registered measurement evidence.

Call make_fixture only inside the existing guarded native Python environment.
The nested result deliberately exercises receipt consistency fields. Its
dataKind=fixture and enclosing fixtureOnly flag prohibit physical acceptance.
"""
from copy import deepcopy


def make_fixture():
    import numpy as np
    from tests.test_science_v3_irb import CampaignContracts
    from adapters.science_runtime.policy import digest
    from adapters.science_v3_irb.plans import normalize_parameters, POLICY
    from adapters.science_v3_irb.sequences import build_campaign
    from adapters.science_v3_irb.analysis import freeze_pre, analyze
    from adapters.science_v3.plans import POLICY as PULSE_POLICY
    p, _ = normalize_parameters({**CampaignContracts().parameters(), 'rb_bootstrap_replicates': 100})
    campaign = build_campaign(p)
    rows = campaign['rows']; rng = np.random.default_rng(72)
    samples = p['shots'] * p['acquisitions_per_point']
    means = []
    for row in rows:
        if row['stage'] in ('pre', 'post'):
            mean = float(row['logicalState'])
        else:
            key = row['sequenceKey']; prob = .97 if row['stage'] == 'rb' else .94
            mean = 1 - (.45 * prob ** key['clifford_m'] + .5) + .003 * np.sin(key['repeat'] + key['clifford_m'])
        means.append(mean)
    i = np.array(means)[:, None] + rng.normal(0, .01, (len(rows), samples))
    q = rng.normal(0, .01, i.shape)
    pre = [j for j, row in enumerate(rows) if row['stage'] == 'pre']
    frozen = freeze_pre([rows[j] for j in pre], (i + 1j*q)[pre], shots=p['shots'], source='synthetic fixture, no hardware')
    analyzed = analyze({'parameters': p, 'campaign': campaign, 'rows': rows}, i, q, frozen)
    if not analyzed['analysis']['accepted']:
        raise AssertionError(analyzed['analysis'])
    proof = {'schema': 'qcal-science-dependencies/1', 'policyVersion': 'synthetic_fixture_only',
             'kind': 'measurement', 'scope': {'workflowId': 'fixture', 'resourceId': 'fixture'}, 'inputs': {},
             'rootReferences': [{'role': 'drag', 'recordId': 'fixture-half-drag'}],
             'nodes': [{'recordId': 'fixture-half-drag', 'procedureId': 'sq_drag_half_scan',
                        'targetPulseIdentity': deepcopy(campaign['targetPulseIdentity'])}], 'edges': []}
    proof['closureSha256'] = digest(proof)
    result = {'dataKind': 'fixture', 'fixtureOnly': True, 'parameters': p,
              'metrics': analyzed['metrics'], 'candidateParameters': {}, 'series': analyzed['series'],
              'analysisQuality': analyzed['analysis'], 'targetPulseIdentity': campaign['targetPulseIdentity'],
              'pulseIdentities': {'target01': campaign['pulseIdentity']},
              'artifacts': [{'id': 'fixture-' + name, 'name': name, 'kind': 'fixture'}
                            for name in ('raw_pre.npz', 'raw_rb.npz', 'raw_irb.npz', 'raw_post.npz', 'raw_scan.npz')],
              'provenance': {'fixtureOnly': True, 'freshAcquisition': True, 'policy': PULSE_POLICY,
                             'campaignPolicy': POLICY, 'scienceDependencies': proof,
                             'scienceIRB': {key: analyzed[key] for key in ('irb', 'reference')}}}
    return {'fixtureOnly': True, 'hardwareConnected': False, 'nativeWaveformsCompiled': False,
            'physicalAcceptanceEligible': False,
            'purpose': 'Pure quality consistency positive fixture; freshAcquisition is a synthetic contract value.',
            'result': result}
