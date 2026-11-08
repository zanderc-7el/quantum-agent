Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetRoot = 'E:\work\QuantaMind'
$relative = 'quantamind\calibration\executor.py'
$target = Join-Path $targetRoot $relative
$expected = 'E304C874FEB7CA8C6581A0732CA35B2D33BC6EBB1530D0EC918086C94F9A850C'
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $expected) {
  throw "目标文件已变化，停止应用补丁：$target"
}
foreach ($test in @('test_measure_once.py')) {
  $destination = Join-Path $targetRoot (Join-Path 'tests\calibration' $test)
  if (Test-Path -LiteralPath $destination) { throw "测试文件已存在：$destination" }
}
Copy-Item -LiteralPath (Join-Path $sourceRoot $relative) -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $sourceRoot 'tests\calibration\test_measure_once.py') -Destination (Join-Path $targetRoot 'tests\calibration\test_measure_once.py')
Write-Output 'Calibration executor and single-measurement test updated.'
