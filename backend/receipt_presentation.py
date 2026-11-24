"""Read-only presentation derived from receipts; never changes stored history."""
from copy import deepcopy
from datetime import datetime
import json

from .stopped_receipts import stopped_summary

LEGACY_STOP_MESSAGE = 'ArcLight worker stopped and cleanup completed; partial data retained'


def read_receipts(db, workflow_id):
    evidence = []
    for row in db.execute(
        "SELECT attempt_id,operation_id,result FROM platform_effects WHERE workflow_id=? AND state IN ('cancelled','failed_after_shutdown') ORDER BY operation_id",
        (workflow_id,)):
        try:
            receipt = json.loads(row[2])
        except (TypeError, ValueError):
            continue
        if isinstance(receipt, dict):
            evidence.append({'workflowId': workflow_id, 'attemptId': row[0], 'operationId': row[1], 'receipt': receipt})
    return evidence


def _summary(workflow_id, attempt_id, result, evidence):
    matched = [e for e in evidence if isinstance(e, dict) and e.get('workflowId') == workflow_id
               and e.get('attemptId') == attempt_id and attempt_id
               and isinstance(e.get('receipt'), dict) and isinstance(e.get('operationId'), str)
               and isinstance(e['receipt'].get('partialArtifacts', []), list)]
    summary = stopped_summary([e['receipt'] for e in matched],
                              selection_retained=bool((result or {}).get('selectionResult')))
    if summary:
        summary['receiptOperationIds'] = [e['operationId'] for e in matched
            if e['receipt'].get('shutdownConfirmed') is True
            and e['receipt'].get('status') in ('cancelled', 'failed_after_shutdown')]
    return summary


def _result(result, summary):
    if not summary or not isinstance(result, dict) or not result.get('partial'):
        return
    technical = result.setdefault('technicalDetails', {})
    technical.setdefault('originalQualityReasons', deepcopy(result.get('quality', {}).get('reasons', [])))
    technical['receiptOperationIds'] = summary['receiptOperationIds']
    result['executionSummary'] = summary
    result.setdefault('quality', {})['reasons'] = [summary['message']]


def _node(node, workflow_id, evidence):
    result = node.get('result') or {}
    summary = _summary(workflow_id, node.get('latestAttemptId'), result, evidence)
    if not summary or not result.get('partial'):
        return
    _result(result, summary)
    technical = node.setdefault('technicalDetails', {})
    technical.setdefault('originalDetail', node.get('detail', ''))
    technical['receiptOperationIds'] = summary['receiptOperationIds']
    node['detail'] = summary['message']


def _time(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp.timestamp() if stamp.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def workflow_view(workflow, attempts, evidence):
    view = deepcopy(workflow)
    for node in view.get('nodes', []):
        _node(node, view['id'], evidence)
    for event in view.get('events', []):
        original = event.get('message', '')
        if LEGACY_STOP_MESSAGE not in original:
            continue
        stamp = _time(event.get('time'))
        matched = []
        for attempt in attempts:
            ended = _time(attempt.get('endedAt'))
            # Old events lack attempt IDs. Only a unique same-error event at
            # the attempt's completion can be linked; otherwise stay neutral.
            if (stamp is not None and ended is not None and 0 <= stamp - ended <= 5
                    and attempt.get('error') == original):
                summary = _summary(view['id'], attempt.get('id'), attempt.get('result'), evidence)
                if summary:
                    matched.append(summary)
        summary = matched[0] if len(matched) == 1 else None
        event['technicalDetails'] = {'originalMessage': original,
                                    'receiptOperationIds': summary['receiptOperationIds'] if summary else []}
        event['message'] = summary['message'] if summary else '历史执行发生异常；具体原因与数据留存情况请查看对应尝试的技术详情。'
    return view


def detail_view(workflow_id, detail, evidence):
    view = deepcopy(detail)
    _node(view['node'], workflow_id, evidence)
    for attempt in view.get('attempts', []):
        _result(attempt.get('result'), _summary(workflow_id, attempt.get('id'), attempt.get('result'), evidence))
    return view


def report_view(report):
    """Use only this report's frozen evidence, without querying newer state."""
    view = deepcopy(report)
    evidence = view.get('stoppedReceipts', [])
    workflow_id = view['workflow']['id']
    attempts = [a for item in view.get('nodes', []) for a in item.get('attempts', [])]
    view['workflow'] = workflow_view(view['workflow'], attempts, evidence)
    view['nodes'] = [detail_view(workflow_id, item, evidence) for item in view.get('nodes', [])]
    return view
