"""Read completed ArcLight point files only; no instrument imports or writes.

The CLI accepts an operation ID, never a user-supplied filesystem root.  NumPy is
loaded in the existing analysis interpreter.  The stdlib API process need not
install or import scientific/device packages.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import sys
import zipfile

MAX_POINTS = 15000
MAX_BATCH = 256
MAX_POINT_BYTES = 4 * 1024 * 1024
PROTECTED = 'd:/qoskit-workspace'


class UnsafeLiveSource(ValueError):
    pass


def safe_path(path, root):
    """Reject links and Windows junctions before following any path component."""
    path, root = Path(os.path.abspath(path)), Path(os.path.abspath(root))
    raw = str(path).replace('\\', '/').lower().rstrip('/')
    if raw == PROTECTED or raw.startswith(PROTECTED + '/'):
        raise UnsafeLiveSource('Protected workspace is inaccessible')
    if not path.is_relative_to(root):
        raise UnsafeLiveSource('Live source is outside the operation root')
    chain = list(reversed(path.parents)) + [path]
    for part in chain:
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise UnsafeLiveSource('Linked live source is not accepted')
    return path


def stable_bytes(path, root, maximum):
    path = safe_path(path, root)
    before = path.stat()
    if before.st_size > maximum:
        raise UnsafeLiveSource('Live source exceeds the bounded file size')
    with path.open('rb') as stream:
        value = stream.read(maximum + 1)
    after = path.stat()
    safe_path(path, root)
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise BlockingIOError('Point file is still being written')
    if len(value) > maximum:
        raise UnsafeLiveSource('Live source exceeds the bounded file size')
    return value


def read_json(path, root, maximum=1024*1024):
    return json.loads(stable_bytes(path, root, maximum).decode('utf-8-sig'))


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid ' + label)
    return value


def read_batch(project_root, request):
    """Pure read-only function; project_root is supplied by trusted host code."""
    import numpy as np

    if not isinstance(request, dict) or set(request) - {'operationId', 'afterByJob', 'limit'}:
        raise ValueError('Unexpected reader input')
    operation_id = request.get('operationId')
    if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 256:
        raise ValueError('Invalid operation ID')
    limit = integer(request.get('limit', MAX_BATCH), 1, MAX_BATCH, 'batch limit')
    offsets = request.get('afterByJob', {})
    if not isinstance(offsets, dict) or len(offsets) > 64:
        raise ValueError('Invalid job cursors')
    for key, value in offsets.items():
        if not isinstance(key, str) or len(key) > 256:
            raise ValueError('Invalid job ID')
        integer(value, -1, MAX_POINTS-1, 'point cursor')
    allowed = Path(project_root) / 'data/runs/arclight_operations'
    operation = allowed / hashlib.sha256(operation_id.encode()).hexdigest()
    result = {'operationId': operation_id, 'points': [], 'state': 'not_started',
              'complete': False, 'terminal': False, 'availablePoints': 0, 'pending': False}
    try:
        safe_path(operation, allowed)
        plan = read_json(operation/'plan.json', operation, 16*1024*1024)
        intent = read_json(operation/'adapter_intent.json', operation)
    except (FileNotFoundError, BlockingIOError, json.JSONDecodeError):
        result['pending'] = True
        return result
    if plan.get('operationId') != operation_id or intent.get('operationId') != operation_id or intent.get('request', {}).get('operationId') != operation_id:
        raise UnsafeLiveSource('Live operation identity mismatch')
    if plan.get('adapter') != 'arclight' or plan.get('compileOnlyEvidence'):
        raise UnsafeLiveSource('Only real ArcLight operation plans are accepted')
    integer(plan.get('totalRows'), 1, MAX_POINTS, 'planned point count')
    planned = [j.get('name') for j in plan.get('jobs', [])]
    if not planned or len(planned) > 64 or any(not isinstance(n, str) for n in planned) or len(set(planned)) != len(planned):
        raise UnsafeLiveSource('Invalid planned job identity')
    planned_samples = {}
    for job in plan['jobs']:
        shots = integer(job.get('spec', {}).get('meta', {}).get('shots'), 1, 2047, 'planned block shots')
        blocks = integer(job.get('acquisitions_per_point'), 1, 16, 'planned acquisition blocks')
        planned_samples[job['name']] = shots * blocks
    result['state'] = 'acquiring'
    try:
        completion = read_json(operation/'completion.json', operation, 16*1024*1024)
        result['state'] = completion.get('state', 'acquiring')
        result['terminal'] = completion.get('state') in ('completed', 'cancelled', 'failed', 'failed_after_shutdown')
        result['complete'] = completion.get('state') == 'completed'
    except (FileNotFoundError, BlockingIOError, json.JSONDecodeError):
        pass
    folders = {}
    try:
        results = safe_path(operation/'results', operation)
        children = list(results.iterdir())
    except FileNotFoundError:
        result['pending'] = True
        return result
    if len(children) > 64:
        raise UnsafeLiveSource('Unexpected result-directory count')
    for folder in children:
        safe_path(folder, operation)
        if not folder.is_dir():
            continue
        try:
            status = read_json(folder/'status.json', operation)
        except (FileNotFoundError, BlockingIOError, json.JSONDecodeError):
            result['pending'] = True
            continue
        if status.get('mode') != 'real_hardware':
            continue
        name = status.get('job')
        if name not in planned or name in folders:
            raise UnsafeLiveSource('Unexpected or duplicate real job')
        done = integer(status.get('points_completed', 0), 0, MAX_POINTS, 'completed point count')
        expected = integer(status.get('points_expected', 0), 0, MAX_POINTS, 'expected point count')
        if done > expected:
            raise UnsafeLiveSource('Completed point count exceeds expectation')
        folders[name] = (folder, status, done)
        result['availablePoints'] += done
    if result['availablePoints'] > plan['totalRows']:
        raise UnsafeLiveSource('Acquired points exceed bounded plan')
    result['complete'] = bool(result['complete'] and result['availablePoints'] == plan['totalRows']
        and len(folders) == len(planned) and all(s['state'] == 'acquired' for _,s,_ in folders.values()))
    for name in planned:
        if name not in folders:
            continue
        folder, status, done = folders[name]
        start = offsets.get(folder.name, -1) + 1
        if start >= done:
            continue
        try:
            axes = read_json(folder/'axes.json', operation, 16*1024*1024)
        except (FileNotFoundError, BlockingIOError, json.JSONDecodeError):
            result['pending'] = True
            continue
        if not isinstance(axes, list) or len(axes) != status['points_expected']:
            raise UnsafeLiveSource('Point axes do not match the acquisition receipt')
        for index in range(start, done):
            if len(result['points']) >= limit:
                return result
            source = folder/f'point_{index:04d}.npz'
            try:
                raw = stable_bytes(source, operation, MAX_POINT_BYTES)
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    if sum(z.file_size for z in archive.infolist()) > MAX_POINT_BYTES:
                        raise UnsafeLiveSource('Expanded point data exceeds bound')
                with np.load(io.BytesIO(raw), allow_pickle=False) as point:
                    i, q = point['i'], point['q']
                    if i.shape != q.shape or i.ndim != 2 or i.shape[0] != 1 or i.shape[1] != planned_samples[name]:
                        raise UnsafeLiveSource('Unexpected single-qubit IQ shape')
                    if i.dtype.kind != 'f' or q.dtype.kind != 'f' or not np.isfinite(i).all() or not np.isfinite(q).all():
                        raise UnsafeLiveSource('Nonfinite or nonnumeric IQ')
                    mean_i, mean_q = float(np.mean(i)), float(np.mean(q))
            except (FileNotFoundError, BlockingIOError, zipfile.BadZipFile, EOFError):
                result['pending'] = True
                break  # Do not advance the cursor past an unfinished point.
            coordinate = axes[index]
            if not isinstance(coordinate, dict) or not isinstance(coordinate.get('axes'), dict):
                raise UnsafeLiveSource('Malformed point coordinate')
            capture = integer(coordinate.get('capture'), 0, 100, 'capture')
            base = integer(coordinate.get('base_index'), 0, MAX_POINTS-1, 'base index')
            result['points'].append({'operationId': operation_id, 'jobId': folder.name, 'jobName': name,
                'pointIndex': index, 'capture': capture, 'baseIndex': base,
                'axes': coordinate['axes'], 'meanI': mean_i, 'meanQ': mean_q,
                'meanAbs': math.hypot(mean_i, mean_q), 'meanMagnitude': math.hypot(mean_i, mean_q),
                'meanAbsDefinition': 'abs(mean(I + 1j*Q))',
                'shots': int(i.size), 'pointSha256': hashlib.sha256(raw).hexdigest(),
                'pointFile': str(source.relative_to(operation)).replace('\\', '/'),
                'acquisitionStartedAt': status.get('started')})
    return result


def main():
    # Guard the reader even if scientific libraries unexpectedly attempt I/O.
    import socket
    def denied(*args, **kwargs):
        raise RuntimeError('Live data reader has no network/instrument capability')
    socket.create_connection = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.socket.sendto = denied
    raw = sys.stdin.buffer.read(1024*1024+1)
    if len(raw) > 1024*1024:
        raise ValueError('Reader request too large')
    request = json.loads(raw)
    result = read_batch(Path(__file__).absolute().parents[1], request)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        sys.stderr.write(type(exc).__name__ + ': ' + str(exc))
        sys.exit(2)
