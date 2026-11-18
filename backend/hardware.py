"""Read-only host socket ownership check; never opens an instrument connection."""
import json
import os
import re
import subprocess
import time

def connection_owners(profile):
    address = profile.get('instrument', {}).get('address', '')
    match = re.fullmatch(r'TCPIP::(\d{1,3}(?:\.\d{1,3}){3})::(\d+)::SOCKET', address)
    if not match:
        return {'verified': False, 'owners': [], 'reason': '设备地址尚未绑定到可核查的 socket 端点'}
    if os.name != 'nt':
        return {'verified': False, 'owners': [], 'reason': '本机资源核查仅支持已部署的 Windows 测控站'}
    host, port = match[1], int(match[2])
    script = f"$rows = @(Get-NetTCPConnection -RemoteAddress '{host}' -RemotePort {port} -State Established -ErrorAction SilentlyContinue | Select-Object OwningProcess,LocalPort); ConvertTo-Json -InputObject $rows -Compress"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=12,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0:
            return {'verified': False, 'owners': [], 'reason': 'Windows socket 状态核查失败'}
        rows = json.loads(result.stdout.strip() or '[]')
        if isinstance(rows, dict):
            rows = [rows]
        return {'verified': True, 'owners': [{'pid': r['OwningProcess'], 'localPort': r['LocalPort']} for r in rows],
                'endpoint': f'{host}:{port}', 'checkedAt': time.time(), 'reason': '只读取连接表，未连接仪器'}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {'verified': False, 'owners': [], 'reason': str(exc)}
