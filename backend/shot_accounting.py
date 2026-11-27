"""Operation accounting for explicitly enabled workflows. Only receipt-backed counts are confirmed."""
import hashlib
import json
from pathlib import Path

from .common import APIError


def reserve(db, workflow_id, operation_id, estimate):
    if type(estimate) is not int or estimate <= 0:
        raise APIError('INVALID_BUDGET', '采集计划缺少有效 shots')
    row = db.execute('INSERT OR IGNORE INTO platform_shot_usage(workflow_id,operation_id,reserved) VALUES(?,?,?)',
                     (workflow_id, operation_id, estimate))
    if row.rowcount != 1:
        raise APIError('SHOT_RESERVATION_EXISTS', '该操作已预留 shots，请先核查原执行回执', 409)


def _archived_json(item, folder):
    path = Path(item.get('sourcePath') or item.get('localPath') or '').resolve()
    if not path.is_relative_to(folder) or path.name != item.get('name') or path.stat().st_size > 32*1024*1024:
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != item['sha256']:
        return None
    return json.loads(raw.decode('utf-8-sig'))


def _quantity(value, minimum=0):
    return type(value) is int and value >= minimum


def confirmed_count(root, operation, result, state, reserved):
    """Return receipt-backed shots and resolution. Partial rows have a lower bound only."""
    if state == 'not_started':
        return 0, 'exact'
    if state not in ('completed', 'cancelled', 'failed_after_shutdown'):
        return None, 'unconfirmed'
    if not isinstance(result,dict) or not _quantity(reserved,1):
        return None, 'unconfirmed'
    folder = (Path(root)/'data/runs/arclight_operations'/hashlib.sha256(operation.encode()).hexdigest()).resolve()
    items = result.get('artifacts') or result.get('partialArtifacts') or []
    if not isinstance(items,list) or any(not isinstance(item,dict) for item in items):
        return None, 'unconfirmed'
    try:
        plans = [a for a in items if a.get('name') == 'plan.json']
        plan = _archived_json(plans[0], folder) if len(plans) == 1 else None
        if not isinstance(plan,dict) or plan.get('operationId') != operation or plan.get('estimated_total_shots') != reserved:
            return None, 'unconfirmed'
        statuses = [a for a in items if a.get('name') == 'status.json']
        if statuses and plan.get('jobs'):
            groups = [_archived_json(item,folder) for item in statuses]
            jobs = plan['jobs']
            rows = plan.get('expectedJobRows', [])
            if len(rows) != len(jobs) or len({j['name'] for j in jobs}) != len(jobs):
                return None, 'unconfirmed'
            expected = {j['name']:(n,j['spec']['meta']['shots'],j['acquisitions_per_point']) for j,n in zip(jobs,rows)}
            observed = set()
            for s in groups:
                if not isinstance(s,dict) or not _quantity(s.get('points_completed')) or s.get('job') not in expected or s['job'] in observed:
                    return None, 'unconfirmed'
                n, shots, blocks = expected[s['job']]
                if (not all(_quantity(v,1) for v in (n,shots,blocks)) or s['points_completed']>n or
                        (s.get('points_expected'),s.get('shots_per_acquisition'),s.get('acquisitions_per_point'),
                         s.get('effective_shots_per_point')) != (n, shots, blocks, shots*blocks)):
                    return None, 'unconfirmed'
                observed.add(s['job'])
            count = sum(s['points_completed']*s['effective_shots_per_point'] for s in groups)
            if count > reserved:
                return None, 'unconfirmed'
            complete = (len(groups)==len(plan['jobs']) and state=='completed' and count==reserved and
                        all(s['points_completed']==s.get('points_expected') and s.get('shutdown_ok') is True for s in groups))
            return count, 'exact' if complete else 'lower_bound'
        if plan.get('rows') and len(statuses) == 1:
            receipt = _archived_json(statuses[0], folder)
            if not receipt or receipt.get('operationId') != operation or receipt.get('shutdown_ok') is not True:
                return None, 'unconfirmed'
            # Science workers archive this status after the ADC loop and before
            # fitting. Native analysis can subsequently fail with all rows acquired.
            if receipt.get('state') not in ('acquired','completed','cancelled','failed_after_shutdown'):
                return None, 'unconfirmed'
            points, total_rows = receipt.get('pointsCompleted'), plan.get('totalRows')
            shots = plan.get('parameters',{}).get('shots')
            blocks = plan.get('parameters',{}).get('acquisitions_per_point')
            if (all(_quantity(v,1) for v in (total_rows,shots,blocks)) and _quantity(points)
                    and points <= total_rows == len(plan['rows']) and total_rows*shots*blocks == reserved):
                expected_raw = ('raw_scan.npz',) if state=='completed' else ('raw_scan.npz','partial_raw_scan.npz')
                if points and not any(a.get('name') in expected_raw and a.get('kind')=='raw' for a in items):
                    return None, 'unconfirmed'
                complete = state=='completed' and points==total_rows and receipt['state'] in ('acquired','completed')
                return points*shots*blocks, 'exact' if complete else 'lower_bound'
    except (OSError, TypeError, ValueError, KeyError):
        pass
    return None, 'unconfirmed'


def capture(db, operation, root):
    row = db.execute('''SELECT u.reserved,e.state,e.result FROM platform_shot_usage u
                      JOIN platform_effects e ON e.operation_id=u.operation_id WHERE u.operation_id=?''',(operation,)).fetchone()
    if not row:
        return
    count, resolution = confirmed_count(root, operation, json.loads(row['result'] or '{}'), row['state'], row['reserved'])
    db.execute('UPDATE platform_shot_usage SET confirmed=?,resolution=? WHERE operation_id=?',(count,resolution,operation))


def import_existing(db, workflow, root):
    """Existing finite tasks can opt in at a checkpoint without rewriting old records."""
    rows = db.execute("""SELECT json_extract(payload,'$.operationId'),json_extract(payload,'$.shots') FROM events
                         WHERE run_id=? AND event='budget_reserved' ORDER BY seq""",(workflow['id'],)).fetchall()
    for operation, shots in rows:
        if isinstance(operation,str) and _quantity(shots,1):
            reserve(db, workflow['id'], operation, shots)
            capture(db, operation, root)


def view(db, workflow):
    if (workflow.get('budget') or {}).get('mode') != 'accounting_only':
        return workflow
    scope = [workflow['id']]
    total_reserved, attempts = workflow.get('reservedShots',0), workflow.get('iterations',0)
    children = db.execute("""SELECT w.id,json_extract(w.payload,'$.reservedShots'),json_extract(w.payload,'$.iterations')
                     FROM platform_accounting_workflows a JOIN platform_workflows w ON w.id=a.workflow_id
                     WHERE a.parent_id=?""",(workflow['id'],)).fetchall()
    for child_id, shots, child_attempts in children:
        scope.append(child_id)
        total_reserved += shots
        attempts += child_attempts
    row = db.execute('''SELECT COALESCE(SUM(u.confirmed),0),COUNT(u.operation_id),
        COALESCE(SUM(CASE WHEN COALESCE(u.resolution,'unconfirmed')!='exact' AND COALESCE(e.state,'pending')!='not_started' THEN 1 ELSE 0 END),0),
        COALESCE(SUM(u.reserved),0) FROM platform_shot_usage u LEFT JOIN platform_effects e
        ON u.operation_id=e.operation_id WHERE u.workflow_id IN (SELECT value FROM json_each(?))''',(json.dumps(scope),)).fetchone()
    return {**workflow, 'shotAccounting': {'confirmedShots':row[0], 'operations':row[1],
            'unresolvedOperations':row[2], 'unattributedReservedShots':max(0,total_reserved-row[3]),
            'reservedShots':total_reserved, 'attempts':attempts, 'scienceChildren':len(children)}}
