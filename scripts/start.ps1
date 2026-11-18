param([switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
function Get-ConfiguredAgentPort($Root) {
    $file = Join-Path $Root 'config/runtime.local.json'
    if (-not (Test-Path -LiteralPath $file)) { return 8766 }
    try { $settings = Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json } catch { throw 'Cannot parse config/runtime.local.json; no service was started.' }
    if ($settings -isnot [pscustomobject]) { throw 'runtime.local.json must contain a configuration object.' }
    $property = $settings.PSObject.Properties | Where-Object { $_.Name -ceq 'agentPort' }
    if (-not $property) { return 8766 }
    $value = $property.Value
    $numeric = $value -is [int] -or $value -is [long] -or $value -is [double] -or $value -is [decimal]
    if (-not $numeric -or $value -notin @(8766,8767)) { throw 'agentPort must be the integer 8766 or 8767.' }
    return [int]$value
}
function Assert-ApiAgentPort($Health, [int]$Expected) {
    $property = $Health.PSObject.Properties | Where-Object { $_.Name -ceq 'agentPort' }
    if (-not $property -and $Expected -eq 8766) { return }
    $actual = $property.Value
    $numeric = $actual -is [int] -or $actual -is [long] -or $actual -is [double] -or $actual -is [decimal]
    if (-not $numeric -or $actual -ne $Expected) { throw "API agentPort does not match configured port $Expected; no service was stopped or reused." }
}
$agentPort = Get-ConfiguredAgentPort $projectRoot
$pythonPath = 'D:\Python313\python.exe'
$nodePath = (Get-Command node.exe).Source
$logRoot = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$owned = @()
$ownedPath = Join-Path $logRoot 'owned-processes.json'
if (Test-Path -LiteralPath $ownedPath) {
    foreach ($record in @(Get-Content -LiteralPath $ownedPath -Raw | ConvertFrom-Json)) {
        $priorProcess = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
        $recordedStart = [DateTime]::Parse([string]$record.startedAt).ToUniversalTime()
        if ($record.startedAt -is [DateTime]) { $recordedStart = $record.startedAt.ToUniversalTime() }
        if ($priorProcess -and $priorProcess.StartTime.ToUniversalTime().Ticks -eq $recordedStart.Ticks) { $owned += $record }
    }
}

function Get-LocalHealth($Url) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec 5 } catch { return $null }
}

$agentUrl = "http://127.0.0.1:$agentPort/health"
$agentHealth = Get-LocalHealth $agentUrl
if ($agentHealth -and ($agentHealth.serviceId -ne 'qcal-brain' -or $agentHealth.projectRoot -ne $projectRoot)) {
    throw "Port $agentPort is already used by another application. No process was stopped."
}
if (-not $agentHealth) {
    if (Get-NetTCPConnection -State Listen -LocalPort $agentPort -ErrorAction SilentlyContinue) { throw "Port $agentPort is occupied without valid health; no service was replaced." }
    $priorAgentPort = $env:QCAL_AGENT_PORT
    try {
        $env:QCAL_AGENT_PORT = [string]$agentPort
        $agentProcess = Start-Process -FilePath $nodePath -ArgumentList @('agent/src/server.mjs') -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot "agent-$agentPort.stdout.log") -RedirectStandardError (Join-Path $logRoot "agent-$agentPort.stderr.log")
    } finally {
        if ($null -eq $priorAgentPort) { Remove-Item Env:QCAL_AGENT_PORT -ErrorAction SilentlyContinue } else { $env:QCAL_AGENT_PORT = $priorAgentPort }
    }
    $owned += @{kind='agent';port=$agentPort;pid=$agentProcess.Id;startedAt=$agentProcess.StartTime.ToUniversalTime().ToString('o')}
}

$apiHealth = Get-LocalHealth 'http://127.0.0.1:8765/api/health'
if ($apiHealth -and ($apiHealth.projectId -ne 'qcal-local-f8-platform' -or $apiHealth.projectRoot -ne $projectRoot)) {
    throw 'Port 8765 is already used by another application. No process was stopped.'
}
if ($apiHealth) { Assert-ApiAgentPort $apiHealth $agentPort }
if (-not $apiHealth) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend/dist/index.html'))) {
        throw 'Build frontend first: cd frontend; npm ci; npm run build'
    }
    if (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue) { throw 'Port 8765 is occupied without valid health; no service was replaced.' }
    $apiProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-B','-X','utf8','-m','backend.server','--agent-port',[string]$agentPort) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'api.stdout.log') -RedirectStandardError (Join-Path $logRoot 'api.stderr.log')
    $owned += @{kind='api';port=8765;agentPort=$agentPort;pid=$apiProcess.Id;startedAt=$apiProcess.StartTime.ToUniversalTime().ToString('o')}
}
if ($owned.Count -gt 0) {
    $owned | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ownedPath -Encoding UTF8
}
$ready = $false
for ($n=0; $n -lt 20; $n++) {
    $health = Get-LocalHealth 'http://127.0.0.1:8765/api/health'
    if ($health -and $health.projectId -eq 'qcal-local-f8-platform' -and $health.projectRoot -eq $projectRoot) { Assert-ApiAgentPort $health $agentPort; $ready=$true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw 'Platform startup did not finish; inspect logs/api.stderr.log.' }
Write-Output 'Quantum control platform: http://127.0.0.1:8765'
Write-Output "Project agent endpoint: http://127.0.0.1:$agentPort"
Write-Output 'Starting the platform does not start an instrument measurement.'
if ($OpenBrowser) { Start-Process 'http://127.0.0.1:8765' }
