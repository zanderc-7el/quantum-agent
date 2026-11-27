"""Read-only host socket ownership check; never opens an instrument connection."""
import json
import os
import re
import subprocess
import time

def connection_owners(profile):
    def result(verified, owners, status, reason):
        return {'verified':verified,'owners':owners,'checkedAt':time.time(),'reason':reason,
                'check':{'schema':'qcal-socket-check/1','source':'windows_tcp_table','status':status}}
    address = profile.get('instrument', {}).get('address', '')
    match = re.fullmatch(r'TCPIP::(\d{1,3}(?:\.\d{1,3}){3})::(\d+)::SOCKET', address)
    if not match:
        return result(False, [], 'unconfigured_endpoint', '设备地址尚未绑定到可核查的 socket 端点')
    if os.name != 'nt':
        return result(False, [], 'unsupported_platform', '本机资源核查仅支持已部署的 Windows 测控站')
    host, port = match[1], int(match[2])
    script = f"$rows = @(Get-NetTCPConnection -RemoteAddress '{host}' -RemotePort {port} -State Established -ErrorAction SilentlyContinue | Select-Object OwningProcess,LocalPort); ConvertTo-Json -InputObject $rows -Compress"
    try:
        process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, timeout=12,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if process.returncode != 0:
            return result(False, [], 'failed', 'Windows socket 状态核查失败')
        rows = json.loads(process.stdout.strip() or '[]')
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows,list) or any(not isinstance(r,dict) or type(r.get('OwningProcess')) is not int or not 0<r['OwningProcess']<=2**32-1 or type(r.get('LocalPort')) is not int or not 0<r['LocalPort']<=65535 for r in rows):
            return result(False, [], 'invalid_response', 'Windows socket 核查返回格式无法识别')
        return {**result(True, [{'pid':r['OwningProcess'],'localPort':r['LocalPort']} for r in rows], 'completed', '只读取连接表，未连接仪器'), 'endpoint':f'{host}:{port}'}
    except subprocess.TimeoutExpired:
        return result(False, [], 'timeout', 'Windows socket 状态核查超时')
    except OSError:
        return result(False, [], 'failed', 'Windows socket 状态核查未能执行')
    except (ValueError,TypeError,AttributeError):
        return result(False, [], 'invalid_response', 'Windows socket 核查返回格式无法识别')
