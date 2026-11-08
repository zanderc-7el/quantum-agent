Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetRoot = 'E:\work\QuantaMind'
$relative = 'quantamind\calibration\adapters.py'
$target = Join-Path $targetRoot $relative
$expected = '608D4B93E26CF0A99C06ECC628435CFE59F4F4D0AAD306D4A43FF6E406DB669D'
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $expected) {
  throw "Target changed: $target"
}
foreach ($newFile in @('quantamind\calibration\simulated_traces.py', 'tests\calibration\test_trace_evidence.py')) {
  if (Test-Path -LiteralPath (Join-Path $targetRoot $newFile)) {
    throw "Target already exists: $newFile"
  }
}
Copy-Item -LiteralPath (Join-Path $sourceRoot $relative) -Destination $target -Force
foreach ($newFile in @('quantamind\calibration\simulated_traces.py', 'tests\calibration\test_trace_evidence.py')) {
  Copy-Item -LiteralPath (Join-Path $sourceRoot $newFile) -Destination (Join-Path $targetRoot $newFile)
}
Write-Output 'Simulator trace evidence added to QuantaMind.'
