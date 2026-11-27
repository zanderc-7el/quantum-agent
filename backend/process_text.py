"""Explicit API display encoding, without altering frozen subprocess protocols.

On Windows the API launchers use ``-X utf8=0`` so an unspecified subprocess
text encoding matches the system locale used by isolated ``python -I`` workers.
HTTP and persisted JSON already specify UTF-8; keep the API's own logs UTF-8 too.
No environment, subprocess function, codec, or native source is patched here.
"""
import sys


def configure_api_streams():
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, 'reconfigure', None)):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')


def api_error_payload(error):
    result = {'code': error.code, 'message': error.message}
    from .ownership_diagnostics import OwnershipRefusal
    if isinstance(error, OwnershipRefusal):
        result['ownershipCheck'] = error.ownershipCheck
    if (error.code == 'SCIENCE_IRB_PLAN_REJECTED'
            and error.message.startswith('Native IRB planning failed: ')
            and error.message.rstrip().endswith('ValueError: Native IRB sequence exceeds fixed control window')):
        result.update(message='IRB 脉冲序列超出 79.7 μs 控制时窗，请缩短序列长度后重新核对。',
                      technicalMessage=error.message)
    return result
