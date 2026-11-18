"""Public owner planning; internal procedures remain evidence dependencies."""
from fnmatch import fnmatchcase
import json
import re
from .advisor import ROOT, catalog

PARAMETER_ALIASES = {
    'readout.frequency_hz': ('readout.resonance_hz',),
    'frequency_coarse_hz': ('qubit.f01_coarse_hz',),
    'frequency_hz': ('qubit.f01_hz', 'qubit.f01_candidate_hz', 'qubit.f01_coarse_hz'),
    'pi_seed_duration_s': ('qubit.pi_duration_s',), 'pi_duration_s': ('qubit.pi_duration_s',),
    'rabi_seed_hz_per_amp': ('qubit.rabi_hz_per_amp',), 'rabi_hz_per_amp': ('qubit.rabi_hz_per_amp',),
    't1_s': ('qubit.t1_s',), 't2_s': ('qubit.t2_ramsey_s',),
}

def aliases(key):
    result = {key}
    result.update(PARAMETER_ALIASES.get(key, ()))
    match = re.fullmatch(r'q\d+\.(.+)', key)
    if match: result.update(PARAMETER_ALIASES.get(match[1], ()))
    return result

def parameter_matches(pattern, key):
    pattern = re.sub(r'\{[^}]+\}', '*', pattern).replace('qN.', 'q[01].')
    return any(fnmatchcase(value, pattern) for value in aliases(key))

def procedure_catalog():
    data = json.loads((ROOT / 'config/procedure_catalog.json').read_text(encoding='utf-8'))
    procedures = data['procedures']
    if data.get('schema_version') != 2 or len(procedures) != 44 or len({p['id'] for p in procedures}) != 44:
        raise ValueError('内部程序目录必须为schema2的44个唯一程序')
    return {p['id']: p for p in procedures}

def topological(agents, selected=None):
    pending = set(agents if selected is None else selected)
    if pending - set(agents): raise ValueError('未知节点: ' + str(sorted(pending - set(agents))))
    missing = set().union(*(set(agents[x].get('depends_on', [])) for x in pending)) - set(agents) if pending else set()
    if missing: raise ValueError('未知依赖: ' + str(sorted(missing)))
    result = []
    while pending:
        ready = sorted(x for x in pending if not (set(agents[x].get('depends_on', [])) & pending))
        if not ready: raise ValueError('依赖存在环：' + str(sorted(pending)))
        result.extend(ready); pending.difference_update(ready)
    return result

def affected_agents(changed, agents=None, procedures=None):
    agents = catalog() if agents is None else agents
    procedures = procedure_catalog() if procedures is None else procedures
    experiments = {i: a for i, a in agents.items() if a.get('kind') == 'experiment'}
    changed = list(changed)
    if any(not isinstance(key, str) or not key for key in changed): raise ValueError('changed必须是非空参数名')
    affected = {}
    for ident, procedure in procedures.items():
        matched = [key for key in changed if any(parameter_matches(pattern, key)
                   for pattern in procedure.get('parameter_reads', []) + procedure.get('parameter_writes', []))]
        if matched: affected[ident] = ['参数变更: ' + key for key in matched]
    while True:
        additions = {i: ['内部前置证据失效: ' + ', '.join(d for d in p.get('depends_on', []) if d in affected)]
                     for i, p in procedures.items() if i not in affected and any(d in affected for d in p.get('depends_on', []))}
        if not additions: break
        affected.update(additions)
    reasons, owned = {}, {}
    for procedure_id, why in affected.items():
        owner = procedures[procedure_id]['owner_agent_id']
        if owner not in experiments: raise ValueError('内部程序归属未知公开角色: ' + owner)
        owned.setdefault(owner, []).append(procedure_id); reasons.setdefault(owner, []).extend(why)
    while True:
        additions = {i: ['原流程前置证据失效: ' + ', '.join(d for d in a.get('depends_on', []) if d in reasons)]
                     for i, a in experiments.items() if i not in reasons and any(d in reasons for d in a.get('depends_on', []))}
        if not additions: break
        reasons.update(additions)
    return [{'agent_id': i, 'reasons': sorted(set(reasons[i])), 'affected_procedures': sorted(owned.get(i, [])),
             'action': 'fresh_measurement_required', 'scope': 'owner_planning_only'}
            for i in topological(experiments, set(reasons))]

def plan(domain='all', agents=None):
    if domain not in ('all', 'single_qubit', 'two_qubit'): raise ValueError('domain仅支持all/single_qubit/two_qubit')
    agents = catalog() if agents is None else agents
    experiments = {i: a for i, a in agents.items() if a.get('kind') == 'experiment'}
    selected = {i for i, a in experiments.items() if domain == 'all' or domain in a.get('domain', '')}
    while True:
        expanded = selected | set().union(*(set(experiments[i].get('depends_on', [])) for i in selected)) if selected else set()
        if expanded - set(experiments): raise ValueError('公开实验依赖只能引用15个流程owner')
        if expanded == selected: break
        selected = expanded
    return [{'agent_id': i, 'name': experiments[i]['name'], 'depends_on': experiments[i].get('depends_on', []),
             'execution_status': experiments[i].get('execution_status', 'contract_only'),
             'main_procedure_id': experiments[i]['main_procedure_id'], 'card_path': experiments[i]['card_path'],
             'scope': 'original_workflow_agent'} for i in topological(experiments, selected)]
