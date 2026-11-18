from copy import deepcopy
import json
from pathlib import Path

from .common import ROOT

VERSION = 'measurement-experience-15step/2'
WORKING_POINT_VERSION = 'working-point-pulse-chain/1'
WORKING_POINT_OMITTED = ('ro_resonator_survey', 'ro_power_shift', 'sq_spectroscopy')
WORKING_POINT_PROFILE_IDS = ('arclight-private-f8-q11-v1', 'arclight-private-f8-q11-v2')
PARAMETERS = {
    'ro_resonator_survey': ([], ['readout_frequency_hz']),
    'sq_spectroscopy': (['readout_frequency_hz'], ['drive_frequency_hz']),
    'sq_rabi_amplitude': (['readout_frequency_hz', 'drive_frequency_hz'], ['drive_amplitude', 'half_pi_amp', 'pi_duration_s']),
    'sq_rabi_time': (['readout_frequency_hz', 'drive_frequency_hz'], ['pi_duration_s']),
    'ro_iq01': (['readout_frequency_hz', 'drive_frequency_hz', 'drive_amplitude', 'pi_duration_s'], ['classifier_revision']),
    'ro_optimize': (['drive_frequency_hz', 'drive_amplitude', 'pi_duration_s', 'classifier_revision'], ['readout_frequency_hz', 'readout_amplitude']),
    'sq_t1': (['readout_frequency_hz', 'readout_amplitude', 'drive_frequency_hz', 'pi_duration_s'], []),
    'sq_cpmg': (['readout_frequency_hz', 'readout_amplitude', 'drive_frequency_hz', 'pi_duration_s'], []),
    'sq_ramsey': (['readout_frequency_hz', 'readout_amplitude', 'drive_frequency_hz', 'pi_duration_s'], []),
    'sq_echo': (['readout_frequency_hz', 'readout_amplitude', 'drive_frequency_hz', 'pi_duration_s'], []),
    'sq_rb': (['readout_frequency_hz', 'readout_amplitude', 'drive_frequency_hz', 'drive_amplitude', 'pi_duration_s'], []),
}

# Auxiliary procedures retain their original owner; they are distinct acquisitions.
FULL_SINGLE_AUXILIARY = (
    ('s01_readout_frequency', 'ro_power_shift', 'S21 功率扫描', 'S21Power', ('s01_readout_frequency',)),
    ('s03_rabi_amplitude', 'sq_rabi_time', '时间 Rabi', 'TimeRabi', ('s03_rabi_amplitude',)),
    ('s03_rabi_amplitude', 'sq_allxy', 'AllXY 诊断', 'AllXY', ('s05_readout_optimization',)),
    ('s07_t2', 'sq_ramsey', 'Ramsey', 'Ramsey', ('s06_t1',)),
    ('s07_t2', 'sq_echo', 'Spin Echo', 'Echo', ('s07_t2:sq_ramsey',)),
)

class Catalog:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        source = ROOT / 'vendor' / 'experience' / 'config'
        self.owners = json.loads((source / 'workflow_ownership.json').read_text(encoding='utf-8'))['agents']
        catalog = json.loads((source / 'agent_catalog.json').read_text(encoding='utf-8'))['agents']
        self.dependencies = {a['id']: a.get('depends_on', []) for a in catalog}

    def rules(self):
        candidates = [self.root / 'knowledge' / 'rules.json', self.root / 'knowledge' / 'field_rules.json']
        for path in candidates:
            if path.exists():
                data = json.loads(path.read_text(encoding='utf-8'))
                return data if isinstance(data, list) else data.get('rules', [])
        return []

    def history(self):
        path = self.root / 'data' / 'imports' / 'history.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version': 1, 'resources': [], 'records': []}

    def nodes(self, resources, mode, capabilities, experiment='single', coverage='main'):
        nodes, edges = [], []
        rules = self.rules()
        available = capabilities.get('procedures', {})
        if isinstance(available, list):
            available = {p: {'available': True} for p in available}
        history = self.history()['records']
        for resource in resources:
            selected = [a for a in self.owners if experiment == 'two' or a['id'].startswith('s')]
            for owner in selected:
                node_id = resource['id'] + ':' + owner['id']
                procedure = owner['main_procedure_id']
                cap = available.get(procedure, {})
                if isinstance(cap, bool):
                    cap = {'available': cap}
                records = [r for r in history if r['resourceId'] == resource['id'] and r['ownerId'] == owner['id']]
                real_available = bool(cap.get('available', cap.get('supported', False)))
                node = {'id': node_id, 'ownerId': owner['id'], 'procedureId': procedure,
                        'name': owner['source_function'], 'label': owner['name'], 'status': 'pending',
                        'progress': 0, 'attempt': 0, 'qubits': resource['label'], 'resourceId': resource['id'],
                        'detail': '等待运行' if real_available or records else '当前方法尚未接入',
                        'capability': 'available' if real_available else ('historical_only' if records else 'unavailable'),
                        'engineState': 'pending', 'parameters': {}, 'parameterVersions': {},
                        'rules': deepcopy([r for r in rules if not r.get('scope') or owner['id'] in r['scope']]), 'dependsOn': [resource['id'] + ':' + d for d in self.dependencies.get(owner['id'], [])],
                        'mainProcedureId': procedure, 'mainProcedureComplete': False}
                nodes.append(node)
                edges.extend({'from': d, 'to': node_id} for d in node['dependsOn'])
            if mode == 'real' and coverage in ('full', 'working_point'):
                by_owner = {n['ownerId']: n for n in nodes if n['resourceId'] == resource['id']}
                for owner_id, procedure, label, name, dependencies in FULL_SINGLE_AUXILIARY:
                    if owner_id not in by_owner:
                        continue
                    parent = by_owner[owner_id]
                    cap = available.get(procedure, {})
                    supported = cap if isinstance(cap, bool) else cap.get('available', cap.get('supported', False))
                    auxiliary = deepcopy(parent)
                    auxiliary.update(id=parent['id'] + ':' + procedure, procedureId=procedure, name=name, label=label,
                                     role='auxiliary', mainProcedureId=procedure, capability='available' if supported else 'unavailable',
                                     detail='等待运行' if supported else '当前执行器尚未接入此测项',
                                     dependsOn=[resource['id'] + ':' + d for d in dependencies])
                    nodes.append(auxiliary)
                by_id = {n['id']: n for n in nodes if n['resourceId'] == resource['id']}
                extra_dependencies = {
                    's02_qubit_frequency': ['s01_readout_frequency:ro_power_shift'],
                    's04_readout_fidelity': ['s03_rabi_amplitude:sq_rabi_time'],
                    's07_t2': ['s07_t2:sq_echo'],
                    's08_single_qubit_rb': ['s03_rabi_amplitude:sq_allxy', 's06_t1', 's07_t2'],
                }
                for target, deps in extra_dependencies.items():
                    node = by_id.get(resource['id'] + ':' + target)
                    if node:
                        node['dependsOn'] = list(dict.fromkeys(node['dependsOn'] + [resource['id'] + ':' + d for d in deps]))
        if coverage == 'working_point':
            # A distinct new-task scope. Never rewrite an existing full graph.
            omitted_ids = {n['id'] for n in nodes if n['procedureId'] in WORKING_POINT_OMITTED}
            nodes = [n for n in nodes if n['id'] not in omitted_ids]
            for node in nodes:
                node['dependsOn'] = [d for d in node['dependsOn'] if d not in omitted_ids]
        edges = [{'from': d, 'to': node['id']} for node in nodes for d in node['dependsOn']]
        return nodes, edges

    def describe(self, capabilities, parameter_bounds=None):
        history = self.history()
        return {'blueprint': {'version': VERSION, 'nodes': self.owners,
                             'edges': [{'from': d, 'to': k} for k, ds in self.dependencies.items() for d in ds]},
                'resources': history['resources'], 'rules': self.rules(), 'capabilities': capabilities,
                'parameterBounds': parameter_bounds or {},
                'coverage': {'realDefault': 'full', 'main': '原流程八个主步骤', 'full': '八个主步骤及功率扫描、时间Rabi、AllXY、Ramsey、Echo独立采集',
                             'working_point': '复刻已有工作点 · 10项脉冲测量'},
                'workingPointCoverage': {
                    'available': capabilities.get('physicalHardware') is True and capabilities.get('id') in WORKING_POINT_PROFILE_IDS and 'Q11' in capabilities.get('resources', []),
                    'reason': '仅限已审计 Q11 ArcLight v1/v2；创建时重新核对历史来源与完整配置。历史参数仅为初值，10项全部重新采集验收。',
                    'resourceIds': ['Q11'], 'adapterProfileIds': list(WORKING_POINT_PROFILE_IDS),
                    'selectedNodeCount': 10, 'seedStatus': 'seed_only', 'label': '复刻已有工作点 · 10项脉冲测量'},
                'stages': [{'id': 'na', 'label': '网分预扫描', 'capability': 'unavailable', 'reason': '当前连接为主机脉冲测控；网分适配未接入'},
                           {'id': 'rewire', 'label': '接线确认', 'requiresHardwareConfirmation': True},
                           {'id': 'pulse', 'label': '脉冲测量', 'blueprintVersion': VERSION}]}
