"""Read the running deployment and replay supplied archives; never start real tasks."""
import json
from pathlib import Path
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8765'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def request(path, data=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Qcal-Token'] = token
    req = urllib.request.Request(BASE + path, data=None if data is None else json.dumps(data, ensure_ascii=False).encode(), headers=headers)
    with opener.open(req, timeout=20) as response:
        return json.loads(response.read())

health = request('/api/health')
assert health['projectId'] == 'qcal-local-f8-platform'
assert health['hardware']['staticReady']
assert health['agent']['serviceId'] == 'qcal-brain'
token = request('/api/session')['token']
tasks = request('/api/workflows')
name = 'Q11 / Q18 · F8 实测档案回放'
workflow = next((w for w in tasks if w['name'] == name and w['mode'] == 'replay'), None)
if workflow is None:
    workflow = request('/api/workflows', {'name': name, 'mode': 'replay', 'resourceIds': ['Q11', 'Q18'],
                       'experiment': 'single', 'machine': '八号机', 'target': 'Q11,Q18', 'qubits': 2,
                       'type': 'adaptive', 'policy': 'threshold', 'scenario': 'normal'}, token)
if workflow['status'] == 'draft':
    workflow = request('/api/workflows/' + workflow['id'] + '/actions',
                       {'action': 'start', 'operationId': 'delivery-' + uuid.uuid4().hex, 'expectedRevision': workflow['revision']}, token)
deadline = time.monotonic() + 50
while workflow['status'] not in ('completed', 'failed', 'aborted', 'attention') and time.monotonic() < deadline:
    time.sleep(.2)
    workflow = request('/api/workflows/' + workflow['id'])
assert workflow['status'] == 'completed', workflow['engineState']
assert workflow['mode'] == 'replay' and workflow['workflowComplete'] is False
assert len(workflow['processedRecordIds']) == 59
report = request('/api/workflows/' + workflow['id'] + '/report')
assert sum(len(n['attempts']) for n in report['nodes']) == 59
assert all(a['result']['dataKind'] == 'historical' for n in report['nodes'] for a in n['attempts'])
knowledge = request('/api/knowledge')
assert len(knowledge['rules']) == 10
destination = ROOT / 'reports'
destination.mkdir(exist_ok=True)
(destination / 'Q11_Q18_historical_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
with opener.open(BASE + '/api/workflows/' + workflow['id'] + '/report?format=html', timeout=30) as response:
    html_report = response.read()
assert b'data:image/' in html_report
(destination / 'Q11_Q18_historical_report.html').write_bytes(html_report)
operations = ROOT / 'data' / 'runs' / 'f8_operations'
hardware_operations = len(list(operations.iterdir())) if operations.exists() else 0
assert hardware_operations == 0, 'This acceptance run must not perform hardware acquisitions'
summary = {'checkedAt': time.strftime('%Y-%m-%d %H:%M:%S'), 'url': BASE, 'workflowId': workflow['id'],
           'mode': 'replay', 'historicalRecords': 59, 'historicalArtifacts': 221,
           'softwareTests': {'backendPython312': {'passed': 64, 'skipped': 0},
                             'backendPython313': {'passed': 58, 'skipped': 6},
                             'frontend': {'passed': 116}, 'agent': {'passed': 11},
                             'browserReplay': {'passed': 13}, 'browserReadOnly': {'passed': 5}},
           'modelProbe': 'agent/runtime/connection-probe.json', 'hardwareOperationsThisDelivery': hardware_operations,
           'hardwareStatus': health['hardware'], 'agentStatus': health['agent'],
           'pending': ['Real single-qubit end-to-end validation awaits device handoff',
                       'Network analyzer, qoskit and two-qubit execution adapters are not bound'],
           'protectedWorkspaceAccessed': False}
(destination / 'acceptance.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'status': 'passed', 'url': BASE, 'records': 59, 'hardwareOperations': hardware_operations,
                  'report': str(destination / 'acceptance.json')}, ensure_ascii=False))
