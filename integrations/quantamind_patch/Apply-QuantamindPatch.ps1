Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$sourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetRoot = 'E:\work\QuantaMind'
$files = @(
  @{
    Relative = 'quantamind\calibration\models.py'
    Sha256 = '7EAC56D034EDE1978D5299C9D1F58A5BE722E991CFED2CFCA4FEABD39DE1904B'
  },
  @{
    Relative = 'backend\quantamind_api\services\calibration_service.py'
    Sha256 = 'ACCDBC01329B1D0E40B1A00865DD6DF648B98C0BBBE4FAF006E94704771DA6D7'
  }
)

# Check every preimage before changing any file. QuantaMind is a separate,
# actively edited repository; a changed target requires a fresh review.
foreach ($file in $files) {
  $target = Join-Path $targetRoot $file.Relative
  if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw "目标文件不存在：$target"
  }
  $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
  if ($actual -ne $file.Sha256) {
    throw "目标文件已变化，停止应用补丁：$target"
  }
}

foreach ($file in $files) {
  $source = Join-Path $sourceRoot $file.Relative
  $target = Join-Path $targetRoot $file.Relative
  Copy-Item -LiteralPath $source -Destination $target -Force
  Write-Output "已更新 $target"
}
