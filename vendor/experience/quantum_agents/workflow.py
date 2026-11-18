"""Fifteen owning flow agents above immutable low-level runtime procedures."""
from __future__ import annotations
import json
import math
from .advisor import ROOT, catalog, rule_proposal
from .planning import parameter_matches, topological
from .runtime import ContractError

def ownership():
    data = json.loads((ROOT / 'config/workflow_ownership.json').read_text(encoding='utf-8'))
    owners = {a['id']: a for a in data['agents']}
    procedures = [p for a in owners.values() for p in a['procedure_ids']]
    if len(owners) != 15 or len(procedures) != 44 or len(set(procedures)) != 44:
        raise ContractError('15 owner / 44 procedure固定归属不完整')
    return owners

def bindings():
    data = json.loads((ROOT / 'config/bindings.json').read_text(encoding='utf-8'))
    if data.get('schema_version') != 2 or data.get('hardware_enabled') is not False:
        raise ContractError('未知绑定版本或实机权限')
    return data['bindings']

class _ScopedBackend:
    def __init__(self, backend, limits): self.backend, self.limits = backend, limits
    def __getattr__(self, name): return getattr(self.backend, name)
    def apply_trial(self, request, candidate):
        for name, value in candidate.items():
            rules = [rule for pattern, rule in self.limits.items() if parameter_matches(pattern, name)]
            if (len(rules) != 1 or type(value) not in (int, float) or not math.isfinite(value)
                    or not rules[0]['min'] <= value <= rules[0]['max']):
                raise ContractError('CANDIDATE_OUTSIDE_OWNER_SCOPE: ' + name)
        return self.backend.apply_trial(request, candidate)

class FlowAgent:
    """One original box; successful substeps never certify the whole box."""
    def __init__(self, agent_id, *, agents=None, owners=None, procedure_bindings=None):
        self.agents = catalog() if agents is None else agents
        self.owners = ownership() if owners is None else owners
        if agent_id not in self.owners or agent_id not in self.agents:
            raise ContractError('UNKNOWN_WORKFLOW_AGENT: ' + str(agent_id))
        self.id, self.definition = agent_id, self.agents[agent_id]
        self.procedure_ids = tuple(self.owners[agent_id]['procedure_ids'])
        declared_main = self.owners[agent_id].get('main_procedure_id', self.definition.get('main_procedure_id'))
        self.main_procedure_id = self.definition.get('main_procedure_id', declared_main)
        if self.main_procedure_id != declared_main or self.main_procedure_id not in self.procedure_ids:
            raise ContractError('原流程主方法与固定归属表不符')
        self.bindings = {}
        for binding in bindings() if procedure_bindings is None else procedure_bindings:
            if binding['owner_agent_id'] == agent_id:
                procedure = binding['procedure_id']
                if procedure not in self.procedure_ids or procedure in self.bindings: raise ContractError('绑定跨owner或重复')
                self.bindings[procedure] = binding

    def parameter_scope(self, context=None):
        limits = {key: dict(rule) for b in self.bindings.values() for key, rule in b['parameter_limits'].items()}
        if context is None: return limits
        if context.get('procedure_id') and context['procedure_id'] not in self.procedure_ids:
            raise ContractError('PROCEDURE_NOT_OWNED')
        narrowed = {}
        for key, requested in context.get('allowed_parameters', {}).items():
            matches = [rule for pattern, rule in limits.items() if parameter_matches(pattern, key)]
            if len(matches) == 1 and requested.get('unit') == matches[0]['unit']:
                low, high = max(requested['min'], matches[0]['min']), min(requested['max'], matches[0]['max'])
                if low <= high: narrowed[key] = {'min': low, 'max': high, 'unit': requested['unit']}
        return narrowed

    def propose(self, context):
        context = {**context, 'allowed_parameters': self.parameter_scope(context)}
        if self.main_procedure_id not in self.bindings and not context.get('procedure_id'):
            context = {**context, 'issue': 'unsupported_capability'}
        if context.get('procedure_id') and context['procedure_id'] not in self.procedure_ids:
            raise ContractError('PROCEDURE_NOT_OWNED')
        return rule_proposal(self.id, context, self.agents)

    def select(self, runtime, *, procedure_id=None, step_ids=None):
        procedure_id = procedure_id or self.main_procedure_id
        if procedure_id not in self.procedure_ids: raise ContractError('PROCEDURE_NOT_OWNED: ' + str(procedure_id))
        binding = self.bindings.get(procedure_id)
        if binding is None: raise ContractError('UNSUPPORTED_MAIN_METHOD' if procedure_id == self.main_procedure_id else 'UNSUPPORTED_PROCEDURE')
        selected = list(step_ids) if step_ids is not None else [s for s in binding['step_ids'] if s in runtime.steps]
        if not selected or len(set(selected)) != len(selected) or any(s not in binding['step_ids'] or s not in runtime.steps for s in selected):
            raise ContractError('步骤未绑定、重复或跨owner；必须显式选择本程序实例')
        for ident in selected:
            step, expected = runtime.steps[ident], binding['step_contracts'][ident]
            if any(list(getattr(step, field)) != expected[field] for field in ('reads', 'writes', 'depends_on')):
                raise ContractError('RUNTIME_PROCEDURE_SCOPE_MISMATCH: ' + ident)
        nodes = {i: {'depends_on': list(s.depends_on)} for i, s in runtime.steps.items()}
        return procedure_id, binding, topological(nodes, set(selected))

    def run(self, runtime, backend, operation_id, *, procedure_id=None, step_ids=None):
        if not isinstance(operation_id, str) or not operation_id.strip(): raise ContractError('operation_id必须显式提供；主动复测使用新ID')
        procedure_id, binding, selected = self.select(runtime, procedure_id=procedure_id, step_ids=step_ids)
        request = json.dumps({'agent_id': self.id, 'procedure_id': procedure_id, 'step_ids': selected, 'binding': binding},
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        runtime.db.execute('CREATE TABLE IF NOT EXISTS workflow_dispatches(operation_id TEXT PRIMARY KEY, request_json TEXT NOT NULL)')
        with runtime._transaction():
            old = runtime.db.execute('SELECT request_json FROM workflow_dispatches WHERE operation_id=?', (operation_id,)).fetchone()
            if old is not None and old[0] != request: raise ContractError('IDEMPOTENCY_CONFLICT: owner或内部程序选择改变')
            runtime.db.execute('INSERT OR IGNORE INTO workflow_dispatches VALUES(?,?)', (operation_id, request))
        operations = {s: operation_id + '/' + self.id + '/' + s for s in selected}
        for ident in selected:
            try:
                runtime.get_run(operations[ident]); continue
            except ContractError: pass
            missing = [d for d in runtime.steps[ident].depends_on if d not in selected and runtime.evidence(d) is None]
            if missing: raise ContractError('MISSING_PREREQUISITE_EVIDENCE: ' + ', '.join(missing))
        scoped, runs = _ScopedBackend(backend, binding['parameter_limits']), []
        for ident in selected:
            result = runtime.run_step(ident, scoped, operations[ident]); runs.append(result)
            if result['state'] != 'completed': break
        valid = {r['step_id']: (runtime.evidence(r['step_id']) or {}).get('evidence_id') == r.get('verification', {}).get('evidence_id')
                 and r.get('verification', {}).get('evidence_id') is not None for r in runs}
        complete = len(runs) == len(selected) and all(r['state'] == 'completed' for r in runs)
        return {'agent_id': self.id, 'operation_id': operation_id, 'procedure_id': procedure_id, 'purpose': binding['purpose'],
                'data_kind': runs[0]['data_kind'] if runs else None,
                'main_procedure_id': self.main_procedure_id,
                'state': 'partial_completed' if complete and all(valid.values()) else 'partial_evidence_stale' if complete else 'procedure_failed',
                'selected_procedures_completed': complete, 'valid_evidence': valid, 'runs': runs,
                'original_step_complete': False, 'workflow_complete': False,
                'scope': '仅所选内部程序；不证明原流程主方法或15步全流程完成'}

class WorkflowSupervisor:
    def __init__(self, *, agents=None, owners=None, procedure_bindings=None):
        self.catalog = catalog() if agents is None else agents
        self.owners = ownership() if owners is None else owners
        self.agents = {i: FlowAgent(i, agents=self.catalog, owners=self.owners, procedure_bindings=procedure_bindings) for i in self.owners}

    def run_agent(self, agent_id, runtime, backend, operation_id, *, procedure_id=None, step_ids=None):
        if agent_id not in self.agents: raise ContractError('总控只能分派15个原流程owner')
        return self.agents[agent_id].run(runtime, backend, operation_id, procedure_id=procedure_id, step_ids=step_ids)

    def resume_run(self, runtime, backend, run_id):
        run = runtime.get_run(run_id)
        table = runtime.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_dispatches'").fetchone()
        if table:
            for row in runtime.db.execute('SELECT operation_id,request_json FROM workflow_dispatches'):
                request = json.loads(row[1])
                expected = row[0] + '/' + request['agent_id'] + '/' + run['step_id']
                if run['operation_id'] == expected:
                    agent = self.agents[request['agent_id']]
                    _, binding, selected = agent.select(runtime, procedure_id=request['procedure_id'], step_ids=request['step_ids'])
                    if binding != request['binding'] or run['step_id'] not in selected:
                        raise ContractError('恢复时owner绑定已变，须核查旧事务')
                    backend = _ScopedBackend(backend, binding['parameter_limits'])
                    break
        return runtime.resume(run_id, backend)

    def group_report(self, summary):
        owner_for = {p: i for i, owner in self.owners.items() for p in owner['procedure_ids']}
        grouped = {owner: {'agent_id': owner, 'main_procedure_id': agent.main_procedure_id,
                          'procedure_ids': [], 'procedure_runs': [], 'original_step_complete': False,
                          'scope': '仅仿真内部程序证据；不证明原函数完整合同'}
                   for owner, agent in self.agents.items()}
        for run in summary.get('runs', []):
            procedure = run['step_id'].split(':')[0]
            if procedure not in owner_for: raise ContractError('未知内部程序，无法归属')
            owner = owner_for[procedure]
            group = grouped[owner]
            if procedure not in group['procedure_ids']: group['procedure_ids'].append(procedure)
            group['procedure_runs'].append({'step_id': run['step_id'], 'run_id': run['run_id'], 'state': run['state'],
                                            'evidence_valid': summary.get('valid_evidence', {}).get(run['step_id']) is True})
        for owner, group in grouped.items():
            runs = group['procedure_runs']
            group['state'] = ('partial_evidence' if all(r['state'] == 'completed' and r['evidence_valid'] for r in runs)
                              else 'partial_incomplete') if runs else 'not_executed'
            group['main_method_bound'] = group['main_procedure_id'] in self.agents[owner].bindings
            group['main_procedure_executed'] = any(r['step_id'].split(':')[0] == group['main_procedure_id']
                                                 and r['state'] == 'completed' and r['evidence_valid'] for r in runs)
        return {'schema_version': 2, 'data_kind': summary.get('data_kind'), 'registered_agents': 16, 'experiment_agents': 15,
                'observed_owner_count': sum(bool(g['procedure_runs']) for g in grouped.values()),
                'physical_procedure_runs': len(summary.get('runs', [])),
                'agents': list(grouped.values()), 'original_steps_completed': 0, 'overall_workflow_complete': False,
                'scope': '按原流程归属展示局部证据；时长Rabi不代替幅度Rabi，Ramsey不代替CPMG，CZ套件不代替XEB'}
