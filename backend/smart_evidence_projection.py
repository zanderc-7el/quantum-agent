"""Expose aggregate acquisition evidence without repeating every block's file record in model input."""
from copy import deepcopy
import re

from .common import APIError, digest
from .decision_context import current_attempt_evidence_ids
from .model_input_projection import MAX_INPUT_UNITS
from .prior_failure_context import javascript_json_units_upper_bound


BLOCK = re.compile(r'point_[0-9]+_block_[0-9]+\.npz\Z')
POINT = re.compile(r'point_[0-9]+_acquisition_blocks\.json\Z')


def compact_block_evidence(payload):
    result = payload.get('observations', {}).get('result') or {}
    selection = result.get('selectionResult') or {}
    if all(len(r.get('artifacts', [])) <= 32 for r in (result, selection)):
        return payload
    view = deepcopy(payload)
    result = view['observations']['result']
    catalog = {}
    for role, part in [('result', result), ('selectionResult', result.get('selectionResult') or {})]:
        items = part.get('artifacts', [])
        if len(items) <= 32 or part.get('dataKind') != 'real':
            continue
        names = {a.get('name') for a in items if isinstance(a, dict) and a.get('id') and a.get('sha256')}
        if not {'raw_scan.npz','platform_analysis.json','sampling_receipt.json'}.issubset(names):
            continue
        headers, blocks, points = [], [], []
        for a in items:
            name = a.get('name', '') if isinstance(a, dict) else ''
            if isinstance(name, str) and BLOCK.fullmatch(name) and a.get('kind') == 'raw':
                blocks.append(a)
            elif isinstance(name, str) and POINT.fullmatch(name) and a.get('kind') == 'json':
                points.append(a)
            else:
                headers.append(a)
        if not blocks and not points:
            continue
        part['artifacts'] = headers
        catalog[role] = {'registeredCount':len(items), 'shownCount':len(headers),
                         'individualBlockFiles':len(blocks), 'pointReceiptFiles':len(points),
                         'catalogSha256':digest(items)}
    if not catalog:
        return payload
    view['allowedEvidenceIds'] = current_attempt_evidence_ids(result)
    view['modelEvidenceCatalog'] = {'roles':catalog, 'scope':'current_attempt_model_view',
        'notice':'模型可引用所列汇总原始数据、原生分析和采样回执。逐块文件仍逐项存放于本地档案。'}
    return view


def check_shown_evidence(payload, decision):
    if payload.get('modelEvidenceCatalog') and not set(decision.get('evidenceIds', [])).issubset(payload['allowedEvidenceIds']):
        raise APIError('BAD_EVIDENCE','本次提案引用了模型未读取的产物，请引用当前采集的数据或汇总回执')


def fit_final_input(payload):
    """Reserve current evidence, active constraints, checkpoint and the last model error."""
    if javascript_json_units_upper_bound(payload) <= MAX_INPUT_UNITS:
        return payload
    view = deepcopy(payload)
    optional = ((view,'historicalRabiGrid'), (view,'priorFailureContext'),
                (view.get('observations', {}),'curveNavigation'))
    removed = []
    for parent, key in optional:
        if key in parent:
            del parent[key]
            removed.append(key)
            view['optionalContextOmitted'] = removed
            if javascript_json_units_upper_bound(view) <= MAX_INPUT_UNITS:
                break
    return view
