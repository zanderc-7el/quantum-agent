from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = 'd:/qoskit-workspace'

class APIError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status

def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False)

def now():
    return datetime.now(timezone.utc).isoformat()

def ident(prefix):
    return prefix + '-' + uuid.uuid4().hex

def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()

def number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)

def checked_path(value, *, parent=None):
    # Check lexically before filesystem resolution as well as after it.
    raw = str(value).replace('\\', '/').rstrip('/').lower()
    if raw == PROTECTED or raw.startswith(PROTECTED + '/'):
        raise APIError('PROTECTED_PATH', '该工作区不可访问', 403)
    path = Path(value).resolve()
    raw = str(path).replace('\\', '/').rstrip('/').lower()
    if raw == PROTECTED or raw.startswith(PROTECTED + '/'):
        raise APIError('PROTECTED_PATH', '该工作区不可访问', 403)
    if parent is not None and not path.is_relative_to(Path(parent).resolve()):
        raise APIError('PATH_OUTSIDE_ROOT', '路径超出允许目录', 403)
    return path

def file_hash(path):
    path = checked_path(path)
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()
