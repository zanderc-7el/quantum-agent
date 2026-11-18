$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$session = $null
$headers = $null
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 10
    if ($health.projectId -ne 'qcal-local-f8-platform' -or $health.projectRoot -ne $projectRoot) { throw 'Port 8765 does not confirm this project.' }
    $session = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/session' -TimeoutSec 5
    $headers = @{'X-Qcal-Token'=$session.token}
    $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/system/shutdown' -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 5
    if ($response.status -ne 'stopping_at_checkpoint') { throw 'API did not confirm checkpoint shutdown; no process was stopped.' }
    Write-Output 'Experiment API accepted checkpoint shutdown. The model service remains available; no processes were forcibly stopped.'
} finally {
    $session = $null
    $headers = $null
}
