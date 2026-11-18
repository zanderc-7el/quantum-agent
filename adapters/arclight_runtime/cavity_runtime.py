"""File/cleanup utilities; no instrument imports or implicit connections."""
import json
import os
from pathlib import Path
import tempfile
import time


def save_json(path, data):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows may briefly deny rename while another reader/writer
                # releases its handle. Keep the old complete file and retry.
                if attempt == 9:
                    raise
                time.sleep(.01 * (attempt + 1))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class InstrumentLock:
    def __init__(self, path):
        self.path = Path(path)
        self.owned = False

    def acquire(self, details):
        stream = self.path.open('x', encoding='utf8')
        self.owned = True
        try:
            with stream:
                json.dump(details, stream)
        except BaseException:
            self.release()
            raise

    def release(self):
        if self.owned:
            self.path.unlink(missing_ok=True)
            self.owned = False


def close_resources(hardware, experiment):
    """Attempt every independent shutdown step; return errors for the caller.

    Catch interruptions here so a second Ctrl+C does not skip later closes.
    The caller retains the measurement's primary exception and owns the lock.
    """
    failures = {}
    first_cleanup = {}

    def attempt(name, operation):
        try:
            return operation()
        except BaseException as exc:
            failures[name] = f'{type(exc).__name__}: {exc}'

    driver = hardware.driver
    if driver.handle is not None:
        first_cleanup = attempt('cleanup_readout', driver.cleanup_readout) or {}
    attempt('hardware_close', hardware.close)
    attempt('driver_close', driver.close)  # covers partially failed Hardware.open
    if experiment is not None:
        attempt('experiment_close', experiment.close)
    final_close = getattr(driver, 'last_close_result', {})
    final_cleanup = getattr(driver, 'last_cleanup', {}) or first_cleanup
    unresolved = {name: value for name, value in final_cleanup.items()
                  if isinstance(value, dict) and value.get('ok') is False}
    connected = driver.handle is not None
    driver_shutdown_failed = final_close.get('ok') is False
    if connected:
        failures['handle_remaining'] = 'Instrument handle remains open after all close attempts.'
    # A suite constructs the next native job in the same isolated process.
    # Close() alone leaves the upstream named-instance registry populated.
    if not connected and not failures and not unresolved and not driver_shutdown_failed:
        hardware.remove_instance(hardware)
    return {'cleanup': final_cleanup, 'first_cleanup': first_cleanup,
            'cleanup_history': getattr(driver, 'cleanup_history', []),
            'driver_close_result': final_close, 'shutdown_errors': failures,
            'hardware_connected': connected,
            'shutdown_ok': not failures and not unresolved and not connected and not driver_shutdown_failed}
