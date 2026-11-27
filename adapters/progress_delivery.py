"""Bounded telemetry delivery; the native event journal remains complete."""
import json


def read_event_batch(path, position, max_lines=512):
    if not path.exists():
        return position, []
    events = []
    with path.open('rb') as stream:
        stream.seek(position)
        # Freeze the read boundary before delivering potentially slow callbacks.
        end = path.stat().st_size
        for _ in range(max_lines):
            if stream.tell() >= end:
                break
            line = stream.readline(min(end - stream.tell(), 1024 * 1024 + 1))
            if len(line) > 1024 * 1024:
                raise ValueError('Native telemetry line exceeds 1 MiB')
            if not line.endswith(b'\n'):
                break
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('Native telemetry event must be an object')
            position = stream.tell()
            previous = events[-1] if events else {}
            if (event.get('event') == previous.get('event') == 'row_acquired'
                    and all(event.get(k) == previous.get(k) for k in ('operationId', 'jobIndex', 'jobId', 'jobCount'))):
                events[-1] = event
            else:
                events.append(event)
    return position, events
